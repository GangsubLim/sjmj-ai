"""tools.curation_report 순수 분석 계층 단위테스트 (ssh/DB 비의존, 합성 데이터만)."""

import pytest

from tests.conftest import _reeval_record  # 게이트가 내는 레코드 shape 공유(L3)
from tools.curation_cohort import (
    COHORTS,
    ITEM_EVALUABLE_COHORTS,
    REEVAL_REJECT_REASONS,
    REEVAL_STATES,
    is_item_failure,
)
from tools.curation_report import (
    _REEVAL_REJECT_TEXT,
    COHORT_TABLE,
    _failure_job_ids,
    amount_bucket,
    enrich_pairs,
    job_flags,
    label_bucket,
    oob_label_counts,
    parse_jobs_tsv,
    parse_pairs_tsv,
    pull_images,
    reeval_notice,
    render_report,
    summarize,
)

BANK = {"엔진오일", "드라이", "타이어", "공임"}
_CUR_VERSION = "cur-fingerprint"


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


def _job(job_id=1, rows=None, retrieval_version=_CUR_VERSION):
    result = {"rows": rows or [], "warp_ok": True}
    if retrieval_version is not None:
        result["retrieval_version"] = retrieval_version
    return {"job_id": job_id, "image_path": f"/data/up/{job_id}.jpeg", "result": result}


def _row(idx=0, top5=None, supply=100000, raw="100", job=1):
    return {
        "row_index": idx,
        "crop_ref": f"job-{job}/row-{idx}",
        "item_top5": [{"label": lb, "sim": s} for lb, s in (top5 or [])],
        "supply": supply,
        "amount_raw": raw,
    }


def _enrich(pairs, jobs, bank=BANK, **kw):
    """기본 현재 지문을 물려주는 래퍼 — 스탬프를 명시하지 않은 테스트는 current_bank가 된다."""
    kw.setdefault("current_retrieval_version", _CUR_VERSION)
    return enrich_pairs(pairs, jobs, bank, **kw)


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
    enriched = _enrich(pairs, jobs)
    rec = enriched[0]
    assert rec["top5_labels"] == ["엔진오일", "드라이"]
    assert rec["top1_sim"] == 0.9
    assert rec["label_bucket"] == "ok"
    assert rec["amount_bucket"] == "ok"
    assert rec["draft_supply"] == 100000


def test_enrich_marks_row_missing_when_crop_ref_not_in_result():
    pairs = [_pair(row_index=9, crop_ref="job-1/row-9")]
    enriched = _enrich(pairs, [_job(rows=[])])
    assert enriched[0]["label_bucket"] == "row_missing"
    assert enriched[0]["amount_bucket"] is None
    assert enriched[0]["draft_supply"] is None


def test_enrich_no_candidates_when_row_exists_but_top5_empty():
    pairs = [_pair()]
    enriched = _enrich(pairs, [_job(rows=[_row(top5=[])])])
    assert enriched[0]["label_bucket"] == "no_candidates"


# --- 정답원 통일 (spec §3-C) ---


def test_enrich_buckets_by_canonical_label_not_final_label():
    """회귀 — 검수자가 canonical을 정정한 쌍은 예측이 맞아도 out_of_bank로 오판됐다."""
    pairs = [_pair(final_label="안가방", canonical_label="엔진오일")]
    enriched = _enrich(pairs, [_job(rows=[_row(top5=[("엔진오일", 0.9)])])])
    assert enriched[0]["label_bucket"] == "ok"
    assert enriched[0]["in_bank"] is True
    assert enriched[0]["final_label"] == "안가방"  # 표시·감사용으로 그대로 남는다


def test_enrich_marks_no_canonical_label_as_unevaluable_without_falling_back():
    # 폴백하면 방금 없앤 불일치가 되살아난다 — 정답이 없으면 채점이 성립하지 않는다.
    pairs = [_pair(final_label="엔진오일", canonical_label=None)]
    enriched = _enrich(pairs, [_job(rows=[_row(top5=[("엔진오일", 0.9)])])])
    assert enriched[0]["label_bucket"] == "unevaluable"
    assert enriched[0]["in_bank"] is False


