"""Product photos (catalogue / bills): one photo per product, kept next to the database.

* Upload JPG / PNG / WEBP / HEIC-converted JPG up to 8 MB; stored as a JPEG, longest side 1200 px.
* Folder: <db folder>/product_photos/<workspace>/<product id>.jpg — included in server backups of /var/data.
* hop_products.photo_v changes on every upload so phones refresh their cached copy.
"""
from __future__ import annotations

import io
import os
import re
import sqlite3
from datetime import datetime, timezone

MAX_BYTES = 8 * 1024 * 1024
MAX_SIDE = 1200


def _dir(db_path: str, ws: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_\-]", "_", str(ws))[:80] or "main"
    d = os.path.join(os.path.dirname(os.path.abspath(db_path)), "product_photos", safe)
    os.makedirs(d, exist_ok=True)
    return d


def path_for(db_path: str, ws: str, product_id: int) -> str:
    return os.path.join(_dir(db_path, ws), f"{int(product_id)}.jpg")


def ensure_column(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(hop_products)")}
    if "photo_v" not in cols:
        conn.execute("ALTER TABLE hop_products ADD COLUMN photo_v TEXT")
        conn.commit()
    if "photo_hash" not in cols:   # fingerprint for "Find by photo" (dHash, 64 bits as 16 hex)
        conn.execute("ALTER TABLE hop_products ADD COLUMN photo_hash TEXT")
        conn.commit()


def save(conn: sqlite3.Connection, db_path: str, ws: str, product_id: int, data: bytes) -> dict:
    ensure_column(conn)
    if not conn.execute("SELECT 1 FROM hop_products WHERE workspace_id=? AND id=?", (ws, int(product_id))).fetchone():
        raise LookupError("Product not found")
    jpg = to_jpeg(data)
    with open(path_for(db_path, ws, product_id), "wb") as f:
        f.write(jpg)
    v = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    conn.execute("UPDATE hop_products SET photo_v=?, photo_hash=? WHERE workspace_id=? AND id=?",
                 (v, fingerprint(jpg), ws, int(product_id)))
    conn.commit()
    return {"product_id": int(product_id), "photo_v": v, "bytes": len(jpg)}


def to_jpeg(data: bytes, max_side: int = MAX_SIDE) -> bytes:
    """Any phone photo / screenshot -> upright JPEG, longest side ``max_side`` (also used by note photos)."""
    if not data:
        raise ValueError("Empty photo")
    if len(data) > MAX_BYTES:
        raise ValueError("Photo is too big (max 8 MB)")
    try:
        from PIL import Image, ImageOps

        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.thumbnail((max_side, max_side))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=85, optimize=True)
        return out.getvalue()
    except Exception as exc:  # not an image Pillow can read
        raise ValueError("Please choose a JPG, PNG or WEBP photo") from exc


def remove(conn: sqlite3.Connection, db_path: str, ws: str, product_id: int) -> None:
    ensure_column(conn)
    p = path_for(db_path, ws, product_id)
    if os.path.exists(p):
        os.remove(p)
    conn.execute("UPDATE hop_products SET photo_v=NULL, photo_hash=NULL WHERE workspace_id=? AND id=?", (ws, int(product_id)))
    conn.commit()


def _hashes(img) -> list[int]:
    """dHash of the middle 70% of the photo (the product, less background), plus the same turned 90° / 180° / 270°."""
    from PIL import Image

    w, h = img.size
    box = (int(w * 0.15), int(h * 0.15), int(w * 0.85), int(h * 0.85))
    g = img.convert("L").crop(box)
    out = []
    for turn in (0, 90, 180, 270):
        t = g.rotate(turn, expand=True) if turn else g
        px = list(t.resize((9, 8), Image.LANCZOS).tobytes())
        bits = 0
        for row in range(8):
            for col in range(8):
                bits = (bits << 1) | (1 if px[row * 9 + col] > px[row * 9 + col + 1] else 0)
        out.append(bits)
    return out


def fingerprint(data: bytes) -> str | None:
    """Photo fingerprint (16 hex = 64-bit dHash of the upright photo); None if the photo cannot be read."""
    try:
        from PIL import Image, ImageOps

        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        return f"{_hashes(img)[0]:016x}"
    except Exception:
        return None


def match(conn: sqlite3.Connection, db_path: str, ws: str, data: bytes, limit: int = 3) -> list[dict]:
    """Items whose saved photo looks most like this one (no AI cost). Each: id, name, code, score 0-100.

    Products photographed before fingerprints existed get theirs now (read from the saved JPEG, written once).
    """
    from PIL import Image, ImageOps

    ensure_column(conn)
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(to_jpeg(data, 800))))
    except Exception as exc:
        raise ValueError("Please choose a JPG, PNG or WEBP photo") from exc
    probes = _hashes(img)
    rows = conn.execute(
        "SELECT id, name, code, barcode, selling_price, purchase_price, mrp, gst_pct, hsn, unit, photo_hash, photo_v "
        "FROM hop_products WHERE workspace_id=? AND photo_v IS NOT NULL AND (deleted_at IS NULL OR trim(deleted_at)='')",
        (ws,)).fetchall()
    scored = []
    for r in rows:
        d = dict(r)
        h = d.get("photo_hash")
        if not h:
            p = path_for(db_path, ws, d["id"])
            if not os.path.exists(p):
                continue
            with open(p, "rb") as f:
                h = fingerprint(f.read())
            if not h:
                continue
            conn.execute("UPDATE hop_products SET photo_hash=? WHERE workspace_id=? AND id=?", (h, ws, d["id"]))
        hv = int(h, 16)
        dist = min(bin(hv ^ pb).count("1") for pb in probes)
        d.pop("photo_hash", None)
        d["score"] = max(0, round(100 - dist * 100 / 32))   # 0 bits apart = 100, 32+ bits apart = 0
        scored.append(d)
    conn.commit()
    scored.sort(key=lambda x: -x["score"])
    return [x for x in scored if x["score"] >= 40][:limit]
