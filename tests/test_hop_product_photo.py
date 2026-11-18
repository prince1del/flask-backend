"""Product photo: upload (resized JPEG), read back, remove (made-up company)."""
from __future__ import annotations

import io

from PIL import Image

from app import hop_product_photos as PH
from app.hop_db import connect
from app.hop_schema import HOP_WORKSPACE_ID as WS
from tests.test_hop_reports import co  # noqa: F401  (fixture)


def _png(w=2400, h=1600):
    b = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(b, "PNG")
    return b.getvalue()


def test_photo_upload_resize_remove(co):
    import os
    import pytest

    with connect(co["path"]) as conn:
        pid = co["prod"]["id"] if "prod" in co else conn.execute("SELECT id FROM hop_products WHERE workspace_id=? LIMIT 1", (WS,)).fetchone()[0]
        r = PH.save(conn, co["path"], WS, pid, _png())
        p = PH.path_for(co["path"], WS, pid)
        assert os.path.exists(p) and max(Image.open(p).size) == 1200 and r["photo_v"]
        assert conn.execute("SELECT photo_v FROM hop_products WHERE id=?", (pid,)).fetchone()[0] == r["photo_v"]
        with pytest.raises(ValueError):
            PH.save(conn, co["path"], WS, pid, b"not an image")
        PH.remove(conn, co["path"], WS, pid)
        assert not os.path.exists(p)
