"""tools.curation_cohort 순수 판정 계층 단위테스트 (IO 비의존, 합성 데이터만)."""

import json

import pytest

from tools.curation_cohort import (
    COHORTS,
    DATA_INTEGRITY_FAILURE_BUCKETS,
    REEVAL_REJECT_REASONS,
    TEMPORAL_UNEVALUABLE_BUCKETS,
    UNEVALUABLE_BUCKETS,
    is_amount_failure,
    is_item_evaluable,
    is_item_failure,
    parse_reeval_jsonl,
    reeval_gate,
    sample_cohort,
)

# --- 코호트 판정 (시점 정합의 핵심 — spec §3-C) ---


def test_cohort_is_reevaluated_when_a_valid_reeval_record_exists():
    # has_reeval은 "유효성 게이트를 통과한 재평가에 그 쌍이 있다"는 뜻이다(§3-C).
    assert (
        sample_cohort(job_retrieval_version="old", current_retrieval_version="cur", has_reeval=True)
        == "reevaluated"
    )
    assert (
        sample_cohort(job_retrieval_version=None, current_retrieval_version="cur", has_reeval=True)
        == "reevaluated"
    )


def test_cohort_is_unknown_when_the_job_has_no_stamp():
    assert (
        sample_cohort(job_retrieval_version=None, current_retrieval_version="cur", has_reeval=False)
        == "unknown"
    )
    assert (
        sample_cohort(job_retrieval_version="", current_retrieval_version="cur", has_reeval=False)
        == "unknown"
    )


def test_cohort_is_current_bank_when_the_stamp_matches_now():
    assert (
        sample_cohort(
            job_retrieval_version="cur", current_retrieval_version="cur", has_reeval=False
        )
        == "current_bank"
    )


def test_cohort_is_stale_bank_when_the_stamp_differs():
    assert (
        sample_cohort(
            job_retrieval_version="old", current_retrieval_version="cur", has_reeval=False
        )
        == "stale_bank"
    )


def test_cohort_is_stale_bank_when_the_current_fingerprint_is_unknown():
    # 현재 지문을 못 얻은 상태에서 "같다"고 볼 근거가 없다 — fail-closed.
    assert (
        sample_cohort(job_retrieval_version="old", current_retrieval_version=None, has_reeval=False)
        == "stale_bank"
    )


def test_sample_cohort_range_matches_cohorts_bijectively():
    """H2 — sample_cohort의 실제 반환값 집합이 COHORTS와 양방향으로 일치하는지 전수로 잡는다.

    COHORTS는 이제 Cohort literal에서 get_args로 도출돼 진실원이 하나지만, 그 타입 힌트는
    런타임에 강제되지 않는다. 5번째 분기를 추가하고 COHORTS를 잊으면(반환값이 COHORTS 밖으로
    새는 경우) 또는 반대로 COHORTS에 넣고 분기를 잊으면(어떤 코호트가 영영 반환되지 않는 경우)
    이 테스트가 잡는다 — 항등식 하나만 보는 예전 테스트는 sample_cohort를 호출조차 하지
    않아 이 두 가지 회귀 모두 놓쳤다.
    """
    stamps = (None, "", "old", "cur")
    currents = (None, "cur")
    reevals = (True, False)
    outputs = {
        sample_cohort(
            job_retrieval_version=stamp,
            current_retrieval_version=current,
            has_reeval=has_reeval,
        )
        for stamp in stamps
        for current in currents
        for has_reeval in reevals
    }
    assert outputs == set(COHORTS)


def test_sample_cohort_rejects_positional_arguments():
    """M7 — 동종 타입(str|None, str|None) 2인자 + bool 위치 시그니처는 두 지문을 뒤바꿔
    넘겨도 예외 없이 unknown/stale_bank만 어긋난다. 키워드 전용으로 막는다.
    """
    with pytest.raises(TypeError):
        sample_cohort("old", "cur", False)


def test_unevaluable_buckets_is_the_union_of_the_two_concern_axes():
    """M3 — 데이터 정합 축과 시점 판정 축이 합쳐진 상수라는 계약을 고정한다."""
    assert set(UNEVALUABLE_BUCKETS) == set(TEMPORAL_UNEVALUABLE_BUCKETS) | set(
        DATA_INTEGRITY_FAILURE_BUCKETS
    )


