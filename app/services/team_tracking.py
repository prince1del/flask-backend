"""Duty (Start day / End day), live location while on duty, and the owner's team view.

Attendance: the owner sets work sites (place + radius) and which sites each
member works from. A member with sites can Start day only inside one of them
(phone sends its location; mock GPS is refused). Hours on duty per India day
give Present / Half day / Absent by the company's rule (default 8 h / 4 h).
A day left open is closed at the last location point (max 14 h).

Members with work sites must also End day at a site. Away from it, they send a
request (why + who allowed it); the day counts as off ("approval pending from
HR / management") until the owner/admin approves. A day never ended counts as
off too ("End day missing") until a request is approved.

Location is shared **only between Start day and End day**, which the member
taps themselves; the phone shows a notification the whole time. Points older
than 30 days are deleted. Only the owner/admin of the same workspace can see a
member's location, route or activity — members never see each other.
"""

from __future__ import annotations

import math
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

RETENTION_DAYS = 30
MAX_BATCH = 500
STOP_RADIUS_M = 150
STOP_MIN_MINUTES = 10
_last_purge = 0.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS duty_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            workspace_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_duty_user ON duty_sessions(user_id, id DESC);
        CREATE TABLE IF NOT EXISTS location_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            workspace_id TEXT NOT NULL,
            session_id INTEGER NOT NULL,
            lat REAL NOT NULL,
            lng REAL NOT NULL,
            accuracy REAL,
            recorded_at TEXT NOT NULL,
            received_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_loc_user_time ON location_points(user_id, recorded_at);
        CREATE INDEX IF NOT EXISTS idx_loc_time ON location_points(recorded_at);
        CREATE TABLE IF NOT EXISTS work_sites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT NOT NULL,
            name TEXT NOT NULL,
            lat REAL NOT NULL,
            lng REAL NOT NULL,
            radius_m INTEGER NOT NULL DEFAULT 200,
            created_by INTEGER,
            created_at TEXT NOT NULL,
            deleted_at TEXT
        );
        CREATE TABLE IF NOT EXISTS member_sites (
            user_id INTEGER NOT NULL,
            site_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, site_id)
        );
        CREATE TABLE IF NOT EXISTS attendance_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL UNIQUE,
            user_id INTEGER NOT NULL,
            workspace_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            note TEXT NOT NULL,
            approver_name TEXT,
            distance_m INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            reply TEXT,
            decided_by INTEGER,
            decided_at TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS attendance_rules (
            workspace_id TEXT PRIMARY KEY,
            full_day_hours REAL NOT NULL DEFAULT 8,
            half_day_hours REAL NOT NULL DEFAULT 4,
            updated_at TEXT
        );
        """
    )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(duty_sessions)").fetchall()}
    for col, typ in (("start_lat", "REAL"), ("start_lng", "REAL"), ("site_id", "INTEGER"),
                     ("end_lat", "REAL"), ("end_lng", "REAL"), ("end_ok", "INTEGER")):
        if col not in cols:
            conn.execute(f"ALTER TABLE duty_sessions ADD COLUMN {col} {typ}")
    conn.commit()


def _connect(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def _purge(conn: sqlite3.Connection) -> None:
    """Delete points older than RETENTION_DAYS (at most once an hour)."""
    global _last_purge
    if time.monotonic() - _last_purge < 3600:
        return
    _last_purge = time.monotonic()
    cutoff = (_now() - timedelta(days=RETENTION_DAYS)).isoformat()
    conn.execute("DELETE FROM location_points WHERE recorded_at < ?", (cutoff,))
    conn.execute("DELETE FROM duty_sessions WHERE ended_at IS NOT NULL AND ended_at < ?", (cutoff,))
    conn.commit()


# ── Member: duty and points ──────────────────────────────────────────────────

def _open_session(conn: sqlite3.Connection, user_id: int):
    return conn.execute(
        "SELECT * FROM duty_sessions WHERE user_id = ? AND ended_at IS NULL ORDER BY id DESC LIMIT 1",
        (int(user_id),),
    ).fetchone()


def duty_status(db_path, user_id: int) -> dict[str, Any]:
    with _connect(db_path) as conn:
        s = _open_session(conn, user_id)
    return {"on_duty": s is not None, "started_at": s["started_at"] if s else None,
            "session_id": s["id"] if s else None}


class NotAtSite(Exception):
    """Start day refused: not at any of the member's work sites."""


