import article_master_parser as amparser


def test_bed_size_order_sb_db_king():
    articles = [
        {"brand": "Cardinal", "size": "KS BS", "product_type": "Sheet Sets", "item_key": "c|ks"},
        {"brand": "Cardinal", "size": "SB BS", "product_type": "Sheet Sets", "item_key": "c|sb"},
        {"brand": "Cardinal", "size": "DB BS", "product_type": "Sheet Sets", "item_key": "c|db"},
        {"brand": "Aster", "size": "DB BS", "product_type": "Sheet Sets", "item_key": "a|db"},
    ]
    sorted_rows = amparser.sort_articles_for_display(articles)
    assert [a["brand"] for a in sorted_rows] == ["Aster", "Cardinal", "Cardinal", "Cardinal"]
    assert [a["size"] for a in sorted_rows if a["brand"] == "Cardinal"] == [
        "SB BS", "DB BS", "KS BS",
    ]


def test_kdb_is_king_not_double():
    assert amparser.bed_size_sort_rank("KDB BS") == 3
    assert amparser.bed_size_sort_rank("DBL BS") == 2
    assert amparser.bed_size_sort_rank("KB FS") == 3
    assert amparser.bed_size_sort_rank("DB FS") == 2
