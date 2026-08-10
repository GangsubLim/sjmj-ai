import pytest

from app.schemas.ocr import LABEL_SOURCES
from app.services.ocr_correction import DRAFT_SUPPLY_MAX, build_correction, build_training_pairs


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
            "label_source": None,
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


def _correction(lines):
    return {"lines": lines, "rows_added": 0, "rows_dropped": 0}


def test_build_training_pairs_maps_line_to_pair():
    correction = _correction(
        [
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
    )
    pairs = build_training_pairs(42, 7, correction)
    assert pairs == [
        {
            "crop_ref": "job-42/row-0",
            "job_id": 42,
            "invoice_id": 7,
            "row_index": 0,
            "draft_label": "삼겹살",
            "draft_supply": 120000,
            "final_label": "목살",
            "canonical_label": "목살",
            "supply": 120000,
            "status": "included",
        }
    ]


def test_build_training_pairs_skips_lines_without_crop_ref():
    correction = _correction([{"final_label": "수기품목", "final_supply": 5000}])
    assert build_training_pairs(1, 1, correction) == []


def test_build_training_pairs_parses_multidigit_row_index():
    correction = _correction(
        [
            {
                "crop_ref": "job-9/row-12",
                "draft_label": None,
                "final_label": "X",
                "final_supply": None,
            }
        ]
    )
    pair = build_training_pairs(9, 3, correction)[0]
    assert pair["row_index"] == 12
    assert pair["canonical_label"] == "X"
    assert pair["supply"] is None


@pytest.mark.parametrize(
    "draft_field",
    [
        pytest.param({"draft_supply": None}, id="explicit-null"),
        pytest.param({}, id="key-absent"),
    ],
)
def test_build_training_pairs_carries_a_missing_draft_supply_as_none(draft_field):
    """모델이 금액을 못 읽은 행은 앵커 없음(None)이 정상 표현이다(spec §3).

    키가 아예 없는 line(옛 확정분의 correction_json)도 같은 자리로 떨어져야 한다 —
    명시적 null만 덮으면 line.get이 line[...]으로 좁아져도 이 단언이 비껴간다.
    """
    correction = _correction(
        [
            {
                "crop_ref": "job-9/row-0",
                "draft_label": None,
                **draft_field,
                "final_label": "X",
                "final_supply": 5000,
            }
        ]
    )
    assert build_training_pairs(9, 3, correction)[0]["draft_supply"] is None


@pytest.mark.parametrize(
    "value",
    [
        DRAFT_SUPPLY_MAX + 1,  # 2147483648 — INT 범위 밖(STRICT_TRANS_TABLES에서 1264 롤백)
        -1,
        "120000",
        True,
        1.5,
    ],
)
def test_build_training_pairs_isolates_an_unstorable_draft_supply(value):
    """신뢰할 수 없는 OCR 값이 정수 컬럼에 들어가는 유일한 지점이라 여기서 거른다(spec §2.3).

    parse_amount는 원문의 모든 숫자 run을 길이 제한 없이 이어붙이므로 30자리도 만들 수 있다.
    운영 sql_mode에 STRICT_TRANS_TABLES가 있어 범위 초과는 잘림이 아니라 에러(1264)이며,
    실패 모양은 "잘린 거짓 앵커"가 아니라 **확정 트랜잭션 전체 롤백** — 사용자가 그
    거래명세서를 저장할 수 없게 된다. 격리해도 손해가 없다: 30자리 값은 어떤 새 인식값과도
    같아질 수 없어 ② 앵커로서 가치가 0이고, NULL은 이미 "앵커 없음"의 정상 표현이다.
    """
    correction = _correction(
        [
            {
                "crop_ref": "job-9/row-0",
                "draft_label": None,
                "draft_supply": value,
                "final_label": "X",
                "final_supply": 5000,
            }
        ]
    )
    assert build_training_pairs(9, 3, correction)[0]["draft_supply"] is None


@pytest.mark.parametrize("value", [0, DRAFT_SUPPLY_MAX])
def test_build_training_pairs_keeps_boundary_draft_supplies(value):
    """경계값은 통과해야 한다 — 0은 정상 판독(빈칸)이지 미판독이 아니다."""
    correction = _correction(
        [
            {
                "crop_ref": "job-9/row-0",
                "draft_label": None,
                "draft_supply": value,
                "final_label": "X",
                "final_supply": 5000,
            }
        ]
    )
    assert build_training_pairs(9, 3, correction)[0]["draft_supply"] == value


_DRAFT = {
    "rows": [
        {
            "crop_ref": "job-1/row-0",
            "item_top5": [{"label": "타이어", "sim": 0.72}],
            "supply": 85000,
        }
    ]
}


def _final(**over) -> list[dict]:
    return [{"crop_ref": "job-1/row-0", "name": "타이어", "supply": 85000, **over}]


@pytest.mark.parametrize("source", sorted(LABEL_SOURCES))
def test_label_source_is_copied_verbatim(source):
    # 어휘를 손으로 복제하지 않고 SSoT(app.schemas.ocr.LABEL_SOURCES)에서 파생시킨다 —
    # 목록이 늘면(TOP_K 변경 등) 이 테스트가 자동으로 따라간다.
    out = build_correction(_DRAFT, _final(label_source=source))
    assert out["lines"][0]["label_source"] == source


def test_unknown_label_source_is_copied_too():
    """서비스는 어휘를 검증하지 않고 그대로 복사한다 — 화이트리스트는 라우터(Pydantic) 책임.

    위 parametrize가 '전량 커버'로 읽히지만 build_correction은 값에 무관심한 통과 함수라
    판별력이 1건과 같다. 그 성질 자체를 여기서 명시적으로 고정한다.
    """
    out = build_correction(_DRAFT, _final(label_source="not_a_known_source"))
    assert out["lines"][0]["label_source"] == "not_a_known_source"


_DRAFT_2ROW = {
    "rows": [
        {
            "crop_ref": "job-1/row-0",
            "item_top5": [{"label": "타이어", "sim": 0.72}],
            "supply": 85000,
        },
        {
            "crop_ref": "job-1/row-1",
            "item_top5": [{"label": "엔진오일", "sim": 0.66}],
            "supply": 42000,
        },
    ]
}


def test_label_source_follows_its_own_row_not_input_position():
    """각 line의 label_source는 그 line에 crop_ref로 매칭된 item에서 와야 한다.

    최종 item 순서를 초안 행 순서와 뒤집어 준다 — 인덱스 기준(final_items[i])이나
    final_items[0] 고정으로 회귀하면 provenance가 행 간에 뒤바뀌어 재학습 데이터가
    조용히 오염된다. 단일 행 fixture로는 이 회귀가 전부 GREEN이다.
    """
    final = [
        {
            "crop_ref": "job-1/row-1",
            "name": "엔진오일",
            "supply": 42000,
            "label_source": "manual_typed",
        },
        {
            "crop_ref": "job-1/row-0",
            "name": "타이어",
            "supply": 85000,
            "label_source": "candidate_picked:2",
        },
    ]
    by_ref = {line["crop_ref"]: line for line in build_correction(_DRAFT_2ROW, final)["lines"]}
    assert by_ref["job-1/row-0"]["label_source"] == "candidate_picked:2"
    assert by_ref["job-1/row-1"]["label_source"] == "manual_typed"


def test_label_source_is_null_when_client_omits_it():
    out = build_correction(_DRAFT, _final())
    assert out["lines"][0]["label_source"] is None


def test_rows_without_crop_ref_produce_no_line_at_all():
    # OCR 초안에서 오지 않은 행은 lines[]에 없다 → label_source를 실을 자리 자체가 없다
    out = build_correction(_DRAFT, [{"name": "수동추가", "label_source": "manual_typed"}])
    assert out["lines"] == []
    assert out["rows_added"] == 1
    assert out["rows_dropped"] == 1


def test_training_pairs_ignore_label_source():
    # training_pairs 스키마는 건드리지 않는다(마이그레이션 0) — 이 계약을 고정한다
    correction = build_correction(_DRAFT, _final(label_source="candidate_picked:2"))
    pairs = build_training_pairs(1, 10, correction)
    assert "label_source" not in pairs[0]
