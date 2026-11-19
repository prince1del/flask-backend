"""Bill copies and bulk bill PDFs.

* **Supplier's bill copy** (owner's note, 19-11-2026): with every purchase bill / expense you can keep the copy of
  the bill the supplier gave you — camera photo, gallery picture or PDF. Purchase bills without one show as
  "Copy pending" until it is added. Settings → Transaction → "Keep the supplier's bill copy …" turns this on / off.
* **Bulk PDF**: every bill of the chosen kinds between two dates in one PDF to print or download, with or without
  the supplier copies placed after each purchase bill.

Files sit next to the database: <db folder>/bill_copies/<workspace>/<bill id>_<copy id>.jpg|pdf (in server backups).
"""
from __future__ import annotations

import io
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

MAX_PDF_BYTES = 12 * 1024 * 1024
MAX_COPIES = 10
MAX_BULK_BILLS = 400
# bills that come from a supplier and can carry their copy
COPY_TYPES = {2: "Purchase Bill", 7: "Expense", 16: "Debit Note", 66: "Purchase Order"}
PENDING_TYPES = (2, 7)
BULK_KINDS = {
    "sale": [1], "purchase": [2], "expense": [7], "payment_in": [3], "payment_out": [4],
    "credit_note": [21], "debit_note": [16], "estimate": [27], "proforma": [83], "sale_order": [65],
    "purchase_order": [66], "challan": [30], "other_income": [29],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS hop_bill_copies (
               id TEXT PRIMARY KEY,
               workspace_id TEXT NOT NULL,
               txn_id INTEGER NOT NULL,
               file_kind TEXT NOT NULL,          -- jpg | pdf
               file_name TEXT,
               bytes INTEGER,
               created_by TEXT,
               created_at TEXT NOT NULL
           )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_hop_bill_copies_txn ON hop_bill_copies(workspace_id, txn_id)")


def _dir(db_path: str, ws: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(ws))[:80] or "main"
    d = os.path.join(os.path.dirname(os.path.abspath(db_path)), "bill_copies", safe)
    os.makedirs(d, exist_ok=True)
    return d


def file_path(db_path: str, ws: str, txn_id: int, copy_id: str, kind: str) -> str:
    cid = re.sub(r"[^0-9a-f]", "", str(copy_id).lower())[:32]
    if not cid or kind not in ("jpg", "pdf"):
        raise LookupError("Copy not found")
    return os.path.join(_dir(db_path, ws), f"{int(txn_id)}_{cid}.{kind}")


def _bill(conn: sqlite3.Connection, ws: str, txn_id: int) -> dict[str, Any]:
    r = conn.execute("SELECT id, txn_type, txn_number, txn_date, party_id, party_type, total_amount FROM hop_party_transactions "
                     "WHERE workspace_id=? AND id=? AND (deleted_at IS NULL OR trim(deleted_at)='')", (ws, int(txn_id))).fetchone()
    if not r:
        raise LookupError("Bill not found")
    return dict(r)


def list_copies(conn: sqlite3.Connection, ws: str, txn_id: int) -> list[dict[str, Any]]:
    ensure_schema(conn)
    return [dict(r) for r in conn.execute(
        "SELECT id, txn_id, file_kind, file_name, bytes, created_by, created_at FROM hop_bill_copies "
        "WHERE workspace_id=? AND txn_id=? ORDER BY created_at", (ws, int(txn_id))).fetchall()]


def add_copy(conn: sqlite3.Connection, db_path: str, ws: str, txn_id: int, data: bytes, file_name: str = "",
             user: str = "") -> dict[str, Any]:
    """Photo (any phone image → JPEG) or PDF of the supplier's bill."""
    ensure_schema(conn)
    _bill(conn, ws, txn_id)
    if len(list_copies(conn, ws, txn_id)) >= MAX_COPIES:
        raise ValueError(f"A bill can keep up to {MAX_COPIES} copies")
    if not data:
        raise ValueError("Choose a photo or PDF of the bill")
    if data[:5] == b"%PDF-":
        if len(data) > MAX_PDF_BYTES:
            raise ValueError("PDF is too big (max 12 MB)")
        kind, body = "pdf", data
    else:
        from app.hop_product_photos import to_jpeg

        kind, body = "jpg", to_jpeg(data, 2000)   # bills stay readable
    cid = uuid.uuid4().hex[:16]
    with open(file_path(db_path, ws, txn_id, cid, kind), "wb") as f:
        f.write(body)
    conn.execute("INSERT INTO hop_bill_copies (id, workspace_id, txn_id, file_kind, file_name, bytes, created_by, created_at) "
                 "VALUES (?,?,?,?,?,?,?,?)", (cid, ws, int(txn_id), kind, (file_name or "")[:120] or None, len(body),
                                              (user or "")[:60] or None, _now()))
    conn.commit()
    return {"txn_id": int(txn_id), "copies": list_copies(conn, ws, txn_id)}


def get_copy(conn: sqlite3.Connection, db_path: str, ws: str, txn_id: int, copy_id: str) -> tuple[str, str]:
    ensure_schema(conn)
    r = conn.execute("SELECT file_kind FROM hop_bill_copies WHERE workspace_id=? AND txn_id=? AND id=?",
                     (ws, int(txn_id), str(copy_id))).fetchone()
    if not r:
        raise LookupError("Copy not found")
    p = file_path(db_path, ws, txn_id, copy_id, r[0])
    if not os.path.exists(p):
        raise LookupError("Copy file is missing")
    return p, ("application/pdf" if r[0] == "pdf" else "image/jpeg")


def remove_copy(conn: sqlite3.Connection, db_path: str, ws: str, txn_id: int, copy_id: str) -> dict[str, Any]:
    p, _ = get_copy(conn, db_path, ws, txn_id, copy_id)
    os.remove(p)
    conn.execute("DELETE FROM hop_bill_copies WHERE workspace_id=? AND txn_id=? AND id=?", (ws, int(txn_id), str(copy_id)))
    conn.commit()
    return {"txn_id": int(txn_id), "copies": list_copies(conn, ws, txn_id)}


def tracking_on(conn: sqlite3.Connection, ws: str) -> bool:
    from app import hop_app_settings

    return bool(hop_app_settings.get(conn, ws, "purchase_copy_track"))


def pending(conn: sqlite3.Connection, ws: str, limit: int = 200) -> dict[str, Any]:
    """Purchase bills / expenses with a supplier but no copy yet (empty list when the setting is off)."""
    ensure_schema(conn)
    if not tracking_on(conn, ws):
        return {"on": False, "count": 0, "bills": []}
    q = f"""SELECT t.id, t.txn_type, t.txn_number, t.txn_date, t.total_amount, t.party_bill_no,
                   COALESCE(v.company, '') AS party_name
            FROM hop_party_transactions t
            LEFT JOIN hop_vendors v ON v.workspace_id=t.workspace_id AND v.id=t.party_id AND t.party_type='vendor'
            WHERE t.workspace_id=? AND t.txn_type IN ({','.join('?' * len(PENDING_TYPES))})
              AND (t.deleted_at IS NULL OR trim(t.deleted_at)='')
              AND (t.txn_type=2 OR t.party_type='vendor')
              AND NOT EXISTS (SELECT 1 FROM hop_bill_copies c WHERE c.workspace_id=t.workspace_id AND c.txn_id=t.id)
            ORDER BY date(t.txn_date) DESC, t.id DESC"""
    rows = [dict(r) for r in conn.execute(q, (ws, *PENDING_TYPES)).fetchall()]
    return {"on": True, "count": len(rows), "bills": rows[:limit]}


def copy_counts(conn: sqlite3.Connection, ws: str, txn_ids: list[int]) -> dict[int, int]:
    ensure_schema(conn)
    if not txn_ids:
        return {}
    out: dict[int, int] = {}
    for i in range(0, len(txn_ids), 500):
        part = txn_ids[i:i + 500]
        for r in conn.execute(f"SELECT txn_id, COUNT(*) FROM hop_bill_copies WHERE workspace_id=? AND txn_id IN "
                              f"({','.join('?' * len(part))}) GROUP BY txn_id", (ws, *part)):
            out[int(r[0])] = int(r[1])
    return out


# ── bulk PDF ───────────────────────────────────────────────────────────────────────────────────────────────
def bulk_bills(conn: sqlite3.Connection, ws: str, date_from: str, date_to: str, kinds: list[str]) -> list[dict[str, Any]]:
    types = sorted({t for k in kinds for t in BULK_KINDS.get(k, [])})
    if not types:
        raise ValueError("Choose what to include (sale invoices, purchase bills …)")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_from or "") or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_to or ""):
        raise ValueError("Choose the From and To dates")
    if date_from > date_to:
        raise ValueError("From date is after To date")
    q = f"""SELECT id, txn_type, txn_number, txn_date, total_amount FROM hop_party_transactions
            WHERE workspace_id=? AND txn_type IN ({','.join('?' * len(types))})
              AND (deleted_at IS NULL OR trim(deleted_at)='') AND date(txn_date) BETWEEN date(?) AND date(?)
            ORDER BY txn_type, date(txn_date), id"""
    return [dict(r) for r in conn.execute(q, (ws, *types, date_from, date_to)).fetchall()]


