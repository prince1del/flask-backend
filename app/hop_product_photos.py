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


def save(conn: sqlite3.Connection, db_path: str, ws: str, product_id: int, data: bytes) -> dict:
    ensure_column(conn)
    if not conn.execute("SELECT 1 FROM hop_products WHERE workspace_id=? AND id=?", (ws, int(product_id))).fetchone():
        raise LookupError("Product not found")
    jpg = to_jpeg(data)
    with open(path_for(db_path, ws, product_id), "wb") as f:
        f.write(jpg)
    v = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    conn.execute("UPDATE hop_products SET photo_v=? WHERE workspace_id=? AND id=?", (v, ws, int(product_id)))
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
    conn.execute("UPDATE hop_products SET photo_v=NULL WHERE workspace_id=? AND id=?", (ws, int(product_id)))
    conn.commit()
