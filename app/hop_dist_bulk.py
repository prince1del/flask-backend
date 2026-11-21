"""Distributor: bulk import of the company's documents — SO, CI (company invoice), credit notes, debit notes.

No row limit. One upload can be an Excel / CSV (one row per line, many documents), a PDF or photo of one document,
or a ZIP / RAR holding any mix of these.

  - Excel / CSV is read here for free: the header row is found anywhere, every column gets a role (guessed,
    remembered per sheet layout, or ✨ AI match columns), rows are grouped into documents by their number.
  - PDF / photo needs ✨ AI (one capped call per file). They wait in the job until the user taps "Read with AI",
    which reads a few at a time (so a big ZIP never times out) — the phone shows how many AI reads it will use.

Nothing is saved until ``commit``: the preview lists every document with its lines, total, whether it is already
saved, and checks against the Article Master / my order / the SO. The user can untick documents.

  so → hop_dist_so (my order linked when the sheet's order no. matches one of my POs)
  ci → Purchase Bill from the company (stock comes in; linked to the SO when the SO no. matches)
  cn → company credit note → Purchase Return / Dr. Note (reduces what I owe; goods lines move stock back,
        amount-only lines do not)
  dn → company debit note → Purchase Bill marked "Debit note" (adds to what I owe)
"""

from __future__ import annotations

import csv
import io
import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KINDS = {"so": "Sales Order (SO)", "ci": "Company invoice (CI)", "cn": "Credit note", "dn": "Debit note"}
ROLES = ("doc_no", "doc_date", "ref_no", "code", "name", "qty", "rate", "amount", "gst_pct", "hsn", "doc_total", "skip")
ROLE_LABELS = {"doc_no": "Document no.", "doc_date": "Date", "ref_no": "Reference (order / SO / invoice no.)",
               "code": "Article code", "name": "Article name", "qty": "Qty", "rate": "Rate", "amount": "Line amount",
               "gst_pct": "GST %", "hsn": "HSN", "doc_total": "Document total", "skip": "Skip"}
MAX_FILE = 40 * 1024 * 1024            # one upload (a ZIP of many PDFs fits)
MAX_FILES_IN_ARCHIVE = 2000
_SHEET_EXT = (".xlsx", ".xlsm", ".xls", ".csv")
_DOC_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".webp")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _s(v: Any) -> str:
    return str(v if v is not None else "").strip()


def _f(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    t = re.sub(r"[₹,\s]|rs\.?|inr", "", str(v), flags=re.I)
    neg = t.startswith("(") and t.endswith(")")
    try:
        x = float(t.strip("()") or 0)
    except ValueError:
        return 0.0
    return -x if neg else x


def _norm(h: Any) -> str:
    return re.sub(r"[^a-z0-9%]+", " ", _s(h).lower()).strip()


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS hop_dist_bulk_jobs (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               workspace_id TEXT NOT NULL,
               kind TEXT NOT NULL,
               vendor_id INTEGER,
               docs_json TEXT NOT NULL DEFAULT '[]',
               sheets_json TEXT NOT NULL DEFAULT '[]',
               created_at TEXT NOT NULL,
               done_at TEXT
           )""")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS hop_dist_bulk_files (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               job_id INTEGER NOT NULL,
               workspace_id TEXT NOT NULL,
               name TEXT NOT NULL,
               mime TEXT NOT NULL,
               data BLOB,
               state TEXT NOT NULL DEFAULT 'waiting',   -- waiting | read | failed
               note TEXT
           )""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_hop_dist_bulk_files ON hop_dist_bulk_files(job_id)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS hop_dist_sheet_maps (
               workspace_id TEXT NOT NULL, sig TEXT NOT NULL, roles_json TEXT NOT NULL, headers TEXT,
               updated_at TEXT NOT NULL, PRIMARY KEY (workspace_id, sig))""")
    # jobs older than 2 days are cleared (they only hold an unfinished preview)
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
    ids = [r[0] for r in conn.execute("SELECT id FROM hop_dist_bulk_jobs WHERE created_at < ?", (old,))]
    if ids:
        q = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM hop_dist_bulk_files WHERE job_id IN ({q})", ids)
        conn.execute(f"DELETE FROM hop_dist_bulk_jobs WHERE id IN ({q})", ids)


# ── files: unpack ZIP / RAR, keep sheets + documents ─────────────────────────────────────────────────────────
def _mime(name: str) -> str:
    n = name.lower()
    return ("application/pdf" if n.endswith(".pdf") else "image/png" if n.endswith(".png") else
            "image/webp" if n.endswith(".webp") else "image/jpeg")