def test_enrich_treats_a_blank_canonical_label_like_a_missing_one():
    enriched = _enrich(
        [_pair(canonical_label="   ")], [_job(rows=[_row(top5=[("엔진오일", 0.9)])])], BANK
    )
    assert enriched[0]["label_bucket"] == "unevaluable"
    assert enriched[0]["in_bank"] is False
    assert oob_label_counts(enriched) == []  # 공백 라벨이 뱅크 후보로 새지 않는다


def test_item_bucket_prefers_row_missing_over_missing_answer():
    """M1 회귀 — row_missing(데이터 정합 장애)은 answer 부재보다 먼저 판정돼야 한다.

    curation_cohort.DATA_INTEGRITY_FAILURE_BUCKETS 계약: row_missing은 unevaluable로
    삼켜지면 안 되고 실패로 남아야 한다(failures.jsonl·pull-images 소비).
    """
    pairs = [_pair(canonical_label=None, row_index=9, crop_ref="job-1/row-9")]
    enriched = _enrich(pairs, [_job(rows=[])])
    assert enriched[0]["label_bucket"] == "row_missing"


def test_oob_candidates_follow_the_canonical_label():
    pairs = [_pair(final_label="엔진오일", canonical_label="중고")]
    enriched = _enrich(pairs, [_job(rows=[_row(top5=[("엔진오일", 0.9)])])])
    assert oob_label_counts(enriched) == [("중고", 1)]


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


def test_summarize_computes_rates():
    pairs = [
        _pair(),
        _pair(
            id=2,
            crop_ref="job-1/row-1",
            row_index=1,
            final_label="안가방",
            canonical_label="안가방",
            supply=50000,
        ),
    ]
    rows = [
        _row(top5=[("엔진오일", 0.9)]),
        _row(idx=1, top5=[("드라이", 0.7)], supply=0, raw="0"),
    ]
    s = summarize(_enrich(pairs, [_job(rows=rows)]))
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
    enriched = _enrich(pairs, [_job(rows=[_row(top5=[("엔진오일", 0.9)])])])
    md = render_report(enriched, {"fetched_at": "t"})
    assert "excluded" in md


def test_render_report_smoke_contains_key_sections():
    pairs = [
        _pair(),
        _pair(
            id=2,
            crop_ref="job-1/row-1",
            row_index=1,
            final_label="안가방",
            canonical_label="안가방",
        ),
    ]
    rows = [_row(top5=[("엔진오일", 0.9)]), _row(idx=1, top5=[("드라이", 0.7)], supply=0, raw="0")]
    enriched = _enrich(pairs, [_job(rows=rows)])
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


def test_misses_list_prints_the_judged_answer_not_just_final_label():
    """H1 회귀 — 버킷은 answer(canonical_label)로 매겨지므로 판정에 쓴 값을 함께 인쇄해야
    한다. final만 찍으면 top5에 없는 라벨이 '정답'으로 읽혀 자기모순이 된다.
    """
    row = _enriched_row(
        answer="엔진오일",
        final_label="안가방",
        label_bucket="top5_only",
        top5_labels=["드라이", "엔진오일"],
        top1_sim=0.7,
    )
    md = render_report([row], {"fetched_at": "t"})
    misses = md.split("## in-bank 리트리벌 미스")[1].split("##")[0]
    assert "answer='엔진오일'" in misses


def test_row_missing_pairs_stay_in_failures_and_pull_images():
    rows = [
        _enriched_row(job_id=1, label_bucket="row_missing", amount_bucket=None),
        _enriched_row(
            job_id=2, crop_ref="job-2/row-0", label_bucket="unevaluable", amount_bucket="ok"
        ),
    ]
    assert _failure_job_ids(rows) == [1]  # 2는 판정 불가일 뿐 실패가 아니다
    assert [r["job_id"] for r in rows if is_item_failure(r)] == [1]


