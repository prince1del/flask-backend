import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.routes.data import _expand_ci_upload_items


def test_expand_ci_zip_and_loose_pdf():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("folder/a.pdf", b"%PDF-1.4 a")
        zf.writestr("b.pdf", b"%PDF-1.4 b")
        zf.writestr("skip.txt", b"nope")
    out = _expand_ci_upload_items(
        [
            ("pack.zip", buf.getvalue()),
            ("solo.pdf", b"%PDF-1.4 solo"),
        ]
    )
    names = [name for name, _ in out]
    assert names == ["a.pdf", "b.pdf", "solo.pdf"]
    assert out[0][1] == b"%PDF-1.4 a"
    assert out[2][1] == b"%PDF-1.4 solo"


def test_expand_ci_rejects_empty_archive():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", b"no pdfs")
    try:
        _expand_ci_upload_items([("empty.zip", buf.getvalue())])
    except ValueError as exc:
        assert "No PDFs" in str(exc)
    else:
        raise AssertionError("expected ValueError")
