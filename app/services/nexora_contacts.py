"""Ask Nexora contacts: find a person's phone number in this login's own data and prepare a phone action
(call / SMS / WhatsApp). Nothing is dialled or sent from the server — the phone shows the action and
does it only after the user confirms (tap or "haan")."""
from __future__ import annotations

import difflib
import re
import sqlite3
from typing import Any

# (table, phone column, name columns, kind label)
SOURCES = [
    ("users", "phone", ("full_name", "username"), "Team"),
    ("master_distributors", "phone_number", ("firm_name", "name"), "Distributor"),
    ("distributors", "phone", ("name", "contact_person"), "Distributor"),
    ("master_retailers", "phone_number", ("name", "contact_person"), "Retailer"),
    ("retailers", "phone", ("name", "contact_person"), "Retailer"),
    ("hop_customers", "mobile", ("company", "contact_person"), "Customer"),
    ("hop_vendors", "mobile", ("company", "contact_person"), "Supplier"),
]


def _digits(phone: str) -> str:
    d = re.sub(r"\D", "", str(phone or ""))
    if len(d) == 10:
        d = "91" + d
    return d


def _score(q: str, name: str) -> float:
    q, n = q.lower().strip(), (name or "").lower().strip()
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    if q in n or n in q:
        return 0.9
    return difflib.SequenceMatcher(None, q, n).ratio()


def find(db_path: str, workspaces: list[str], name: str, limit: int = 5) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with sqlite3.connect(db_path) as conn:
        for table, pcol, ncols, kind in SOURCES:
            try:
                cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            except sqlite3.Error:
                continue
            if pcol not in cols or "workspace_id" not in cols:
                continue
            ncols = tuple(c for c in ncols if c in cols)
            if not ncols:
                continue
            marks = ",".join("?" * len(workspaces))
            try:
                rows = conn.execute(f"SELECT {pcol}, {', '.join(ncols)} FROM {table} WHERE workspace_id IN ({marks}) "
                                    f"AND {pcol} IS NOT NULL AND trim({pcol}) <> ''", tuple(workspaces)).fetchall()
            except sqlite3.Error:
                continue
            for r in rows:
                names = [str(x) for x in r[1:] if x]
                best = max((_score(name, n) for n in names), default=0.0)
                if best >= 0.6:
                    out.append({"name": names[0], "also": names[1] if len(names) > 1 else "", "phone": str(r[0]).strip(),
                                "digits": _digits(r[0]), "kind": kind, "score": round(best, 2)})
    seen, uniq = set(), []
    for c in sorted(out, key=lambda c: -c["score"]):
        if c["digits"] in seen:
            continue
        seen.add(c["digits"])
        uniq.append(c)
    return uniq[:limit]


def contact_action(db_path: str, workspaces: list[str], name: str, do: str = "show", message: str = "") -> dict[str, Any]:
    """Look the person up; when one match is clear, return a device action for the phone to confirm."""
    do = (do or "show").strip().lower()
    if do not in ("show", "call", "sms", "whatsapp"):
        do = "show"
    hits = find(db_path, workspaces, name)
    if not hits:
        return {"found": False, "note": f"No phone number found for '{name}' in your NEXORA data."}
    top = hits[0]
    clear = len(hits) == 1 or top["score"] - hits[1]["score"] >= 0.15 or top["score"] == 1.0
    result: dict[str, Any] = {"found": True, "matches": [{k: h[k] for k in ("name", "also", "phone", "kind")} for h in hits]}
    if clear:
        result["device_action"] = {"kind": do, "name": top["name"], "phone": top["digits"],
                                   "display_phone": top["phone"], "message": (message or "")[:600]}
        result["note"] = ("Phone shows Call / Message / WhatsApp buttons." if do == "show" else
                          f"The phone will ask the user to confirm the {do}. Reply in one short line asking them to "
                          f"confirm (they can say haan).")
    else:
        result["note"] = "More than one match — ask the user which one."
    return result
