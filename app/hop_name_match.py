"""Find a party or an item from words that were spoken unclearly (owner 21-11: "jo hum bolte hai agar woh clear na
ho tab bhi aap samajh jaye"). Used by the 🎤 voice bill and by Ask Nexora.

* word match — every spoken word should be in the name (prefix ok: "aster dbl" → "Aster Double Bedsheet");
* sound match — Indian English / Hinglish spellings sound the same ("aaster" = "Aster", "flowra" = "Flora",
  "dabal" = "double", "sharmaa" = "Sharma");
* learned words — when the user picks the right item / party for what was said, that is remembered per UI
  (``hop_voice_aliases``) and wins next time.

Only saved parties / items are ever returned; nothing new is made up.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

_STOP = {"ka", "ki", "ke", "aur", "and", "the", "of", "wala", "wali", "wale", "piece", "pieces", "pcs", "pc", "nos",
         "ko", "se", "me", "mein", "hai", "kitna", "kitne", "kya", "batao"}
_SOUNDS = (("ph", "f"), ("bh", "b"), ("dh", "d"), ("th", "t"), ("kh", "k"), ("gh", "g"), ("sh", "s"), ("ch", "c"),
           ("ck", "k"), ("q", "k"), ("c", "k"), ("w", "v"), ("z", "j"), ("x", "ks"))


def words(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", str(s or "").lower()) if w not in _STOP]


def sound(w: str) -> str:
    """Sound key of one word: first letter kept, similar sounds folded, vowels / h / v after it dropped."""
    w = str(w or "").lower()
    if not w or w.isdigit():
        return w
    for a, b in _SOUNDS:
        w = w.replace(a, b)
    head, tail = w[0], re.sub(r"[aeiouyhv]", "", w[1:])
    key = head + tail
    return re.sub(r"(.)\1+", r"\1", key)


def said_key(said: str) -> str:
    return " ".join(sound(w) for w in words(said))[:120]


def score(spoken: str, name: str, code: str = "") -> float:
    """0..1 how well the spoken words fit a name (every spoken word should be in it)."""
    sw, nw = words(spoken), words(name) + words(code)
    if not sw or not nw:
        return 0.0
    ns = [sound(n) for n in nw]
    hit = 0.0
    for w in sw:
        best, ws = 0.0, sound(w)
        for n, nsnd in zip(nw, ns):
            if w == n:
                best = 1.0
                break
            if len(w) >= 3 and (n.startswith(w) or (w.startswith(n) and len(n) >= 3)):
                best = max(best, 0.9)
            if len(ws) >= 2 and ws == nsnd:
                best = max(best, 0.88)
            elif len(ws) >= 3 and nsnd.startswith(ws):
                best = max(best, 0.8)
            if len(w) >= 4:
                r = SequenceMatcher(None, w, n).ratio()
                if r >= 0.8:
                    best = max(best, r * 0.95)
        hit += best
    cover = hit / len(sw)
    extra = len(nw) - len(sw)          # a shorter name that holds every word beats a long one
    return round(cover - max(0, extra) * 0.02, 3)


def rank(spoken: str, rows: list[dict], name_key: str, code_key: str | None = None, *,
         limit: int = 3, floor: float = 0.5) -> list[tuple[float, dict]]:
    out = [(score(spoken, r.get(name_key) or "", (r.get(code_key) or "") if code_key else ""), r) for r in rows]
    out = [x for x in out if x[0] >= floor]
    out.sort(key=lambda x: -x[0])
    return out[:limit]


def sure(ranked: list[tuple[float, dict]]) -> bool:
    return bool(ranked) and ranked[0][0] >= 0.85 and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= 0.1)


# ── learned words ────────────────────────────────────────────────────────────

def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS hop_voice_aliases (
        workspace_id TEXT NOT NULL, kind TEXT NOT NULL, said_key TEXT NOT NULL, ref_id INTEGER NOT NULL,
        said TEXT, hits INTEGER DEFAULT 1, updated_at TEXT,
        PRIMARY KEY (workspace_id, kind, said_key))""")