def bulk_summary(conn: sqlite3.Connection, ws: str, date_from: str, date_to: str, kinds: list[str]) -> dict[str, Any]:
    rows = bulk_bills(conn, ws, date_from, date_to, kinds)
    counts = copy_counts(conn, ws, [int(r["id"]) for r in rows])
    by: dict[int, dict[str, Any]] = {}
    for r in rows:
        b = by.setdefault(int(r["txn_type"]), {"txn_type": int(r["txn_type"]), "count": 0, "total": 0.0})
        b["count"] += 1
        b["total"] = round(b["total"] + float(r["total_amount"] or 0), 2)
    return {"count": len(rows), "with_copy": sum(1 for r in rows if counts.get(int(r["id"]))),
            "copies": sum(counts.values()), "by_type": list(by.values()), "max": MAX_BULK_BILLS}


def _image_pdf(path: str, title: str) -> bytes:
    from fpdf import FPDF
    from PIL import Image

    with Image.open(path) as im:
        w, h = im.size
    pdf = FPDF(unit="mm", format="A4")
    pdf.set_auto_page_break(False)
    pdf.add_page()
    pdf.set_font("Helvetica", size=8)
    pdf.set_xy(8, 4)
    pdf.cell(0, 5, title[:110])
    box_w, box_h = 194.0, 280.0
    scale = min(box_w / w, box_h / h)
    iw, ih = w * scale, h * scale
    pdf.image(path, x=8 + (box_w - iw) / 2, y=12 + (box_h - ih) / 2, w=iw, h=ih)
    return bytes(pdf.output())


