"""tools.curation_report 순수 분석 계층 단위테스트 (ssh/DB 비의존, 합성 데이터만)."""

import pytest

from tools.curation_cohort import is_item_failure
from tools.curation_report import (
    _failure_job_ids,
    amount_bucket,
    enrich_pairs,
    job_flags,
    label_bucket,
    oob_label_counts,
    parse_jobs_tsv,
    parse_pairs_tsv,
    pull_images,
    render_report,
    summarize,
)

BANK = {"엔진오일", "드라이", "타이어", "공임"}


def _pair(**over):
    base = {
        "id": 1,
        "crop_ref": "job-1/row-0",
        "job_id": 1,
        "row_index": 0,
        "draft_label": "엔진오일",
        "final_label": "엔진오일",
        "canonical_label": "엔진오일",
        "supply": 100000,
        "status": "included",
        "reviewed_at": None,
    }
    return {**base, **over}


def _job(job_id=1, rows=None):
    return {
        "job_id": job_id,
        "image_path": f"/data/up/{job_id}.jpeg",
        "result": {"rows": rows or [], "warp_ok": True},
    }


def _row(idx=0, top5=None, supply=100000, raw="100"):
    return {
        "row_index": idx,
        "crop_ref": f"job-1/row-{idx}",
        "item_top5": [{"label": lb, "sim": s} for lb, s in (top5 or [])],
        "supply": supply,
        "amount_raw": raw,
    }


# --- TSV 파싱 ---


def test_parse_pairs_tsv_converts_types_and_null():
    text = (
        "id\tcrop_ref\tjob_id\trow_index\tdraft_label\tfinal_label\t"
        "canonical_label\tsupply\tstatus\treviewed_at\n"
        "7\tjob-3/row-1\t3\t1\t드라이\t드럼\t드럼\tNULL\tincluded\tNULL"
    )
    pairs = parse_pairs_tsv(text)
    assert pairs == [
        {
            "id": 7,
            "crop_ref": "job-3/row-1",
            "job_id": 3,
            "row_index": 1,
            "draft_label": "드라이",
            "final_label": "드럼",
            "canonical_label": "드럼",
            "supply": None,
            "status": "included",
            "reviewed_at": None,
        }
    ]


def test_parse_jobs_tsv_parses_result_json():
    text = 'id\timage_path\tresult\n5\t/up/a.jpeg\t{"rows": [], "warp_ok": false}'
    jobs = parse_jobs_tsv(text)
    assert jobs == [
        {"job_id": 5, "image_path": "/up/a.jpeg", "result": {"rows": [], "warp_ok": False}}
    ]


def test_parse_jobs_tsv_fails_fast_when_image_path_contains_tab():
    text = 'id\timage_path\tresult\n5\t/up/a\t.jpeg\t{"rows": []}'
    with pytest.raises(ValueError, match="컬럼 경계"):
        parse_jobs_tsv(text)


# --- 라벨 버킷 ---


def test_label_bucket_ok_when_top1_matches():
    assert label_bucket("엔진오일", ["엔진오일", "드라이"], BANK) == "ok"


def test_label_bucket_out_of_bank_when_final_not_in_bank():
    assert label_bucket("안가방", ["엔진오일"], BANK) == "out_of_bank"


def test_label_bucket_top5_only_when_in_candidates_but_not_top1():
    assert label_bucket("드라이", ["엔진오일", "드라이"], BANK) == "top5_only"


def test_label_bucket_in_bank_miss_when_not_in_candidates():
    assert label_bucket("타이어", ["엔진오일", "드라이"], BANK) == "in_bank_miss"


def test_label_bucket_no_candidates_when_top5_empty():
    assert label_bucket("타이어", [], BANK) == "no_candidates"


# --- 금액 버킷 ---


def test_amount_bucket_ok():
    assert amount_bucket(100000, 100000) == "ok"


def test_amount_bucket_degenerate_when_draft_none():
    assert amount_bucket(None, 30000) == "degenerate"


def test_amount_bucket_zero_drift_when_draft_zero_but_final_positive():
    assert amount_bucket(0, 170000) == "zero_drift"


