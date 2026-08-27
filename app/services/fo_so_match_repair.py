"""Self-healing repair for FO ↔ SO Order Match data (single shared implementation).

The damage this repairs (Bernina AW26 incident): one Filled Order ended up with
several match runs because a re-upload created a rival run instead of merging,
so the UI read a run that only knew the re-uploaded SO → spurious MISSING_ON_SO
rows and a wrong / zero order value. `fo_so_match_so_index` could also still
claim SO numbers whose run is gone, which makes a clean re-upload fail with
409 "Sales Order already uploaded". The third shape is a run that still claims
Sales Orders while holding no usable SO line detail at all (FO side intact, SO
qty / net 0, everything MISSING_ON_SO): the claim makes a fresh upload of that
SO look like a *revision* of an empty old SO, so the match never fills again.
That one is restored from `order_desk_archive` when a snapshot exists, else the
unusable claims are freed so the next upload attaches cleanly.

The repair consolidates all runs of one FO into the newest run (deduplicating SO
lines by SO number, newest wins), rematches the surviving lines against that FO
and rebuilds the SO index. It never deletes a surviving SO line: lines are merged
first, and rival run rows are dropped only after their lines were carried over.
It is idempotent — a second pass changes nothing.

Callers (all share this module, no copied logic):
  * `autoheal()` — invoked silently from the normal Order Desk read / upload /
    delete paths in `app/routes/data.py`. Cheap `damage_probe()` first; the heavy
    rematch only runs when damage is actually present.
  * `scripts/repair_fo_so_match.py` — CLI dry-run / apply.

Scope (AGENTS.md user isolation):
  * a normal caller may only ever touch rows with their own `user_id`;
  * the workspace owner (`WORKSPACE_OWNER_USERNAME`, `is_workspace_owner=1`) may
    run workspace-wide. The flag must come from the signed JWT / session — see
    `app.routes.auth.is_request_workspace_owner` — never from request input.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from typing import Any

from app.services import fo_so_match_db as matchdb
from app.services import fo_so_revision as sorev

logger = logging.getLogger(__name__)

# One in-process repair at a time: the heal path is best-effort, and serialising
# it keeps two concurrent requests from consolidating the same FO twice.
_HEAL_LOCK = threading.Lock()


class RepairScope:
    """Resolved, trusted scope for a repair call.

    `global_scope=True` is only legal for the workspace owner; build instances
    through `RepairScope.for_request()` / `for_user()` so callers cannot pass a
    raw flag in from request data.
    """

    def __init__(self, *, user_id: int, global_scope: bool = False) -> None:
        self.user_id = int(user_id)
        self.global_scope = bool(global_scope)

    @property
    def user_filter(self) -> int | None:
        """user_id to filter rows by — None means workspace-wide (owner only)."""
        return None if self.global_scope else self.user_id

    def describe(self) -> str:
        return "workspace" if self.global_scope else f"user:{self.user_id}"

    @classmethod
    def for_user(cls, user_id: int) -> RepairScope:
        return cls(user_id=int(user_id), global_scope=False)

    @classmethod
    def for_request(cls, user_id: int) -> RepairScope:
        """Scope derived from the authenticated request (owner ⇒ workspace-wide)."""
        is_owner = False
        try:
            from app.routes.auth import is_request_workspace_owner

            is_owner = bool(is_request_workspace_owner())
        except Exception:
            is_owner = False
        return cls(user_id=int(user_id), global_scope=is_owner)


def _lines_of(run: dict[str, Any] | None) -> list[dict[str, Any]]:
    detail = (run or {}).get("so_line_detail") or []
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except ValueError:
            detail = []
    return [r for r in detail if isinstance(r, dict)]


def _totals_of(run: dict[str, Any] | None) -> dict[str, Any]:
    run = run or {}
    return {
        "so_qty": run.get("so_qty"),
        "so_net_amount": run.get("so_net_amount"),
        "missing_count": run.get("missing_count"),
        "line_count": len(_lines_of(run)),
    }


# ---------------------------------------------------------------- cheap probe


def orphan_index_so_numbers(
    conn: sqlite3.Connection, *, user_filter: int | None
) -> list[str]:
    """SO index rows whose match run is gone — these block clean re-uploads."""
    sql = (
        "SELECT so_number FROM fo_so_match_so_index i "
        "WHERE NOT EXISTS (SELECT 1 FROM fo_so_match_runs r WHERE r.id = i.run_id)"
    )
    params: list[Any] = []
    if user_filter is not None:
        sql += " AND i.user_id = ?"
        params.append(int(user_filter))
    return [str(r[0]) for r in conn.execute(sql, params).fetchall()]


def duplicate_run_groups(
    conn: sqlite3.Connection,
    *,
    user_filter: int | None,
    filled_order_id: int | None = None,
) -> list[tuple[int, int]]:
    """(user_id, filled_order_id) pairs that carry more than one match run."""
    sql = (
        "SELECT user_id, filled_order_id FROM fo_so_match_runs "
        "WHERE filled_order_id IS NOT NULL AND user_id IS NOT NULL"
    )
    params: list[Any] = []
    if user_filter is not None:
        sql += " AND user_id = ?"
        params.append(int(user_filter))
    if filled_order_id is not None:
        sql += " AND filled_order_id = ?"
        params.append(int(filled_order_id))
    sql += " GROUP BY user_id, filled_order_id HAVING COUNT(*) > 1"
    return [(int(r[0]), int(r[1])) for r in conn.execute(sql, params).fetchall()]


def empty_so_detail_runs(
    conn: sqlite3.Connection,
    *,
    user_filter: int | None,
    filled_order_id: int | None = None,
) -> list[tuple[int, int, int]]:
    """(run_id, user_id, filled_order_id) of runs that claim SOs but hold none.

    This is the Bernina re-upload shape: the Filled Order side is intact (full
    qty and ExMill value) while the SO side is completely empty — no SO line
    detail, SO qty 0, SO net 0, every FO bucket MISSING_ON_SO — yet an SO number
    is still claimed for the run, so the run reports a Sales Order it cannot
    show and a re-upload of that SO is treated as a revision instead of a fresh
    attach.

    Legacy runs that never stored line detail but do carry a real SO qty / value
    are healthy and deliberately not matched here.
    """
    sql = (
        "SELECT r.id, r.user_id, r.filled_order_id FROM fo_so_match_runs r "
        "WHERE r.filled_order_id IS NOT NULL AND r.user_id IS NOT NULL "
        "  AND COALESCE(r.so_qty, 0) = 0 AND COALESCE(r.so_net_amount, 0) = 0 "
        "  AND (r.so_line_detail_json IS NULL "
        "       OR TRIM(r.so_line_detail_json) = '' "
        "       OR TRIM(r.so_line_detail_json) = '[]') "
        "  AND EXISTS (SELECT 1 FROM fo_so_match_so_index i WHERE i.run_id = r.id)"
    )
    params: list[Any] = []
    if user_filter is not None:
        sql += " AND r.user_id = ?"
        params.append(int(user_filter))
    if filled_order_id is not None:
        sql += " AND r.filled_order_id = ?"
        params.append(int(filled_order_id))
    return [
        (int(r[0]), int(r[1]), int(r[2])) for r in conn.execute(sql, params).fetchall()
    ]


def damage_probe(
    conn: sqlite3.Connection,
    *,
    scope: RepairScope,
    filled_order_id: int | None = None,
) -> dict[str, Any]:
    """Three cheap aggregate queries — no rematching, no writes.

    Returns {"damaged": bool, "duplicate_run_groups": [...],
             "orphan_so_numbers": [...], "empty_so_runs": [...]}.
    """
    matchdb.ensure_schema(conn)
    dupes = duplicate_run_groups(
        conn, user_filter=scope.user_filter, filled_order_id=filled_order_id
    )
    orphans = orphan_index_so_numbers(conn, user_filter=scope.user_filter)
    empties = empty_so_detail_runs(
        conn, user_filter=scope.user_filter, filled_order_id=filled_order_id
    )
    return {
        "damaged": bool(dupes or orphans or empties),
        "duplicate_run_groups": dupes,
        "orphan_so_numbers": orphans,
        "empty_so_runs": empties,
    }


# --------------------------------------------------------------- core repair


def find_targets(
    conn: sqlite3.Connection,
    *,
    filled_order_id: int | None = None,
    distributor: str | None = None,
    user_filter: int | None = None,
) -> list[tuple[int, int]]:
    """(user_id, filled_order_id) pairs in scope."""
    matchdb.ensure_schema(conn)
    sql = (
        "SELECT DISTINCT user_id, filled_order_id FROM fo_so_match_runs "
        "WHERE filled_order_id IS NOT NULL"
    )
    params: list[Any] = []
    if filled_order_id is not None:
        sql += " AND filled_order_id = ?"
        params.append(int(filled_order_id))
    if distributor:
        sql += " AND LOWER(COALESCE(distributor_name, '')) LIKE ?"
        params.append(f"%{distributor.strip().lower()}%")
    if user_filter is not None:
        sql += " AND user_id = ?"
        params.append(int(user_filter))
    return [
        (int(r[0]), int(r[1]))
        for r in conn.execute(sql, params).fetchall()
        if r[0] is not None
    ]


def _merge_run_lines(
    conn: sqlite3.Connection, *, user_id: int, run_ids: list[int]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Lines of every run of one FO — newest run first, newest wins per SO number."""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rid in run_ids:
        run = matchdb.get_match_run(conn, rid, user_id=user_id)
        if not run:
            continue
        lines = _lines_of(run)
        for line in lines:
            so_n = (matchdb.normalize_so_number(line.get("so_number")) or "").upper()
            if so_n and so_n in seen:
                continue
            merged.append(line)
        for line in lines:
            so_n = (matchdb.normalize_so_number(line.get("so_number")) or "").upper()
            if so_n:
                seen.add(so_n)
    return merged, sorted(seen)


