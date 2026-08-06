"""Savitri-style booking forms: Brand header cell blank, Size/Product present."""

import pandas as pd

import article_master_parser as amparser


def test_detect_header_with_blank_brand_cell():
    raw = pd.DataFrame([
        ["Bedsheet Booking form", None, None, None, None],
        [None, "TC", "Size", "Product", "MRP"],
        ["Aster", 100, "DB BS", "Sheet Sets", 1049],
    ])
    assert amparser.detect_header_row(raw) == 1


def test_map_blank_first_column_to_brand():
    header = [None, "TC", "Size", "Product", "MRP", "Bale Size", "ExMill Price"]
    mapping = amparser.map_columns_to_core(header)
    assert mapping[0] == "brand"
    assert mapping[2] == "size"
    assert mapping[3] == "product_type"


def test_normal_brand_header_still_wins():
    header = ["Brand", "Size", "Product", "MRP"]
    mapping = amparser.map_columns_to_core(header)
    assert mapping[0] == "brand"
    assert mapping[1] == "size"
