import pytest

from app.services.ocr_correction import build_correction, build_training_pairs


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


@pytest.mark.parametrize(
    "source",
    [
        "top1_kept",
        "candidate_picked:0",
        "candidate_picked:3",
        "manual_picked",
        "manual_typed",
        "new_item_created",
    ],
)
def test_label_source_is_copied_verbatim(source):
    out = build_correction(_DRAFT, _final(label_source=source))
    assert out["lines"][0]["label_source"] == source


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