# --- era-aware 재판정 (spec §3-C — unevaluable의 생산 지점) ---


def test_cohort_is_unknown_and_bucket_unevaluable_for_a_job_without_a_stamp():
    """§5 — 지금 데이터(잡 22~54는 전부 스탬프 이전)는 재평가 없이는 판정 불가다."""
    enriched = _enrich(
        [_pair()], [_job(rows=[_row(top5=[("엔진오일", 0.9)])], retrieval_version=None)]
    )
    assert enriched[0]["cohort"] == "unknown"
    assert enriched[0]["label_bucket"] == "unevaluable"


def test_cohort_is_stale_bank_when_the_job_stamp_differs_from_now():
    enriched = _enrich(
        [_pair()], [_job(rows=[_row(top5=[("엔진오일", 0.9)])], retrieval_version="old")]
    )
    assert enriched[0]["cohort"] == "stale_bank"
    assert enriched[0]["label_bucket"] == "unevaluable"


def test_cohort_is_current_bank_when_the_stamp_matches_and_bucket_is_computed():
    enriched = _enrich([_pair()], [_job(rows=[_row(top5=[("엔진오일", 0.9)])])])
    assert enriched[0]["cohort"] == "current_bank"
    assert enriched[0]["label_bucket"] == "ok"


def test_reeval_record_wins_over_result_json():
    """재평가가 있으면 그 레코드가 진실원이다 — stale top5가 지표에 섞이지 않는다."""
    reeval = {"job-1/row-0": _reeval_record(preds=["엔진오일", "공임"], top1_sim=0.88)}
    enriched = _enrich(
        [_pair()],
        [_job(rows=[_row(top5=[("타이어", 0.4)])], retrieval_version="old")],
        reeval=reeval,
    )
    assert enriched[0]["cohort"] == "reevaluated"
    assert enriched[0]["top5_labels"] == ["엔진오일", "공임"]
    assert enriched[0]["label_bucket"] == "ok"


def test_reeval_supplies_top1_sim_too():
    # top5만 갈아끼우면 유사도 분포에서 시점이 다시 섞인다(summarize의 hit/miss sim).
    reeval = {"job-1/row-0": _reeval_record(preds=["엔진오일"], top1_sim=0.88)}
    enriched = _enrich([_pair()], [_job(rows=[_row(top5=[("타이어", 0.4)])])], reeval=reeval)
    assert enriched[0]["top1_sim"] == 0.88


def test_reeval_falls_back_to_result_json_for_pairs_it_does_not_cover():
    reeval = {"job-1/row-9": _reeval_record("job-1/row-9")}
    enriched = _enrich([_pair()], [_job(rows=[_row(top5=[("엔진오일", 0.9)])])], reeval=reeval)
    assert enriched[0]["cohort"] == "current_bank"
    assert enriched[0]["top5_labels"] == ["엔진오일"]


def test_reeval_pair_is_evaluable_even_when_the_result_json_row_is_missing():
    # 재평가는 preds를 직접 주므로 result_json 조인 실패와 무관하게 품목 판정이 성립한다.
    reeval = {"job-1/row-0": _reeval_record(preds=["엔진오일"], top1_sim=0.9)}
    enriched = _enrich([_pair()], [_job(rows=[])], reeval=reeval)
    assert enriched[0]["label_bucket"] == "ok"
    assert enriched[0]["amount_bucket"] is None  # 금액은 여전히 result_json에서만 온다


def test_reeval_in_bank_is_recomputed_from_the_current_canonical_label():
    """PATCH 회귀 — A로 채점한 뒤 검수자가 B로 고치면 preds는 유효하지만 in_bank는 A 기준이다.

    canonical_label은 검수 완료 후에도 PATCH로 바뀔 수 있다(curation_repository의 화이트리스트).
    """
    reeval = {"job-1/row-0": _reeval_record(label="엔진오일", in_bank=True, preds=["엔진오일"])}
    enriched = _enrich([_pair(canonical_label="중고")], [_job(rows=[_row()])], reeval=reeval)
    assert enriched[0]["in_bank"] is False  # 현재 canonical('중고')은 뱅크에 없다
    assert enriched[0]["top5_labels"] == ["엔진오일"]  # preds는 그대로 재사용
    assert enriched[0]["label_bucket"] == "out_of_bank"