# --- 판정 술어 (판정 불가는 실패가 아니다 — spec §3-C) ---


def _row_for_predicates(label_bucket="ok", amount_bucket="ok"):
    return {"label_bucket": label_bucket, "amount_bucket": amount_bucket}


def test_is_item_evaluable_excludes_unevaluable_and_row_missing():
    assert is_item_evaluable(_row_for_predicates("ok")) is True
    assert is_item_evaluable(_row_for_predicates("out_of_bank")) is True
    assert is_item_evaluable(_row_for_predicates("unevaluable")) is False
    assert is_item_evaluable(_row_for_predicates("row_missing")) is False


def test_is_item_failure_is_false_for_unevaluable_items_with_healthy_amounts():
    assert is_item_failure(_row_for_predicates("unevaluable", "ok")) is False


def test_row_missing_is_excluded_from_performance_but_stays_an_operational_failure():
    """데이터 정합 장애는 성능 분모에서 빠지지만 실패 목록에서 사라지면 안 된다(신호 상실)."""
    row = _row_for_predicates("row_missing", None)
    assert is_item_evaluable(row) is False
    assert is_item_failure(row) is True


def test_is_item_failure_keeps_amount_failures_even_when_the_item_is_unevaluable():
    # 두 축은 독립이고(금액 버킷은 뱅크와 무관), 재평가 전 유일하게 살아 있는 검수 루프다.
    assert is_item_failure(_row_for_predicates("unevaluable", "zero_drift")) is True
    assert is_item_failure(_row_for_predicates("row_missing", "degenerate")) is True


def test_is_item_failure_is_true_for_an_evaluable_item_miss():
    assert is_item_failure(_row_for_predicates("in_bank_miss", "ok")) is True
    assert is_item_failure(_row_for_predicates("ok", "ok")) is False


def test_is_item_failure_is_false_for_an_ok_item_with_no_amount_recorded():
    """M6 — enrich_pairs는 금액 미기재(supply is None)에 label_bucket과 무관하게
    amount_bucket=None을 낸다(실데이터 경로). `(None, "ok")`에서 None을 지우는 뮤턴트가
    이 테스트 없이는 통과했다 — 회귀하면 금액 미기재 쌍 전부가 실패로 계상된다.
    """
    assert is_item_failure(_row_for_predicates("ok", None)) is False


def test_is_amount_failure_true_for_a_failure_bucket():
    """M1 — 금액 실패 판정을 술어 하나로 굳힌다(그 전엔 두 자리에 서로 다른 문법이 인라인됐다)."""
    assert is_amount_failure({"amount_bucket": "zero_drift"}) is True
    assert is_amount_failure({"amount_bucket": "degenerate"}) is True
    assert is_amount_failure({"amount_bucket": "sign_mismatch"}) is True
    assert is_amount_failure({"amount_bucket": "misread"}) is True


def test_is_amount_failure_false_for_ok_or_unrecorded():
    assert is_amount_failure({"amount_bucket": "ok"}) is False
    assert is_amount_failure({"amount_bucket": None}) is False


# --- 재평가 유효성 게이트 (spec §3-C — 채택 조건 넷) ---

_CUR = "cur-fingerprint"


def _reeval_record(crop_ref="job-1/row-0", side="after", axis="invoice", **over):
    base = {
        "side": side,
        "axis": axis,
        "crop_ref": crop_ref,
        "label": "안가방",
        "in_bank": True,
        "top1": True,
        "top5": True,
        "has_peer": True,
        "preds": ["안가방", "공임"],
        "top1_sim": 0.91,
    }
    return {**base, **over}


def _four_vintages(crop_ref="job-1/row-0"):
    """score.jsonl은 같은 crop_ref에 (side, axis) 4벌을 담는다(bank_update.py:845-853)."""
    return [
        _reeval_record(crop_ref, side=side, axis=axis)
        for side in ("before", "after")
        for axis in ("crop_ref", "invoice")
    ]


def _reeval_meta(**over):
    base = {
        "generated_at": "2026-07-30T05:12:00+09:00",
        "scope": "all",
        "axes": ["crop_ref", "invoice"],
        "n_pairs": 1,
        "retrieval_version": {"before": "old", "after": _CUR},
        "score_jsonl_sha256": "digest-1",
    }
    return {**base, **over}