def repair_one(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    filled_order_id: int,
    apply: bool,
) -> dict[str, Any]:
    """Consolidate every match run of one FO into the newest one and rematch.

    `apply=False` reports what would change and writes nothing.
    """
    run_ids = [
        int(r[0])
        for r in conn.execute(
            "SELECT id FROM fo_so_match_runs "
            "WHERE user_id = ? AND filled_order_id = ? ORDER BY id DESC",
            (int(user_id), int(filled_order_id)),
        ).fetchall()
    ]
    report: dict[str, Any] = {
        "user_id": int(user_id),
        "filled_order_id": int(filled_order_id),
        "distributor_name": None,
        "runs_found": run_ids,
        "kept_run_id": run_ids[0] if run_ids else None,
        "dropped_run_ids": run_ids[1:],
        "so_numbers": [],
        "changed": False,
    }
    if not run_ids:
        return report

    keep_id = run_ids[0]
    merged, so_numbers = _merge_run_lines(conn, user_id=user_id, run_ids=run_ids)
    kept = matchdb.get_match_run(conn, keep_id, user_id=user_id) or {}
    report["distributor_name"] = kept.get("distributor_name")
    report["so_numbers"] = so_numbers
    report["before"] = _totals_of(kept)

    if not apply:
        report["would_merge_lines"] = len(merged)
        return report

    if not merged:
        # Nothing to carry over — never destroy rows we cannot rebuild.
        return report

    for rid in run_ids[1:]:
        matchdb.delete_match_run(conn, int(user_id), rid)
    updated = sorev.rebuild_run_from_lines(
        conn, user_id=int(user_id), run_id=keep_id, lines=merged
    )
    report["after"] = _totals_of(updated)
    report["changed"] = bool(run_ids[1:]) or bool(merged)
    return report