def learn(conn: sqlite3.Connection, ws: str, kind: str, said: str, ref_id: int) -> dict[str, Any]:
    """The user picked ``ref_id`` for what was said → remember it (kind = item | customer | vendor)."""
    if kind not in ("item", "customer", "vendor"):
        raise ValueError("kind must be item, customer or vendor")
    key = said_key(said)
    if not key:
        raise ValueError("Nothing was said")
    table = {"item": "hop_products", "customer": "hop_customers", "vendor": "hop_vendors"}[kind]
    if not conn.execute(f"SELECT 1 FROM {table} WHERE workspace_id=? AND id=?", (ws, int(ref_id))).fetchone():
        raise LookupError("Not found in this UI")
    ensure_schema(conn)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO hop_voice_aliases (workspace_id, kind, said_key, ref_id, said, hits, updated_at) VALUES (?,?,?,?,?,1,?) "
        "ON CONFLICT(workspace_id, kind, said_key) DO UPDATE SET ref_id=excluded.ref_id, said=excluded.said, "
        "hits=CASE WHEN ref_id=excluded.ref_id THEN hits+1 ELSE 1 END, updated_at=excluded.updated_at",
        (ws, kind, key, int(ref_id), str(said)[:120], now))
    conn.commit()
    return {"kind": kind, "said": said, "ref_id": int(ref_id)}


def learned(conn: sqlite3.Connection, ws: str, kind: str, said: str) -> int | None:
    ensure_schema(conn)
    r = conn.execute("SELECT ref_id FROM hop_voice_aliases WHERE workspace_id=? AND kind=? AND said_key=?",
                     (ws, kind, said_key(said))).fetchone()
    return int(r[0]) if r else None


# ── one-stop finders (voice bill + Ask Nexora) ───────────────────────────────

def _products(conn: sqlite3.Connection, ws: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM hop_products WHERE workspace_id=? AND (deleted_at IS NULL OR trim(deleted_at)='') "
        "AND COALESCE(status,'active')!='inactive'", (ws,))]


def _parties(conn: sqlite3.Connection, ws: str, kind: str) -> list[dict]:
    table = "hop_vendors" if kind == "vendor" else "hop_customers"
    return [dict(r) for r in conn.execute(f"SELECT * FROM {table} WHERE workspace_id=?", (ws,))]


def find(conn: sqlite3.Connection, ws: str, kind: str, said: str, rows: list[dict] | None = None,
         limit: int = 3) -> dict[str, Any]:
    """{"best": row | None (sure), "options": [(score, row)…], "learned": bool} for kind item | customer | vendor."""
    if rows is None:
        rows = _products(conn, ws) if kind == "item" else _parties(conn, ws, kind)
    name_key, code_key = ("name", "code") if kind == "item" else ("company", None)
    lid = learned(conn, ws, kind, said)
    if lid is not None:
        hit = next((r for r in rows if r.get("id") == lid), None)
        if hit:
            return {"best": hit, "options": [(1.0, hit)], "learned": True}
    ranked = rank(said, rows, name_key, code_key, limit=limit)
    return {"best": ranked[0][1] if sure(ranked) else None, "options": ranked, "learned": False}


def shortlist(text_or_texts: Any, rows: list[dict], name_key: str, code_key: str | None = None,
              limit: int = 40) -> list[dict]:
    """Rows any spoken word sounds like (for the AI to pick from — it may only answer with these names)."""
    texts = text_or_texts if isinstance(text_or_texts, (list, tuple)) else [text_or_texts]
    sw = {w for t in texts for w in words(t) if not w.isdigit() and len(w) >= 3}
    keys = {sound(w) for w in sw}
    out = []
    for r in rows:
        nw = words(r.get(name_key) or "") + (words(r.get(code_key) or "") if code_key else [])
        hits = sum(1 for n in nw if n in sw or sound(n) in keys or any(n.startswith(w) for w in sw))
        if hits:
            out.append((hits, r))
    out.sort(key=lambda x: -x[0])
    return [r for _, r in out[:limit]]
