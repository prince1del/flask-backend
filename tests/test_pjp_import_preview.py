from app.routes.pjp import _day_has_manual_data, _days_content_equal, _split_import_conflicts


def test_day_has_manual_data_requires_saved_row_with_content() -> None:
    assert not _day_has_manual_data({"id": None, "place_to_visit": "Delhi"})
    assert _day_has_manual_data({"id": 1, "place_to_visit": "Delhi"})
    assert not _day_has_manual_data({"id": 1, "place_to_visit": "holiday"})


def test_split_import_conflicts_auto_applies_new_and_same_dates() -> None:
    existing = {
        "2026-09-01": {"id": 1, "plan_date": "2026-09-01", "place_to_visit": "Delhi"},
        "2026-09-02": {"id": 2, "plan_date": "2026-09-02", "place_to_visit": "Jaipur"},
    }
    incoming = [
        {"plan_date": "2026-09-01", "place_to_visit": "Delhi"},
        {"plan_date": "2026-09-02", "place_to_visit": "Udaipur"},
        {"plan_date": "2026-09-03", "place_to_visit": "Agra"},
    ]
    auto, conflicts = _split_import_conflicts(existing, incoming)
    assert [d["plan_date"] for d in auto] == ["2026-09-01", "2026-09-03"]
    assert len(conflicts) == 1
    assert conflicts[0]["plan_date"] == "2026-09-02"
    assert conflicts[0]["existing"]["place_to_visit"] == "Jaipur"
    assert conflicts[0]["incoming"]["place_to_visit"] == "Udaipur"


def test_days_content_equal_ignores_whitespace() -> None:
    left = {"place_to_visit": " Delhi ", "from_place": None}
    right = {"place_to_visit": "Delhi", "from_place": ""}
    assert _days_content_equal(left, right)
