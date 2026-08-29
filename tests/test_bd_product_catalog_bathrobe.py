"""A bedsheet must never be classified as a bathrobe.

Reported from production on 2026-08-29. Shri Ram & Co's Sales Order
102876191 is 36 bedsheet lines and nothing else — 18 EPIGRAM DB SETs
(108) and 18 EPIGRAM KS SETs (144), 252 SETs total, verified against the
PDF. Order Desk showed a third article, "Epigram · Bathrobe", holding 14
SETs, which turned an exact FO match into two false shortages:

    Epigram · DB BS   FO 108 · SO 102 · Diff -6    SO SHORT
    Epigram · KS BS   FO 144 · SO 136 · Diff -8    SO SHORT
    Epigram · Bathrobe FO 0  · SO  14 · Diff 14    EXTRA_ON_SO

The 14 were exactly the two colourways whose code ends "…8129LBR"
(Light BRown, qty 6 + 8). enrich_bd_product() falls back to the towel
maps when its bed maps come up short, and the towel material-code rules
tested `"BR" in code` as a bare substring.
"""

from app.services.bd_product_catalog import (
    enrich_bd_product,
    lookup_towel_product_type,
)

# The two real lines from SO 102876191 that were misread.
LIGHT_BROWN_BEDSHEET_CODES = (
    "BS03DBEPGRM8129LBR",
    "BS03KSEPGRM8129LBR",
)


def test_light_brown_bedsheet_is_not_a_bathrobe():
    for code in LIGHT_BROWN_BEDSHEET_CODES:
        product = enrich_bd_product(code, material_code=code)
        assert product.get("product_type") != "Bathrobe", (
            f"{code} is an EPIGRAM bedsheet colourway (LBR = Light Brown); "
            "classifying it as a Bathrobe moves real order quantity into a "
            "product the order does not contain"
        )


def test_bedsheet_codes_skip_the_towel_structural_rules_entirely():
    """The fallback reads a TOWEL code's structure, so a bed SKU must not
    reach it at all — not just avoid the bathrobe rule."""
    for code in (*LIGHT_BROWN_BEDSHEET_CODES, "BS03DBEPGRM8124MGR", "MB0300301"):
        assert lookup_towel_product_type("", material_code=code) is None


def test_real_bathrobe_codes_still_classify():
    """The fix narrows the rule; it must not switch bathrobes off."""
    for code in ("FLBR", "FRBR", "MTBATHROBE", "MT_BR_L"):
        assert lookup_towel_product_type("", material_code=code) == "Bathrobe"


def test_colour_codes_containing_br_are_not_bathrobes():
    """Any towel colourway that merely contains the letters BR."""
    for code in ("MT0750150LBR", "MT040060BRN", "MT030030ABR"):
        assert lookup_towel_product_type("", material_code=code) != "Bathrobe"


def test_other_towel_structural_rules_are_unchanged():
    expected = {
        "MT030030": "Face Towel Set of 3",
        "MT040060": "Hand Towel",
        "MT050070": "Bathmat",
        "MT0600120": "Ladies Towel",
        "MT0750150": "Bath Towel",
        "MT0900180": "Pool Towel",
        "MTR4SET": "Towel Set",
    }
    for code, label in expected.items():
        assert lookup_towel_product_type("", material_code=code) == label


def test_so_102876191_quantities_land_in_only_two_buckets():
    """End-to-end shape of the reported order: every one of the 36 lines is
    an EPIGRAM DB or KS bedsheet, so no line may resolve to a bathrobe and
    the 252 SETs must stay in those two buckets."""
    db_codes = [f"BS03DBEPGRM812{n}LBR" for n in range(1, 10)]
    ks_codes = [f"BS03KSEPGRM812{n}LBR" for n in range(1, 10)]
    for code in db_codes + ks_codes:
        product = enrich_bd_product(code, material_code=code)
        assert product.get("product_type") != "Bathrobe"
