"""Seasons (AW26, SS27 ...) across Order Desk and money.

Rule: a season is chosen once, when the Filled Order is uploaded. Everything
else inherits it: an SO through its FO match, a CI through its SO number, a
payment through the distributor's open season. Bombay Dyeing has no fixed
season dates, so nothing is guessed from a calendar.

An SO/CI that never met a Filled Order has no season. For those the app
suggests the season of the nearest known SO number of the same distributor
(SO numbers keep rising season to season) and the user confirms it once; the
choice is stored in so_season_overrides and used like a match.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

_SEASON_RX = re.compile(r"\b(AW|SS)\s*[-_/ ]?\s*'?(\d{2})\b", re.I)


def canonical_season(raw: str | None) -> str | None:
    """'aw 26', 'AW-26', 'Aw26' -> 'AW26'; other names upper-cased and trimmed."""
    text = (raw or "").strip()
    if not text:
        return None
    m = _SEASON_RX.search(text)
    if m:
        return f"{m.group(1).upper()}{m.group(2)}"
    return " ".join(text.upper().split())


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS so_season_overrides (
            workspace_id TEXT NOT NULL,
            so_number TEXT NOT NULL,
            distributor_id INTEGER,
            season TEXT NOT NULL,
            category TEXT NOT NULL,
            set_by INTEGER,
            set_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (workspace_id, so_number)
        )"""
    )


def overrides(conn: sqlite3.Connection, workspace_id: str) -> dict[str, tuple[int | None, str, str]]:
    """SO number -> (distributor_id, season, category) chosen by the user."""
    ensure_schema(conn)
    return {
        str(so): (dist, season, category)
        for so, dist, season, category in conn.execute(
            "SELECT so_number, distributor_id, season, category FROM so_season_overrides WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchall()
    }


def assign(conn: sqlite3.Connection, *, workspace_id: str, user_id: int, so_number: str,
           distributor_id: int | None, season: str, category: str) -> dict[str, Any]:
    season_c = canonical_season(season)
    category_c = (category or "").strip()
    so = re.sub(r"\D", "", str(so_number or ""))
    if not so or not season_c or not category_c:
        raise ValueError("so_number, season and category are required")
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO so_season_overrides (workspace_id, so_number, distributor_id, season, category, set_by) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(workspace_id, so_number) DO UPDATE SET "
        "distributor_id = excluded.distributor_id, season = excluded.season, category = excluded.category, "
        "set_by = excluded.set_by, set_at = datetime('now')",
        (workspace_id, so, distributor_id, season_c, category_c, user_id),
    )
    conn.commit()
    return {"so_number": so, "season": season_c, "category": category_c}


def suggest(so_number: str, distributor_id: int | None,
            known: dict[str, tuple[int, str, str]]) -> dict[str, Any] | None:
    """Season/category of the nearest known SO number (same distributor first)."""
    try:
        target = int(re.sub(r"\D", "", str(so_number or "")) or "0")
    except ValueError:
        return None
    if not target:
        return None
    best = None
    for pool_same in (True, False):
        for so, (dist, season, category) in known.items():
            if pool_same and distributor_id is not None and dist != distributor_id:
                continue
            try:
                gap = abs(int(re.sub(r"\D", "", so)) - target)
            except ValueError:
                continue
            if best is None or gap < best[0]:
                best = (gap, season, category, so)
        if best:
            break
    if not best:
        return None
    return {"season": best[1], "category": best[2], "near_so": best[3], "gap": best[0]}


def season_overview(db, conn: sqlite3.Connection, *, user_id: int, workspace_id: str) -> dict[str, Any]:
    """Every season with its counts and money, plus CIs that have no season yet."""
    from app.services import distributor_ledger as dl
    from app.services import fo_so_match_db as matchdb

    matchdb.ensure_schema(conn)  # a fresh workspace has no match tables yet
    ledger = dl.build_ledger(db, conn, user_id=user_id, workspace_id=workspace_id)
    pending = matchdb.list_pending_so_for_user(conn, user_id)
    slots = dl._so_slots(conn, user_id)
    chosen = overrides(conn, workspace_id)
    for so, (dist, season, category) in chosen.items():
        slots.setdefault(so, (dist, season, category))

    seasons: dict[str, dict[str, Any]] = {}

    def bucket(name: str) -> dict[str, Any]:
        return seasons.setdefault(name, {
            "season": name, "fo_count": 0, "so_count": 0, "ci_count": 0,
            "order_payable": 0.0, "received": 0.0, "to_collect": 0.0,
            "pending_so_count": 0, "pending_so_value": 0.0,
        })

    try:
        for season, n in conn.execute(
            "SELECT season, count(*) FROM filled_orders WHERE user_id = ? AND season IS NOT NULL "
            "AND TRIM(season) <> '' GROUP BY season", (user_id,)
        ).fetchall():
            bucket(canonical_season(season) or season)["fo_count"] += int(n)
    except sqlite3.OperationalError:
        pass
    so_seen: set[tuple[str, str]] = set()
    for so, (_dist, season, _cat) in slots.items():
        key = canonical_season(season) or season
        if (key, so) not in so_seen:
            so_seen.add((key, so))
            bucket(key)["so_count"] += 1
    for d in ledger.get("distributors") or []:
        for c in d.get("categories") or []:
            b = bucket(canonical_season(c.get("season")) or c.get("season") or "—")
            b["ci_count"] += int(c.get("ci_count") or 0)
            b["order_payable"] += float(c.get("order_payable") or 0)
            b["received"] += float(c.get("received") or 0)
            b["to_collect"] += max(0.0, float(c.get("payment_due") or 0))
    for p in pending:
        b = bucket(canonical_season(p.get("season")) or p.get("season") or "—")
        b["pending_so_count"] += 1
        b["pending_so_value"] += float(p.get("pending_net") or 0)

    out = []
    for b in seasons.values():
        for k in ("order_payable", "received", "to_collect", "pending_so_value"):
            b[k] = round(b[k], 2)
        b["open"] = b["to_collect"] > 1 or b["pending_so_count"] > 0
        out.append(b)
    # Open seasons first, newest code first (AW26 before AW25; SS27 vs AW26 by year then AW>SS).
    def sort_key(b):
        m = _SEASON_RX.search(b["season"] or "")
        year = int(m.group(2)) if m else 0
        half = 1 if (m and m.group(1).upper() == "AW") else 0
        return (0 if b["open"] else 1, -year, -half, b["season"])
    out.sort(key=sort_key)

    # CIs whose SO met no Filled Order and has no override: no season yet.
    known = {so: v for so, v in slots.items() if v[0] is not None}
    unassigned = []
    for d in ledger.get("distributors") or []:
        for ci in d.get("unlinked_cis") or []:
            so = ci.get("so_number") or ""
            if so in chosen:
                continue  # season already chosen by the user
            unassigned.append({
                "distributor_id": d.get("distributor_id"),
                "distributor_name": d.get("distributor_name"),
                "tracking_id": ci.get("tracking_id"),  # opens the CI PDF
                "invoice_no": ci.get("invoice_no"),
                "invoice_date": ci.get("invoice_date"),
                "so_number": so,
                "gross": ci.get("gross"),
                "suggestion": suggest(so, d.get("distributor_id"), known),
            })
    return {
        "seasons": out,
        "active": next((b["season"] for b in out if b["open"]), out[0]["season"] if out else None),
        "unassigned": unassigned,
    }
