from handwriting.infer_job import ITEM_CONF_THRESHOLD, assemble_result_json

# 임계는 Task 3의 재채점으로 바뀐다 — 테스트가 상수를 하드코딩하면 캘리브 때마다 무조건 깨진다.
# tests/conftest.py의 make_warped가 MIN_BLUE_RATIO에서 두께를 유도한 것과 같은 이유다.
_EPS = 0.01


def _row(sim: float | None) -> dict:
    top5 = [] if sim is None else [{"label": "타이어", "sim": sim}]
    return {"row_index": 0, "item_top5": top5, "supply": 85, "amount_raw": "85"}


def test_assembles_crop_ref_and_supply_sum():
    rows = [
        {
            "row_index": 0,
            "item_top5": [{"label": "삼겹살", "sim": 0.83}],
            "supply": 120,
            "amount_raw": "120",
        },
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
    assert out == {
        "rows": [],
        "supply_sum": 0,
        "warp_ok": False,
        "item_conf_threshold": ITEM_CONF_THRESHOLD,
    }


def test_row_below_threshold_is_flagged_uncertain():
    out = assemble_result_json(1, [_row(ITEM_CONF_THRESHOLD - _EPS)], True)
    assert out["rows"][0]["item_uncertain"] is True


def test_row_at_threshold_is_confident():
    # 경계는 '미만'만 미확신 — 임계 자체는 확신 쪽이다
    out = assemble_result_json(1, [_row(ITEM_CONF_THRESHOLD)], True)
    assert out["rows"][0]["item_uncertain"] is False


def test_row_without_candidates_is_uncertain():
    out = assemble_result_json(1, [_row(None)], True)
    assert out["rows"][0]["item_uncertain"] is True


def test_nan_sim_is_flagged_uncertain():
    # NaN 비교는 항상 False → `<` 관용구였다면 미확신 대신 "확신"으로 fail-open했다.
    # warp_gate.py의 `not (>=)` 관용구와 동일하게 fail-close(미확신)로 닫혀야 한다.
    out = assemble_result_json(1, [_row(float("nan"))], True)
    assert out["rows"][0]["item_uncertain"] is True


def test_result_carries_the_threshold_used_for_the_decision():
    # 캘리브가 바뀐 뒤에도 과거 잡의 플래그를 그 시점 기준으로 해석하기 위한 필드
    out = assemble_result_json(1, [_row(0.9)], True)
    assert out["item_conf_threshold"] == ITEM_CONF_THRESHOLD


def test_existing_fields_are_untouched_by_the_new_flag():
    out = assemble_result_json(42, [_row(0.9)], True)
    row = out["rows"][0]
    assert row["crop_ref"] == "job-42/row-0"
    assert row["supply"] == 85000
    assert row["amount_raw"] == "85"
    assert out["supply_sum"] == 85000
    assert out["warp_ok"] is True
