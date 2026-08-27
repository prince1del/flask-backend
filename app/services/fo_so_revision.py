"""FO ↔ SO revision helpers: already-in-system, replace, split, additional.

Rules (Balaji AW26 reference):
- Same SO number + same material qty/net → already in system (no duplicate).
- Same SO number + changed qty/value/lines → Old vs New compare → replace confirm.
- After a reduce/replace, FO leftover (mismatch) is free for a *new* SO number:
  if leftover covers the new SO qty → Additional automatically (no Split prompt).
  Example: replace 193 1044→648 (leftover 396), then 543@396 → Additional, not Split.
- Split only when FO is already covered (little/no leftover) and the new SO overlaps
  materials still booked on a parent SO — then parent must be reduced (case 3a).
- Rematch only against that FO (season/category stay with the FO row).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from app.services import fo_so_match_db as matchdb


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def material_key(row: dict[str, Any]) -> str:
    mat = str(row.get("material_code") or "").strip().upper()
    if mat:
        return mat
    detail = str(row.get("product_detail") or row.get("product_name") or "").strip().upper()
    return detail or "?"


def so_number_of(row: dict[str, Any]) -> str | None:
    return matchdb.normalize_so_number(row.get("so_number"))


def lines_for_so(lines: list[Any], so_number: str) -> list[dict[str, Any]]:
    want = (matchdb.normalize_so_number(so_number) or "").upper()
    out: list[dict[str, Any]] = []
    for row in lines:
        if not isinstance(row, dict):
            continue
        got = (so_number_of(row) or "").upper()
        if got == want:
            out.append(row)
    return out


def summarize_lines(lines: list[dict[str, Any]]) -> dict[str, Any]:
    mats: Counter[str] = Counter()
    qty = 0.0
    net = 0.0
    for row in lines:
        q = _f(row.get("qty") or row.get("quantity"))
        n = _f(row.get("net_amount") or row.get("net") or row.get("value"))
        qty += q
        net += n
        mats[material_key(row)] += q
    return {
        "line_count": len(lines),
        "qty": round(qty, 4),
        "net": round(net, 2),
        "materials": {k: round(v, 4) for k, v in sorted(mats.items()) if k != "?"},
    }


def so_effectively_same(old_sum: dict[str, Any], new_sum: dict[str, Any]) -> bool:
    if int(old_sum.get("line_count") or 0) != int(new_sum.get("line_count") or 0):
        return False
    if abs(_f(old_sum.get("qty")) - _f(new_sum.get("qty"))) > 0.05:
        return False
    if abs(_f(old_sum.get("net")) - _f(new_sum.get("net"))) > 1.0:
        return False
    old_m = old_sum.get("materials") or {}
    new_m = new_sum.get("materials") or {}
    if set(old_m.keys()) != set(new_m.keys()):
        return False
    for k, v in old_m.items():
        if abs(_f(v) - _f(new_m.get(k))) > 0.05:
            return False
    return True


def build_so_compare(
    *,
    so_number: str,
    old_lines: list[dict[str, Any]],
    new_lines: list[dict[str, Any]],
    run_id: int | None = None,
    filled_order_id: int | None = None,
    season: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    old_s = summarize_lines(old_lines)
    new_s = summarize_lines(new_lines)
    return {
        "so_number": so_number,
        "run_id": run_id,
        "filled_order_id": filled_order_id,
        "season": season,
        "category": category,
        "same_content": so_effectively_same(old_s, new_s),
        "old": old_s,
        "new": new_s,
        "delta_qty": round(_f(new_s["qty"]) - _f(old_s["qty"]), 4),
        "delta_net": round(_f(new_s["net"]) - _f(old_s["net"]), 2),
    }


def remove_so_numbers(lines: list[Any], so_numbers: set[str]) -> list[dict[str, Any]]:
    drop = {(matchdb.normalize_so_number(s) or "").upper() for s in so_numbers if s}
    out: list[dict[str, Any]] = []
    for row in lines:
        if not isinstance(row, dict):
            continue
        got = (so_number_of(row) or "").upper()
        if got in drop:
            continue
        out.append(row)
    return out


def reduce_parent_by_child(
    parent_lines: list[dict[str, Any]],
    child_lines: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Subtract child material qtys from parent lines (split case 3a)."""
    need: Counter[str] = Counter()
    for row in child_lines:
        need[material_key(row)] += _f(row.get("qty") or row.get("quantity"))

    out: list[dict[str, Any]] = []
    for row in parent_lines:
        key = material_key(row)
        q = _f(row.get("qty") or row.get("quantity"))
        take = min(q, need.get(key, 0.0))
        if take > 0.05:
            need[key] -= take
            q2 = q - take
            if q2 <= 0.05:
                continue
            cloned = dict(row)
            ratio = q2 / q if q else 0.0
            cloned["qty"] = round(q2, 4)
            if row.get("net_amount") is not None:
                cloned["net_amount"] = round(_f(row.get("net_amount")) * ratio, 2)
            if row.get("gst_amount") is not None:
                cloned["gst_amount"] = round(_f(row.get("gst_amount")) * ratio, 2)
            if row.get("total_amount") is not None:
                cloned["total_amount"] = round(_f(row.get("total_amount")) * ratio, 2)
            out.append(cloned)
        else:
            out.append(row)
    return out