def test_amount_bucket_sign_mismatch():
    assert amount_bucket(190000, -190000) == "sign_mismatch"


def test_amount_bucket_misread():
    assert amount_bucket(19000, 117000) == "misread"


def test_amount_bucket_ok_when_both_zero():
    assert amount_bucket(0, 0) == "ok"


# --- enrich (pairs × result_json × bank 조인) ---


def test_enrich_joins_top5_and_draft_supply():
    pairs = [_pair()]
    jobs = [_job(rows=[_row(top5=[("엔진오일", 0.9), ("드라이", 0.8)])])]
    enriched = enrich_pairs(pairs, jobs, BANK)
    rec = enriched[0]
    assert rec["top5_labels"] == ["엔진오일", "드라이"]
    assert rec["top1_sim"] == 0.9
    assert rec["label_bucket"] == "ok"
    assert rec["amount_bucket"] == "ok"
    assert rec["draft_supply"] == 100000


def test_enrich_marks_row_missing_when_crop_ref_not_in_result():
    pairs = [_pair(row_index=9, crop_ref="job-1/row-9")]
    enriched = enrich_pairs(pairs, [_job(rows=[])], BANK)
    assert enriched[0]["label_bucket"] == "row_missing"
    assert enriched[0]["amount_bucket"] is None
    assert enriched[0]["draft_supply"] is None


def test_enrich_no_candidates_when_row_exists_but_top5_empty():
    pairs = [_pair()]
    enriched = enrich_pairs(pairs, [_job(rows=[_row(top5=[])])], BANK)
    assert enriched[0]["label_bucket"] == "no_candidates"


# --- 잡 플래그 / OOB 후보 / 요약 ---


def test_job_flags_warp_suspect_on_majority_zero_drift():
    recs = [
        {"job_id": 1, "status": "included", "amount_bucket": "zero_drift"},
        {"job_id": 1, "status": "included", "amount_bucket": "zero_drift"},
        {"job_id": 1, "status": "included", "amount_bucket": "ok"},
    ]
    assert job_flags(recs)[1] == ["warp_suspect"]


def test_job_flags_empty_when_amounts_ok():
    recs = [{"job_id": 2, "status": "included", "amount_bucket": "ok"}]
    assert job_flags(recs)[2] == []


def test_oob_label_counts_orders_by_frequency():
    recs = [
        {"status": "included", "in_bank": False, "canonical_label": "중고", "answer": "중고"},
        {"status": "included", "in_bank": False, "canonical_label": "중고", "answer": "중고"},
        {"status": "included", "in_bank": False, "canonical_label": "안가방", "answer": "안가방"},
        {"status": "included", "in_bank": True, "canonical_label": "공임", "answer": "공임"},
    ]
    assert oob_label_counts(recs) == [("중고", 2), ("안가방", 1)]


def test_oob_label_counts_includes_unevaluable_samples():
    """§1.2 — 후보 집계는 현재 뱅크 기준이며 코호트·버킷과 무관하다.

    버킷을 보면 판정 불가 표본이 unevaluable로 귀속되는 순간 후보 목록이 통째로 비고,
    재평가 전에는 기존 잡이 전부 unknown이라 후보가 0건이 된다(개선 워크플로 단절).
    """
    recs = [
        {
            "status": "included",
            "in_bank": False,
            "canonical_label": "중고",
            "answer": "중고",
            "label_bucket": "unevaluable",
        },
        {
            "status": "included",
            "in_bank": False,
            "canonical_label": "중고",
            "answer": "중고",
            "label_bucket": "row_missing",
        },
    ]
    assert oob_label_counts(recs) == [("중고", 2)]


def test_oob_label_counts_skips_pairs_without_a_canonical_label():
    recs = [{"status": "included", "in_bank": False, "canonical_label": None, "answer": ""}]
    assert oob_label_counts(recs) == []


def test_oob_label_counts_ignores_a_whitespace_only_label():
    """정답 라벨의 정규화 규칙이 한 곳이어야 한다 — 공백 라벨이 뱅크 후보로 올라가지 않는다."""
    recs = [{"status": "included", "in_bank": False, "canonical_label": "   ", "answer": ""}]
    assert oob_label_counts(recs) == []