def _keep(name: str) -> bool:
    n = name.lower()
    base = Path(n).name
    return not base.startswith(("~$", ".")) and "__macosx" not in n and n.endswith(_SHEET_EXT + _DOC_EXT)


def unpack(data: bytes, filename: str) -> list[tuple[str, bytes]]:
    """(name, bytes) of every sheet / PDF / photo in the upload (a ZIP or RAR is opened, also one inside another)."""
    name = Path(filename or "file").name
    low = name.lower()
    if low.endswith(".zip") or (data[:4] == b"PK\x03\x04" and not low.endswith((".xlsx", ".xlsm"))):
        out: list[tuple[str, bytes]] = []
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for info in z.infolist()[:MAX_FILES_IN_ARCHIVE]:
                if info.is_dir():
                    continue
                inner = info.filename
                if inner.lower().endswith((".zip", ".rar")):
                    out += unpack(z.read(info), inner)
                elif _keep(inner):
                    out.append((Path(inner).name, z.read(info)))
        return out
    if low.endswith(".rar") or data[:4] == b"Rar!":
        return _unrar(data)
    if _keep(low):
        return [(name, data)]
    raise ValueError(f"{name}: send Excel, CSV, PDF, a photo, ZIP or RAR")


def _unrar(data: bytes) -> list[tuple[str, bytes]]:
    dest = tempfile.mkdtemp(prefix="nx_rar_")
    src = Path(dest) / "in.rar"
    try:
        src.write_bytes(data)
        out_dir = Path(dest) / "out"
        out_dir.mkdir()
        for cmd in (["unar", "-q", "-f", "-o", str(out_dir), str(src)], ["unrar", "x", "-o+", "-y", str(src), str(out_dir) + "/"],
                    ["bsdtar", "-xf", str(src), "-C", str(out_dir)]):
            if not shutil.which(cmd[0]):
                continue
            try:
                p = subprocess.run(cmd, capture_output=True, timeout=180, check=False)
            except (OSError, subprocess.TimeoutExpired):
                continue
            if p.returncode != 0:
                continue
            files = [f for f in sorted(out_dir.rglob("*")) if f.is_file()][:MAX_FILES_IN_ARCHIVE]
            got: list[tuple[str, bytes]] = []
            for f in files:
                if f.name.lower().endswith(".zip"):
                    got += unpack(f.read_bytes(), f.name)
                elif _keep(f.name):
                    got.append((f.name, f.read_bytes()))
            return got
        raise ValueError("Could not open the RAR on the server. Send the same files as a ZIP.")
    finally:
        shutil.rmtree(dest, ignore_errors=True)


def read_rows(data: bytes, filename: str) -> list[list[Any]]:
    """Every row of the first useful sheet — no row limit."""
    low = filename.lower()
    if low.endswith(".csv"):
        text = data.decode("utf-8-sig", errors="replace")
        return [list(r) for r in csv.reader(io.StringIO(text))]
    if low.endswith(".xls"):
        try:
            import xlrd  # type: ignore
        except ImportError as exc:
            raise ValueError(f"{filename}: old .xls — open it and save as .xlsx") from exc
        book = xlrd.open_workbook(file_contents=data)
        best = max(book.sheets(), key=lambda s: s.nrows)
        return [best.row_values(i) for i in range(best.nrows)]
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    best, best_score = None, -1
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        score = max((_header_score(r) for r in rows[:30]), default=0) * 100000 + len(rows)
        if score > best_score:
            best, best_score = rows, score
    wb.close()
    return best or []


# ── columns → roles ──────────────────────────────────────────────────────────────────────────────────────────
_WORDS = {
    "doc_date": ("date", "dt"),
    "code": ("article", "article code", "art no", "material", "material code", "item code", "sku", "code", "style",
             "quality no", "design no"),
    "name": ("description", "material description", "item", "item name", "article name", "product", "particulars",
             "article description", "item description", "quality"),
    "qty": ("qty", "quantity", "billed qty", "order qty", "pcs", "nos", "inv qty", "so qty", "units"),
    "rate": ("rate", "price", "unit price", "net price", "dp", "basic rate", "rate per unit", "nlc"),
    "amount": ("amount", "value", "net value", "taxable value", "taxable", "line total", "basic value", "taxable amount",
               "net amount"),
    "gst_pct": ("gst %", "gst", "gst rate", "tax %", "tax rate", "igst %", "gst%"),
    "hsn": ("hsn", "hsn code", "hsn sac"),
    "doc_total": ("invoice value", "grand total", "total value", "bill amount", "invoice amount", "note amount",
                  "document total", "total amount", "gross value"),
}
_NO = ("no", "number", "num", "nr", "#", "doc")
_SO_W = ("so", "sales order", "sale order", "s o", "order")
_CI_W = ("invoice", "inv", "bill", "billing document", "ci", "tax invoice")
_CN_W = ("credit note", "cn", "credit memo", "cr note")
_DN_W = ("debit note", "dn", "debit memo", "dr note")
_PO_W = ("po", "purchase order", "customer po", "my order", "cust po", "buyer order")


