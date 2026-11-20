"""Honorifics must not make unrelated parties look alike (Shri / SH / Shree)."""
from app.hop_party_match import name_similarity, normalize_party_name


def test_shri_prefix_stripped():
    assert "shri" not in normalize_party_name("Shri Ram Traders").split()
    assert normalize_party_name("Shri Ram Traders") == normalize_party_name("Ram Traders")
    assert normalize_party_name("SH Sharma Store").endswith("sharma store") or \
        "sharma" in normalize_party_name("SH Sharma Store")


def test_shri_x_not_match_shri_y():
    # Same honorific alone must not link different firms
    assert name_similarity("Shri Ram & Co", "Shri Shyam Traders") < 0.84
    assert name_similarity("SH Cool Air", "SH Night Cool") < 0.84


def test_same_party_still_matches_with_honorific():
    assert name_similarity("Shri Ram Traders", "Ram Traders") >= 0.86
    assert name_similarity("Shree Ram Traders", "Shri Ram Traders") >= 0.86