def test_no_label_cohort_takes_precedence_over_the_stamp():
    enriched = _enrich([_pair(canonical_label=None)], [_job(rows=[_row()])])
    assert enriched[0]["cohort"] == "no_label"
    assert enriched[0]["label_bucket"] == "unevaluable"


def test_row_missing_survives_an_unevaluable_cohort():
    """M1 계약 유지 — 데이터 정합 장애는 시점 판정 불가에 삼켜지지 않는다.

    plan Task 11의 _item_bucket 초안은 코호트를 row_missing보다 먼저 봐서, 스탬프 없는 잡
    (현재 데이터 전량)의 조인 결손을 unevaluable로 흡수한다. 그러면 row_missing이
    failures.jsonl·pull-images에서 통째로 사라진다(curation_cohort.DATA_INTEGRITY_
    FAILURE_BUCKETS 계약 위반). 그래서 row_missing을 코호트보다 먼저 판정한다.
    """
    pairs = [_pair(row_index=9, crop_ref="job-1/row-9")]
    enriched = _enrich(pairs, [_job(rows=[], retrieval_version=None)])
    assert enriched[0]["cohort"] == "unknown"
    assert enriched[0]["label_bucket"] == "row_missing"
    assert _failure_job_ids(enriched) == [1]


def test_unevaluable_pairs_still_feed_the_bank_candidate_list():
    """§3-C — 판정 불가 표본도 후보 집계에는 포함된다(추론 시점과 무관한 사실이므로)."""
    pairs = [_pair(canonical_label="중고")]
    enriched = _enrich([*pairs], [_job(rows=[_row()], retrieval_version=None)])
    assert enriched[0]["label_bucket"] == "unevaluable"
    assert oob_label_counts(enriched) == [("중고", 1)]


def test_a_rejected_reeval_leaves_each_pair_on_its_own_stamp():
    """게이트가 기각한 재평가는 통째로 버려지고(reeval=None) 각 쌍이 스탬프로 재분기한다.

    낡은 재평가가 없어도 스탬프가 현재 지문과 같은 잡은 current_bank로 남는 것이 맞다 —
    그 잡은 현재 retrieval 상태 그대로 추론된 것이기 때문이다.
    """
    pairs = [_pair(), _pair(id=2, job_id=2, crop_ref="job-2/row-0")]
    jobs = [
        _job(rows=[_row(top5=[("엔진오일", 0.9)])]),
        _job(job_id=2, rows=[_row(job=2, top5=[("엔진오일", 0.9)])], retrieval_version="old"),
    ]
    enriched = _enrich(pairs, jobs, reeval=None)
    assert [r["cohort"] for r in enriched] == ["current_bank", "stale_bank"]
    assert [r["label_bucket"] for r in enriched] == ["ok", "unevaluable"]


def test_unevaluable_jobs_do_not_stampede_the_failure_list():
    """전 잡 폭주 실증 — 스탬프 이전 잡을 대량으로 넣어도 실패 목록이 비어 있어야 한다."""
    pairs = [_pair(id=i, job_id=i, crop_ref=f"job-{i}/row-0") for i in range(1, 6)]
    jobs = [
        _job(job_id=i, rows=[_row(job=i, top5=[("타이어", 0.4)])], retrieval_version=None)
        for i in range(1, 6)
    ]
    enriched = _enrich(pairs, jobs)
    assert {r["label_bucket"] for r in enriched} == {"unevaluable"}
    assert _failure_job_ids(enriched) == []


