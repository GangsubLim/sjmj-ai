from ocr_poc.data import load_label, LabelRow


def test_load_label_uses_text_only_ignores_geometry():
    raw = {
        "image_id": "inv_x",
        "doc_bbox": [1, 2, 3, 4],
        "doc_corners": [[0, 0]],
        "rows": [
            {"row_id": 1, "item_class": "부동액", "quantity_text": "4",
             "unit_price_text": "25000", "amount_text": "100000",
             "quantity_bbox": [9, 9, 9, 9]},   # geometry 는 무시되어야 함
            {"row_id": 2, "item_class": "엔진오일", "quantity_text": "1",
             "unit_price_text": "365000", "amount_text": "365000"},
        ],
    }
    rows = load_label(raw)
    assert rows == (
        LabelRow(row_id=1, item="부동액", quantity="4", unit_price="25000", amount="100000"),
        LabelRow(row_id=2, item="엔진오일", quantity="1", unit_price="365000", amount="365000"),
    )


def test_load_label_missing_text_becomes_empty_string():
    raw = {"rows": [{"row_id": 1, "item_class": "x"}]}
    rows = load_label(raw)
    assert rows[0] == LabelRow(row_id=1, item="x", quantity="", unit_price="", amount="")
