"""GSTIN lookup — prefer registry full address line + PIN field."""

from app.services.gstin_lookup import _parse_taxpayer_blob, _extract_pincode


def test_prefer_pradr_adr_over_thin_addr_bits():
    blob = {
        "lgnm": "Neelam Julka",
        "tradeNam": "House Of Prizm",
        "dty": "Regular",
        "sts": "Active",
        "ctb": "Proprietorship",
        "rgdt": "01/07/2017",
        "nba": ["Retail Business", "Wholesale Business"],
        "einvoiceStatus": "No",
        "pradr": {
            "adr": (
                "Second Floor, D97/B, Shyam Park Extension, Gurudwara, "
                "Sahibabad, Ghaziabad, Ghaziabad, Uttar Pradesh, 201005"
            ),
            "addr": {
                "flno": "",
                "bno": "0",
                "bnm": "Gurudwara",
                "st": "Sahibabad",
                "loc": "Ghaziabad",
                "dst": "Ghaziabad",
                "city": "",
                "stcd": "Uttar Pradesh",
                "pncd": "201005",
            },
        },
    }
    out = _parse_taxpayer_blob(blob, "gstincheck")
    assert out is not None
    assert out["company"] == "House Of Prizm"
    assert out["billing_name"] == "Neelam Julka"
    assert out["pincode"] == "201005"
    assert out["city"] == "Ghaziabad"
    assert out["state"] == "Uttar Pradesh"
    assert "Shyam Park Extension" in (out["address"] or "")
    assert "D97/B" in (out["address"] or "")
    assert not (out["address"] or "").startswith("0 ")
    assert out["constitution"] == "Proprietorship"
    assert out["registration_date"] == "01/07/2017"
    assert "Retail" in (out["nature_of_business"] or "")
    assert out["einvoice_status"] == "No"


def test_extract_pincode_from_line():
    assert _extract_pincode("foo 201005 bar") == "201005"
    assert _extract_pincode({"pncd": "110001"}) == "110001"
    assert _extract_pincode({"pncd": "0"}) is None
