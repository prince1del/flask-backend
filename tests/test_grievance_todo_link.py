"""Raising a grievance from an existing My To-Do note."""

import importlib
import sqlite3

from centralized_db_system.db import CentralizedDB


def setup_app(tmp_path, monkeypatch):
    db_path = tmp_path / "grievance_todo.sqlite3"

    def _apply_env():
        monkeypatch.setenv("DATABASE_PATH", str(db_path))
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("SECRET_KEY", "grievance-todo-test-key")

    _apply_env()

    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    # Reloading web_app re-runs load_env_file(), which reads the real .env and
    # overwrites these overrides — re-apply them afterwards.
    importlib.reload(web_app_module)
    _apply_env()

    app = web_app_module.create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    db = CentralizedDB(str(db_path))
    user = db.create_user(
        "grievance_user", "pass123", role="sales_executive", workspace_id="ws-1"
    )
    user_id = int(user["id"])
    distributor_id = db.add_master_distributor(
        name="Bernina International P Ltd",
        firm_name="Bernina International P Ltd",
        workspace_id="ws-1",
        user_id=user_id,
    )

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "grievance_user", "password": "pass123"},
    )
    assert login.status_code == 200, login.get_data(as_text=True)
    token = login.get_json()["data"]["access_token"]
    return client, token, distributor_id, str(db_path)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _create_note(client, token, title="Bernina ka payment pending hai"):
    resp = client.post(
        "/api/v1/personal-todos",
        json={"task_title": title, "category": "Distributor Query", "remarks": title},
        headers=_auth(token),
    )
    assert resp.status_code in (200, 201), resp.get_data(as_text=True)
    return int(resp.get_json()["data"]["id"])


def _todos(client, token):
    resp = client.get("/api/v1/personal-todos", headers=_auth(token))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["data"]["todos"]


def test_grievance_from_note_adopts_that_note(tmp_path, monkeypatch):
    client, token, distributor_id, _db_path = setup_app(tmp_path, monkeypatch)
    todo_id = _create_note(client, token)

    resp = client.post(
        "/api/v1/distributor-grievances",
        json={
            "distributor_id": distributor_id,
            "problem_text": "Bernina ka payment pending hai",
            "link_todo_id": todo_id,
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    grievance = resp.get_json()["data"]
    assert grievance["linked_todo_id"] == todo_id

    todos = _todos(client, token)
    assert len(todos) == 1, f"note must not be duplicated: {todos}"
    note = todos[0]
    assert note["id"] == todo_id
    assert note["category"] == "Grievance"
    assert note["linked_grievance_id"] == grievance["id"]
    assert note["person_party"] == "Bernina International P Ltd"


def test_grievance_without_link_still_creates_its_own_todo(tmp_path, monkeypatch):
    client, token, distributor_id, _db_path = setup_app(tmp_path, monkeypatch)
    todo_id = _create_note(client, token)

    resp = client.post(
        "/api/v1/distributor-grievances",
        json={
            "distributor_id": distributor_id,
            "problem_text": "Short supply of AW26 bedsheets",
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    grievance = resp.get_json()["data"]

    todos = _todos(client, token)
    assert len(todos) == 2
    assert grievance["linked_todo_id"] != todo_id


def test_link_todo_id_of_another_user_is_ignored(tmp_path, monkeypatch):
    client, token, distributor_id, db_path = setup_app(tmp_path, monkeypatch)

    _create_note(client, token)  # also ensures personal_todos exists

    db = CentralizedDB(db_path)
    other = db.create_user(
        "grievance_other", "pass123", role="sales_executive", workspace_id="ws-1"
    )
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO personal_todos (
                workspace_id, user_id, username, task_title, category, status,
                created_at, updated_at
            ) VALUES (
                'ws-1', ?, 'grievance_other', 'Someone else note', 'Note', 'pending',
                '2026-01-01T00:00:00', '2026-01-01T00:00:00'
            )
            """,
            (int(other["id"]),),
        )
        foreign_todo_id = int(cur.lastrowid)

    resp = client.post(
        "/api/v1/distributor-grievances",
        json={
            "distributor_id": distributor_id,
            "problem_text": "Damaged cartons received",
            "link_todo_id": foreign_todo_id,
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    grievance = resp.get_json()["data"]
    assert grievance["linked_todo_id"] != foreign_todo_id

    # The other user's note stays untouched.
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT category, linked_grievance_id FROM personal_todos WHERE id = ?",
            (foreign_todo_id,),
        ).fetchone()
    assert row["category"] == "Note"
    assert row["linked_grievance_id"] is None