def test_oob_label_counts_falls_back_to_canonical_label_when_answer_key_is_absent():
    """H1 — `answer` 생산자(Task 10)가 아직 없으므로 실제 프로덕션 행은 `answer` 키 자체가
    없다. 위 테스트들처럼 `"answer": ""`를 주입하면 `dict.get`이 키 존재만으로 폴백을
    건너뛰어 `canonical_label` 경로가 한 번도 실행되지 않는다(false guard) — 이 테스트는
    키를 아예 빼서 실제 폴백을 강제한다.
    """
    recs = [{"status": "included", "in_bank": False, "canonical_label": "중고"}]
    assert oob_label_counts(recs) == [("중고", 1)]


def test_oob_label_counts_falls_back_and_strips_whitespace_only_canonical_label():
    """H1 — 폴백 경로에서도 공백만인 라벨은 후보에 오르지 않는다(`.strip()` 회귀 포착)."""
    recs = [{"status": "included", "in_bank": False, "canonical_label": "   "}]
    assert oob_label_counts(recs) == []


def test_oob_label_counts_falls_back_when_canonical_label_is_none():
    """H1 — `answer` 키도 없고 `canonical_label`도 없는 행은 후보에서 빠진다."""
    recs = [{"status": "included", "in_bank": False, "canonical_label": None}]
    assert oob_label_counts(recs) == []


def test_oob_label_counts_falls_back_when_answer_key_is_present_but_falsy():
    """H1② — 우선순위는 키 존재 여부가 아니라 값의 유효성이어야 한다. `answer`가 빈
    문자열이어도 `canonical_label`이 있으면 그것을 쓴다. `dict.get(key, default)`은 키가
    있으면 값이 falsy여도 default를 쓰지 않으므로, 이 테스트는 그 형태에서는 RED다.
    """
    recs = [{"status": "included", "in_bank": False, "canonical_label": "중고", "answer": ""}]
    assert oob_label_counts(recs) == [("중고", 1)]


def test_summarize_computes_rates():
    pairs = [
        _pair(),
        _pair(id=2, crop_ref="job-1/row-1", row_index=1, final_label="안가방", supply=50000),
    ]
    rows = [
        _row(top5=[("엔진오일", 0.9)]),
        _row(idx=1, top5=[("드라이", 0.7)], supply=0, raw="0"),
    ]
    s = summarize(enrich_pairs(pairs, [_job(rows=rows)], BANK))
    assert s["n_included"] == 2
    assert s["top1_hits"] == 1
    assert s["in_bank_n"] == 1
    assert s["amount_ok"] == 1
    assert s["label_buckets"]["out_of_bank"] == 1


def test_pull_images_noop_on_empty_job_ids(tmp_path):
    out_dir = pull_images("unused-host", "unused-env", tmp_path, [], with_originals=False)
    assert out_dir == tmp_path / "images"
    assert out_dir.is_dir()


def test_render_report_handles_job_with_only_excluded_pairs():
    pairs = [_pair(status="excluded")]
    enriched = enrich_pairs(pairs, [_job(rows=[_row(top5=[("엔진오일", 0.9)])])], BANK)
    md = render_report(enriched, {"fetched_at": "t"})
    assert "excluded" in md


def test_render_report_smoke_contains_key_sections():
    pairs = [_pair(), _pair(id=2, crop_ref="job-1/row-1", row_index=1, final_label="안가방")]
    rows = [_row(top5=[("엔진오일", 0.9)]), _row(idx=1, top5=[("드라이", 0.7)], supply=0, raw="0")]
    enriched = enrich_pairs(pairs, [_job(rows=rows)], BANK)
    md = render_report(enriched, {"fetched_at": "2026-07-27T00:00:00", "bank_distinct": 4})
    assert "핵심 지표" in md
    assert "뱅크 추가 후보" in md
    assert "안가방" in md
    assert "잡별" in md


# --- 판정 불가 소비자 회귀 (spec §3-C의 표 — 소비자 6곳) ---