def test_parse_reeval_jsonl_reads_records_and_fails_fast_on_broken_lines():
    text = '{"side": "after", "axis": "invoice", "crop_ref": "job-1/row-0"}\n'
    assert parse_reeval_jsonl(text)[0]["axis"] == "invoice"
    with pytest.raises(json.JSONDecodeError):
        parse_reeval_jsonl("{not json}\n")


def test_parse_reeval_jsonl_rejects_a_line_that_is_not_an_object():
    """M3 — '123\\n'이 [123]으로 통과하면 게이트 안쪽에서 AttributeError가 난다.

    근거로 든 parse_jobs_tsv는 경계에서 형식을 명시적으로 검사해 조용한 오파싱을 막는다 —
    원격에서 회수한 신뢰 못 할 입력이므로 같은 선례를 따른다.
    """
    with pytest.raises(ValueError, match="객체"):
        parse_reeval_jsonl("123\n")


def test_reeval_gate_picks_the_after_invoice_axis_record():
    gate = reeval_gate(
        records=_four_vintages(),
        meta=_reeval_meta(),
        current_retrieval_version=_CUR,
        jsonl_sha256="digest-1",
    )
    assert gate.reason is None
    assert set(gate.pairs) == {"job-1/row-0"}
    picked = gate.pairs["job-1/row-0"]
    assert (picked["side"], picked["axis"]) == ("after", "invoice")


def test_reeval_gate_treats_missing_meta_as_no_reeval():
    """서버의 #53 이전 산출물 회귀 — score.jsonl 70줄이 남아 있고 axis 키도 meta도 없다.

    이 게이트 하나로 구 산출물이 전부 걸러지므로 axis 키 유무를 보는 폴백 분기를 두지 않는다.
    """
    gate = reeval_gate(
        records=_four_vintages(),
        meta=None,
        current_retrieval_version=_CUR,
        jsonl_sha256="digest-1",
    )
    assert gate.pairs is None and gate.reason == "no_meta"


def test_reeval_gate_rejects_a_stale_reeval():
    meta = _reeval_meta(retrieval_version={"before": "old", "after": "older"})
    gate = reeval_gate(
        records=_four_vintages(),
        meta=meta,
        current_retrieval_version=_CUR,
        jsonl_sha256="digest-1",
    )
    assert gate.pairs is None and gate.reason == "stale"


def test_reeval_gate_rejects_when_both_fingerprints_are_unknown():
    # None == None을 "일치"로 읽으면 fail-open이다 — 값이 문자열이고 서로 같아야 한다.
    meta = _reeval_meta(retrieval_version={"before": None, "after": None})
    gate = reeval_gate(
        records=_four_vintages(),
        meta=meta,
        current_retrieval_version=None,
        jsonl_sha256="digest-1",
    )
    assert gate.pairs is None and gate.reason == "no_fingerprint"


def test_reeval_gate_rejects_a_digest_mismatch():
    gate = reeval_gate(
        records=_four_vintages(),
        meta=_reeval_meta(),
        current_retrieval_version=_CUR,
        jsonl_sha256="digest-other",
    )
    assert gate.pairs is None and gate.reason == "digest_mismatch"


def test_reeval_gate_rejects_a_truncated_rerun_by_record_count():
    """중단된 재실행 회귀 — 다이제스트와 독립인 두 번째 그물(n_pairs × 2 × len(axes))."""
    gate = reeval_gate(
        records=_four_vintages()[:3],
        meta=_reeval_meta(),
        current_retrieval_version=_CUR,
        jsonl_sha256="digest-1",
    )
    assert gate.pairs is None and gate.reason == "record_count"


def test_reeval_gate_fails_fast_on_duplicate_unique_keys():
    # crop_ref만 키로 잡으면 순서에 따라 다른 레코드가 이겨 조용히 뒤바뀐다 — 덮어쓰기 금지.
    records = _four_vintages() + [_reeval_record(top1=False)]
    with pytest.raises(ValueError, match="중복"):
        reeval_gate(
            records=records,
            meta=_reeval_meta(n_pairs=1, axes=["crop_ref", "invoice"]),
            current_retrieval_version=_CUR,
            jsonl_sha256="digest-1",
        )


