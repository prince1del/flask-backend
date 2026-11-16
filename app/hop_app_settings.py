"""HoP app settings (Transaction / Taxes / Print / Party / Item …) — one list, one store.

The same SCHEMA drives the settings screen on the web and on the phone, so every
setting shows on both. Each setting below is wired to real behaviour (see "used in").
Values are per company (workspace) in ``hop_app_settings``.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

# key, section, label, type, default, help, used in
SCHEMA: list[dict[str, Any]] = [
    # Transaction
    {"key": "prefix_1", "section": "Transaction", "label": "Sale Invoice number prefix", "type": "text", "default": "HOP",
     "help": "Numbers look like PREFIX/2026-27/1."},
    {"key": "prefix_27", "section": "Transaction", "label": "Estimate number prefix", "type": "text", "default": "HOPPI"},
    {"key": "prefix_65", "section": "Transaction", "label": "Sale Order number prefix", "type": "text", "default": ""},
    {"key": "prefix_30", "section": "Transaction", "label": "Delivery Challan number prefix", "type": "text", "default": ""},
    {"key": "prefix_83", "section": "Transaction", "label": "Proforma number prefix", "type": "text", "default": ""},
    {"key": "prefix_3", "section": "Transaction", "label": "Payment-In (receipt) prefix", "type": "text", "default": ""},
    {"key": "auto_round_off", "section": "Transaction", "label": "Round off bill total to nearest ₹1", "type": "bool",
     "default": False, "help": "Adds the round-off line by itself when you don't type one."},
    {"key": "tax_inclusive_rates", "section": "Transaction", "label": "Item rates include GST", "type": "bool",
     "default": False, "help": "Type the rate with GST; NEXORA splits out the GST."},
    {"key": "default_due_days", "section": "Transaction", "label": "Default payment due (days)", "type": "number",
     "default": 0, "help": "Used when the party has no credit days. 0 = no due date."},
    {"key": "cash_sale_default", "section": "Transaction", "label": "New sale invoice = fully received (cash sale)",
     "type": "bool", "default": False},
    {"key": "short_stock", "section": "Transaction", "label": "When stock is not enough", "type": "select",
     "default": "warn", "options": [["warn", "Warn me"], ["block", "Don't allow the sale"], ["off", "Do nothing"]]},
    # Taxes
    {"key": "composition_scheme", "section": "Taxes & GST", "label": "Composition scheme (Bill of Supply, no GST on sales)",
     "type": "bool", "default": False},
    # Print
    {"key": "print_amount_words", "section": "Print", "label": "Show amount in words", "type": "bool", "default": True},
    {"key": "print_bank", "section": "Print", "label": "Show bank details", "type": "bool", "default": True},
    {"key": "print_signature", "section": "Print", "label": "Show signature", "type": "bool", "default": True},
    {"key": "print_terms", "section": "Print", "label": "Show terms & conditions", "type": "bool", "default": True},
    {"key": "print_paper", "section": "Print", "label": "Paper size", "type": "select", "default": "a4",
     "options": [["a4", "A4"], ["a5", "A5"], ["thermal80", "Thermal 3 inch (80 mm)"], ["thermal58", "Thermal 2 inch (58 mm)"]],
     "help": "Thermal prints a narrow receipt from the web; the phone PDF uses A4 for thermal."},
    {"key": "print_copies", "section": "Print", "label": "Copies when printing", "type": "select", "default": "1",
     "options": [["1", "Original only"], ["2", "Original + Duplicate"], ["3", "Original + Duplicate + Triplicate"]]},
    {"key": "print_footer", "section": "Print", "label": "Footer text on every bill", "type": "text", "default": "",
     "help": "e.g. Thank you for your business!"},
    # Sharing
    {"key": "upi_id", "section": "Sharing", "label": "Your UPI ID (for payment link on bills)", "type": "text",
     "default": "", "help": "e.g. yourshop@okbank. Customers tap the link and pay in any UPI app."},
    {"key": "upi_name", "section": "Sharing", "label": "Name shown in the UPI app", "type": "text", "default": "",
     "help": "Blank = your business name."},
    {"key": "print_upi", "section": "Sharing", "label": "Show 'Pay by UPI' on sale invoices with money due",
     "type": "bool", "default": True},
    {"key": "msg_bill", "section": "Sharing", "label": "WhatsApp message when sharing a bill", "type": "text",
     "default": "", "help": "Use {party} {doc} {number} {date} {amount} {due} {due_date} {firm} {upi_link}. Blank = standard message."},
    {"key": "msg_reminder", "section": "Sharing", "label": "WhatsApp payment reminder message", "type": "text",
     "default": "Dear {party}, a gentle reminder: ₹{due} is due on {doc} {number} dated {date}. {upi_link} — {firm}",
     "help": "Same words in {…} as above."},
    # Party
    {"key": "party_groups", "section": "Party", "label": "Party groups", "type": "list",
     "default": ["Buyer", "Supplier", "Architect", "Hotel", "Retail"], "help": "Shown when you add or edit a party."},
    {"key": "credit_limit_action", "section": "Party", "label": "When a credit limit is crossed", "type": "select",
     "default": "warn", "options": [["warn", "Warn me"], ["block", "Don't allow the bill"]]},
]
BY_KEY = {s["key"]: s for s in SCHEMA}


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS hop_app_settings (
                        workspace_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT, updated_at TEXT NOT NULL,
                        PRIMARY KEY (workspace_id, key))""")


