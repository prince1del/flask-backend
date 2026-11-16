"""HoP backup download: every hop_* table of this company as CSV, in one zip.

Only this company's rows (``workspace_id``) are included. Restoring is done with support
(not one click), so a wrong file can never overwrite live data.
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
import zipfile
from datetime import datetime, timezone

SKIP_COLUMNS = {"password", "password_hash", "token", "secret"}


def build_zip(conn: sqlite3.Connection, ws: str) -> tuple[bytes, dict[str, int]]:
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'hop_%' ORDER BY name")]
    counts: dict[str, int] = {}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for t in tables:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
            if "workspace_id" not in cols:
                continue
            keep = [c for c in cols if c.lower() not in SKIP_COLUMNS]
            rows = conn.execute(f"SELECT {', '.join(keep)} FROM {t} WHERE workspace_id=?", (ws,)).fetchall()
            out = io.StringIO()
            w = csv.writer(out)
            w.writerow(keep)
            w.writerows([list(r) for r in rows])
            z.writestr(f"{t}.csv", out.getvalue())
            counts[t] = len(rows)
        z.writestr("README.txt", "NEXORA House of Prizm backup\n"
                   f"Made: {datetime.now(timezone.utc).isoformat(timespec='seconds')} UTC\n"
                   "One CSV per table. Keep this file safe; it has your business data.\n")
        z.writestr("counts.json", json.dumps(counts, indent=1))
    return buf.getvalue(), counts
