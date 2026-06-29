from app.services.ocr_correction import build_correction


def _result(rows):
    return {"rows": rows, "supply_sum": 0, "warp_ok": True}


def test_label_changed_and_supply_unchanged():
    result = _result(
        [
            {
                "crop_ref": "job-42/row-0",
                "item_top5": [{"label": "삼겹살", "sim": 0.83}],
                "supply": 120000,
            }
        ]
    )
    final = [{"crop_ref": "job-42/row-0", "name": "목살", "supply": 120000}]
    out = build_correction(result, final)
    assert out["lines"] == [
        {
            "crop_ref": "job-42/row-0",
            "draft_label": "삼겹살",
            "final_label": "목살",
            "label_changed": True,
            "draft_supply": 120000,
            "final_supply": 120000,
            "supply_changed": False,
        }
    ]
    assert out["rows_added"] == 0
    assert out["rows_dropped"] == 0


def test_row_added_when_final_item_has_no_crop_ref():
    result = _result([])
    final = [{"name": "수기품목", "supply": 5000}]
    out = build_correction(result, final)
    assert out["lines"] == []
    assert out["rows_added"] == 1
    assert out["rows_dropped"] == 0


def test_row_dropped_when_draft_crop_unmatched():
    result = _result(
        [
            {
                "crop_ref": "job-42/row-0",
                "item_top5": [{"label": "삼겹살", "sim": 0.8}],
                "supply": 120000,
            }
        ]
    )
    final = []
    out = build_correction(result, final)
    assert out["rows_dropped"] == 1
    assert out["rows_added"] == 0


def test_empty_top5_yields_none_draft_label():
    result = _result([{"crop_ref": "job-42/row-0", "item_top5": [], "supply": None}])
    final = [{"crop_ref": "job-42/row-0", "name": "신규", "supply": 5000}]
    out = build_correction(result, final)
    line = out["lines"][0]
    assert line["draft_label"] is None
    assert line["label_changed"] is True
    assert line["draft_supply"] is None
    assert line["supply_changed"] is True
