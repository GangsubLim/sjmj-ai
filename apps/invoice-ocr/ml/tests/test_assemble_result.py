from handwriting.infer_job import assemble_result_json


def test_assembles_crop_ref_and_supply_sum():
    rows = [
        {"row_index": 0, "item_top5": [{"label": "삼겹살", "sim": 0.83}], "supply": 120000, "amount_raw": "120,000"},
        {"row_index": 1, "item_top5": [], "supply": None, "amount_raw": "—"},
    ]
    out = assemble_result_json(42, rows, True)
    assert out["rows"][0]["crop_ref"] == "job-42/row-0"
    assert out["rows"][1]["crop_ref"] == "job-42/row-1"
    assert out["supply_sum"] == 120000  # None은 합산 제외
    assert out["warp_ok"] is True


def test_warp_failure_yields_empty_rows():
    out = assemble_result_json(7, [], False)
    assert out == {"rows": [], "supply_sum": 0, "warp_ok": False}
