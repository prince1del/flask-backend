"""Server-side per-user UI theme isolation."""

from centralized_db_system.db import CentralizedDB


def test_each_user_has_independent_theme(tmp_path):
    db = CentralizedDB(str(tmp_path / "theme_users.sqlite3"))
    a = db.create_user("exec_a", "pass", role="sales_executive")
    b = db.create_user("exec_b", "pass", role="sales_executive")
    hop = db.create_user("hop_user", "pass", role="hop_admin", workspace_id="house_of_prizm")

    db.set_user_ui_theme(a["id"], "emerald")
    db.set_user_ui_theme(b["id"], "bright")
    db.set_user_ui_theme(
        hop["id"],
        "royal_navy",
        {"sidebar": "#0B1F3A", "bg": "#F8F4EA", "text": "#0B1F3A",
         "accent": "#C6A15B", "border": "#B8B1A7", "card": "#FFFFFF", "muted": "#6E675F"},
    )

    assert db.get_user_ui_theme(a["id"])["theme"] == "emerald"
    assert db.get_user_ui_theme(b["id"])["theme"] == "bright"
    assert db.get_user_ui_theme(hop["id"])["theme"] == "royal_navy"
    assert db.get_user_ui_theme(hop["id"])["custom_colors"]["sidebar"] == "#0B1F3A"
    # Changing A must not touch B; retired "nexora" maps to emerald
    db.set_user_ui_theme(a["id"], "nexora")
    assert db.get_user_ui_theme(a["id"])["theme"] == "emerald"
    assert db.get_user_ui_theme(b["id"])["theme"] == "bright"