def test_reeval_carries_has_peer_when_the_label_is_unchanged():
    reeval = {"job-1/row-0": _reeval_record(label="엔진오일", has_peer=False)}
    enriched = _enrich([_pair()], [_job(rows=[_row()])], reeval=reeval)
    assert enriched[0]["reeval_has_peer"] is False
    assert enriched[0]["in_bank"] is True  # 커버리지 정의는 spec §3-C대로 불변


def test_reeval_has_peer_is_withheld_when_the_label_was_patched():
    reeval = {"job-1/row-0": _reeval_record(label="엔진오일", has_peer=False)}
    enriched = _enrich([_pair(canonical_label="안가방")], [_job(rows=[_row()])], reeval=reeval)
    assert enriched[0]["reeval_has_peer"] is None  # 채점 당시 라벨 기준이라 낡았다


def test_pairs_without_a_reachable_peer_are_kept_out_of_the_retrieval_miss_list():
    """구조적 도달 불가를 '리트리벌 실패'로 사람에게 보내지 않는다(전표 축 제외의 귀결)."""
    rows = [
        _enriched_row(
            cohort="reevaluated",
            label_bucket="in_bank_miss",
            reeval_has_peer=False,
            top1_sim=0.4,
        ),
        _enriched_row(
            crop_ref="job-1/row-1",
            cohort="reevaluated",
            label_bucket="in_bank_miss",
            reeval_has_peer=True,
            top1_sim=0.5,
        ),
    ]
    md = render_report(rows, {"fetched_at": "t"})
    misses = md.split("## in-bank 리트리벌 미스")[1].split("##")[0]
    assert "job-1/row-1" in misses and "job-1/row-0" not in misses
    assert "도달 불가" in md  # 제외 건수를 공개한다


# --- 리포트 구조 (표본 구성표를 핵심 지표 위에 — spec §3-C) ---


def test_summarize_counts_cohorts_over_included_pairs_only():
    rows = [
        _enriched_row(cohort="unknown", label_bucket="unevaluable"),
        _enriched_row(crop_ref="job-1/row-1", cohort="current_bank", label_bucket="ok"),
        _enriched_row(
            crop_ref="job-1/row-2", cohort="unknown", status="excluded", label_bucket="unevaluable"
        ),
    ]
    s = summarize(rows)
    assert s["cohorts"]["unknown"] == 1  # excluded는 코호트 표에 들어가지 않는다
    assert s["cohorts"]["current_bank"] == 1
    assert s["n_excluded"] == 1


def test_reeval_notice_reports_an_adopted_reevaluation():
    meta = {
        "reeval": {
            "state": "present",
            "adopted": True,
            "reason": None,
            "generated_at": "2026-07-30T05:12:00+09:00",
            "after": "a1b2c3d4e5f6",
            "scope": "all",
            "n_pairs": 44,
        }
    }
    line = reeval_notice(meta)
    assert "a1b2c3d4e5f6" in line and "현재와 일치" in line and "44" in line


def test_reeval_notice_explains_absent_output():
    assert "재평가 없음" in reeval_notice({"reeval": {"state": "absent", "adopted": False}})
    assert "score --scope all" in reeval_notice({"reeval": {"state": "absent", "adopted": False}})


def test_reeval_notice_explains_a_score_jsonl_without_meta():
    # 사용자가 재평가를 돌렸다고 착각하지 않도록 정상 경로임을 한 줄로 알린다(§3-C).
    # state 철자는 게이트 사유와 공유한다(no_meta) — 같은 조건을 두 이름으로 부르지 않는다(M2).
    line = reeval_notice({"reeval": {"state": "no_meta", "adopted": False}})
    assert "score_meta.json" in line


def test_reeval_notice_explains_a_discarded_stale_reevaluation():
    line = reeval_notice({"reeval": {"state": "present", "adopted": False, "reason": "stale"}})
    assert "폐기" in line


def test_reeval_notice_uses_the_reason_when_the_state_key_is_missing():
    """H1 회귀 — state 기본값을 absent로 두면 사유가 손에 있는데도 '산출물이 없다'고 단정한다.

    그 단정은 이 절이 막으려던 오인(사용자가 원인을 모른 채 엉뚱한 조치를 함)을 그대로 만든다.
    """
    line = reeval_notice({"reeval": {"adopted": False, "reason": "stale"}})
    assert "폐기" in line
    assert "산출물이 없다" not in line