def get_all(conn: sqlite3.Connection, ws: str) -> dict[str, Any]:
    ensure_schema(conn)
    vals = {s["key"]: s["default"] for s in SCHEMA}
    for k, v in conn.execute("SELECT key, value FROM hop_app_settings WHERE workspace_id=?", (ws,)).fetchall():
        if k in BY_KEY:
            try:
                vals[k] = json.loads(v)
            except (TypeError, ValueError):
                pass
    return vals


def get(conn: sqlite3.Connection, ws: str, key: str) -> Any:
    try:
        return get_all(conn, ws).get(key, BY_KEY.get(key, {}).get("default"))
    except sqlite3.OperationalError:
        return BY_KEY.get(key, {}).get("default")


def _clean(spec: dict[str, Any], value: Any) -> Any:
    t = spec["type"]
    if t == "bool":
        return value in (True, 1, "1", "true", "yes", "on")
    if t == "number":
        try:
            n = float(value or 0)
        except (TypeError, ValueError):
            raise ValueError(f"{spec['label']}: enter a number")
        if n < 0 or n > 3650:
            raise ValueError(f"{spec['label']}: 0 to 3650")
        return int(n) if n == int(n) else n
    if t == "select":
        allowed = [o[0] for o in spec["options"]]
        if value not in allowed:
            raise ValueError(f"{spec['label']}: choose one of {', '.join(allowed)}")
        return value
    if t == "list":
        items = value if isinstance(value, list) else str(value or "").split(",")
        out = []
        for x in items:
            x = str(x).strip()[:40]
            if x and x.lower() not in {y.lower() for y in out}:
                out.append(x)
        return out[:50]
    text = str(value or "").strip()
    if spec["key"] == "upi_id" and text:
        from app import hop_share

        if not hop_share.valid_upi(text):
            raise ValueError("UPI ID looks wrong — it should be like name@bank")
    if spec["key"].startswith("msg_"):
        return text[:600]
    if spec["key"].startswith("prefix_"):
        text = text.upper().replace(" ", "")[:12]
        if text and not all(c.isalnum() or c in "-_" for c in text):
            raise ValueError(f"{spec['label']}: letters, numbers, - and _ only")
    return text[:200]


def save(conn: sqlite3.Connection, ws: str, changes: dict[str, Any]) -> dict[str, Any]:
    ensure_schema(conn)
    now = datetime.now(timezone.utc).isoformat()
    for k, v in (changes or {}).items():
        spec = BY_KEY.get(k)
        if not spec:
            raise ValueError(f"Unknown setting: {k}")
        conn.execute("INSERT OR REPLACE INTO hop_app_settings (workspace_id, key, value, updated_at) VALUES (?, ?, ?, ?)",
                     (ws, k, json.dumps(_clean(spec, v)), now))
    conn.commit()
    return get_all(conn, ws)


def payload(conn: sqlite3.Connection, ws: str) -> dict[str, Any]:
    """What the settings screen needs: sections in order, fields with their current value."""
    vals = get_all(conn, ws)
    sections: list[dict[str, Any]] = []
    for s in SCHEMA:
        if not sections or sections[-1]["title"] != s["section"]:
            sections.append({"title": s["section"], "fields": []})
        sections[-1]["fields"].append({k: s.get(k) for k in ("key", "label", "type", "help", "options", "default")}
                                      | {"value": vals[s["key"]]})
    return {"sections": sections, "values": vals}
