import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import article_master_db as amdb
import article_master_parser as amp

DB = ROOT / "centralized_db.sqlite3"
AW26 = Path(r"g:\My Drive\2026-2027\AW26 order sh\Bedsheet\Order sheet AW26.xlsx")

conn = sqlite3.connect(DB)
amdb.ensure_schema(conn)
print("brand_aliases table:", conn.execute("SELECT name FROM sqlite_master WHERE name='brand_aliases'").fetchall())
print("aliases:", conn.execute("SELECT * FROM brand_aliases LIMIT 5").fetchall() if conn.execute("SELECT name FROM sqlite_master WHERE name='brand_aliases'").fetchone() else "N/A")

for uid in [1, 2]:
    rows = conn.execute(
        "SELECT id, user_id, brand, item_key, mrp FROM article_master WHERE (brand LIKE '%Blue%' OR brand LIKE '%Blum%') AND user_id=?",
        (uid,),
    ).fetchall()
    if rows:
        print(f"user {uid} blue/blum rows:", rows)

user_id = 2
lookup = {"Bed": ["brand", "TC", "size"]}
amdb.ensure_default_brand_aliases(conn, user_id)
print("aliases after seed:", conn.execute("SELECT user_id, alias, canonical_brand FROM brand_aliases WHERE user_id=?", (user_id,)).fetchall())
import pandas as pd
with pd.ExcelFile(AW26) as xl:
    sheet = xl.sheet_names[0]
articles, *_ = amp.parse_article_sheet(str(AW26), sheet, lookup, ["brand", "size"], forced_category="Bed")
articles = amdb.apply_brand_aliases_to_articles(conn, user_id, articles, lookup, ["brand", "size"])
blu = [a for a in articles if "blue" in str(a.get("brand", "")).lower()]
print("AW26 bluemen rows parsed:", [(a["brand"], a["item_key"], a["mrp"]) for a in blu])
for a in blu[:2]:
    c = amdb.classify_upload_article(conn, user_id, a, ["brand", "TC", "size"])
    print("classify", a["item_key"], "->", c["action"], "existing", (c["existing"] or {}).get("id"), (c["existing"] or {}).get("brand"))

groups = amdb.find_duplicate_groups(conn, user_id, lookup)
print("total duplicate groups:", len(groups))
for g in groups:
    arts = [[a["id"], a["brand"], a["item_key"]] for a in g["articles"]]
    print(" group:", g["identity_label"], arts)
conn.close()
