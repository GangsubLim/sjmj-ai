"""tools.curation_enrich 순수 분석 계층 단위테스트 (ssh/DB 비의존, 합성 데이터만)."""

import re
from pathlib import Path

import pytest

from tests.conftest import (  # 합성 헬퍼는 렌더 계층 테스트와 공유한다
    BANK,
    _correction,
    _enrich,
    _enriched_row,
    _job,
    _pair,
    _reeval_record,
    _row,
)
from tools.curation_enrich import (
    CORRECTION_COLS,
    CORRECTIONS_SQL,
    PAIR_COLS,
    PAIRS_SQL,
    amount_bucket,
    is_row_balance_known,
    job_flags,
    label_bucket,
    oob_label_counts,
    parse_corrections_tsv,
    parse_jobs_tsv,
    parse_pairs_tsv,
    summarize,
    summarize_row_balance,
)

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


_CORRECTIONS_HEADER = "job_id\tn_corrections\trows_added\trows_dropped\tn_lines\timage_path"


def test_parse_corrections_tsv_derives_the_row_balance():
    text = _CORRECTIONS_HEADER + "\n7\t1\t3\t1\t12\t/data/up/7.jpeg"
    assert parse_corrections_tsv(text) == [
        {
            "job_id": 7,
            "n_corrections": 1,
            "has_correction": True,
            "rows_added": 3,
            "rows_dropped": 1,
            "n_lines": 12,
            "draft_rows": 13,  # n_lines + rows_dropped
            "confirmed_rows": 15,  # n_lines + rows_added
            "image_path": "/data/up/7.jpeg",
        }
    ]


def test_parse_corrections_tsv_marks_a_job_without_a_correction_row_as_unknown():
    """LEFT JOIN 미스(구 데이터·link_invoice 백필) — 0으로 접지 않는다(AC 5)."""
    text = _CORRECTIONS_HEADER + "\n7\t0\tNULL\tNULL\tNULL\t/data/up/7.jpeg"
    row = parse_corrections_tsv(text)[0]
    assert row["has_correction"] is False
    assert (row["rows_added"], row["rows_dropped"], row["n_lines"]) == (None, None, None)
    assert (row["draft_rows"], row["confirmed_rows"]) == (None, None)


def test_parse_corrections_tsv_keeps_has_correction_when_the_json_is_missing():
    """미상 두 종을 가른다 — 교정 행은 있는데 correction_json이 NULL이면 데이터 결손이다."""
    text = _CORRECTIONS_HEADER + "\n7\t1\tNULL\tNULL\tNULL\t/data/up/7.jpeg"
    row = parse_corrections_tsv(text)[0]
    assert row["has_correction"] is True
    assert row["n_lines"] is None


def test_parse_corrections_tsv_folds_a_partial_null_into_unknown():
    """세 값 중 하나만 NULL이어도 미상이다 — 반쪽 수지는 합계를 조용히 왜곡한다."""
    text = _CORRECTIONS_HEADER + "\n7\t1\t3\tNULL\t12\t/data/up/7.jpeg"
    assert parse_corrections_tsv(text)[0]["draft_rows"] is None


def test_parse_corrections_tsv_keeps_an_escaped_path_verbatim():
    """raw=False에서 mysql이 탭을 \\t로 이스케이프하므로 컬럼 경계는 밀리지 않는다.

    역이스케이프는 하지 않는다(parse_pairs_tsv와 같은 계약) — image_path는
    ocr_service.create_job이 uuid4().hex + 화이트리스트 suffix로 만들어 특수문자가
    구조적으로 들어가지 않는다. 이 테스트는 그 계약을 못박아 둔다.
    """
    text = _CORRECTIONS_HEADER + "\n7\t1\t0\t0\t5\t/data/up/a\\tb.jpeg"
    row = parse_corrections_tsv(text)[0]
    assert row["image_path"] == "/data/up/a\\tb.jpeg"
    assert row["n_lines"] == 5


def test_parse_corrections_tsv_maps_a_null_image_path_to_none():
    """image_path는 nullable(migration_007:30) — 문자열 'NULL'을 경로로 쓰면 cat이 죽는다."""
    text = _CORRECTIONS_HEADER + "\n7\t1\t0\t0\t5\tNULL"
    assert parse_corrections_tsv(text)[0]["image_path"] is None