def drop_orphan_index_rows(
    conn: sqlite3.Connection, *, apply: bool, user_filter: int | None = None
) -> int:
    stale = orphan_index_so_numbers(conn, user_filter=user_filter)
    if apply and stale:
        sql = (
            "DELETE FROM fo_so_match_so_index WHERE run_id NOT IN "
            "(SELECT id FROM fo_so_match_runs)"
        )
        params: list[Any] = []
        if user_filter is not None:
            sql += " AND user_id = ?"
            params.append(int(user_filter))
        conn.execute(sql, params)
        conn.commit()
    return len(stale)


def clear_stale_so_claims(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    so_numbers: list[str],
    apply: bool = True,
) -> list[str]:
    """Free claims on these SO numbers whose owning run holds no line for them.

    A claim without any SO line behind it is data the user can neither see nor
    delete, and it makes a fresh upload of that Sales Order look like a revision
    of something ("old qty 0") instead of a first attach. Only this user's own
    claims are ever touched.
    """
    matchdb.ensure_schema(conn)
    freed: list[str] = []
    for raw in so_numbers or []:
        key = matchdb.normalize_so_number(raw)
        if not key:
            continue
        row = conn.execute(
            "SELECT i.so_number, i.run_id FROM fo_so_match_so_index i "
            "JOIN fo_so_match_runs r ON r.id = i.run_id "
            "WHERE UPPER(i.so_number) = UPPER(?) AND r.user_id = ?",
            (key, int(user_id)),
        ).fetchone()
        if not row:
            continue
        if matchdb.lines_for_so_in_run(
            conn, run_id=int(row[1]), so_number=str(row[0])
        ):
            continue
        freed.append(str(row[0]))
        if apply:
            conn.execute(
                "DELETE FROM fo_so_match_so_index WHERE run_id = ? "
                "AND UPPER(so_number) = UPPER(?)",
                (int(row[1]), str(row[0])),
            )
    if apply and freed:
        conn.commit()
    return freed


