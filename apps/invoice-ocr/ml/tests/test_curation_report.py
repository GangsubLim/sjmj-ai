"""tools.curation_report 순수 분석 계층 단위테스트 (ssh/DB 비의존, 합성 데이터만)."""

import json

import pytest

from tools.curation_report import (
    _load_enriched,
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
        "exclusion_reason": None,
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
        "canonical_label\tsupply\tstatus\texclusion_reason\treviewed_at\n"
        "7\tjob-3/row-1\t3\t1\t드라이\t드럼\t드럼\tNULL\tincluded\tNULL\tNULL"
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
            "exclusion_reason": None,
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
        {"status": "included", "label_bucket": "out_of_bank", "canonical_label": "중고"},
        {"status": "included", "label_bucket": "out_of_bank", "canonical_label": "중고"},
        {"status": "included", "label_bucket": "out_of_bank", "canonical_label": "안가방"},
        {"status": "included", "label_bucket": "ok", "canonical_label": "공임"},
    ]
    assert oob_label_counts(recs) == [("중고", 2), ("안가방", 1)]


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


def test_summarize_splits_excluded_by_owner():
    # 기계배제 2 / 사람배제 1로 비대칭을 둔다 — is not None/is None 조건이
    # 서로 바뀌면 2/1이 아니라 1/2가 나와 즉시 드러나도록.
    pairs = [
        _pair(id=1, crop_ref="job-1/row-0", status="excluded", exclusion_reason="blank_crop"),
        _pair(
            id=2,
            crop_ref="job-1/row-1",
            row_index=1,
            status="excluded",
            exclusion_reason="blank_crop",
        ),
        _pair(id=3, crop_ref="job-1/row-2", row_index=2, status="excluded", exclusion_reason=None),
        _pair(id=4, crop_ref="job-1/row-3", row_index=3),
    ]
    enriched = enrich_pairs(pairs, [_job(rows=[])], BANK)
    s = summarize(enriched)
    assert s["n_excluded"] == 3
    assert s["n_excluded_machine"] == 2
    assert s["n_excluded_human"] == 1


def test_render_report_splits_excluded_sections():
    # 기계배제 2 (row-0, row-1) / 사람배제 1 (row-2)로 비대칭을 두고, 각 crop_ref가
    # 어느 섹션 "아래"에 찍히는지(섹션 경계로 분할한 뒤 부분 문자열 검사)까지 확인한다.
    # 두 리스트 필터가 뒤바뀌어도 헤더 존재만 보면 통과했을 취약점을 막는다.
    pairs = [
        _pair(id=1, crop_ref="job-1/row-0", status="excluded", exclusion_reason="blank_crop"),
        _pair(
            id=2,
            crop_ref="job-1/row-1",
            row_index=1,
            status="excluded",
            exclusion_reason="blank_crop",
        ),
        _pair(id=3, crop_ref="job-1/row-2", row_index=2, status="excluded", exclusion_reason=None),
    ]
    enriched = enrich_pairs(pairs, [_job(rows=[])], BANK)
    md = render_report(enriched, {"fetched_at": "t"})
    machine_section = md.split("## excluded — 기계 자동 배제")[1].split("## excluded — 사람 배제")[
        0
    ]
    human_section = md.split("## excluded — 사람 배제")[1]
    assert "job-1/row-0" in machine_section
    assert "job-1/row-1" in machine_section
    assert "job-1/row-2" not in machine_section
    assert "job-1/row-2" in human_section
    assert "job-1/row-0" not in human_section
    assert "job-1/row-1" not in human_section


def test_summarize_counts_machine_exclusions_reverted_by_human():
    # 되돌림 3 / 기계배제 1로 비대칭을 둔다 — status/exclusion_reason 조건이 서로
    # 바뀌면 3/1이 아니라 다른 값이 나와 즉시 드러나도록.
    pairs = [
        _pair(id=1, crop_ref="job-1/row-0", status="included", exclusion_reason="blank_crop"),
        _pair(
            id=2,
            crop_ref="job-1/row-1",
            row_index=1,
            status="included",
            exclusion_reason="blank_crop",
        ),
        _pair(
            id=3,
            crop_ref="job-1/row-2",
            row_index=2,
            status="included",
            exclusion_reason="blank_crop",
        ),
        _pair(
            id=4,
            crop_ref="job-1/row-3",
            row_index=3,
            status="excluded",
            exclusion_reason="blank_crop",
        ),
    ]
    s = summarize(enrich_pairs(pairs, [_job(rows=[])], BANK))
    assert s["n_reverted_machine"] == 3
    assert s["n_excluded_machine"] == 1