def find_parent_candidates(
    existing_lines: list[Any],
    new_lines: list[Any],
) -> list[dict[str, Any]]:
    """SOs on this FO whose materials overlap the incoming SO (real material keys)."""
    new_mats: Counter[str] = Counter()
    for row in new_lines:
        if isinstance(row, dict):
            new_mats[material_key(row)] += _f(row.get("qty") or row.get("quantity"))
    new_mats.pop("?", None)
    if not new_mats:
        return []

    by_so: dict[str, Counter[str]] = defaultdict(Counter)
    for row in existing_lines:
        if not isinstance(row, dict):
            continue
        so = so_number_of(row)
        if not so:
            continue
        by_so[so][material_key(row)] += _f(row.get("qty") or row.get("quantity"))

    candidates: list[dict[str, Any]] = []
    for so, mats in by_so.items():
        mats.pop("?", None)
        overlap_keys = set(new_mats) & set(mats)
        if not overlap_keys:
            continue
        overlap_qty = sum(min(new_mats[k], mats[k]) for k in overlap_keys)
        candidates.append(
            {
                "so_number": so,
                "overlap_materials": len(overlap_keys),
                "overlap_qty": round(overlap_qty, 4),
                "parent_qty": round(sum(mats.values()), 4),
                "new_qty": round(sum(new_mats.values()), 4),
            }
        )
    candidates.sort(key=lambda c: (-c["overlap_qty"], -c["overlap_materials"]))
    return candidates


def fo_qty_leftover(existing_run: dict[str, Any] | None) -> float:
    """Pcs still open on FO after current SOs (sum of max(0, fo − so) per match row)."""
    if not existing_run:
        return 0.0
    leftover = 0.0
    for row in existing_run.get("rows") or []:
        if not isinstance(row, dict):
            continue
        fo_q = _f(row.get("fo_qty"))
        so_q = _f(row.get("so_qty"))
        if fo_q > so_q + 0.05:
            leftover += fo_q - so_q
    return round(leftover, 4)


def leftover_covers_new_so(
    existing_run: dict[str, Any] | None,
    new_lines: list[dict[str, Any]],
    *,
    tol: float = 0.5,
) -> bool:
    """True when FO mismatch leftover can absorb this new SO without cutting a parent.

    Balaji: after replace 193→648, leftover≈396; SO 543@396 → Additional (not Split).
    """
    leftover = fo_qty_leftover(existing_run)
    new_qty = _f(summarize_lines(new_lines).get("qty"))
    if new_qty <= 0.05:
        return False
    return leftover + tol >= new_qty