def test_reeval_notice_does_not_claim_absence_when_the_reason_is_unknown():
    """정보가 있는데 사유만 못 읽은 경우 — '없다'고 단정하지 않고 사유 미상으로 물러선다."""
    line = reeval_notice({"reeval": {"adopted": False, "reason": "낯선사유"}})
    assert "산출물이 없다" not in line
    assert "미상" in line


def test_reeval_notice_covers_every_reeval_state():
    """M2 — 생산자(fetch)가 낼 수 있는 state 치역 전량이 사유 미상 폴백으로 새지 않는다."""
    reason_of = {"present": "stale"}  # present인데 미채택이면 게이트 사유가 반드시 있다
    for state in REEVAL_STATES:
        line = reeval_notice(
            {"reeval": {"state": state, "adopted": False, "reason": reason_of.get(state)}}
        )
        assert line.startswith("재평가 없음"), state
        assert "미상" not in line, state


def test_reeval_notice_survives_a_meta_without_reeval_info():
    assert "재평가 없음" in reeval_notice({"fetched_at": "t"})


def test_every_reeval_reject_reason_has_display_text():
    # 새 사유 코드를 추가하고 문구를 빠뜨리면 reeval_notice가 "사유 미상"을 낸다.
    assert set(_REEVAL_REJECT_TEXT) == set(REEVAL_REJECT_REASONS)


def test_render_report_puts_the_sample_composition_above_the_core_metrics():
    rows = [
        _enriched_row(cohort="unknown", label_bucket="unevaluable"),
        _enriched_row(
            crop_ref="job-1/row-1", cohort="current_bank", label_bucket="ok", top1_sim=0.9
        ),
        _enriched_row(
            crop_ref="job-1/row-2",
            cohort="no_label",
            label_bucket="unevaluable",
            canonical_label=None,
            in_bank=False,
        ),
    ]
    md = render_report(rows, {"fetched_at": "t"})
    assert md.index("## 표본 구성") < md.index("## 핵심 지표")
    assert "| unknown | 1 |" in md
    assert "| no_label | 1 |" in md
    assert "| current_bank | 1 |" in md
    # 분모는 총 3쌍이 아니라 평가 가능 1쌍(현재 라벨 텍스트 유지 — 기존 지표 표 규약).
    assert "| 품목 top-1 (평가 가능 쌍 기준) | 1/1 (100.0%) |" in md
    assert "뱅크 추가 후보는 코호트와 무관하게" in md
    assert "peer" in md  # score.md 포인터(중복 구현 회피)


def test_cohort_table_covers_every_cohort_a_pair_can_get():
    """표에 없는 코호트가 생기면 그 쌍들은 표에서 조용히 사라지고 합계가 안 맞는다."""
    assert {name for name, _ in COHORT_TABLE} == set(COHORTS) | {"no_label"}


def test_cohort_table_marks_are_derived_from_the_evaluable_constant():
    """M3 — ○/✗를 표에 손으로 적으면 ITEM_EVALUABLE_COHORTS가 바뀔 때 표만 옛말을 인쇄한다."""
    md = render_report([_enriched_row(label_bucket="ok", top1_sim=0.9)], {"fetched_at": "t"})
    for name, note in COHORT_TABLE:
        assert "○" not in note and "✗" not in note  # 설명문에는 마크가 없다(상수에서 도출)
        row = next(ln for ln in md.splitlines() if ln.startswith(f"| {name} |"))
        expected = "○" if name in ITEM_EVALUABLE_COHORTS else "✗"
        assert expected in row, name
        assert ("✗" if expected == "○" else "○") not in row, name