def build_bulk_pdf(conn: sqlite3.Connection, db_path: str, ws: str, date_from: str, date_to: str, kinds: list[str],
                   with_copies: bool, theme: Any = None) -> tuple[bytes, int]:
    """One PDF: every chosen bill (same layout as its own PDF), supplier copies after each purchase bill if asked."""
    import pypdfium2 as pdfium

    from app.hop_doc_pdf import build_preview_pdf
    from app.hop_doc_preview import build_txn_preview

    rows = bulk_bills(conn, ws, date_from, date_to, kinds)
    if not rows:
        raise ValueError("No bills in these dates")
    if len(rows) > MAX_BULK_BILLS:
        raise ValueError(f"{len(rows)} bills — choose shorter dates (up to {MAX_BULK_BILLS} in one PDF)")
    out = pdfium.PdfDocument.new()

    def add(pdf_bytes: bytes) -> None:
        src = pdfium.PdfDocument(pdf_bytes)
        out.import_pages(src)
        src.close()

    for r in rows:
        data = build_txn_preview(conn, ws, party_txn_id=int(r["id"]))
        if not data:
            continue
        add(build_preview_pdf(data, theme=theme))
        if with_copies and int(r["txn_type"]) in COPY_TYPES:
            for c in list_copies(conn, ws, int(r["id"])):
                try:
                    p, _ = get_copy(conn, db_path, ws, int(r["id"]), c["id"])
                    add(open(p, "rb").read() if c["file_kind"] == "pdf"
                        else _image_pdf(p, f"Supplier copy of {r.get('txn_number') or ''} ({r.get('txn_date') or ''})"))
                except Exception:
                    continue   # a broken copy never stops the rest
    buf = io.BytesIO()
    out.save(buf)
    out.close()
    return buf.getvalue(), len(rows)