def _where(where: dict[str, Any] | None) -> tuple[float, float, float] | None:
    if not isinstance(where, dict):
        return None
    try:
        lat, lng = float(where["lat"]), float(where["lng"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    acc = where.get("accuracy")
    return lat, lng, float(acc) if isinstance(acc, (int, float)) else 50.0


def my_sites(conn: sqlite3.Connection, user_id: int) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        """SELECT s.id, s.name, s.lat, s.lng, s.radius_m FROM member_sites m
           JOIN work_sites s ON s.id = m.site_id WHERE m.user_id = ? AND s.deleted_at IS NULL ORDER BY s.name""",
        (int(user_id),),
    ).fetchall()]


def start_duty(db_path, user: dict[str, Any], where: dict[str, Any] | None = None) -> dict[str, Any]:
    """Start day. A member with work sites must be inside one (location sent by the phone)."""
    if isinstance(where, dict) and where.get("mock"):
        raise NotAtSite("Fake GPS is on. Turn off mock location apps to start your day.")
    here = _where(where)
    with _connect(db_path) as conn:
        s = _open_session(conn, user["user_id"])
        if s is None:
            sites = my_sites(conn, user["user_id"])
            site_id = None
            if sites:
                if here is None:
                    raise NotAtSite("Turn on location to start your day at your work place.")
                lat, lng, acc = here
                best, gap = None, None
                for site in sites:
                    dist = _metres((lat, lng), (site["lat"], site["lng"]))
                    slack = min(max(acc, 0), 100)  # poor GPS gets up to 100 m benefit of doubt
                    if dist <= site["radius_m"] + slack:
                        best = site
                        break
                    if gap is None or dist < gap[0]:
                        gap = (dist, site)
                if best is None:
                    d, site = gap
                    far = f"{d / 1000:.1f} km" if d >= 1000 else f"{int(d)} m"
                    raise NotAtSite(f"You are {far} away from {site['name']}. Start your day when you reach there.")
                site_id = best["id"]
            conn.execute(
                """INSERT INTO duty_sessions (user_id, workspace_id, started_at, start_lat, start_lng, site_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (int(user["user_id"]), str(user.get("workspace_id") or "default"), _now().isoformat(),
                 here[0] if here else None, here[1] if here else None, site_id),
            )
            conn.commit()
    return duty_status(db_path, user["user_id"])


class EndAway(NotAtSite):
    """End day away from the work place: needs a note and who allowed it."""

    def __init__(self, message: str, distance_m: int | None, site_name: str | None):
        super().__init__(message)
        self.distance_m = distance_m
        self.site_name = site_name


def _nearest(sites: list[dict[str, Any]], here: tuple[float, float, float] | None):
    """(inside?, distance to nearest site in m, that site)."""
    if not sites or here is None:
        return False, None, (sites[0] if sites else None)
    lat, lng, acc = here
    best = None
    for site in sites:
        dist = _metres((lat, lng), (site["lat"], site["lng"]))
        if dist <= site["radius_m"] + min(max(acc, 0), 100):
            return True, int(dist), site
        if best is None or dist < best[0]:
            best = (dist, site)
    return False, int(best[0]), best[1]


def end_duty(db_path, user: dict[str, Any], where: dict[str, Any] | None = None) -> dict[str, Any]:
    """End day. With work places it must happen at one; otherwise a request
    (note + who allowed it) is sent and the day stays off until approved."""
    where = where or {}
    here = None if where.get("mock") else _where(where)
    note = str(where.get("note") or "").strip()[:300]
    approver = str(where.get("approver") or "").strip()[:80]
    with _connect(db_path) as conn:
        s = _open_session(conn, user["user_id"])
        if s is None:
            return duty_status(db_path, user["user_id"])
        sites = my_sites(conn, user["user_id"])
        inside, dist, site = _nearest(sites, here) if sites else (True, None, None)
        if not inside and not (note and approver):
            far = (f"{dist / 1000:.1f} km" if dist >= 1000 else f"{dist} m") if dist is not None else "not at"
            raise EndAway(
                f"You are {far} away from {site['name']}. To end your day here, tell why and who allowed it — "
                "the day stays off until HR / management approves.",
                dist, site["name"] if site else None,
            )
        now = _now().isoformat()
        conn.execute(
            "UPDATE duty_sessions SET ended_at = ?, end_lat = ?, end_lng = ?, end_ok = ? WHERE id = ?",
            (now, here[0] if here else None, here[1] if here else None, 1 if inside else 0, s["id"]),
        )
        if not inside:
            conn.execute(
                """INSERT OR REPLACE INTO attendance_requests
                   (session_id, user_id, workspace_id, kind, note, approver_name, distance_m, status, created_at)
                   VALUES (?, ?, ?, 'end_away', ?, ?, ?, 'pending', ?)""",
                (s["id"], int(user["user_id"]), s["workspace_id"], note, approver, dist, now),
            )
        conn.commit()
    return {**duty_status(db_path, user["user_id"]), "pending_approval": not inside}


def request_for_day(db_path, user: dict[str, Any], day: str, note: str, approver: str) -> dict[str, Any]:
    """Member: a day whose End day was missed (or ended away) — ask for approval."""
    note, approver = (note or "").strip()[:300], (approver or "").strip()[:80]
    if not note or not approver:
        raise ValueError("Write why, and who allowed it")
    d = date.fromisoformat(day)
    start = datetime(d.year, d.month, d.day, tzinfo=IST).astimezone(timezone.utc)
    with _connect(db_path) as conn:
        s = conn.execute(
            """SELECT * FROM duty_sessions WHERE user_id = ? AND started_at >= ? AND started_at < ?
               AND IFNULL(end_ok, 0) = 0 ORDER BY id DESC LIMIT 1""",
            (int(user["user_id"]), start.isoformat(), (start + timedelta(days=1)).isoformat()),
        ).fetchone()
        if s is None:
            raise LookupError("No day to ask about on this date")
        conn.execute(
            """INSERT INTO attendance_requests (session_id, user_id, workspace_id, kind, note, approver_name, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
               ON CONFLICT(session_id) DO UPDATE SET note = excluded.note, approver_name = excluded.approver_name,
                 status = 'pending', reply = NULL, decided_by = NULL, decided_at = NULL""",
            (s["id"], int(user["user_id"]), str(user.get("workspace_id") or s["workspace_id"]),
             "no_end" if not s["ended_at"] else "end_away",
             note, approver, _now().isoformat()),
        )
        conn.commit()
    return {"status": "pending"}


def list_requests(db, manager: dict[str, Any], status: str | None = "pending") -> list[dict[str, Any]]:
    ws = _manager_check(db, manager)
    with _connect(db.db_path) as conn:
        sql = """SELECT r.*, u.username, u.full_name, s.started_at, s.ended_at
                 FROM attendance_requests r JOIN users u ON u.id = r.user_id
                 JOIN duty_sessions s ON s.id = r.session_id
                 WHERE r.workspace_id = ? AND u.workspace_id = ?"""
        params: list[Any] = [ws, ws]
        if status:
            sql += " AND r.status = ?"
            params.append(status)
        rows = conn.execute(sql + " ORDER BY r.id DESC LIMIT 200", params).fetchall()
    return [dict(r) for r in rows]


def decide_request(db, manager: dict[str, Any], request_id: int, approve: bool, reply: str | None = None) -> dict[str, Any]:
    ws = _manager_check(db, manager)
    with _connect(db.db_path) as conn:
        r = conn.execute(
            """SELECT r.* FROM attendance_requests r JOIN users u ON u.id = r.user_id
               WHERE r.id = ? AND r.workspace_id = ? AND u.workspace_id = ?""",
            (int(request_id), ws, ws),
        ).fetchone()
        if r is None:
            raise LookupError("Request not found")
        if int(r["user_id"]) == int(manager["user_id"]):
            raise PermissionError("You cannot approve your own request")
        conn.execute(
            "UPDATE attendance_requests SET status = ?, reply = ?, decided_by = ?, decided_at = ? WHERE id = ?",
            ("approved" if approve else "rejected", (reply or "").strip()[:300] or None,
             int(manager["user_id"]), _now().isoformat(), int(request_id)),
        )
        conn.commit()
    return {"id": int(request_id), "status": "approved" if approve else "rejected"}


def add_points(db_path, user: dict[str, Any], points: list[dict[str, Any]]) -> dict[str, Any]:
    """Store points sent while on duty. Points outside the duty window are dropped."""
    if not isinstance(points, list):
        raise ValueError("points must be a list")
    if len(points) > MAX_BATCH:
        raise ValueError(f"At most {MAX_BATCH} points at once")
    now = _now()
    with _connect(db_path) as conn:
        s = _open_session(conn, user["user_id"])
        if s is None:
            raise PermissionError("Start your day first")
        started = _parse(s["started_at"]) - timedelta(minutes=2)
        kept = 0
        for p in points:
            try:
                lat, lng = float(p["lat"]), float(p["lng"])
            except (KeyError, TypeError, ValueError):
                continue
            if not (-90 <= lat <= 90 and -180 <= lng <= 180) or (lat == 0 and lng == 0):
                continue
            at = _parse(p.get("recorded_at")) or now
            if at < started or at > now + timedelta(minutes=5):
                continue  # never store anything from before Start day
            acc = p.get("accuracy")
            conn.execute(
                """INSERT INTO location_points (user_id, workspace_id, session_id, lat, lng, accuracy, recorded_at, received_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (int(user["user_id"]), s["workspace_id"], s["id"], lat, lng,
                 float(acc) if isinstance(acc, (int, float)) else None, at.isoformat(), now.isoformat()),
            )
            kept += 1
        conn.commit()
        _purge(conn)
    return {"saved": kept}


# ── Owner / admin: team map, route and activity ─────────────────────────────

def _manager_check(db, manager: dict[str, Any]) -> str:
    from app.services import company_invites as ci

    if not ci.can_manage_invites(db, manager):
        raise PermissionError("Only your company's owner or admin can see the team")
    ws = str(manager.get("workspace_id") or "")
    if not ws or ws == "default":
        raise PermissionError("Your login has no company workspace")
    return ws


def _member(conn: sqlite3.Connection, ws: str, member_id: int):
    row = conn.execute(
        "SELECT id, username, full_name, role, workspace_id FROM users WHERE id = ?", (int(member_id),)
    ).fetchone()
    if row is None or row["workspace_id"] != ws:  # never another company's login
        raise LookupError("Member not found in your company")
    return row


def team_live(db, manager: dict[str, Any]) -> list[dict[str, Any]]:
    """Every login in my workspace: on duty?, last location and how long ago."""
    ws = _manager_check(db, manager)
    db.ensure_user_profile_columns()
    now = _now()
    with _connect(db.db_path) as conn:
        users = conn.execute(
            """SELECT id, username, full_name, role FROM users
               WHERE workspace_id = ? AND lower(username) NOT LIKE 'archived_dup_%' ORDER BY id""",
            (ws,),
        ).fetchall()
        out = []
        for u in users:
            s = _open_session(conn, u["id"])
            p = conn.execute(
                "SELECT lat, lng, accuracy, recorded_at FROM location_points WHERE user_id = ? ORDER BY recorded_at DESC LIMIT 1",
                (u["id"],),
            ).fetchone()
            at = _parse(p["recorded_at"]) if p else None
            out.append({
                "id": u["id"], "username": u["username"], "full_name": u["full_name"], "role": u["role"],
                "on_duty": s is not None, "duty_started_at": s["started_at"] if s else None,
                "last": ({"lat": p["lat"], "lng": p["lng"], "accuracy": p["accuracy"], "at": p["recorded_at"],
                          "minutes_ago": int((now - at).total_seconds() // 60) if at else None} if p else None),
            })
    return out


def _stops(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Places where the member stayed within STOP_RADIUS_M for STOP_MIN_MINUTES or more."""
    stops: list[dict[str, Any]] = []
    i = 0
    while i < len(points):
        j = i
        while j + 1 < len(points) and _metres((points[i]["lat"], points[i]["lng"]),
                                              (points[j + 1]["lat"], points[j + 1]["lng"])) <= STOP_RADIUS_M:
            j += 1
        t0, t1 = _parse(points[i]["at"]), _parse(points[j]["at"])
        minutes = int((t1 - t0).total_seconds() // 60) if t0 and t1 else 0
        if minutes >= STOP_MIN_MINUTES:
            group = points[i:j + 1]
            stops.append({
                "lat": sum(p["lat"] for p in group) / len(group),
                "lng": sum(p["lng"] for p in group) / len(group),
                "from": points[i]["at"], "to": points[j]["at"], "minutes": minutes,
            })
            i = j + 1
        else:
            i += 1
    return stops


def member_day(db, manager: dict[str, Any], member_id: int, day: str | None = None) -> dict[str, Any]:
    """One member's day: route, stops, km, duty times, and the work they did."""
    ws = _manager_check(db, manager)
    d = date.fromisoformat(day) if day else _now().astimezone(timezone(timedelta(hours=5, minutes=30))).date()
    # The day in India time, as UTC bounds.
    ist = timezone(timedelta(hours=5, minutes=30))
    start = datetime(d.year, d.month, d.day, tzinfo=ist).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    with _connect(db.db_path) as conn:
        m = _member(conn, ws, member_id)
        pts = [dict(r) for r in conn.execute(
            """SELECT lat, lng, accuracy, recorded_at AS at FROM location_points
               WHERE user_id = ? AND recorded_at >= ? AND recorded_at < ? ORDER BY recorded_at""",
            (m["id"], start.isoformat(), end.isoformat()),
        ).fetchall()]
        sessions = [dict(r) for r in conn.execute(
            """SELECT started_at, ended_at FROM duty_sessions WHERE user_id = ?
               AND started_at < ? AND (ended_at IS NULL OR ended_at >= ?) ORDER BY id""",
            (m["id"], end.isoformat(), start.isoformat()),
        ).fetchall()]
        work = _work_for_day(conn, m["id"], d, start, end)
    km = 0.0
    for a, b in zip(pts, pts[1:]):
        step = _metres((a["lat"], a["lng"]), (b["lat"], b["lng"]))
        if step < 5000:  # ignore GPS jumps
            km += step / 1000
    return {
        "member": {"id": m["id"], "username": m["username"], "full_name": m["full_name"], "role": m["role"]},
        "date": d.isoformat(), "duty": sessions, "points": pts, "stops": _stops(pts),
        "km": round(km, 1), "work": work,
    }


def _rows(conn: sqlite3.Connection, sql: str, params: tuple) -> list[dict[str, Any]]:
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except sqlite3.OperationalError:
        return []  # module not used in this workspace yet


def _work_for_day(conn: sqlite3.Connection, user_id: int, d: date, start: datetime, end: datetime) -> dict[str, Any]:
    """Read-only summary of what the member entered that day. Personal to-dos stay private."""
    s, e = start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")
    si, ei = start.isoformat(), end.isoformat()
    return {
        "market_visits": _rows(conn, """SELECT customer_name, area_text, city_area, channel_type, order_lacs, created_at
               FROM dsr_market_visits WHERE user_id = ? AND visit_date = ? AND IFNULL(is_draft, 0) = 0
               ORDER BY created_at""", (user_id, d.isoformat())),
        "filled_orders": _rows(conn, """SELECT id, distributor_name_raw AS distributor_name, category, season, total_lines, created_at FROM filled_orders
               WHERE user_id = ? AND ((created_at >= ? AND created_at < ?) OR (created_at >= ? AND created_at < ?))
               ORDER BY id""", (user_id, s, e, si, ei)),
        "so_matches": _rows(conn, """SELECT id, distributor_name, category, season, so_net_amount, created_at
               FROM fo_so_match_runs WHERE user_id = ? AND ((created_at >= ? AND created_at < ?) OR (created_at >= ? AND created_at < ?))
               ORDER BY id""", (user_id, s, e, si, ei)),
        "payments": _rows(conn, """SELECT distributor_id, season, category, amount, payment_date, created_at
               FROM distributor_category_payments WHERE user_id = ?
               AND ((created_at >= ? AND created_at < ?) OR (created_at >= ? AND created_at < ?)) ORDER BY id""",
                          (user_id, s, e, si, ei)),
    }


# ── Work sites and attendance ────────────────────────────────────────────────

IST = timezone(timedelta(hours=5, minutes=30))
MAX_OPEN_HOURS = 14


def list_sites(db, manager: dict[str, Any]) -> list[dict[str, Any]]:
    ws = _manager_check(db, manager)
    with _connect(db.db_path) as conn:
        sites = [dict(r) for r in conn.execute(
            "SELECT id, name, lat, lng, radius_m FROM work_sites WHERE workspace_id = ? AND deleted_at IS NULL ORDER BY name",
            (ws,),
        ).fetchall()]
        for site in sites:
            site["member_ids"] = [r[0] for r in conn.execute(
                "SELECT user_id FROM member_sites WHERE site_id = ?", (site["id"],)).fetchall()]
    return sites


def save_site(db, manager: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    ws = _manager_check(db, manager)
    name = str(data.get("name") or "").strip()[:60]
    if not name:
        raise ValueError("Give the place a name, e.g. Office")
    here = _where(data)
    if here is None:
        raise ValueError("Pick the place on the map or use your current location")
    try:
        radius = int(data.get("radius_m") or 200)
    except (TypeError, ValueError):
        raise ValueError("Radius must be a number of metres") from None
    if not 50 <= radius <= 5000:
        raise ValueError("Radius must be 50–5000 metres")
    with _connect(db.db_path) as conn:
        site_id = data.get("id")
        if site_id:
            n = conn.execute(
                "UPDATE work_sites SET name = ?, lat = ?, lng = ?, radius_m = ? WHERE id = ? AND workspace_id = ? AND deleted_at IS NULL",
                (name, here[0], here[1], radius, int(site_id), ws),
            ).rowcount
            if n != 1:
                raise LookupError("Place not found")
        else:
            site_id = conn.execute(
                "INSERT INTO work_sites (workspace_id, name, lat, lng, radius_m, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ws, name, here[0], here[1], radius, int(manager["user_id"]), _now().isoformat()),
            ).lastrowid
        conn.commit()
    return next(x for x in list_sites(db, manager) if x["id"] == int(site_id))


def delete_site(db, manager: dict[str, Any], site_id: int) -> None:
    ws = _manager_check(db, manager)
    with _connect(db.db_path) as conn:
        n = conn.execute("UPDATE work_sites SET deleted_at = ? WHERE id = ? AND workspace_id = ? AND deleted_at IS NULL",
                         (_now().isoformat(), int(site_id), ws)).rowcount
        if n != 1:
            raise LookupError("Place not found")
        conn.execute("DELETE FROM member_sites WHERE site_id = ?", (int(site_id),))
        conn.commit()


def set_member_sites(db, manager: dict[str, Any], member_id: int, site_ids: list[int]) -> list[dict[str, Any]]:
    """Which places this member may start the day at ([] = anywhere)."""
    ws = _manager_check(db, manager)
    with _connect(db.db_path) as conn:
        _member(conn, ws, member_id)
        ids = [int(x) for x in (site_ids or [])]
        if ids:
            ok = {r[0] for r in conn.execute(
                f"SELECT id FROM work_sites WHERE workspace_id = ? AND deleted_at IS NULL AND id IN ({','.join('?' * len(ids))})",
                (ws, *ids)).fetchall()}
            if ok != set(ids):
                raise LookupError("Place not found")
        conn.execute("DELETE FROM member_sites WHERE user_id = ?", (int(member_id),))
        conn.executemany("INSERT INTO member_sites (user_id, site_id) VALUES (?, ?)", [(int(member_id), i) for i in ids])
        conn.commit()
        return my_sites(conn, member_id)


def get_rules(db_path, workspace_id: str) -> dict[str, float]:
    with _connect(db_path) as conn:
        r = conn.execute("SELECT full_day_hours, half_day_hours FROM attendance_rules WHERE workspace_id = ?",
                         (workspace_id,)).fetchone()
    return {"full_day_hours": r[0], "half_day_hours": r[1]} if r else {"full_day_hours": 8.0, "half_day_hours": 4.0}


def set_rules(db, manager: dict[str, Any], full_day: float, half_day: float) -> dict[str, float]:
    ws = _manager_check(db, manager)
    try:
        full_day, half_day = float(full_day), float(half_day)
    except (TypeError, ValueError):
        raise ValueError("Hours must be numbers") from None
    if not (0 < half_day < full_day <= 16):
        raise ValueError("Half day must be less than a full day (max 16 hours)")
    with _connect(db.db_path) as conn:
        conn.execute(
            """INSERT INTO attendance_rules (workspace_id, full_day_hours, half_day_hours, updated_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(workspace_id) DO UPDATE SET full_day_hours = excluded.full_day_hours,
                 half_day_hours = excluded.half_day_hours, updated_at = excluded.updated_at""",
            (ws, full_day, half_day, _now().isoformat()),
        )
        conn.commit()
    return get_rules(db.db_path, ws)


def _session_end(conn: sqlite3.Connection, s) -> datetime:
    """When a session really ended: End day, else last point, never more than 14 h, never in the future."""
    started = _parse(s["started_at"])
    if s["ended_at"]:
        return _parse(s["ended_at"])
    cap = started + timedelta(hours=MAX_OPEN_HOURS)
    now = _now()
    last = conn.execute("SELECT MAX(recorded_at) FROM location_points WHERE session_id = ?", (s["id"],)).fetchone()[0]
    last_at = _parse(last)
    started_day = started.astimezone(IST).date()
    if started_day == now.astimezone(IST).date():
        return min(now, cap)  # still working today
    return min(last_at or started, cap)  # forgot to End day: last place seen


def _status(hours: float, rules: dict[str, float]) -> str:
    if hours >= rules["full_day_hours"]:
        return "P"
    if hours >= rules["half_day_hours"]:
        return "H"
    return "A"


def _tracks_attendance(db_path, user_id: int) -> bool:
    """False when the owner switched attendance off for this member (e.g. a freelancer)."""
    from app.services import member_access

    with _connect(db_path) as conn:
        r = conn.execute("SELECT features FROM member_access WHERE user_id = ?", (int(user_id),)).fetchone()
    if r is None or not r["features"]:
        return True
    import json

    return "attendance" in json.loads(r["features"])


def attendance_month(db, manager: dict[str, Any] | None, month: str, member_id: int | None = None,
                     self_user: dict[str, Any] | None = None) -> dict[str, Any]:
    """Hours and P/H/A for every day of ``month`` (YYYY-MM). Manager: whole team; self_user: only me."""
    try:
        first = datetime.strptime(month + "-01", "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("month must be YYYY-MM") from None
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    days = [(first + timedelta(days=i)).isoformat() for i in range((nxt - first).days)]
    today = _now().astimezone(IST).date().isoformat()
    if self_user is not None:
        ws = str(self_user.get("workspace_id") or "default")
        people = [{"id": int(self_user["user_id"]), "username": self_user.get("username"), "full_name": None}]
    else:
        ws = _manager_check(db, manager)
        with _connect(db.db_path) as conn:
            people = [dict(r) for r in conn.execute(
                """SELECT id, username, full_name FROM users WHERE workspace_id = ?
                   AND lower(username) NOT LIKE 'archived_dup_%' ORDER BY id""", (ws,)).fetchall()]
            if member_id is not None:
                _member(conn, ws, member_id)
                people = [p for p in people if p["id"] == int(member_id)]
    rules = get_rules(db.db_path, ws)
    start_utc = datetime(first.year, first.month, 1, tzinfo=IST).astimezone(timezone.utc)
    end_utc = datetime(nxt.year, nxt.month, 1, tzinfo=IST).astimezone(timezone.utc)
    out = []
    with _connect(db.db_path) as conn:
        for person in people:
            hours = {d: 0.0 for d in days}
            sessions = conn.execute(
                """SELECT * FROM duty_sessions WHERE user_id = ? AND started_at < ?
                   AND (ended_at IS NULL OR ended_at >= ?) ORDER BY id""",
                (person["id"], end_utc.isoformat(), (start_utc - timedelta(hours=MAX_OPEN_HOURS)).isoformat()),
            ).fetchall()
            has_sites = bool(my_sites(conn, person["id"]))
            pending_days: dict[str, str] = {}
            for s in sessions:
                a, b = _parse(s["started_at"]), _session_end(conn, s)
                if has_sites and not s["end_ok"]:
                    req = conn.execute("SELECT status FROM attendance_requests WHERE session_id = ?",
                                       (s["id"],)).fetchone()
                    day_key = a.astimezone(IST).date().isoformat()
                    if req is None or req["status"] != "approved":
                        if s["ended_at"] is None and day_key == today:
                            pass  # still at work today
                        else:
                            # Off until approved: "pending" / "rejected" / "no_end" (never ended, no request)
                            pending_days[day_key] = (req["status"] if req else
                                                     ("no_end" if s["ended_at"] is None else "pending"))
                            continue
                cur = a
                while cur < b:  # split across midnight (India time)
                    day_end = datetime.combine(cur.astimezone(IST).date() + timedelta(days=1),
                                               datetime.min.time(), IST).astimezone(timezone.utc)
                    piece_end = min(b, day_end)
                    key = cur.astimezone(IST).date().isoformat()
                    if key in hours:
                        hours[key] += (piece_end - cur).total_seconds() / 3600
                    cur = piece_end
            if not _tracks_attendance(db.db_path, person["id"]):
                out.append({"id": person["id"], "username": person["username"],
                            "full_name": person.get("full_name"), "not_tracked": True,
                            "days": [{"date": d, "hours": 0.0, "status": ""} for d in days],
                            "present": 0, "half": 0, "absent": 0, "total_hours": 0.0})
                continue
            row = []
            for d in days:
                h = round(hours[d], 2)
                status = "" if d > today else ("•" if d == today and h < rules["full_day_hours"] else _status(h, rules))
                note = None
                if d in pending_days and status in ("A", "•", ""):
                    status, note = {
                        "pending": ("PEND", "Approval pending from HR / management"),
                        "no_end": ("NOEND", "End day missing — send a request"),
                        "rejected": ("A", "Request rejected"),
                    }[pending_days[d]]
                row.append({"date": d, "hours": h, "status": status, "note": note})
            present = sum(1 for x in row if x["status"] == "P")
            half = sum(1 for x in row if x["status"] == "H")
            out.append({"id": person["id"], "username": person["username"], "full_name": person.get("full_name"),
                        "not_tracked": False, "days": row, "present": present, "half": half,
                        "pending": sum(1 for x in row if x["status"] in ("PEND", "NOEND")),
                        "absent": sum(1 for x in row if x["status"] == "A"),
                        "total_hours": round(sum(hours.values()), 1)})
    return {"month": month, "rules": rules, "people": out}