def _has(h: str, words: tuple[str, ...]) -> bool:
    return any(re.search(rf"(^| ){re.escape(w)}( |$)", h) for w in words)


def _number_of(h: str, words: tuple[str, ...]) -> bool:
    return _has(h, words) and (_has(h, _NO) or h in words)


def guess_role(kind: str, header: Any) -> str:
    h = _norm(header)
    if not h:
        return "skip"
    own = {"so": _SO_W, "ci": _CI_W, "cn": _CN_W, "dn": _DN_W}[kind]
    if _has(h, ("date",)) and not _has(h, ("due", "delivery", "lr", "po", "order")) or h in ("dt", "doc date"):
        return "doc_date" if not (_has(h, ("po",)) or (kind != "so" and _has(h, ("so", "order")))) else "skip"
    if _number_of(h, own) or (kind == "ci" and _has(h, ("billing document",))):
        return "doc_no"
    ref = {"so": _PO_W, "ci": _SO_W + _PO_W, "cn": _CI_W, "dn": _CI_W}[kind]
    if _number_of(h, ref) or (kind in ("cn", "dn") and _has(h, ("against", "ref", "reference"))):
        return "ref_no"
    if h in ("document no", "doc no", "document number", "voucher no", "number", "no", "doc number", "reference no",
             "ref no", "vch no", "bill no"):
        return "doc_no"
    for role in ("hsn", "gst_pct", "doc_total", "qty", "rate", "amount", "code", "name"):
        if _has(h, _WORDS[role]) or h in _WORDS[role]:
            if role == "gst_pct" and _has(h, ("amount", "amt", "value")):
                return "skip"
            if role == "amount" and _has(h, ("gst", "igst", "cgst", "sgst", "tax")) and not _has(h, ("taxable",)):
                return "skip"
            return role
    return "skip"


def _header_score(row: list[Any]) -> int:
    return sum(1 for c in row if _s(c) and not isinstance(c, (int, float)) and
               any(guess_role(k, c) != "skip" for k in ("so", "ci")))


def layout_sig(kind: str, headers: list[str]) -> str:
    import hashlib

    return kind + ":" + hashlib.sha1("|".join(_norm(h) for h in headers).encode()).hexdigest()[:20]