def test_render_report_shows_reverted_section():
    pairs = [_pair(id=1, crop_ref="job-1/row-0", status="included", exclusion_reason="blank_crop")]
    md = render_report(enrich_pairs(pairs, [_job(rows=[])], BANK), {"fetched_at": "t"})
    section = "## included — 기계 자동 배제를 사람이 되돌림"
    assert section in md
    assert "job-1/row-0" in md.split(section)[1]


def test_render_report_hides_reverted_section_when_none_reverted():
    # 되돌림 0건이면 섹션 자체가 없어야 한다 — 지운 것과 동치인 RED를 방지하는 음성 테스트.
    pairs = [_pair(id=1, crop_ref="job-1/row-0", status="included", exclusion_reason=None)]
    rows = [_row(top5=[("엔진오일", 0.9)])]
    md = render_report(enrich_pairs(pairs, [_job(rows=rows)], BANK), {"fetched_at": "t"})
    assert "기계 자동 배제를 사람이 되돌림" not in md


def test_summarize_breaks_down_reverted_reasons():
    pairs = [
        _pair(id=1, crop_ref="job-1/row-0", status="included", exclusion_reason="blank_crop"),
        _pair(
            id=2,
            crop_ref="job-1/row-1",
            row_index=1,
            status="included",
            exclusion_reason="blank_crop",
        ),
    ]
    s = summarize(enrich_pairs(pairs, [_job(rows=[])], BANK))
    assert s["reverted_reason_counts"] == {"blank_crop": 2}


def test_render_report_shows_machine_exclusion_false_positive_rate():
    # 분모(기계 판정 총계 = 기계배제 + 되돌림) 없이 되돌림 절대수만 찍으면
    # 리포트 간 비교로 임계를 조정하려는 사람이 오판할 수 있다(M3).
    pairs = [
        _pair(id=1, crop_ref="job-1/row-0", status="included", exclusion_reason="blank_crop"),
        _pair(
            id=2,
            crop_ref="job-1/row-1",
            row_index=1,
            status="excluded",
            exclusion_reason="blank_crop",
        ),
        _pair(
            id=3,
            crop_ref="job-1/row-2",
            row_index=2,
            status="excluded",
            exclusion_reason="blank_crop",
        ),
    ]
    md = render_report(enrich_pairs(pairs, [_job(rows=[])], BANK), {"fetched_at": "t"})
    assert "빈 크롭 가드 오탐" in md
    assert "1/3" in md


def test_load_enriched_fails_fast_on_stale_pairs_cache(tmp_path):
    """exclusion_reason 컬럼 이전 캐시(구버전 fetch 산출물)는 조용히 0으로 세지 말고 fail-fast."""
    stale_pair = {
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
        # exclusion_reason 키 없음(구버전 fetch)
    }
    (tmp_path / "pairs.json").write_text(json.dumps([stale_pair]))
    (tmp_path / "jobs.json").write_text("[]")
    (tmp_path / "bank.json").write_text(json.dumps({"size": 0, "counts": {}}))
    (tmp_path / "meta.json").write_text("{}")
    with pytest.raises(ValueError, match="구버전"):
        _load_enriched(tmp_path)


def test_render_report_smoke_contains_key_sections():
    pairs = [_pair(), _pair(id=2, crop_ref="job-1/row-1", row_index=1, final_label="안가방")]
    rows = [_row(top5=[("엔진오일", 0.9)]), _row(idx=1, top5=[("드라이", 0.7)], supply=0, raw="0")]
    enriched = enrich_pairs(pairs, [_job(rows=rows)], BANK)
    md = render_report(enriched, {"fetched_at": "2026-07-27T00:00:00", "bank_distinct": 4})
    assert "핵심 지표" in md
    assert "뱅크 추가 후보" in md
    assert "안가방" in md
    assert "잡별" in md
