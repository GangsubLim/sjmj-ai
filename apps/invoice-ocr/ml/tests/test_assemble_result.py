from handwriting.infer_job import ITEM_CONF_THRESHOLD, assemble_result_json

# 임계는 Task 3의 재채점으로 바뀐다 — 테스트가 상수를 하드코딩하면 캘리브 때마다 무조건 깨진다.
# tests/conftest.py의 make_warped가 MIN_BLUE_RATIO에서 두께를 유도한 것과 같은 이유다.
_EPS = 0.01


def _row(sim: float | None, row_index: int = 0) -> dict:
    top5 = [] if sim is None else [{"label": "타이어", "sim": sim}]
    return {"row_index": row_index, "item_top5": top5, "supply": 85, "amount_raw": "85"}


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


def test_flag_is_computed_per_row_from_that_rows_own_candidates():
    # 행이 1개뿐인 테스트로는 '행별' 성질이 고정되지 않는다 — rows[0]으로 한 번 계산해 전 행에
    # 같은 값을 찍거나 한 칸 밀려도 통과하기 때문. 검수 UI가 이 플래그로 행 단위 후보 칩
    # 펼침을 결정하므로 정렬이 어긋나면 정확히 잘못된 행이 펼쳐진다. crop_ref와 짝지어 단언해
    # 값뿐 아니라 어느 행의 값인지까지 고정한다.
    rows = [
        _row(0.9, row_index=0),
        _row(ITEM_CONF_THRESHOLD - _EPS, row_index=1),
        _row(None, row_index=2),
        _row(0.95, row_index=3),
    ]
    out = assemble_result_json(9, rows, True)
    assert [(r["crop_ref"], r["item_uncertain"]) for r in out["rows"]] == [
        ("job-9/row-0", False),
        ("job-9/row-1", True),
        ("job-9/row-2", True),
        ("job-9/row-3", False),
    ]


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


def test_result_carries_the_retrieval_version_when_given():
    out = assemble_result_json(1, [_row(0.9)], True, retrieval_version="a1b2c3d4e5f6")
    assert out["retrieval_version"] == "a1b2c3d4e5f6"


def test_retrieval_version_key_is_absent_when_not_given():
    # 자리표시자("unknown" 등)를 넣으면 서로 다른 retrieval 상태가 한 코호트로 합쳐진다.
    out = assemble_result_json(1, [_row(0.9)], True)
    assert "retrieval_version" not in out


def test_retrieval_version_key_is_absent_when_blank_or_whitespace():
    # 빈 문자열/공백은 "값이 있는 지문"이 아니라 그 자체로 자리표시자 코호트가 된다 —
    # None과 동일하게 키를 만들지 않아야 한다(Issue #49 재발 방지).
    out_empty = assemble_result_json(1, [_row(0.9)], True, retrieval_version="")
    out_blank = assemble_result_json(1, [_row(0.9)], True, retrieval_version="   ")
    assert "retrieval_version" not in out_empty
    assert "retrieval_version" not in out_blank


def test_warp_failure_path_also_carries_the_stamp():
    # 그 잡의 쌍은 어차피 row_missing이지만, 스탬프 유무가 경로에 따라 갈리면
    # "왜 이 잡만 unknown인가"라는 해석 불가 상태가 된다(spec §3-A).
    out = assemble_result_json(7, [], False, retrieval_version="a1b2c3d4e5f6")
    assert out == {
        "rows": [],
        "supply_sum": 0,
        "warp_ok": False,
        "item_conf_threshold": ITEM_CONF_THRESHOLD,
        "retrieval_version": "a1b2c3d4e5f6",
    }
