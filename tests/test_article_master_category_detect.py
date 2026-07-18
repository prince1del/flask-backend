"""Majority-vote category detection — one comforter word must not override a bedsheet sheet."""

from article_master_parser import detect_category, detect_category_for_text


def test_majority_vote_prefers_bedsheet_even_if_one_comforter_row_exists():
    products = [
        "Bedsheet SS-26 Aster",
        "Sheet Sets Cardinal",
        "Fitted Sheet Blumen",
        "Bedsheet DB BS",
        "All Season Comforter",  # minority fringe row
    ]
    assert detect_category(products) == "Bed"


def test_majority_vote_prefers_tob_when_blanket_rows_dominate():
    products = [
        "All Season Blanket",
        "Fleece Comforter",
        "Slumber Quilt",
        "Bedsheet Accent Row",  # single fringe mention
    ]
    assert detect_category(products) == "TOB"


def test_filename_hint_breaks_tie_toward_bed():
    # Equal votes: one bedsheet + one comforter → bedsheet filename tips to Bed
    products = ["Bedsheet Aster", "Comforter Jade"]
    assert detect_category(products, filename="Order sheet AW26 Bedsheet.xlsx") == "Bed"


def test_filename_alone_when_no_product_keyword():
    products = ["ASTER Design Code XYZ", "CARDINAL Line"]
    assert detect_category(products, filename="Bedsheet SS-26 booking form.xlsx") == "Bed"


def test_mixed_sheet_assigns_per_row_categories():
    """One sheet can contain Bedsheet + Towel + TOB — each row keeps its own category."""
    from article_master_parser import detect_category_for_text

    assert detect_category_for_text("Bedsheet Aster DB") == "Bed"
    assert detect_category_for_text("Bamboo Towel Set") == "Bath"
    assert detect_category_for_text("All Season Blanket") == "TOB"