def test_parse_corrections_tsv_surfaces_a_reconfirmed_job():
    """재확정(교정 2건) 잡 — SQL이 MAX(id) 1건만 주고, 중복 사실은 n_corrections로 남는다."""
    text = _CORRECTIONS_HEADER + "\n7\t2\t1\t0\t5\t/data/up/7.jpeg"
    row = parse_corrections_tsv(text)[0]
    assert row["n_corrections"] == 2
    assert row["has_correction"] is True
    assert row["confirmed_rows"] == 6


def test_corrections_sql_pins_the_dedup_subquery_and_left_join_shape():
    """MAX(c2.id) 서브쿼리·n_corrections 별칭·LEFT JOIN 존재를 문서로 고정한다.

    계약 검증이 아니라 문서 고정이다(M1) — CORRECTIONS_SQL 정의를 그대로 재진술하는 항진이라
    표기만 바뀌어도 깨질 뿐, 실제 위험(백엔드 predicate와의 분기·컬럼 순서 어긋남)은 각각
    test_corrections_sql_mirrors_the_backend_confirmed_predicate와
    test_corrections_cols_match_the_sql_alias_order가 구조적으로 검증한다.
    """
    assert "MAX(c2.id)" in CORRECTIONS_SQL
    assert "AS n_corrections" in CORRECTIONS_SQL
    assert "LEFT JOIN ocr_corrections" in CORRECTIONS_SQL  # 교정 행 부재가 미상으로 남는 자리


def test_parse_corrections_tsv_returns_empty_when_the_query_matched_nothing():
    """mysql --batch는 0행이면 헤더조차 찍지 않는다(parse_jobs_tsv와 같은 경계)."""
    assert parse_corrections_tsv("") == []


def test_corrections_sql_mirrors_the_backend_confirmed_predicate():
    """확정 모집단은 백엔드 `_UNCONFIRMED_WHERE`의 부정이다 — 세 predicate를 실제로 대조한다.

    두 곳이 갈라지면 리포트의 확정 잡 수와 처리 관측(/curation/pending)의 미확정 수의 합이
    전체와 어긋난다. ml/은 backend/를 import할 수 없어 상수 공유가 불가능하다(spec §3-1).
    문자열을 재진술하는 항진 대신 backend 소스를 직접 읽어 대조한다(M1) — backend는
    git-tracked라 fresh clone·CI에서도 경로가 성립한다
    (test_bank_update_shares_the_pairs_query와 같은 관용구).
    """
    backend_src = (
        Path(__file__).resolve().parent.parent.parent
        / "backend"
        / "app"
        / "repositories"
        / "ocr_repository.py"
    ).read_text(encoding="utf-8")
    assert "WHERE j.invoice_id IS NULL" in backend_src
    assert "NOT EXISTS (SELECT 1 FROM ocr_corrections c WHERE c.job_id = j.id)" in backend_src
    assert "NOT EXISTS (SELECT 1 FROM training_pairs tp WHERE tp.job_id = j.id)" in backend_src
    assert "j.invoice_id IS NOT NULL" in CORRECTIONS_SQL
    assert "EXISTS (SELECT 1 FROM ocr_corrections c2 WHERE c2.job_id = j.id)" in CORRECTIONS_SQL
    assert "EXISTS (SELECT 1 FROM training_pairs tp WHERE tp.job_id = j.id)" in CORRECTIONS_SQL


def test_corrections_cols_match_the_sql_alias_order():
    """SELECT 별칭 순서가 CORRECTION_COLS와 어긋나면 파서의 위치 인덱싱이 조용히 뒤바뀐다(M3).

    부분 순서 단언(예: n_lines가 image_path보다 앞) 대신 전체 별칭 시퀀스를 SSoT와 통째로
    대조한다 — rows_added/rows_dropped 자리가 바뀌어도 이 테스트가 즉시 잡는다
    (test_pair_cols_and_the_parsed_row_keys_are_one_contract와 같은 관용구).
    """
    aliases = tuple(re.findall(r"AS (\w+)", CORRECTIONS_SQL))
    assert aliases == CORRECTION_COLS