def test_reeval_gate_rejects_a_single_axis_artifact_with_a_reason():
    """axes가 전표 축을 주장하지 않는다 = 손상이 아니라 축이 하나뿐인 산출물 → 사유 기각."""
    records = [
        _reeval_record("job-1/row-0", side="before", axis="crop_ref"),
        _reeval_record("job-1/row-0", side="after", axis="crop_ref"),
        _reeval_record("job-2/row-0", side="before", axis="crop_ref"),
        _reeval_record("job-2/row-0", side="after", axis="crop_ref"),
    ]
    gate = reeval_gate(
        records=records,
        meta=_reeval_meta(n_pairs=2, axes=["crop_ref"]),
        current_retrieval_version=_CUR,
        jsonl_sha256="digest-1",
    )
    assert gate.pairs is None and gate.reason == "no_invoice_axis"


def test_reeval_gate_fails_fast_when_axes_claim_the_invoice_axis_but_records_lack_it():
    """axes에 전표 축이 있다고 적혀 있는데 레코드가 없다 = 산출물 손상 → 예외."""
    records = [
        _reeval_record(cr, side=side, axis="crop_ref")
        for cr in ("job-1/row-0", "job-2/row-0")
        for side in ("before", "after")
    ] + [
        _reeval_record(cr, side=side, axis="crop_ref_dup")
        for cr in ("job-1/row-0", "job-2/row-0")
        for side in ("before", "after")
    ]
    with pytest.raises(ValueError, match="전표 축"):
        reeval_gate(
            records=records,
            meta=_reeval_meta(n_pairs=2, axes=["crop_ref", "invoice"]),
            current_retrieval_version=_CUR,
            jsonl_sha256="digest-1",
        )


def test_reeval_gate_rejects_when_a_pair_is_replaced_by_a_wrong_side_axis_combo():
    records = _four_vintages("job-1/row-0") + _four_vintages("job-2/row-0")
    records[-1] = _reeval_record("job-3/row-0", side="after", axis="crop_ref")  # invoice 1건 소실
    gate = reeval_gate(
        records=records,
        meta=_reeval_meta(n_pairs=2, axes=["crop_ref", "invoice"]),
        current_retrieval_version=_CUR,
        jsonl_sha256="digest-1",
    )
    assert gate.pairs is None and gate.reason == "pair_count"


def test_reeval_gate_rejects_an_unexpected_side_axis_combination():
    records = _four_vintages()
    records[0] = _reeval_record(side="sideways", axis="invoice")
    gate = reeval_gate(
        records=records,
        meta=_reeval_meta(),
        current_retrieval_version=_CUR,
        jsonl_sha256="digest-1",
    )
    assert gate.pairs is None and gate.reason == "record_shape"


def test_reeval_gate_treats_an_empty_scoring_run_as_no_reeval():
    """H1 — 채점 대상 0건은 정상 산출물이다(손상이 아니다).

    cmd_score는 --scope 필터·크롭 부재로 valid를 0건까지 줄일 수 있고, score_summary([])와
    _pct(n=0)이 0건을 의도적으로 처리해 빈 score.jsonl + n_pairs=0 meta를 정상적으로 남긴다.
    그 산출물이 "전표 축 레코드 0건 = 손상" 예외로 리포트 전체를 죽이면, 게이트 과민이
    정상 상태에서 도구를 멈춘다.
    """
    gate = reeval_gate(
        records=[],
        meta=_reeval_meta(n_pairs=0),
        current_retrieval_version=_CUR,
        jsonl_sha256="digest-1",
    )
    assert gate.pairs is None and gate.reason == "no_records"


def test_reeval_gate_rejects_positional_arguments():
    """H3 — current_retrieval_version과 jsonl_sha256은 인접 동종(str|None)이다.

    뒤바꿔 호출하면 예외 없이 stale/digest_mismatch로 재평가 전량이 폐기되고 운영자는 잘못된
    원인을 보고 무의미한 재채점을 돌린다. sample_cohort가 같은 위험 때문에 이미 키워드
    전용을 강제하고 위치 인자 거부 테스트를 걸어놨다 — 같은 선례를 따른다.
    """
    with pytest.raises(TypeError):
        reeval_gate(_four_vintages(), _reeval_meta(), _CUR, "digest-1")


def test_reeval_gate_rejects_when_only_the_current_fingerprint_is_unknown():
    """L2 — after는 유효 문자열이고 현재 지문만 None인 경우가 어느 사유인지 고정한다."""
    gate = reeval_gate(
        records=_four_vintages(),
        meta=_reeval_meta(),
        current_retrieval_version=None,
        jsonl_sha256="digest-1",
    )
    assert gate.pairs is None and gate.reason == "no_fingerprint"