def test_sample_table_states_the_item_metric_denominator_and_row_missing():
    """M4 — ○ 코호트 합계와 품목 지표 분모가 어긋나는 이유(row_missing)를 표가 말해야 한다."""
    rows = [
        _enriched_row(cohort="current_bank", label_bucket="ok", top1_sim=0.9),
        _enriched_row(
            crop_ref="job-1/row-1",
            cohort="current_bank",
            label_bucket="row_missing",
            amount_bucket=None,
        ),
    ]
    md = render_report(rows, {"fetched_at": "t"})
    assert "| current_bank | 2 |" in md  # ○ 코호트 합계는 2인데
    assert "품목 지표 분모(평가 가능 쌍) 1쌍" in md  # 분모는 1이고
    assert "row_missing 1건" in md  # 그 차이의 출처를 밝힌다


def test_reeval_notice_line_is_its_own_paragraph():
    """L4 — 알림 2건이 빈 줄 없이 붙으면 마크다운에서 한 문단으로 병합돼 한 문장처럼 읽힌다."""
    md = render_report([_enriched_row(label_bucket="ok", top1_sim=0.9)], {"fetched_at": "t"})
    assert f"{reeval_notice({'fetched_at': 't'})}\n\n뱅크 추가 후보는 코호트와 무관하게" in md


def test_sample_table_rows_sum_to_the_included_count():
    rows = [
        _enriched_row(cohort="unknown", label_bucket="unevaluable"),
        _enriched_row(
            crop_ref="job-1/row-1", cohort="current_bank", label_bucket="ok", top1_sim=0.9
        ),
        _enriched_row(
            crop_ref="job-1/row-2",
            cohort="no_label",
            label_bucket="unevaluable",
            canonical_label=None,
            in_bank=False,
        ),
    ]
    s = summarize(rows)
    assert sum(s["cohorts"].values()) == s["n_included"]


def test_current_bank_coverage_line_has_a_nonzero_denominator_even_when_unevaluable():
    """뱅크 추가 후보 절 머리의 커버리지 줄은 코호트와 무관하게 라벨 있는 included 전체가 분모다."""
    rows = [
        _enriched_row(cohort="unknown", label_bucket="unevaluable", in_bank=True),
        _enriched_row(
            crop_ref="job-1/row-1", cohort="unknown", label_bucket="unevaluable", in_bank=False
        ),
    ]
    md = render_report(rows, {"fetched_at": "t"})
    assert "현재 뱅크 보유: 1/2" in md


def test_bank_coverage_line_is_its_own_paragraph_not_a_list_continuation():
    """H2 — 후보 불릿 바로 뒤에 붙으면 CommonMark lazy continuation으로 마지막 항목에 흡수돼
    그 라벨의 커버리지인 것처럼 렌더된다(계산은 맞는데 표시가 거짓이 된다).
    """
    rows = [
        _enriched_row(answer="새라벨", label_bucket="unevaluable", in_bank=False),
        _enriched_row(crop_ref="job-1/row-1", label_bucket="ok", in_bank=True, top1_sim=0.9),
    ]
    md = render_report(rows, {"fetched_at": "t"})
    assert "- 새라벨 ×1\n\n현재 뱅크 보유:" in md


def test_bank_coverage_line_keeps_its_blank_line_when_there_are_no_candidates():
    md = render_report([_enriched_row(label_bucket="ok", top1_sim=0.9)], {"fetched_at": "t"})
    assert "- 없음\n\n현재 뱅크 보유:" in md


def test_next_actions_hints_pull_images_with_explicit_jobs():
    """Task 8 리뷰 M2 이관 — 재평가 전에는 pull-images 기본 호출이 판정 불가 잡을 당기지
    않아 "가져올 이미지가 없습니다"만 나온다. 안내 문구는 실행 가능한 대안(--jobs)을
    가리켜야 한다(pull 대상 자체는 넓히지 않는다 — spec §5).
    """
    rows = [_enriched_row(cohort="unknown", label_bucket="unevaluable", in_bank=False)]
    md = render_report(rows, {"fetched_at": "t"})
    next_actions = md.split("## 다음 액션")[1]
    assert "--jobs" in next_actions