def test_parse_corrections_tsv_fails_fast_when_the_header_does_not_match_correction_cols():
    """SELECT 순서가 바뀌어도 헤더 대조가 위치 인덱싱의 조용한 뒤바뀜을 막는다(M3)."""
    bad_header = "job_id\tn_corrections\trows_dropped\trows_added\tn_lines\timage_path"
    with pytest.raises(ValueError, match="헤더 불일치"):
        parse_corrections_tsv(bad_header + "\n7\t1\t3\t1\t12\t/data/up/7.jpeg")


def test_parse_corrections_tsv_fails_fast_when_the_column_count_is_wrong():
    """컬럼 경계가 밀리면 조용한 오파싱 대신 즉시 실패한다(M2 — 형제 파서와 같은 계약)."""
    with pytest.raises(ValueError, match="컬럼 수"):
        parse_corrections_tsv(_CORRECTIONS_HEADER + "\n7\t1\t3\t1\t12")


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


def test_enrich_leaves_the_amount_bucket_none_when_the_truth_supply_is_absent():
    """금액 축 가드 — 정답 금액(training_pairs.supply)이 NULL이면 채점 자체가 성립하지 않는다.

    가드가 빠지면 `amount_bucket(draft, None)`이 `draft == final` 비교를 지나 `draft == -final`
    에서 `-None` TypeError로 리포트가 통째로 죽거나(draft 있음), draft=0일 때 zero_drift로
    오분류돼 그 잡이 warp_suspect로 오염된다. 품목 축은 이 가드와 무관하게 살아 있어야 한다
    (두 축 독립 — spec §8).
    """
    pairs = [_pair(supply=None)]
    enriched = _enrich(pairs, [_job(rows=[_row(top5=[("엔진오일", 0.9)], supply=0)])])
    assert enriched[0]["amount_bucket"] is None
    assert enriched[0]["draft_supply"] == 0  # 초안은 그대로 남아 검수자가 볼 수 있다
    assert enriched[0]["label_bucket"] == "ok"


def test_summarize_drops_pairs_without_a_truth_supply_from_the_amount_denominator():
    """분모 오염 회귀 — 정답 금액 부재 쌍이 amount_n에 들어가면 금액 일치율이 눌린다."""
    rows = [
        _enriched_row(label_bucket="ok", amount_bucket="ok", top1_sim=0.9),
        _enriched_row(crop_ref="job-1/row-1", label_bucket="ok", amount_bucket=None, supply=None),
    ]
    s = summarize(rows)
    assert (s["amount_n"], s["amount_ok"]) == (1, 1)


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


def _amount_job(*buckets, job_id=1):
    return [{"job_id": job_id, "status": "included", "amount_bucket": b} for b in buckets]


def test_job_flags_warp_suspect_on_majority_zero_drift():
    assert job_flags(_amount_job("zero_drift", "zero_drift", "ok"))[1] == ["warp_suspect"]


def test_job_flags_empty_when_amounts_ok():
    recs = [{"job_id": 2, "status": "included", "amount_bucket": "ok"}]
    assert job_flags(recs)[2] == []


def test_job_flags_pins_both_arms_of_the_warp_suspect_threshold():
    """양성 1건·전패 1건만으로는 하한(≥MIN_WARP_SUSPECT_BAD)도 비율 조건도 고정되지 않는다.

    실행으로 확인한 현재 동작을 그대로 못박는다(임계·연산자 변경은 운영 검수 대상 목록을
    바꾸므로 이 이슈의 범위 밖이다 — 그래서 "고정"이 목적이다):
      - 정확히 절반(2/4)은 켜진다 — `bad * 2 >= len(amts)`는 과반이 아니라 절반 이상이다.
      - 절반 미달(2/5)은 꺼진다.
      - 비율은 채우지만 절대 건수가 1건(1/1·1/2)이면 꺼진다 — 단일 오독은 잡 신호가 아니다.
    """
    assert job_flags(_amount_job("zero_drift", "degenerate", "ok", "ok"))[1] == ["warp_suspect"]
    assert job_flags(_amount_job("zero_drift", "zero_drift", "ok", "ok", "ok"))[1] == []
    assert job_flags(_amount_job("zero_drift"))[1] == []
    assert job_flags(_amount_job("degenerate", "ok"))[1] == []


def test_job_flags_ignores_rows_without_a_recorded_amount():
    """금액 미기재(None)는 분모에도 분자에도 들어가지 않는다 — 섞이면 비율 조건이 흔들린다."""
    recs = _amount_job("zero_drift", "zero_drift", None, None, None)
    assert job_flags(recs)[1] == ["warp_suspect"]