def repair_empty_so_run(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    run_id: int,
    filled_order_id: int,
    apply: bool,
) -> dict[str, Any]:
    """Heal one run that claims Sales Orders but holds no SO line detail.

    Restores the SO lines from `order_desk_archive` when a snapshot exists,
    otherwise frees the unusable SO claims so the next upload is accepted as a
    fresh attach. The Filled Order side is never touched.
    """
    report: dict[str, Any] = {
        "run_id": int(run_id),
        "user_id": int(user_id),
        "filled_order_id": int(filled_order_id),
        "restored_so_numbers": [],
        "cleared_claims": 0,
        "changed": False,
    }
    if not apply:
        return report

    run = matchdb.get_match_run(conn, int(run_id), user_id=int(user_id))
    if not run:
        return report
    try:
        from app.services import order_desk_archive as archive

        restored = archive.restore_match_for_fo(
            conn,
            user_id=int(user_id),
            filled_order_id=int(filled_order_id),
            fo_key=archive.fo_key_for(
                run.get("distributor_id"), run.get("category"), run.get("season")
            ),
            incoming_so_numbers=[],
        )
    except Exception:
        logger.debug("empty-SO heal: archive restore failed", exc_info=True)
        restored = None
    if restored and _lines_of(restored.get("run")):
        report["restored_so_numbers"] = restored.get("restored_so_numbers") or []
        report["changed"] = True
        return report

    # Nothing to bring back — drop the claims the run cannot honour.
    fresh = matchdb.get_match_run(conn, int(run_id), user_id=int(user_id))
    if not fresh or _lines_of(fresh):
        return report
    report["cleared_claims"] = matchdb.clear_so_claims_for_run(
        conn, run_id=int(run_id), user_id=int(user_id)
    )
    report["changed"] = bool(report["cleared_claims"])
    return report


def repair(
    conn: sqlite3.Connection,
    *,
    scope: RepairScope,
    filled_order_id: int | None = None,
    distributor: str | None = None,
    apply: bool = True,
) -> dict[str, Any]:
    """Repair (or dry-run) every FO in scope. Idempotent."""
    matchdb.ensure_schema(conn)
    orphans = drop_orphan_index_rows(
        conn, apply=apply, user_filter=scope.user_filter
    )
    empty_runs = [
        repair_empty_so_run(
            conn,
            user_id=uid,
            run_id=rid,
            filled_order_id=foid,
            apply=apply,
        )
        for rid, uid, foid in empty_so_detail_runs(
            conn, user_filter=scope.user_filter, filled_order_id=filled_order_id
        )
    ]
    targets = find_targets(
        conn,
        filled_order_id=filled_order_id,
        distributor=distributor,
        user_filter=scope.user_filter,
    )
    orders = [
        repair_one(conn, user_id=uid, filled_order_id=foid, apply=apply)
        for uid, foid in targets
    ]
    merged_runs = sum(len(o.get("dropped_run_ids") or []) for o in orders)
    empty_healed = sum(1 for e in empty_runs if e.get("changed"))
    return {
        "scope": scope.describe(),
        "applied": bool(apply),
        "processed_orders": len(orders),
        "orphan_index_rows": orphans,
        "runs_merged": merged_runs,
        "empty_so_runs_healed": empty_healed,
        "changed": bool(apply)
        and (bool(orphans) or bool(merged_runs) or bool(empty_healed)),
        "orders": orders,
        "empty_so_runs": empty_runs,
    }


