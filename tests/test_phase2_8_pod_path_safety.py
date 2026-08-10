"""POD attach must not OCR arbitrary client filesystem paths."""

from __future__ import annotations

import importlib

from centralized_db_system.db import CentralizedDB


def test_pod_attach_does_not_ocr_client_filesystem_path(tmp_path, monkeypatch):
    db_path = tmp_path / "pod_ocr_safe.sqlite3"
    secret_img = tmp_path / "secret.png"
    # Minimal valid 1x1 PNG
    secret_img.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
        )
    )

    def _apply():
        monkeypatch.setenv("DATABASE_PATH", str(db_path))
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("SECRET_KEY", "pod-attach-ocr-secret-key-32b!!")
        monkeypatch.setenv("JWT_SECRET_KEY", "pod-attach-ocr-secret-key-32b!!")

    _apply()
    import app.init_db as init_db_module
    import app.web_app as web_app_module

    importlib.reload(init_db_module)
    importlib.reload(web_app_module)
    _apply()

    from app.db import db
    from app.models import User
    from app.jwt_service import JWTService

    app = web_app_module.create_app()
    app.config.update(TESTING=True, DATABASE_PATH=str(db_path))
    with app.app_context():
        user = User(
            username="poduser",
            email="pod@example.com",
            role="admin",
            status="active",
            workspace_id="default",
        )
        user.set_password("pass123")
        db.session.add(user)
        db.session.commit()
        user_id = int(user.id)

    cdb = CentralizedDB(str(db_path))
    distributor_id = cdb.add_master_distributor(name="PodDist", buyer_code="PODX")
    tracking_id = cdb.create_order_lifecycle_tracking(
        order_ref_no="SO-POD-1", distributor_id=distributor_id
    )
    pod_id = cdb.record_dispatch_pod(
        tracking_id=tracking_id, pod_number="POD-1", workspace_id="default"
    )

    client = app.test_client()
    token, _ = JWTService(secret_key=app.config["SECRET_KEY"]).create_tokens(
        user_id=user_id, username="poduser", role="admin", workspace_id="default"
    )
    headers = {"Authorization": f"Bearer {token}"}

    # Attacker supplies absolute path to a readable image — must NOT OCR/exfiltrate.
    resp = client.post(
        "/api/v1/phase2_8/pod/attach",
        headers=headers,
        json={
            "pod_id": pod_id,
            "attachment_reference": str(secret_img.resolve()),
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()["data"]
    assert data.get("pod_text") in (None, "")
    # Reference may be stored as opaque metadata, but must not become OCR content
    assert data.get("pod_text") != "secret"

    # Explicit pod_text still works
    ok = client.post(
        "/api/v1/phase2_8/pod/attach",
        headers=headers,
        json={
            "pod_id": pod_id,
            "pod_text": "Delivered OK",
            "attachment_reference": "opaque-ref-123",
        },
    )
    assert ok.status_code == 200
    assert ok.get_json()["data"]["pod_text"] == "Delivered OK"