def test_job_flags_skips_excluded_pairs():
    recs = _amount_job("zero_drift", "zero_drift") + [
        {"job_id": 1, "status": "excluded", "amount_bucket": "ok"}
    ]
    assert job_flags(recs)[1] == ["warp_suspect"]  # excluded는 분모에 없다


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


def test_oob_label_counts_reads_the_normalized_answer_not_the_raw_canonical_label():
    """정규화 규칙은 한 곳(enrich_pairs의 strip)이며 집계는 그 결과(answer)만 읽는다.

    이름이 "공백 라벨 무시"였지만 이 함수는 공백을 판정하지 않는다 — 그 경로는
    test_enrich_treats_a_blank_canonical_label_like_a_missing_one이 닫는다. 여기서 고정할 것은
    원본 canonical_label이 공백으로 남아 있어도 판정에 쓰이지 않는다는 쪽이다:
    `r["canonical_label"] or r["answer"]` 같은 폴백이 들어오면 공백 라벨이 후보로 되살아난다.
    """
    recs = [{"status": "included", "in_bank": False, "canonical_label": "   ", "answer": ""}]
    assert oob_label_counts(recs) == []


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
    s = summarize(_enrich(pairs, [_job(rows=[])]))
    assert s["n_excluded"] == 3
    assert s["n_excluded_machine"] == 2
    assert s["n_excluded_human"] == 1


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
    s = summarize(_enrich(pairs, [_job(rows=[])]))
    assert s["n_reverted_machine"] == 3
    assert s["n_excluded_machine"] == 1


def test_summarize_breaks_down_reverted_reasons():
    """사유 2종 + 아직 배제 상태인 쌍으로 분해의 두 축을 함께 고정한다.

    사유가 한 종뿐이면 `{"blank_crop": n}`을 하드코딩한 구현도 통과하는데, ADR 0006은 사유가
    늘어난다고 전제한다(그때 이 분해가 곧 가드별 오탐 창구다 — 두 번째 값은 아직 없는 사유를
    가정한 합성값이다). 그리고 되돌림은 **included**만 세므로, 같은 사유로 아직 배제된 쌍이
    분해에 새지 않는지(status 필터)도 함께 본다 — 새면 blank_crop이 2가 아니라 3이 된다.
    """
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
            exclusion_reason="tiny_crop",
        ),
        _pair(
            id=4,
            crop_ref="job-1/row-3",
            row_index=3,
            status="excluded",
            exclusion_reason="blank_crop",
        ),
    ]
    s = summarize(_enrich(pairs, [_job(rows=[])]))
    assert s["reverted_reason_counts"] == {"blank_crop": 2, "tiny_crop": 1}


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


def test_summarize_reports_top5_in_bank_and_amount_breakdowns():
    """summarize의 절반(top5_hits·in_bank_top1/top5·amount_buckets·n_jobs)이 미검증이었다.

    분자·분모를 뒤바꾸거나 top5 판정에서 "ok"를 빠뜨리는 회귀가 리포트 표 전체를 오보한다.
    한 표본으로 네 축을 동시에 고정한다 — 잡 2개(in_bank 적중 1 · in_bank top5 1 · 뱅크 밖 1).
    """
    rows = [
        _enriched_row(label_bucket="ok", in_bank=True, top1_sim=0.9),
        _enriched_row(crop_ref="job-1/row-1", label_bucket="top5_only", in_bank=True, top1_sim=0.4),
        _enriched_row(
            job_id=2,
            crop_ref="job-2/row-0",
            label_bucket="out_of_bank",
            in_bank=False,
            top1_sim=0.3,
            amount_bucket="zero_drift",
        ),
    ]
    s = summarize(rows)
    assert s["n_jobs"] == 2
    assert (s["top1_hits"], s["top5_hits"]) == (1, 2)  # top5는 ok도 포함한다
    assert (s["in_bank_n"], s["in_bank_top1"], s["in_bank_top5"]) == (2, 1, 2)
    assert dict(s["amount_buckets"]) == {"ok": 2, "zero_drift": 1}
    assert (s["amount_n"], s["amount_ok"]) == (3, 2)


