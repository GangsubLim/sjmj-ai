from handwriting.infer_job import assemble_result_json


def test_assembles_crop_ref_and_supply_sum():
    rows = [
        {"row_index": 0, "item_top5": [{"label": "삼겹살", "sim": 0.83}], "supply": 120, "amount_raw": "120"},
        {"row_index": 1, "item_top5": [], "supply": None, "amount_raw": "—"},
    ]
    out = assemble_result_json(42, rows, True)
    assert out["rows"][0]["crop_ref"] == "job-42/row-0"
    assert out["rows"][1]["crop_ref"] == "job-42/row-1"
    assert out["supply_sum"] == 120000  # 120 ×1000, None은 합산 제외
    assert out["warp_ok"] is True


def test_supply_face_value_multiplied_by_thousand():
    # 수기 거래명세서는 천 단위 생략 → 인식 액면값에 ×1000 (spec: 단가·금액 100% 천원 배수)
    rows = [
        {"row_index": 0, "item_top5": [], "supply": 364, "amount_raw": "364"},
        {"row_index": 1, "item_top5": [], "supply": 1250, "amount_raw": "1250"},
    ]
    out = assemble_result_json(42, rows, True)
    assert out["rows"][0]["supply"] == 364000
    assert out["rows"][1]["supply"] == 1250000
    assert out["supply_sum"] == 1614000


def test_none_supply_stays_none_and_excluded_from_sum():
    rows = [
        {"row_index": 0, "item_top5": [], "supply": None, "amount_raw": "—"},
        {"row_index": 1, "item_top5": [], "supply": 100, "amount_raw": "100"},
    ]
    out = assemble_result_json(7, rows, True)
    assert out["rows"][0]["supply"] is None
    assert out["supply_sum"] == 100000


def test_amount_raw_kept_as_face_text():
    rows = [{"row_index": 0, "item_top5": [], "supply": 364, "amount_raw": "364"}]
    out = assemble_result_json(1, rows, True)
    assert out["rows"][0]["amount_raw"] == "364"  # 원문은 곱하지 않음


def test_warp_failure_yields_empty_rows():
    out = assemble_result_json(7, [], False)
    assert out == {"rows": [], "supply_sum": 0, "warp_ok": False}
