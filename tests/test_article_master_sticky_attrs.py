"""Sticky schema-attr merge: blank must not wipe; non-blank updates."""
import article_master_parser as amparser


def test_blank_incoming_keeps_old_sticky_attrs():
    existing = {
        "Pillow Size": "46 X 69",
        "Blend": "100% Cotton",
        "Packing": "Envelope",
        "TC": "100",
    }
    incoming = {
        "Pillow Size": None,
        "Blend": "",
        "Print Style": "Pigment",  # new fill
        "TC": "100",
        "Qnty Per Color": 750,  # excluded
    }
    merged = amparser.merge_extra_attributes(existing, incoming)
    assert merged["Pillow Size"] == "46 X 69"
    assert merged["Blend"] == "100% Cotton"
    assert merged["Packing"] == "Envelope"
    assert merged["Print Style"] == "Pigment"
    assert merged["TC"] == "100"
    assert "Qnty Per Color" not in merged


def test_nonblank_updates_sticky_attr():
    existing = {"Pillow Size": "46 X 69", "Print Style": "Pigment"}
    incoming = {"Pillow Size (Cms)": "48 X 72", "Print Style": "Digital"}
    merged = amparser.merge_extra_attributes(existing, incoming)
    assert merged["Pillow Size"] == "48 X 72"
    assert merged["Print Style"] == "Digital"
    assert "Pillow Size (Cms)" not in merged


def test_blend_alias_collapses_case():
    existing = {"BLEND": "100% COTTON"}
    incoming = {"Blend": "100% Cotton Satin"}
    merged = amparser.merge_extra_attributes(existing, incoming)
    assert merged == {"Blend": "100% Cotton Satin"}


def test_missing_key_does_not_clear():
    existing = {"Pillow Stitching Style": "ZigZag", "Packing": "Side Book Fold"}
    incoming = {"TC": "120"}  # no packing / stitch columns at all
    merged = amparser.merge_extra_attributes(existing, incoming)
    assert merged["Pillow Stitching Style"] == "ZigZag"
    assert merged["Packing"] == "Side Book Fold"
    assert merged["TC"] == "120"