def test_summarize_keeps_a_zero_similarity_in_the_distribution():
    """C7 — truthiness 필터는 유사도 0.0을 관측 부재와 함께 버려 분포를 낙관 쪽으로 민다.

    0.0은 bank_update.score_one의 ranked[0][1]이 낼 수 있는 유효 관측치다(후보가 없을 때만
    None). 미스 쪽 max가 0.0으로, 적중 쪽 min이 0.0으로 내려가는 경계를 함께 고정한다.
    """
    rows = [
        _enriched_row(label_bucket="ok", top1_sim=0.0),
        _enriched_row(crop_ref="job-1/row-1", label_bucket="ok", top1_sim=1.0),
        _enriched_row(crop_ref="job-1/row-2", label_bucket="in_bank_miss", top1_sim=0.0),
    ]
    s = summarize(rows)
    assert (s["hit_sim_mean"], s["hit_sim_min"]) == (0.5, 0.0)
    assert (s["miss_sim_mean"], s["miss_sim_max"]) == (0.0, 0.0)


def test_summarize_leaves_similarity_stats_none_when_nothing_was_observed():
    """관측이 0건이면 None이다 — 0.0과 구분돼야 리포트가 유사도 줄을 아예 빼는 분기를 탄다."""
    s = summarize([_enriched_row(label_bucket="ok", top1_sim=None)])
    assert (s["hit_sim_mean"], s["hit_sim_min"]) == (None, None)
    assert (s["miss_sim_mean"], s["miss_sim_max"]) == (None, None)


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


# --- 행 수지 집계 (spec §4-2) ---


def test_correction_helper_matches_the_parser_shape():
    """합성 헬퍼가 파서 출력과 키 집합이 같은지 고정한다 — 갈라지면 렌더 테스트가 거짓이 된다.

    known 분기뿐 아니라 unknown 분기도 대조한다 — 다섯 값을 None으로 접는 규칙이
    curation_enrich.py와 여기 두 곳에 손으로 복제돼 있어, known만 고정하면 한쪽만
    바뀌어도 아무 테스트도 울리지 않는다.
    """
    hdr = "job_id\tn_corrections\trows_added\trows_dropped\tn_lines\timage_path\n"
    parsed = parse_corrections_tsv(hdr + "1\t1\t0\t0\t3\t/data/up/1.jpeg")[0]
    assert _correction().keys() == parsed.keys()
    assert _correction() == parsed

    parsed_null = parse_corrections_tsv(hdr + "1\t0\tNULL\tNULL\tNULL\t/data/up/1.jpeg")[0]
    assert _correction(n_lines=None, has_correction=False, n_corrections=0) == parsed_null


def test_is_row_balance_known_is_the_single_source_of_the_unknown_predicate():
    """M2 — 미상 판정의 SSoT. 집계와 렌더가 이 술어를 공유해야 모집단이 갈라지지 않는다."""
    assert is_row_balance_known(_correction(n_lines=3))
    assert is_row_balance_known(_correction(n_lines=0))  # 0행은 유효한 관측치다
    assert not is_row_balance_known(_correction(n_lines=None))


def test_summarize_row_balance_sums_only_the_known_jobs():
    corrections = [
        _correction(job_id=1, n_lines=10, rows_added=3, rows_dropped=1),
        _correction(job_id=2, n_lines=5, rows_added=0, rows_dropped=2),
        _correction(job_id=3, n_lines=None),  # 미상 — 합계에서 빠진다
    ]
    rb = summarize_row_balance(corrections)
    assert rb["n_confirmed_jobs"] == 3
    assert rb["n_lines"] == 15
    assert rb["rows_added"] == 3
    assert rb["rows_dropped"] == 3
    assert rb["draft_rows"] == 18  # (10+1) + (5+2)
    assert rb["confirmed_rows"] == 18  # (10+3) + (5+0)


def test_summarize_row_balance_splits_the_two_kinds_of_unknown():
    """AC 5 — 운영자의 후속 조치가 다르다(구 데이터 vs 데이터 결손)."""
    corrections = [
        _correction(job_id=1, n_lines=None, has_correction=False, n_corrections=0),
        _correction(job_id=2, n_lines=None, has_correction=False, n_corrections=0),
        _correction(job_id=3, n_lines=None, has_correction=True),
        _correction(job_id=4, n_lines=4),
    ]
    rb = summarize_row_balance(corrections)
    assert rb["n_unknown_jobs"] == 3
    assert rb["n_no_correction_jobs"] == 2
    # 결손 종은 집계 계층이 낸다 — 렌더가 뺄셈으로 파생하면 "렌더는 조립만" 원칙이 깨진다.
    assert rb["n_missing_json_jobs"] == 1