def _date(v: Any) -> str:
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, (int, float)) and 20000 < v < 80000:          # Excel serial day
        return (date(1899, 12, 30) + timedelta(days=int(v))).isoformat()
    t = _s(v)[:20]
    m = re.match(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", t)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.match(r"(\d{1,2})[-/.](\d{1,2}|[A-Za-z]{3})[-/.](\d{2,4})", t)
        if not m:
            return ""
        d, mo_s, y = int(m.group(1)), m.group(2), int(m.group(3))
        mons = "jan feb mar apr may jun jul aug sep oct nov dec".split()
        mo = mons.index(mo_s.lower()) + 1 if mo_s.isalpha() and mo_s.lower() in mons else int(mo_s) if mo_s.isdigit() else 0
        y = y + 2000 if y < 100 else y
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return ""


def _docno(v: Any) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return _s(v)[:60]


def analyze_sheet(kind: str, rows: list[list[Any]], name: str, saved: dict[str, str] | None = None,
                  roles: dict[str, str] | None = None) -> dict[str, Any]:
    """Header row + columns with samples and roles (user's > remembered > guessed)."""
    hi = max(range(min(len(rows), 30)), key=lambda i: _header_score(rows[i]), default=0)
    header = [_s(c) for c in (rows[hi] if rows else [])]
    body = rows[hi + 1:]
    cols = []
    for i, h in enumerate(header):
        samples = [_s(r[i])[:30] for r in body[:200] if i < len(r) and _s(r[i])][:3]
        if not h and not samples:
            continue
        role = (roles or {}).get(str(i)) or (saved or {}).get(str(i)) or guess_role(kind, h)
        cols.append({"i": i, "header": h or f"Column {i + 1}", "samples": samples, "role": role if role in ROLES else "skip"})
    return {"name": name, "header_row": hi + 1, "columns": cols, "sig": layout_sig(kind, header),
            "rows": len(body), "_body": body}


def build_docs(kind: str, sheet: dict[str, Any]) -> list[dict[str, Any]]:
    """Group the rows into documents (a row without a number continues the document above)."""
    role_of = {c["i"]: c["role"] for c in sheet["columns"] if c["role"] != "skip"}
    if "doc_no" not in role_of.values():
        raise ValueError(f"{sheet['name']}: no column for the {KINDS[kind]} number — pick it in the columns list")
    docs: dict[str, dict[str, Any]] = {}
    cur: dict[str, Any] | None = None
    for r in sheet["_body"]:
        val = {role: r[i] for i, role in role_of.items() if i < len(r)}
        no = _docno(val.get("doc_no"))
        low = _norm(" ".join(_s(x) for x in r[:4]))
        if not no and not any(_s(v) for v in val.values()):
            continue
        if low.startswith(("total", "grand total", "sub total")) and not _s(val.get("code")):
            continue
        if no:
            cur = docs.get(no.lower())
            if cur is None:
                cur = docs[no.lower()] = {"doc_no": no, "doc_date": "", "ref_no": "", "lines": [], "doc_total": 0.0,
                                          "source": sheet["name"]}
        if cur is None:
            continue
        cur["doc_date"] = cur["doc_date"] or _date(val.get("doc_date"))
        cur["ref_no"] = cur["ref_no"] or _docno(val.get("ref_no"))
        if _f(val.get("doc_total")):
            cur["doc_total"] = _f(val.get("doc_total"))
        name, code = _s(val.get("name"))[:120], _s(val.get("code"))[:60]
        qty, rate, amt = _f(val.get("qty")), _f(val.get("rate")), _f(val.get("amount"))
        if not (name or code) and not amt:
            continue
        if not rate and amt and qty:
            rate = round(amt / qty, 4)
        if not amt and rate:
            amt = round(rate * (qty or 1), 2)
        gst = val.get("gst_pct")
        g = _f(gst)
        if 0 < g < 1:                      # 0.05 in a % cell
            g = round(g * 100, 2)
        cur["lines"].append({"code": code, "item_name": name or code, "qty": qty, "rate": rate, "amount": round(amt, 2),
                             "tax_pct": g if _s(gst) else None, "hsn": _s(val.get("hsn"))[:12]})
    return list(docs.values())


# ── job: preview, AI read, commit ──────────────────────────────────────────────────────────────────────────
def _saved_roles(conn, ws, sig) -> dict[str, str] | None:
    r = conn.execute("SELECT roles_json FROM hop_dist_sheet_maps WHERE workspace_id=? AND sig=?", (ws, sig)).fetchone()
    return json.loads(r[0]) if r else None


def remember_roles(conn, ws, sig: str, roles: dict[str, str], headers: list[str]) -> None:
    clean = {str(k): v for k, v in (roles or {}).items() if v in ROLES}
    conn.execute("INSERT OR REPLACE INTO hop_dist_sheet_maps (workspace_id, sig, roles_json, headers, updated_at) "
                 "VALUES (?,?,?,?,?)", (ws, sig, json.dumps(clean), " | ".join(headers)[:1000], _now()))


def start(conn: sqlite3.Connection, ws: str, kind: str, vendor_id: int | None, files: list[tuple[str, bytes]],
          roles: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    """New job from the uploaded files: sheets are read now, PDFs / photos wait for ✨ AI."""
    ensure_schema(conn)
    if kind not in KINDS:
        raise ValueError("kind must be so, ci, cn or dn")
    vid = _company(conn, ws, vendor_id, required=False)
    parts: list[tuple[str, bytes]] = []
    for name, data in files:
        parts += unpack(data, name)
    if not parts:
        raise ValueError("No Excel, CSV, PDF or photo found in the upload")
    sheet_files = [(n, d) for n, d in parts if n.lower().endswith(_SHEET_EXT)]
    waiting = [(n, d) for n, d in parts if not n.lower().endswith(_SHEET_EXT)]
    docs, sheets = _read_sheets(conn, ws, kind, sheet_files, roles)
    cur = conn.execute("INSERT INTO hop_dist_bulk_jobs (workspace_id, kind, vendor_id, docs_json, sheets_json, created_at) "
                       "VALUES (?,?,?,?,?,?)", (ws, kind, vid, json.dumps(docs, ensure_ascii=False),
                                                json.dumps(sheets, ensure_ascii=False), _now()))
    job = int(cur.lastrowid)
    for name, data in waiting:
        conn.execute("INSERT INTO hop_dist_bulk_files (job_id, workspace_id, name, mime, data) VALUES (?,?,?,?,?)",
                     (job, ws, name[:200], _mime(name), data))
    for name, data in sheet_files:   # kept until the job is saved, so the columns can be changed
        conn.execute("INSERT INTO hop_dist_bulk_files (job_id, workspace_id, name, mime, data, state) VALUES (?,?,?,?,?,'sheet')",
                     (job, ws, name[:200], "sheet", data))
    conn.commit()
    return preview(conn, ws, job)


def _read_sheets(conn, ws, kind, sheet_files, roles=None) -> tuple[list[dict], list[dict]]:
    docs, sheets = [], []
    for name, data in sheet_files:
        try:
            rows = read_rows(data, name)
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001 — a broken workbook
            raise ValueError(f"Could not read {name}. Save it as .xlsx or .csv and try again.") from exc
        sh = analyze_sheet(kind, rows, name)
        saved = _saved_roles(conn, ws, sh["sig"])
        mine = (roles or {}).get(sh["sig"])
        if saved or mine:
            sh = analyze_sheet(kind, rows, name, None if mine else saved, mine)
        sh["remembered"] = bool(saved) and not mine
        try:
            docs += build_docs(kind, sh)
            sh["error"] = None
        except ValueError as exc:
            sh["error"] = str(exc)
        sh.pop("_body")
        sheets.append(sh)
    return docs, sheets


def set_roles(conn: sqlite3.Connection, ws: str, job_id: int, sig: str, roles: dict[str, str]) -> dict[str, Any]:
    """The user changed the column roles of one sheet layout: read the kept sheets again (AI-read PDFs stay)."""
    j = _job(conn, ws, job_id)
    files = [(r[0], r[1]) for r in conn.execute(
        "SELECT name, data FROM hop_dist_bulk_files WHERE job_id=? AND state='sheet' ORDER BY id", (job_id,))]
    if not files:
        raise ValueError("No sheet in this import")
    clean = {str(k): v for k, v in (roles or {}).items() if v in ROLES}
    docs, sheets = _read_sheets(conn, ws, j["kind"], files, {sig: clean})
    docs += [d for d in j["docs"] if d.get("ai")]
    conn.execute("UPDATE hop_dist_bulk_jobs SET docs_json=?, sheets_json=? WHERE id=?",
                 (json.dumps(docs, ensure_ascii=False), json.dumps(sheets, ensure_ascii=False), job_id))
    conn.commit()
    return preview(conn, ws, job_id)


def set_company(conn: sqlite3.Connection, ws: str, job_id: int, vendor_id: int) -> dict[str, Any]:
    _job(conn, ws, job_id)
    vid = _company(conn, ws, vendor_id)
    conn.execute("UPDATE hop_dist_bulk_jobs SET vendor_id=? WHERE id=?", (vid, job_id))
    conn.commit()
    return preview(conn, ws, job_id)


def _company(conn, ws, vendor_id, required: bool = True) -> int | None:
    if vendor_id:
        if not conn.execute("SELECT 1 FROM hop_vendors WHERE workspace_id=? AND id=?", (ws, int(vendor_id))).fetchone():
            raise LookupError("Company not found")
        return int(vendor_id)
    ids = [r[0] for r in conn.execute("SELECT id FROM hop_vendors WHERE workspace_id=? AND is_principal=1", (ws,))]
    if len(ids) == 1:
        return int(ids[0])
    if not required and ids:
        return None          # several companies: the user picks one on the preview
    raise ValueError("Pick the company these documents are from" if ids else
                     "Add the company you buy from first (My brands → + Add brand)")


def _job(conn, ws, job_id) -> dict[str, Any]:
    ensure_schema(conn)
    r = conn.execute("SELECT * FROM hop_dist_bulk_jobs WHERE workspace_id=? AND id=?", (ws, int(job_id))).fetchone()
    if not r:
        raise LookupError("This import has expired — upload the file again")
    d = dict(r)
    d["docs"] = json.loads(d.pop("docs_json") or "[]")
    d["sheets"] = json.loads(d.pop("sheets_json") or "[]")
    return d


def _existing(conn, ws, kind, vid) -> set[str]:
    if kind == "so":
        return {_s(r[0]).lower() for r in conn.execute(
            "SELECT so_number FROM hop_dist_so WHERE workspace_id=? AND deleted_at IS NULL", (ws,))}
    t = {"ci": 2, "dn": 2, "cn": 16}[kind]
    return {_s(r[0]).lower() for r in conn.execute(
        "SELECT party_bill_no FROM hop_party_transactions WHERE workspace_id=? AND txn_type=? AND party_type='vendor' "
        "AND party_id=? AND (deleted_at IS NULL OR trim(deleted_at)='') AND party_bill_no IS NOT NULL", (ws, t, vid))}


def _checks(conn, ws, kind, d: dict) -> list[str]:
    from app import hop_dist_extra as X

    out = []
    if not d["lines"]:
        out.append("No lines found")
    if not d.get("doc_date"):
        out.append("No date — today's date will be used")
    tot = round(sum(_f(ln["amount"]) for ln in d["lines"]), 2)
    if d.get("doc_total") and tot and abs(d["doc_total"] - tot) > max(2.0, d["doc_total"] * 0.01) \
            and abs(d["doc_total"] - tot * 1.05) > 2 and abs(d["doc_total"] - tot * 1.12) > 2 \
            and abs(d["doc_total"] - tot * 1.18) > 2:
        out.append(f"Lines add up to ₹{tot:,.0f} but the document says ₹{d['doc_total']:,.0f}")
    goods = [ln for ln in d["lines"] if ln.get("qty")]
    if kind in ("so", "ci") and goods:
        against = None
        if kind == "ci" and d.get("ref_no"):
            so = conn.execute("SELECT lines_json FROM hop_dist_so WHERE workspace_id=? AND lower(so_number)=lower(?) "
                              "AND deleted_at IS NULL", (ws, d["ref_no"])).fetchone()
            if so:
                against = json.loads(so[0] or "[]")
            else:
                out.append(f"SO {d['ref_no']} is not saved yet — import the SOs first to link them")
        out += X.check_lines(conn, ws, goods[:200], against=against, against_label=f"SO {d.get('ref_no')}")
    return out


def preview(conn: sqlite3.Connection, ws: str, job_id: int, show: int = 400) -> dict[str, Any]:
    j = _job(conn, ws, job_id)
    have = _existing(conn, ws, j["kind"], j["vendor_id"]) if j["vendor_id"] else set()
    docs = []
    for d in j["docs"]:
        exists = d["doc_no"].lower() in have
        docs.append({**{k: d.get(k) for k in ("doc_no", "doc_date", "ref_no", "doc_total", "source")},
                     "lines": len(d["lines"]), "qty": round(sum(_f(x["qty"]) for x in d["lines"]), 2),
                     "amount": round(sum(_f(x["amount"]) for x in d["lines"]), 2), "exists": exists,
                     "first_items": [x["item_name"] for x in d["lines"][:3]]})
    files = [dict(r) for r in conn.execute(
        "SELECT name, state, note FROM hop_dist_bulk_files WHERE job_id=? AND state!='sheet' ORDER BY id", (job_id,))]
    companies = [{"id": r[0], "company": r[1]} for r in conn.execute(
        "SELECT id, company FROM hop_vendors WHERE workspace_id=? AND is_principal=1 ORDER BY lower(company)", (ws,))]
    comp = conn.execute("SELECT company FROM hop_vendors WHERE id=?", (j["vendor_id"],)).fetchone() if j["vendor_id"] else None
    return {"job_id": j["id"], "kind": j["kind"], "kind_label": KINDS[j["kind"]], "company": comp[0] if comp else None,
            "vendor_id": j["vendor_id"], "sheets": j["sheets"], "role_labels": ROLE_LABELS,
            "count": len(docs), "new": sum(1 for d in docs if not d["exists"]), "already": sum(1 for d in docs if d["exists"]),
            "lines": sum(d["lines"] for d in docs), "amount": round(sum(d["amount"] for d in docs if not d["exists"]), 2),
            "docs": docs[:show], "shown": min(show, len(docs)),
            "files_waiting": sum(1 for f in files if f["state"] == "waiting"),
            "files": files[:200], "companies": companies, "roles": list(ROLES),
            "ai_reads_needed": sum(1 for f in files if f["state"] == "waiting")}


def doc_detail(conn, ws, job_id: int, doc_no: str) -> dict[str, Any]:
    j = _job(conn, ws, job_id)
    d = next((x for x in j["docs"] if x["doc_no"].lower() == _s(doc_no).lower()), None)
    if not d:
        raise LookupError("Document not in this import")
    return {**d, "checks": _checks(conn, ws, j["kind"], d)}


def check_all(conn, ws, job_id: int, limit: int = 300) -> dict[str, Any]:
    """✨ Checks for the documents (first ``limit``): Article Master, SO match, totals."""
    j = _job(conn, ws, job_id)
    out = {}
    for d in j["docs"][:limit]:
        c = _checks(conn, ws, j["kind"], d)
        if c:
            out[d["doc_no"]] = c[:6]
    return {"checks": out, "checked": min(limit, len(j["docs"])), "with_checks": len(out)}


def read_waiting(conn: sqlite3.Connection, ws: str, job_id: int, *, user_id: int, n: int = 3) -> dict[str, Any]:
    """✨ AI reads the next ``n`` PDFs / photos of the job (one capped call each) and adds them as documents."""
    from app.services import hop_ai

    j = _job(conn, ws, job_id)
    rows = conn.execute("SELECT id, name, mime, data FROM hop_dist_bulk_files WHERE job_id=? AND state='waiting' "
                        "ORDER BY id LIMIT ?", (job_id, max(1, min(int(n), 5)))).fetchall()
    docs = j["docs"]
    known = {d["doc_no"].lower() for d in docs}
    for fid, name, mime, data in rows:
        try:
            r = hop_ai.read_bill(conn, ws, data, mime, user_id=user_id)
            no = _s(r.get("bill_no"))[:60] or Path(name).stem[:60]
            lines = [{"code": "", "item_name": ln["item_name"], "qty": _f(ln.get("qty")), "rate": _f(ln.get("rate")),
                      "amount": round(_f(ln.get("qty")) * _f(ln.get("rate")), 2), "tax_pct": ln.get("tax_pct"),
                      "hsn": ln.get("hsn") or "", "product_id": ln.get("product_id")} for ln in r.get("lines") or []]
            if not lines and _f(r.get("total")):
                lines = [{"code": "", "item_name": f"{KINDS[j['kind']]} {no}", "qty": 0, "rate": 0,
                          "amount": _f(r.get("total")), "tax_pct": None, "hsn": ""}]
            if no.lower() in known:
                note, state = f"{no} is already in this import", "read"
            else:
                docs.append({"doc_no": no, "doc_date": r.get("bill_date") or "", "ref_no": _s(r.get("po_number"))[:60],
                             "lines": lines, "doc_total": _f(r.get("total")), "source": name, "ai": True})
                known.add(no.lower())
                note, state = f"✨ {no}: {len(lines)} line(s)", "read"
        except ValueError as exc:     # daily AI limit, unreadable file …
            note, state = str(exc)[:200], "failed"
            if "limit" in str(exc).lower():
                conn.execute("UPDATE hop_dist_bulk_files SET note=? WHERE id=?", (note, fid))
                break
        conn.execute("UPDATE hop_dist_bulk_files SET state=?, note=?, data=NULL WHERE id=?", (state, note, fid))
    conn.execute("UPDATE hop_dist_bulk_jobs SET docs_json=? WHERE id=?", (json.dumps(docs, ensure_ascii=False), job_id))
    conn.commit()
    return preview(conn, ws, job_id)


def _my_po(conn, ws, ref: str) -> int | None:
    if not ref:
        return None
    r = conn.execute("SELECT id FROM hop_party_transactions WHERE workspace_id=? AND txn_type=66 AND "
                     "lower(txn_number)=lower(?) AND (deleted_at IS NULL OR trim(deleted_at)='')", (ws, ref)).fetchone()
    return int(r[0]) if r else None


def commit(conn: sqlite3.Connection, ws: str, job_id: int, skip: list[str] | None = None) -> dict[str, Any]:
    """Save every new document of the job except the ones the user unticked. Already-saved numbers are skipped."""
    from app import hop_dist_extra as X
    from app import hop_distributor as D
    from app.hop_doc_preview import create_manual_party_document as create

    j = _job(conn, ws, job_id)
    if j.get("done_at"):
        raise ValueError("This import is already saved")
    kind, vid = j["kind"], j["vendor_id"]
    if not vid:
        raise ValueError("Pick the company these documents are from")
    skip_set = {_s(x).lower() for x in skip or []}
    have = _existing(conn, ws, kind, vid)
    saved, skipped, errors, linked = 0, 0, [], 0
    for d in j["docs"]:
        no = d["doc_no"]
        if no.lower() in skip_set or no.lower() in have:
            skipped += 1
            continue
        when = d.get("doc_date") or date.today().isoformat()
        try:
            if kind == "so":
                D.create_so(conn, ws, {"so_number": no, "so_date": when, "vendor_id": vid,
                                       "po_txn_id": _my_po(conn, ws, d.get("ref_no")),
                                       "amount": d.get("doc_total") or None,
                                       "lines": [{"item_name": ln["item_name"], "qty": ln["qty"], "rate": ln["rate"]}
                                                 for ln in d["lines"]]})
            else:
                lines = []
                for ln in d["lines"]:
                    _k, art = X._key(conn, ws, ln) if (ln.get("qty") and (ln.get("code") or ln.get("item_name"))) else ("", None)
                    goods = bool(art and ln.get("qty"))
                    lines.append({"item_name": art["name"] if art else ln["item_name"] or KINDS[kind],
                                  "product_id": art["id"] if goods else None,
                                  "qty": ln["qty"] if goods or ln.get("qty") else 1,
                                  "rate": ln["rate"] if (goods or ln.get("qty")) and ln.get("rate") else ln["amount"],
                                  "tax_pct": ln.get("tax_pct") if ln.get("tax_pct") is not None else (art or {}).get("gst_pct") or 0,
                                  "hsn": ln.get("hsn") or (art or {}).get("hsn") or ""})
                note = {"ci": "", "cn": f"Company credit note {no}", "dn": f"Company debit note {no}"}[kind]
                if d.get("ref_no"):
                    note = (note + f" · ref {d['ref_no']}").strip(" ·")
                r = create(conn, ws, {"txn_type": 16 if kind == "cn" else 2, "party_type": "vendor", "vendor_id": vid,
                                      "txn_date": when, "party_bill_no": no, "party_bill_date": when,
                                      "notes": note or None, "lines": lines})
                if kind == "ci" and d.get("ref_no"):
                    so = conn.execute("SELECT id FROM hop_dist_so WHERE workspace_id=? AND lower(so_number)=lower(?) "
                                      "AND deleted_at IS NULL", (ws, d["ref_no"])).fetchone()
                    if so:
                        D.link_bill(conn, ws, int(so[0]), int(r["party_txn_id"]))
                        linked += 1
            saved += 1
            have.add(no.lower())
        except (ValueError, LookupError) as exc:
            errors.append(f"{no}: {exc}")
    conn.execute("UPDATE hop_dist_bulk_jobs SET done_at=? WHERE id=?", (_now(), job_id))
    conn.execute("DELETE FROM hop_dist_bulk_files WHERE job_id=?", (job_id,))
    for sh in j["sheets"]:
        remember_roles(conn, ws, sh["sig"], {str(c["i"]): c["role"] for c in sh["columns"]},
                       [c["header"] for c in sh["columns"]])
    conn.commit()
    return {"saved": saved, "skipped": skipped, "linked_to_so": linked, "errors": errors[:50], "error_count": len(errors),
            "kind_label": KINDS[kind]}


def ai_roles(kind: str, columns: list[dict[str, Any]], *, user_id: int) -> dict[str, str]:
    """✨ One capped AI call: a role for every column (the user confirms on screen)."""
    from app.services import hop_ai

    lines = "\n".join(f'{c["i"]}: "{c["header"]}" e.g. {", ".join(c.get("samples") or [])}' for c in columns[:50])
    prompt = (f"These are the columns of an Indian textile company's {KINDS[kind]} export (one row per line, many "
              f"documents) sent to its distributor.\nGive each column ONE role from: {', '.join(ROLES)}.\n"
              "doc_no = the number of this document; ref_no = the number of another document it refers to "
              "(my order / SO on an invoice, invoice on a credit note); amount = line value before GST; "
              "doc_total = the whole document's total; tax amounts, discounts, plant, transport = skip.\n"
              f"Columns:\n{lines}\n"
              'Answer JSON only: {"roles": {"<column number>": "<role>"}}')
    out = hop_ai.ask_json(prompt, user_id=user_id, feature="dist_bulk_columns", max_tokens=300)
    got = out.get("roles") if isinstance(out, dict) else None
    return {str(k): v for k, v in (got or {}).items() if v in ROLES}