def test_reeval_gate_rejects_a_meta_whose_retrieval_version_is_not_a_mapping():
    """M2 — meta 필드 타입 불일치가 AttributeError로 새면 손상 채널과 구분되지 않는다."""
    gate = reeval_gate(
        records=_four_vintages(),
        meta=_reeval_meta(retrieval_version=_CUR),
        current_retrieval_version=_CUR,
        jsonl_sha256="digest-1",
    )
    assert gate.pairs is None and gate.reason == "no_fingerprint"


def test_reeval_gate_rejects_a_meta_whose_n_pairs_is_not_an_int():
    """M2 — int()의 ValueError는 Raises가 "손상 2종"으로 규정한 채널에 섞이면 안 된다."""
    gate = reeval_gate(
        records=_four_vintages(),
        meta=_reeval_meta(n_pairs="1"),
        current_retrieval_version=_CUR,
        jsonl_sha256="digest-1",
    )
    assert gate.pairs is None and gate.reason == "bad_meta"


def test_reeval_gate_rejects_when_both_digests_are_absent():
    """M4 — 지문 검사는 양쪽 문자열을 요구했는데 다이제스트 검사만 None == None을 통과시켰다."""
    gate = reeval_gate(
        records=_four_vintages(),
        meta=_reeval_meta(score_jsonl_sha256=None),
        current_retrieval_version=_CUR,
        jsonl_sha256=None,
    )
    assert gate.pairs is None and gate.reason == "digest_mismatch"


def test_reeval_gate_fails_fast_when_a_record_lacks_the_crop_ref_key():
    """M2 — 키 누락이 "유일키 중복: (…, None)"으로 오보되면 사람이 원인을 오독한다."""
    records = _four_vintages()
    del records[3]["crop_ref"]
    with pytest.raises(ValueError, match="crop_ref"):
        reeval_gate(
            records=records,
            meta=_reeval_meta(),
            current_retrieval_version=_CUR,
            jsonl_sha256="digest-1",
        )


def test_reeval_gate_reject_reasons_match_the_literal_bijectively():
    """H2 — 실제 반환 사유 집합 == REEVAL_REJECT_REASONS를 양방향으로 고정한다.

    사유가 bare 문자열 리터럴이면 반환 오타("stale_bank" vs "stale")나 사유 추가 누락을 잡는
    장치가 0이다 — 리포트가 사람에게 잘못된 원인을 알리는 경로다. Cohort/COHORTS 선례처럼
    Literal을 진실원으로 두고, 치역이 상수 밖으로 새거나(오타) 상수에만 있고 아무도 반환하지
    않는(dead 사유) 두 회귀를 전수 호출로 잡는다.
    """
    cases = [
        (_four_vintages(), None, _CUR, "digest-1"),
        (_four_vintages(), _reeval_meta(retrieval_version={"after": None}), None, "digest-1"),
        (_four_vintages(), _reeval_meta(retrieval_version={"after": "older"}), _CUR, "digest-1"),
        (_four_vintages(), _reeval_meta(), _CUR, "digest-other"),
        (_four_vintages(), _reeval_meta(n_pairs="1"), _CUR, "digest-1"),
        ([], _reeval_meta(n_pairs=0), _CUR, "digest-1"),
        (_four_vintages()[:3], _reeval_meta(), _CUR, "digest-1"),
        (_four_vintages(), _reeval_meta(axes=["crop_ref"]), _CUR, "digest-1"),
        (
            [_reeval_record(side="sideways", axis="invoice"), *_four_vintages()[1:]],
            _reeval_meta(),
            _CUR,
            "digest-1",
        ),
        (
            [*_four_vintages("job-1/row-0"), *_four_vintages("job-2/row-0")[:3]]
            + [_reeval_record("job-3/row-0", side="after", axis="crop_ref")],
            _reeval_meta(n_pairs=2),
            _CUR,
            "digest-1",
        ),
    ]
    reasons = {
        reeval_gate(
            records=records,
            meta=meta,
            current_retrieval_version=current,
            jsonl_sha256=digest,
        ).reason
        for records, meta, current, digest in cases
    }
    assert reasons == set(REEVAL_REJECT_REASONS)