def test_summarize_row_balance_of_an_empty_population_is_all_zero():
    rb = summarize_row_balance([])
    assert rb == {
        "n_confirmed_jobs": 0,
        "n_unknown_jobs": 0,
        "n_no_correction_jobs": 0,
        "n_missing_json_jobs": 0,
        "n_multi_correction_jobs": 0,
        "n_lines": 0,
        "draft_rows": 0,
        "rows_added": 0,
        "rows_dropped": 0,
        "confirmed_rows": 0,
    }


def test_summarize_row_balance_counts_reconfirmed_jobs():
    """재확정 잡은 최신 1건만 읽었다는 사실이 수치로 남는다(중복은 SQL이 이미 걸렀다)."""
    rb = summarize_row_balance([_correction(job_id=1, n_corrections=2), _correction(job_id=2)])
    assert rb["n_multi_correction_jobs"] == 1


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


def test_reeval_carries_has_peer_when_the_label_is_unchanged():
    reeval = {"job-1/row-0": _reeval_record(label="엔진오일", has_peer=False)}
    enriched = _enrich([_pair()], [_job(rows=[_row()])], reeval=reeval)
    assert enriched[0]["reeval_has_peer"] is False
    assert enriched[0]["in_bank"] is True  # 커버리지 정의는 spec §3-C대로 불변


def test_reeval_has_peer_is_withheld_when_the_label_was_patched():
    reeval = {"job-1/row-0": _reeval_record(label="엔진오일", has_peer=False)}
    enriched = _enrich([_pair(canonical_label="안가방")], [_job(rows=[_row()])], reeval=reeval)
    assert enriched[0]["reeval_has_peer"] is None  # 채점 당시 라벨 기준이라 낡았다


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


def test_bank_update_shares_the_pairs_query_instead_of_inlining_its_own():
    """L6 — SQL 상수 하나 때문에 bank_update가 리포트 모듈에 의존할 이유가 없다.

    그 모듈은 fetch 글루·CLI에 더해 handwriting.bank_id까지 끌고 온다. SQL은 그 결과를 푸는
    파서(parse_pairs_tsv) 옆에 두는 것이 컬럼 계약과도 맞는다.

    예전 단언 둘은 계약을 비껴갔다: 하나는 PAIRS_SQL의 정의를 그대로 재진술한 항진이었고
    (정의를 고치면 기대도 함께 바뀌어 아무것도 못 잡는다), 다른 하나는 "리포트를 import하지
    않는다"만 봐서 **SQL 사본을 인라인해도** 통과했다. 지금은 공유 상수를 실제로 쓰는지를 본다.
    """
    src = (Path(__file__).resolve().parent.parent / "tools" / "bank_update.py").read_text(
        encoding="utf-8"
    )
    assert "curation_report" not in src
    assert "from tools.curation_enrich import" in src and "PAIRS_SQL" in src
    # 인라인 사본 금지 — 사본이 생기면 컬럼 계약이 파서와 조용히 갈라진다(docstring 언급은
    # SQL이 아니므로 SELECT와 같은 줄에 있는 것만 본다).
    assert [ln for ln in src.splitlines() if "SELECT" in ln and "training_pairs" in ln] == []


def test_pair_cols_and_the_parsed_row_keys_are_one_contract():
    """SQL이 파서 옆에 사는 이유 자체를 고정한다 — 컬럼 목록과 파서 산출 키가 한 벌이다.

    컬럼을 늘리고 parse_pairs_tsv를 안 고치면(또는 반대) 새 컬럼이 조용히 사라지거나 소비자가
    KeyError로 터진다. 합성 pair(`_pair`)는 파서 산출과 같은 shape이라 그 키로 대조한다.
    """
    assert {col.strip() for col in PAIR_COLS.split(",")} == set(_pair())
    assert PAIRS_SQL.endswith("ORDER BY job_id, row_index")  # 행 순서가 곧 검수 순서다
