"""Start/End day, live location only on duty, owner's map/route/day — never across companies."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tests.test_member_access import _team
from tests.test_subscription_and_invites import app, client  # noqa: F401  (fixtures)


def _pt(lat, lng, minutes_ago):
    at = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    return {"lat": lat, "lng": lng, "accuracy": 12, "recorded_at": at}


def test_location_only_between_start_and_end_day(app, client):
    _o, h, member, mh = _team(app, client, "trackboss", "trackrep", ["attendance", "live_location", "market_visit"])
    r = client.post("/api/v1/team/location", headers=mh, json={"points": [_pt(28.6, 77.2, 0)]})
    assert r.status_code == 409  # not on duty: nothing is stored

    assert client.post("/api/v1/team/duty/start", headers=mh).get_json()["data"]["on_duty"] is True
    pts = [_pt(28.6, 77.2, 30),          # before Start day -> dropped
           _pt(28.6139, 77.2090, 0.2), _pt(28.6139, 77.2091, 0.1), _pt(28.6200, 77.2200, 0)]
    saved = client.post("/api/v1/team/location", headers=mh, json={"points": pts}).get_json()["data"]
    assert saved["saved"] == 3
    assert client.post("/api/v1/team/location", headers=mh,
                       json={"points": [{"lat": 999, "lng": 1}]}).get_json()["data"]["saved"] == 0

    live = client.get("/api/v1/team/live", headers=h).get_json()["data"]["members"]
    rep = next(m for m in live if m["username"] == "trackrep")
    assert rep["on_duty"] is True and rep["last"]["lat"] == 28.62 and rep["last"]["minutes_ago"] <= 1

    day = client.get(f"/api/v1/team/members/{rep['id']}/day", headers=h).get_json()["data"]
    assert len(day["points"]) == 3 and day["km"] > 0.5 and day["duty"]
    assert set(day["work"]) == {"market_visits", "filled_orders", "so_matches", "payments"}

    assert client.post("/api/v1/team/duty/end", headers=mh).get_json()["data"]["on_duty"] is False
    assert client.post("/api/v1/team/location", headers=mh, json={"points": [_pt(28.6, 77.2, 0)]}).status_code == 409


def test_stops_are_found(app, client):
    from app.services.team_tracking import _stops

    t0 = datetime(2026, 11, 15, 5, 0, tzinfo=timezone.utc)
    pts = [{"lat": 28.6 + i * 1e-5, "lng": 77.2, "at": (t0 + timedelta(minutes=2 * i)).isoformat()} for i in range(8)]
    pts.append({"lat": 28.7, "lng": 77.3, "at": (t0 + timedelta(minutes=20)).isoformat()})
    stops = _stops(pts)
    assert len(stops) == 1 and stops[0]["minutes"] == 14


def test_only_my_owner_sees_me(app, client):
    _a, ha, _ma, mha = _team(app, client, "ownera", "repa", ["attendance", "live_location"])
    _b, hb, _mb, mhb = _team(app, client, "ownerb", "repb", ["attendance", "live_location"])
    client.post("/api/v1/team/duty/start", headers=mha)
    live_a = client.get("/api/v1/team/live", headers=ha).get_json()["data"]["members"]
    repa_id = next(m["id"] for m in live_a if m["username"] == "repa")
    # another company's owner cannot see repa
    assert {m["username"] for m in client.get("/api/v1/team/live", headers=hb).get_json()["data"]["members"]} \
        == {"ownerb", "repb"}
    assert client.get(f"/api/v1/team/members/{repa_id}/day", headers=hb).status_code == 404
    # members never see the team
    assert client.get("/api/v1/team/live", headers=mhb).status_code == 403
    assert client.get(f"/api/v1/team/members/{repa_id}/day", headers=mha).status_code == 403


def test_owner_can_close_start_day(app, client):
    _o, _h, _m, mh = _team(app, client, "nodutyboss", "nodutyrep", ["market_visit"])
    r = client.post("/api/v1/team/duty/start", headers=mh)
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "FEATURE_OFF"


# ── Work places and attendance ───────────────────────────────────────────────

OFFICE = {"lat": 28.6139, "lng": 77.2090}


def test_start_day_only_at_the_work_place(app, client):
    _o, h, member, mh = _team(app, client, "siteboss", "siterep", ["attendance", "live_location"])
    site = client.post("/api/v1/team/sites", headers=h,
                       json={"name": "Office", **OFFICE, "radius_m": 200})
    assert site.status_code == 200, site.get_json()
    sid = site.get_json()["data"]["id"]
    rep_id = next(m["id"] for m in client.get("/api/v1/team/live", headers=h).get_json()["data"]["members"]
                  if m["username"] == "siterep")
    assert client.post(f"/api/v1/team/members/{rep_id}/sites", headers=h, json={"site_ids": [sid]}).status_code == 200
    assert client.get("/api/v1/team/duty/sites", headers=mh).get_json()["data"]["sites"][0]["name"] == "Office"

    no_gps = client.post("/api/v1/team/duty/start", headers=mh, json={})
    assert no_gps.status_code == 403 and no_gps.get_json()["error"]["code"] == "NOT_AT_SITE"
    far = client.post("/api/v1/team/duty/start", headers=mh, json={"lat": 28.6239, "lng": 77.2090, "accuracy": 10})
    assert far.status_code == 403 and "1.1 km away from Office" in far.get_json()["error"]["message"]
    fake = client.post("/api/v1/team/duty/start", headers=mh, json={**OFFICE, "accuracy": 5, "mock": True})
    assert fake.status_code == 403 and "Fake GPS" in fake.get_json()["error"]["message"]
    ok = client.post("/api/v1/team/duty/start", headers=mh, json={"lat": 28.6150, "lng": 77.2090, "accuracy": 20})
    assert ok.status_code == 200 and ok.get_json()["data"]["on_duty"] is True


def test_attendance_hours_give_present_half_absent(app, client):
    import sqlite3

    _o, h, member, mh = _team(app, client, "attboss", "attrep", ["attendance", "live_location"])
    rep_id = next(m["id"] for m in client.get("/api/v1/team/live", headers=h).get_json()["data"]["members"]
                  if m["username"] == "attrep")
    # 1 Nov: 9 h, 2 Nov: 5 h, 3 Nov: 1 h, 4 Nov: forgot End day — last point 6 h after start
    with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
        for start, end in (("2026-11-01T04:00:00+00:00", "2026-11-01T13:00:00+00:00"),
                           ("2026-11-02T04:00:00+00:00", "2026-11-02T09:00:00+00:00"),
                           ("2026-11-03T04:00:00+00:00", "2026-11-03T05:00:00+00:00")):
            conn.execute("INSERT INTO duty_sessions (user_id, workspace_id, started_at, ended_at) VALUES (?, 'x', ?, ?)",
                         (rep_id, start, end))
        sid = conn.execute("INSERT INTO duty_sessions (user_id, workspace_id, started_at) VALUES (?, 'x', ?)",
                           (rep_id, "2026-11-04T04:00:00+00:00")).lastrowid
        conn.execute("""INSERT INTO location_points (user_id, workspace_id, session_id, lat, lng, recorded_at, received_at)
                        VALUES (?, 'x', ?, 28.6, 77.2, '2026-11-04T10:00:00+00:00', '2026-11-04T10:00:00+00:00')""",
                     (rep_id, sid))
    sheet = client.get("/api/v1/team/attendance?month=2026-11", headers=h).get_json()["data"]
    rep = next(p for p in sheet["people"] if p["username"] == "attrep")
    days = {d["date"]: d for d in rep["days"]}
    assert (days["2026-11-01"]["hours"], days["2026-11-01"]["status"]) == (9.0, "P")
    assert days["2026-11-02"]["status"] == "H" and days["2026-11-03"]["status"] == "A"
    assert (days["2026-11-04"]["hours"], days["2026-11-04"]["status"]) == (6.0, "H")
    assert days["2026-11-05"]["status"] in ("A", "•", "")

    assert client.post("/api/v1/team/attendance/rules", headers=h,
                       json={"full_day_hours": 6, "half_day_hours": 3}).status_code == 200
    rep = next(p for p in client.get("/api/v1/team/attendance?month=2026-11", headers=h)
               .get_json()["data"]["people"] if p["username"] == "attrep")
    assert {d["date"]: d["status"] for d in rep["days"]}["2026-11-04"] == "P"
    mine = client.get("/api/v1/team/attendance/me?month=2026-11", headers=mh).get_json()["data"]
    assert len(mine["people"]) == 1 and mine["people"][0]["present"] == 2
    assert client.get("/api/v1/team/attendance?month=2026-11", headers=mh).status_code == 403


def test_other_company_cannot_touch_my_places(app, client):
    _a, ha, _ma, _mha = _team(app, client, "placea", "placerepa", ["attendance", "live_location"])
    _b, hb, _mb, _mhb = _team(app, client, "placeb", "placerepb", ["attendance", "live_location"])
    sid = client.post("/api/v1/team/sites", headers=ha, json={"name": "A office", **OFFICE}).get_json()["data"]["id"]
    assert client.get("/api/v1/team/sites", headers=hb).get_json()["data"]["sites"] == []
    assert client.delete(f"/api/v1/team/sites/{sid}", headers=hb).status_code == 404
    repb = next(m["id"] for m in client.get("/api/v1/team/live", headers=hb).get_json()["data"]["members"]
                if m["username"] == "placerepb")
    assert client.post(f"/api/v1/team/members/{repb}/sites", headers=hb, json={"site_ids": [sid]}).status_code == 404
    repa = next(m["id"] for m in client.get("/api/v1/team/live", headers=ha).get_json()["data"]["members"]
                if m["username"] == "placerepa")
    assert client.post(f"/api/v1/team/members/{repa}/sites", headers=hb, json={"site_ids": []}).status_code == 404


def test_freelancer_without_attendance_or_location(app, client):
    _o, h, member, mh = _team(app, client, "freeboss", "freerep", ["market_visit"])
    assert client.post("/api/v1/team/duty/start", headers=mh).status_code == 403
    assert client.post("/api/v1/team/location", headers=mh, json={"points": []}).status_code == 403
    sheet = client.get("/api/v1/team/attendance?month=2026-11", headers=h).get_json()["data"]
    rep = next(p for p in sheet["people"] if p["username"] == "freerep")
    assert rep["not_tracked"] is True and rep["absent"] == 0


def test_attendance_without_live_location(app, client):
    _o, h, member, mh = _team(app, client, "officeboss", "officerep", ["attendance"])
    assert client.post("/api/v1/team/duty/start", headers=mh, json={}).status_code == 200
    r = client.post("/api/v1/team/location", headers=mh, json={"points": [_pt(28.6, 77.2, 0)]})
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "FEATURE_OFF"


def test_office_staff_end_day_away_needs_approval(app, client):
    import sqlite3

    _o, h, member, mh = _team(app, client, "hrboss", "officeguy", ["attendance"])
    sid = client.post("/api/v1/team/sites", headers=h, json={"name": "Office", **OFFICE}).get_json()["data"]["id"]
    rep_id = next(m["id"] for m in client.get("/api/v1/team/live", headers=h).get_json()["data"]["members"]
                  if m["username"] == "officeguy")
    client.post(f"/api/v1/team/members/{rep_id}/sites", headers=h, json={"site_ids": [sid]})
    assert client.post("/api/v1/team/duty/start", headers=mh, json={**OFFICE, "accuracy": 10}).status_code == 200

    away = {"lat": 28.70, "lng": 77.10, "accuracy": 10}
    r = client.post("/api/v1/team/duty/end", headers=mh, json=away)
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "END_AWAY"
    assert client.get("/api/v1/team/duty", headers=mh).get_json()["data"]["on_duty"] is True  # not ended

    r = client.post("/api/v1/team/duty/end", headers=mh,
                    json={**away, "note": "Client meeting at Karol Bagh", "approver": "Mr. Sharma"})
    assert r.status_code == 200 and r.get_json()["data"]["pending_approval"] is True

    # make the day long enough for a full day, and in the past
    with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
        conn.execute("UPDATE duty_sessions SET started_at = '2026-11-02T03:00:00+00:00', "
                     "ended_at = '2026-11-02T13:00:00+00:00' WHERE user_id = ?", (rep_id,))
    def day_status():
        rep = next(p for p in client.get("/api/v1/team/attendance?month=2026-11", headers=h)
                   .get_json()["data"]["people"] if p["username"] == "officeguy")
        return {d["date"]: d for d in rep["days"]}["2026-11-02"]

    st = day_status()
    assert st["status"] == "PEND" and "HR / management" in st["note"]
    reqs = client.get("/api/v1/team/attendance/requests", headers=h).get_json()["data"]["requests"]
    assert reqs[0]["note"] == "Client meeting at Karol Bagh" and reqs[0]["approver_name"] == "Mr. Sharma"
    assert client.post(f"/api/v1/team/attendance/requests/{reqs[0]['id']}", headers=mh,
                       json={"approve": True}).status_code == 403  # a member cannot approve
    assert client.post(f"/api/v1/team/attendance/requests/{reqs[0]['id']}", headers=h,
                       json={"approve": True}).status_code == 200
    assert day_status()["status"] == "P"
    client.post(f"/api/v1/team/attendance/requests/{reqs[0]['id']}", headers=h, json={"approve": False})
    assert day_status()["status"] == "A" and day_status()["note"] == "Request rejected"


def test_missed_end_day_is_off_until_request_approved(app, client):
    import sqlite3

    _o, h, member, mh = _team(app, client, "missboss", "missrep", ["attendance"])
    sid = client.post("/api/v1/team/sites", headers=h, json={"name": "Office", **OFFICE}).get_json()["data"]["id"]
    rep_id = next(m["id"] for m in client.get("/api/v1/team/live", headers=h).get_json()["data"]["members"]
                  if m["username"] == "missrep")
    client.post(f"/api/v1/team/members/{rep_id}/sites", headers=h, json={"site_ids": [sid]})
    with sqlite3.connect(app.config["DATABASE_PATH"]) as conn:
        conn.execute("INSERT INTO duty_sessions (user_id, workspace_id, started_at) VALUES (?, 'x', '2026-11-03T03:00:00+00:00')",
                     (rep_id,))
    mine = client.get("/api/v1/team/attendance/me?month=2026-11", headers=mh).get_json()["data"]["people"][0]
    assert {d["date"]: d["status"] for d in mine["days"]}["2026-11-03"] == "NOEND"
    r = client.post("/api/v1/team/attendance/me/request", headers=mh,
                    json={"date": "2026-11-03", "note": "Phone battery died", "approver": "Store manager"})
    assert r.status_code == 200
    req = client.get("/api/v1/team/attendance/requests", headers=h).get_json()["data"]["requests"][0]
    assert req["kind"] == "no_end"
    # another company's owner can neither see nor decide it
    _b, hb, _mb, _mhb = _team(app, client, "otherhr", "otherrep", ["attendance"])
    assert client.get("/api/v1/team/attendance/requests", headers=hb).get_json()["data"]["requests"] == []
    assert client.post(f"/api/v1/team/attendance/requests/{req['id']}", headers=hb, json={"approve": True}).status_code == 404