def tag_additional(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in lines:
        cloned = dict(row)
        cloned["nexora_so_role"] = "additional"
        out.append(cloned)
    return out


def get_latest_run_for_fo(
    conn,
    *,
    user_id: int,
    filled_order_id: int,
) -> dict[str, Any] | None:
    matchdb.ensure_schema(conn)
    row = conn.execute(
        """
        SELECT id FROM fo_so_match_runs
        WHERE user_id = ? AND filled_order_id = ?
        ORDER BY id DESC LIMIT 1
        """,
        (user_id, filled_order_id),
    ).fetchone()
    if not row:
        return None
    return matchdb.get_match_run(conn, int(row[0]), user_id=user_id)


def analyze_incoming_against_existing(
    *,
    existing_run: dict[str, Any] | None,
    so_pack: dict[str, Any],
    conflicts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Decide already / replace / split-or-additional / clean / auto-additional."""
    new_lines = [
        r for r in (so_pack.get("line_detail") or []) if isinstance(r, dict)
    ]
    new_numbers = matchdb.extract_so_numbers_from_pack(so_pack)
    existing_lines = list(existing_run.get("so_line_detail") or []) if existing_run else []

    if conflicts:
        compares = []
        all_same = True
        for c in conflicts:
            so_n = str(c.get("so_number") or "")
            old_l = lines_for_so(existing_lines, so_n)
            new_l = lines_for_so(new_lines, so_n)
            if not new_l:
                if len(new_numbers) == 1:
                    new_l = new_lines
            cmp = build_so_compare(
                so_number=so_n,
                old_lines=old_l,
                new_lines=new_l,
                run_id=c.get("run_id") or (existing_run or {}).get("id"),
                filled_order_id=(existing_run or {}).get("filled_order_id")
                or c.get("filled_order_id"),
                season=(existing_run or {}).get("season"),
                category=(existing_run or {}).get("category"),
            )
            compares.append(cmp)
            if not cmp["same_content"]:
                all_same = False
        return {
            "action": "already_in_system" if all_same else "replace_confirm",
            "compares": compares,
            "so_numbers": new_numbers,
        }

    if existing_run and new_lines:
        parents = find_parent_candidates(existing_lines, new_lines)
        leftover = fo_qty_leftover(existing_run)
        new_summary = summarize_lines(new_lines)
        # FO still short and this SO fits the gap → Additional (silent).
        # Do NOT offer Split: parent was already reduced via Replace.
        if parents and leftover_covers_new_so(existing_run, new_lines):
            return {
                "action": "save_new",
                "so_numbers": new_numbers,
                "auto_additional": True,
                "fo_leftover_qty": leftover,
                "new_summary": new_summary,
                "parent_candidates": parents,
            }
        if parents:
            recommended = "additional" if leftover > 0.5 else "split"
            return {
                "action": "split_or_additional",
                "parent_candidates": parents,
                "new_summary": new_summary,
                "so_numbers": new_numbers,
                "filled_order_id": existing_run.get("filled_order_id"),
                "season": existing_run.get("season"),
                "category": existing_run.get("category"),
                "run_id": existing_run.get("id"),
                "fo_leftover_qty": leftover,
                "recommended_action": recommended,
            }

    return {"action": "save_new", "so_numbers": new_numbers}


def merge_lines_for_replace(
    existing_lines: list[Any],
    new_lines: list[Any],
    replace_so_numbers: set[str],
) -> list[dict[str, Any]]:
    kept = remove_so_numbers(existing_lines, replace_so_numbers)
    incoming = [r for r in new_lines if isinstance(r, dict)]
    if replace_so_numbers and incoming and not any(so_number_of(r) for r in incoming):
        only = next(iter(replace_so_numbers))
        stamped = []
        for r in incoming:
            c = dict(r)
            c["so_number"] = only
            stamped.append(c)
        incoming = stamped
    return kept + incoming


def merge_lines_for_split(
    existing_lines: list[Any],
    new_lines: list[Any],
    parent_so_number: str,
) -> list[dict[str, Any]]:
    parent = lines_for_so(existing_lines, parent_so_number)
    others = remove_so_numbers(existing_lines, {parent_so_number})
    child = [r for r in new_lines if isinstance(r, dict)]
    reduced = reduce_parent_by_child(parent, child)
    return others + reduced + child


def merge_lines_for_additional(
    existing_lines: list[Any],
    new_lines: list[Any],
) -> list[dict[str, Any]]:
    incoming = tag_additional([r for r in new_lines if isinstance(r, dict)])
    return list(existing_lines) + incoming


def rebuild_run_from_lines(
    conn,
    *,
    user_id: int,
    run_id: int,
    lines: list[dict[str, Any]],
    source_filename: str | None = None,
) -> dict[str, Any] | None:
    """Rematch a run's surviving SO lines against its own FO, in place.

    Used when a single SO is removed from a run that holds several SOs: the
    other SOs' lines, match rows and totals must stay intact.
    """
    import filled_orders_db as fodb
    from app.services.fo_so_match_lab import run_match_saved_fo_vs_so_pack

    run = matchdb.get_match_run(conn, int(run_id), user_id=int(user_id))
    if not run:
        raise ValueError("Match run not found")
    filled_order_id = run.get("filled_order_id")
    if filled_order_id is None:
        raise ValueError("Match run has no filled order to rematch against")

    fodb.ensure_schema(conn)
    fo = fodb.get_filled_order(conn, int(user_id), int(filled_order_id))
    if not fo:
        raise ValueError("Filled order not found")
    items = fodb.get_filled_order_items(conn, int(filled_order_id))

    pack = pack_from_lines(
        lines, source_filename=source_filename or run.get("so_source_filename")
    )
    result = run_match_saved_fo_vs_so_pack(
        fo_meta=fo, fo_items=items, so_pack_payload=pack
    )
    return matchdb.update_run_from_match(
        conn,
        run_id=int(run_id),
        user_id=int(user_id),
        match_payload=result,
        so_line_detail=lines,
        so_pack=pack,
    )


def remove_so_from_run(
    conn,
    *,
    user_id: int,
    run_id: int,
    so_numbers: list[str],
) -> dict[str, Any]:
    """Delete only these SO numbers from a run; keep the FO's other SOs.

    Returns {"deleted_run": bool, "run": <run or None>, "removed": [...]}.
    """
    run = matchdb.get_match_run(conn, int(run_id), user_id=int(user_id))
    if not run:
        raise ValueError("Match run not found")
    drop = {
        (matchdb.normalize_so_number(n) or "").upper()
        for n in so_numbers
        if matchdb.normalize_so_number(n)
    }
    if not drop:
        raise ValueError("No valid SO numbers to remove")

    existing_lines = [
        r for r in (run.get("so_line_detail") or []) if isinstance(r, dict)
    ]
    remaining = remove_so_numbers(existing_lines, drop)
    survivors = {
        (so_number_of(r) or "").upper() for r in remaining if so_number_of(r)
    }
    if not remaining or not survivors:
        # Last SO on this FO — the run itself has nothing left to show.
        matchdb.delete_match_run(conn, int(user_id), int(run_id))
        return {"deleted_run": True, "run": None, "removed": sorted(drop)}

    updated = rebuild_run_from_lines(
        conn, user_id=int(user_id), run_id=int(run_id), lines=remaining
    )
    return {"deleted_run": False, "run": updated, "removed": sorted(drop)}


def pack_from_lines(lines: list[dict[str, Any]], *, source_filename: str | None = None) -> dict[str, Any]:
    """Minimal so_pack payload for rematch from merged line_detail."""
    qty = sum(_f(r.get("qty") or r.get("quantity")) for r in lines)
    net = sum(_f(r.get("net_amount") or r.get("net")) for r in lines)
    return {
        "meta": {
            "source_filename": source_filename or "merged_so_revision",
            "line_rows": len(lines),
            "total_qty": qty,
            "net_amount": net,
        },
        "line_detail": lines,
        "consolidated": [],
        "so_summary": [],
    }
