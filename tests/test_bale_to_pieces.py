from centralized_db_system.bale_to_pieces import calculate_bale_to_pieces


def test_calculates_basic_bale_breakdown() -> None:
    result = calculate_bale_to_pieces(total_bales=2, packs_per_bale=3, pcs_per_pack=10)

    assert result["total_bales"] == 2
    assert result["total_packs"] == 6
    assert result["total_pieces"] == 60
    assert result["pieces_per_bale_breakdown"]["packs_per_bale"] == 3


def test_applies_design_and_color_multipliers() -> None:
    result = calculate_bale_to_pieces(total_bales=1, packs_per_bale=2, pcs_per_pack=5, number_of_designs=3, number_of_colors=2)

    assert result["total_packs"] == 2
    assert result["total_pieces"] == 60


def test_zero_and_negative_values_are_normalized() -> None:
    result = calculate_bale_to_pieces(total_bales=-2, packs_per_bale=0, pcs_per_pack=-5)

    assert result["total_bales"] == 0
    assert result["total_packs"] == 0
    assert result["total_pieces"] == 0


def test_large_numbers_are_supported() -> None:
    result = calculate_bale_to_pieces(total_bales=1000, packs_per_bale=200, pcs_per_pack=50)

    assert result["total_packs"] == 200000
    assert result["total_pieces"] == 10000000