# ------------------------------------------------------------------ autoheal


def autoheal(
    conn: sqlite3.Connection,
    *,
    scope: RepairScope,
    filled_order_id: int | None = None,
    reason: str = "",
) -> dict[str, Any] | None:
    """Silent self-heal for the normal Order Desk paths.

    Cheap probe first; only actually repairs when damage is present. Never
    raises — a failed heal must not break the request that triggered it.
    Returns the repair summary when something was healed, else None.
    """
    try:
        probe = damage_probe(conn, scope=scope, filled_order_id=filled_order_id)
    except Exception:
        logger.debug("order-match autoheal probe failed", exc_info=True)
        return None
    if not probe["damaged"]:
        return None

    if not _HEAL_LOCK.acquire(blocking=False):
        return None
    try:
        # Re-probe under the lock: another request may have healed it already.
        probe = damage_probe(conn, scope=scope, filled_order_id=filled_order_id)
        if not probe["damaged"]:
            return None
        logger.warning(
            "Order Match autoheal (%s, reason=%s): duplicate run groups=%s, "
            "orphan SO index rows=%s, runs claiming SOs without SO lines=%s",
            scope.describe(),
            reason or "read",
            probe["duplicate_run_groups"],
            len(probe["orphan_so_numbers"]),
            [rid for rid, _uid, _foid in probe["empty_so_runs"]],
        )
        # Only the FOs the probe flagged (plus the one being touched) need work.
        fo_ids = {foid for _uid, foid in probe["duplicate_run_groups"]}
        fo_ids |= {foid for _rid, _uid, foid in probe["empty_so_runs"]}
        if filled_order_id is not None:
            fo_ids.add(int(filled_order_id))
        summary: dict[str, Any]
        if fo_ids:
            merged: list[dict[str, Any]] = []
            empties: list[dict[str, Any]] = []
            orphans = drop_orphan_index_rows(
                conn, apply=True, user_filter=scope.user_filter
            )
            for foid in sorted(fo_ids):
                part = repair(
                    conn, scope=scope, filled_order_id=foid, apply=True
                )
                merged.extend(part["orders"])
                empties.extend(part.get("empty_so_runs") or [])
            summary = {
                "scope": scope.describe(),
                "applied": True,
                "processed_orders": len(merged),
                "orphan_index_rows": orphans,
                "runs_merged": sum(
                    len(o.get("dropped_run_ids") or []) for o in merged
                ),
                "empty_so_runs_healed": sum(
                    1 for e in empties if e.get("changed")
                ),
                "orders": merged,
                "empty_so_runs": empties,
            }
        else:
            summary = repair(conn, scope=scope, apply=True)
        logger.info(
            "Order Match autoheal done (%s): %s runs merged, %s orphan index rows "
            "cleared, %s empty-SO runs healed",
            scope.describe(),
            summary.get("runs_merged"),
            summary.get("orphan_index_rows"),
            summary.get("empty_so_runs_healed"),
        )
        return summary
    except Exception:
        logger.exception("order-match autoheal failed (%s)", scope.describe())
        return None
    finally:
        _HEAL_LOCK.release()


def autoheal_for_request(
    conn: sqlite3.Connection,
    *,
    user_id: int | None,
    filled_order_id: int | None = None,
    reason: str = "",
) -> dict[str, Any] | None:
    """Autoheal with the scope of the current authenticated request."""
    if user_id is None:
        return None
    return autoheal(
        conn,
        scope=RepairScope.for_request(int(user_id)),
        filled_order_id=filled_order_id,
        reason=reason,
    )