def _enriched_row(**over):
    """소비자 술어 테스트용 최소 enriched 행(전 잡 폭주 회귀를 합성으로 재현한다)."""
    base = {
        "job_id": 1,
        "crop_ref": "job-1/row-0",
        "status": "included",
        "canonical_label": "안가방",
        "answer": "안가방",
        "final_label": "안가방",
        "draft_label": "안가방",
        "supply": 100000,
        "draft_supply": 100000,
        "amount_raw": "100",
        "top5_labels": [],
        "top1_sim": None,
        "in_bank": True,
        "label_bucket": "unevaluable",
        "amount_bucket": "ok",
        "cohort": "current_bank",
        "reeval_has_peer": None,
    }
    return {**base, **over}


def test_summarize_excludes_unevaluable_from_item_denominators():
    rows = [
        _enriched_row(label_bucket="ok", top1_sim=0.9),
        _enriched_row(crop_ref="job-1/row-1", label_bucket="unevaluable"),
        _enriched_row(crop_ref="job-1/row-2", label_bucket="row_missing", amount_bucket=None),
    ]
    s = summarize(rows)
    assert s["n_included"] == 3  # 표본 구성표가 쓰는 전체 수는 유지
    assert s["n_item_evaluable"] == 1  # 품목 지표 분모는 평가 가능 쌍만
    assert s["top1_hits"] == 1
    assert s["in_bank_n"] == 1
    assert s["label_buckets"]["unevaluable"] == 1  # 버킷 분포에는 남아 수가 보인다


def test_summarize_keeps_amount_metrics_independent_of_item_evaluability():
    rows = [
        _enriched_row(label_bucket="unevaluable", amount_bucket="zero_drift"),
        _enriched_row(crop_ref="job-1/row-1", label_bucket="unevaluable", amount_bucket="ok"),
    ]
    s = summarize(rows)
    assert s["n_item_evaluable"] == 0
    assert (s["amount_n"], s["amount_ok"]) == (2, 1)


def test_failure_job_ids_does_not_stampede_on_unevaluable_items():
    """전 잡 폭주 회귀 — 판정 불가를 실패로 세면 pull-images가 전 잡 크롭을 당긴다(실측 18잡)."""
    rows = [
        _enriched_row(job_id=1, label_bucket="unevaluable", amount_bucket="ok"),
        _enriched_row(
            job_id=2,
            crop_ref="job-2/row-0",
            label_bucket="unevaluable",
            amount_bucket="zero_drift",
        ),
        _enriched_row(
            job_id=3, crop_ref="job-3/row-0", status="excluded", label_bucket="unevaluable"
        ),
        _enriched_row(job_id=4, crop_ref="job-4/row-0", label_bucket="in_bank_miss"),
    ]
    assert _failure_job_ids(rows) == [2, 3, 4]  # 1은 판정 불가일 뿐 실패가 아니다


def test_render_report_shows_evaluable_denominators_and_no_none_sim_crash():
    rows = [
        _enriched_row(label_bucket="unevaluable", top1_sim=None),
        _enriched_row(
            crop_ref="job-1/row-1",
            label_bucket="in_bank_miss",
            top1_sim=0.7,
            top5_labels=["공임"],
        ),
    ]
    md = render_report(rows, {"fetched_at": "t"})
    assert "| 품목 top-1 (평가 가능 쌍 기준) | 0/1 (0.0%) |" in md  # 분모가 2가 아니라 1
    assert "| 1 | 2 | 0/1 |" in md  # 잡별 top-1은 k/n 표기
    assert "job-1/row-1" in md  # 미스 목록엔 평가 가능 쌍만
    assert "job-1/row-0" not in md.split("## in-bank 리트리벌 미스")[1].split("##")[0]


def test_row_missing_pairs_stay_in_failures_and_pull_images():
    rows = [
        _enriched_row(job_id=1, label_bucket="row_missing", amount_bucket=None),
        _enriched_row(
            job_id=2, crop_ref="job-2/row-0", label_bucket="unevaluable", amount_bucket="ok"
        ),
    ]
    assert _failure_job_ids(rows) == [1]  # 2는 판정 불가일 뿐 실패가 아니다
    assert [r["job_id"] for r in rows if is_item_failure(r)] == [1]
