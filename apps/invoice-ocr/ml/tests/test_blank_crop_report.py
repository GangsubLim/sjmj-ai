"""tools.blank_crop_report 순수 계층 단위테스트(ssh/cv2 비의존, 합성 데이터만)."""

import pytest

from tools.blank_crop_calib import (
    STATUS_CROP_MISSING,
    STATUS_CROP_UNREADABLE,
    STATUS_OK,
    count_exact_zero_ink,
    crop_status,
    label_margin,
    load_labels,
    read_labels_csv,
    render_blank_report,
)
from tools.blank_crop_report import (
    PairUpdate,
    apply_exit_code,
    build_apply_script,
    classify_affected,
    parse_apply_output,
    parse_pairs_tsv,
    plan_updates,
    select_targets,
)

THRESHOLD = 0.01


def _rec(
    crop_ref="job-1/row-0",
    *,
    ratio=0.5,
    crop_status=STATUS_OK,
    pair_status="included",
    reason=None,
    pair_id=1,
    job_id=1,
):
    return {
        "id": pair_id,
        "crop_ref": crop_ref,
        "job_id": job_id,
        "pair_status": pair_status,
        "exclusion_reason": reason,
        "curation_reviewed": False,
        "crop_status": crop_status,
        "ratio": ratio,
    }


# --- TSV 파싱 ---


def test_parse_pairs_tsv_converts_types_and_null():
    text = (
        "id\tcrop_ref\tjob_id\tstatus\texclusion_reason\tcuration_reviewed\n"
        "7\tjob-3/row-1\t3\texcluded\tblank_crop\t1\n"
        "8\tjob-3/row-2\t3\tincluded\tNULL\t0"
    )
    assert parse_pairs_tsv(text) == [
        {
            "id": 7,
            "crop_ref": "job-3/row-1",
            "job_id": 3,
            "pair_status": "excluded",
            "exclusion_reason": "blank_crop",
            "curation_reviewed": True,
        },
        {
            "id": 8,
            "crop_ref": "job-3/row-2",
            "job_id": 3,
            "pair_status": "included",
            "exclusion_reason": None,
            "curation_reviewed": False,
        },
    ]


@pytest.mark.parametrize(
    "bad_ref",
    [
        "../../etc/passwd",
        "job-3/../../row-1",
        "job-3/row-1|깨는값",
        "job-3/row-1\r",
        "",
    ],
)
def test_parse_pairs_tsv_rejects_crop_ref_outside_the_job_row_shape(bad_ref):
    # M6: crop_ref는 crop_path가 `cache/crops/<ref>.png`로 조립하는 값이자 마크다운 표에
    # 그대로 실리는 값이다 — DB 원문을 무검증으로 통과시키면 캐시 밖 파일의 잉크율로
    # 판정이 갈리고(`../`), `|`·개행이 리포트 표를 깨뜨린다.
    text = (
        "id\tcrop_ref\tjob_id\tstatus\texclusion_reason\tcuration_reviewed\n"
        f"7\t{bad_ref}\t3\tincluded\tNULL\t0"
    )
    with pytest.raises(ValueError, match="crop_ref"):
        parse_pairs_tsv(text)


# --- manifest 검증 (spec §7) ---


def test_load_labels_returns_ref_to_label_map():
    rows = [
        {"crop_ref": "job-1/row-0", "label": "blank", "확인자": "임강섭", "확인일": "2026-07-30"},
        {
            "crop_ref": "job-1/row-1",
            "label": "nonblank",
            "확인자": "임강섭",
            "확인일": "2026-07-30",
        },
    ]
    assert load_labels(rows, {"job-1/row-0", "job-1/row-1"}) == {
        "job-1/row-0": "blank",
        "job-1/row-1": "nonblank",
    }


def test_load_labels_fails_fast_on_unknown_crop_ref():
    # 조용히 빠진 표본은 마진을 낙관적으로 만든다(spec §7).
    rows = [{"crop_ref": "job-9/row-9", "label": "blank"}]
    with pytest.raises(ValueError, match="job-9/row-9"):
        load_labels(rows, {"job-1/row-0"})


def test_load_labels_fails_fast_on_duplicate_crop_ref():
    rows = [
        {"crop_ref": "job-1/row-0", "label": "blank"},
        {"crop_ref": "job-1/row-0", "label": "nonblank"},
    ]
    with pytest.raises(ValueError, match="중복"):
        load_labels(rows, {"job-1/row-0"})


def test_load_labels_fails_fast_on_unknown_label_value():
    rows = [{"crop_ref": "job-1/row-0", "label": "maybe"}]
    with pytest.raises(ValueError, match="maybe"):
        load_labels(rows, {"job-1/row-0"})


# --- read_labels_csv: 사람이 Excel로 쓰는 파일의 인코딩·헤더 실측 대응 ---


def test_read_labels_csv_handles_utf8_bom_header(tmp_path):
    # Excel "UTF-8로 저장"은 BOM을 붙인다 — utf-8로 열면 헤더가 '﻿crop_ref'가 되어
    # 전 행이 알 수 없는 컬럼으로 읽힌다.
    path = tmp_path / "labels.csv"
    path.write_bytes("crop_ref,label\r\njob-1/row-0,blank\r\n".encode("utf-8-sig"))
    rows = read_labels_csv(path)
    assert rows == [{"crop_ref": "job-1/row-0", "label": "blank"}]


def test_read_labels_csv_fails_fast_on_missing_required_columns(tmp_path):
    path = tmp_path / "labels.csv"
    path.write_text("ref,판정\njob-1/row-0,blank\n", encoding="utf-8")
    with pytest.raises(ValueError, match="crop_ref"):
        read_labels_csv(path)


# --- 마진 (임계 선정 근거) ---


def test_label_margin_reports_worst_normal_and_best_blank():
    records = [
        _rec("job-1/row-0", ratio=0.004),
        _rec("job-1/row-1", ratio=0.006),
        _rec("job-1/row-2", ratio=0.040),
        _rec("job-1/row-3", ratio=0.090),
    ]
    labels = {
        "job-1/row-0": "blank",
        "job-1/row-1": "blank",
        "job-1/row-2": "nonblank",
        "job-1/row-3": "nonblank",
    }
    m = label_margin(records, labels)
    assert m["worst_normal"] == pytest.approx(0.040)
    assert m["best_blank"] == pytest.approx(0.006)
    assert m["gap"] == pytest.approx(0.034)
    assert m["margin_pct"] == pytest.approx(85.0)
    assert m["n_blank"] == 2
    assert m["n_nonblank"] == 2
    assert m["n_unlabeled"] == 0
    assert m["n_labeled_dropped"] == 0
    assert m["labeled_dropped_refs"] == ()


def test_label_margin_is_none_when_a_side_is_empty():
    records = [_rec("job-1/row-0", ratio=0.004)]
    assert label_margin(records, {"job-1/row-0": "blank"}) is None


def test_label_margin_counts_unlabeled_records():
    records = [
        _rec("job-1/row-0", ratio=0.004),
        _rec("job-1/row-1", ratio=0.040),
        _rec("job-1/row-2", ratio=0.070),
    ]
    labels = {"job-1/row-0": "blank", "job-1/row-1": "nonblank"}
    assert label_margin(records, labels)["n_unlabeled"] == 1


def test_label_margin_ignores_hold_records():
    # 잉크율이 없는 보류 레코드가 마진 계산에 섞이면 근거가 오염된다.
    records = [
        _rec("job-1/row-0", ratio=0.004),
        _rec("job-1/row-1", ratio=None, crop_status=STATUS_CROP_MISSING),
        _rec("job-1/row-2", ratio=0.040),
    ]
    labels = {"job-1/row-0": "blank", "job-1/row-1": "blank", "job-1/row-2": "nonblank"}
    m = label_margin(records, labels)
    assert m["n_blank"] == 1


def test_label_margin_reports_labeled_samples_dropped_by_hold_status():
    # 라벨 3건(blank 2 + nonblank 1) 중 blank 1건이 crop_missing이라 측정에서 빠진다.
    # 조용히 빠지면 best_blank가 내려가 마진이 낙관적으로 커진다(spec §7의 다른 문).
    records = [
        _rec("job-1/row-0", ratio=0.004),  # blank, 측정됨
        _rec("job-1/row-1", ratio=None, crop_status=STATUS_CROP_MISSING),  # blank, 보류(빠짐)
        _rec("job-1/row-2", ratio=0.040),  # nonblank, 측정됨
    ]
    labels = {
        "job-1/row-0": "blank",
        "job-1/row-1": "blank",
        "job-1/row-2": "nonblank",
    }
    m = label_margin(records, labels)
    assert m["n_blank"] == 1
    assert m["n_labeled_dropped"] == 1
    assert m["labeled_dropped_refs"] == ("job-1/row-1",)


def test_label_margin_computes_negative_gap_when_distributions_overlap():
    # blank 최고값이 nonblank 최저값보다 높으면(겹침) gap이 음수여야 한다.
    records = [
        _rec("job-1/row-0", ratio=0.050),
        _rec("job-1/row-1", ratio=0.010),
    ]
    labels = {"job-1/row-0": "blank", "job-1/row-1": "nonblank"}
    m = label_margin(records, labels)
    assert m["gap"] < 0


# --- crop_status: 판정 불가를 crop_ink_ratio 호출 전에 가른다 (spec §8, Task 2 이월) ---
# crop_ink_ratio(None)은 .size 접근에서 AttributeError로 샌다 — 호출자는 cv2.imread
# 결과가 None인지 이 함수로 먼저 가른 뒤에만 crop_ink_ratio를 부른다.


def test_crop_status_is_crop_missing_when_file_absent():
    assert crop_status(exists=False, readable=False) == STATUS_CROP_MISSING


def test_crop_status_is_crop_unreadable_when_cv2_imread_returns_none():
    assert crop_status(exists=True, readable=False) == STATUS_CROP_UNREADABLE


def test_crop_status_is_ok_when_readable():
    assert crop_status(exists=True, readable=True) == STATUS_OK


# --- count_exact_zero_ink: 균일/클리핑 크롭 퇴화 관측 (Task 2 이월 #2) ---
# ratio == 0.0은 "잉크 0"과 "측정 불가"를 붕괴시킬 수 있다. 코드 동작은 바꾸지 않고
# 별도 집계만 노출한다.


def test_count_exact_zero_ink_counts_only_exact_zero_ok_records():
    records = [
        _rec("job-1/row-0", ratio=0.0),
        _rec("job-1/row-1", ratio=0.004),
        _rec("job-1/row-2", ratio=0.0),
        _rec("job-1/row-3", ratio=None, crop_status=STATUS_CROP_MISSING),
    ]
    assert count_exact_zero_ink(records) == 2


def test_count_exact_zero_ink_is_zero_when_none_are_exact_zero():
    records = [_rec("job-1/row-0", ratio=0.004)]
    assert count_exact_zero_ink(records) == 0


# --- 렌더 ---


def test_render_blank_report_contains_key_sections():
    records = [
        _rec("job-1/row-0", ratio=0.004),
        _rec("job-1/row-1", ratio=0.040),
        _rec("job-2/row-0", ratio=None, crop_status=STATUS_CROP_MISSING),
        _rec("job-2/row-1", ratio=None, crop_status=STATUS_CROP_UNREADABLE),
    ]
    labels = {"job-1/row-0": "blank", "job-1/row-1": "nonblank"}
    md = render_blank_report(records, labels, {"fetched_at": "t", "host": "macmini"})
    assert "# 빈 크롭 캘리브레이션 리포트" in md
    assert "정상최악" in md
    assert "빈크롭최선" in md
    assert STATUS_CROP_MISSING in md
    assert STATUS_CROP_UNREADABLE in md
    assert "job-1/row-0" in md
    # M7: 두 f-string 값이 서로 바뀌어도 안 잡히던 렌더 수치를 완성 줄로 단언한다.
    assert "정상최악(nonblank 최저 잉크율): 0.04000 (n=1)" in md
    assert "빈크롭최선(blank 최고 잉크율): 0.00400 (n=1)" in md


def test_render_blank_report_works_without_threshold():
    # BLANK_INK_MAX가 None인 동안에도 분포·마진은 출력돼야 한다(임계 결정의 입력이므로).
    md = render_blank_report([_rec(ratio=0.01)], {}, {"fetched_at": "t"}, threshold=None)
    assert "BLANK_INK_MAX: 미확정" in md


def test_render_blank_report_uses_injected_threshold():
    # 임계는 모듈 전역이 아니라 인자로 주입된다(M6) — 같은 인자면 항상 같은 출력.
    md = render_blank_report([_rec(ratio=0.01)], {}, {"fetched_at": "t"}, threshold=0.01234)
    assert "- 임계 BLANK_INK_MAX = 0.01234" in md


def test_render_blank_report_flags_exact_zero_ink_ratio_records():
    records = [
        _rec("job-1/row-0", ratio=0.0),
        _rec("job-1/row-1", ratio=0.004),
    ]
    md = render_blank_report(records, {}, {"fetched_at": "t"})
    assert "잉크율 정확히 0.0인 표본 1건" in md


def test_render_blank_report_shows_zero_count_when_none_are_exact_zero():
    records = [_rec("job-1/row-0", ratio=0.004)]
    md = render_blank_report(records, {}, {"fetched_at": "t"})
    assert "잉크율 정확히 0.0인 표본 0건" in md


def test_render_blank_report_flags_labeled_dropped_samples():
    # H1: 라벨은 있는데 crop_missing이라 측정 안 된 표본이 조용히 사라지면 안 된다.
    records = [
        _rec("job-1/row-0", ratio=0.004),
        _rec("job-1/row-1", ratio=None, crop_status=STATUS_CROP_MISSING),
        _rec("job-1/row-2", ratio=0.040),
    ]
    labels = {
        "job-1/row-0": "blank",
        "job-1/row-1": "blank",
        "job-1/row-2": "nonblank",
    }
    md = render_blank_report(records, labels, {"fetched_at": "t"})
    assert "⚠️" in md
    assert "job-1/row-1" in md
    assert "라벨 없는 표본 0건" in md  # 별개 축(비라벨) 카운터는 그대로 유지


def test_render_blank_report_omits_dropped_warning_when_nothing_dropped():
    records = [
        _rec("job-1/row-0", ratio=0.004),
        _rec("job-1/row-1", ratio=0.040),
    ]
    labels = {"job-1/row-0": "blank", "job-1/row-1": "nonblank"}
    md = render_blank_report(records, labels, {"fetched_at": "t"})
    assert "측정 보류라 근거에서 빠졌다" not in md


def test_render_blank_report_margin_message_when_no_labels_provided():
    # M1: 라벨 0건과 "한쪽뿐"은 원인이 다르다 — 다른 문구를 내야 사람이 오독하지 않는다.
    md = render_blank_report([_rec(ratio=0.01)], {}, {"fetched_at": "t"})
    assert "라벨된 표본이 0건이다" in md
    assert "labels.csv를 지정" in md


def test_render_blank_report_margin_message_when_only_one_label_side_present():
    records = [_rec("job-1/row-0", ratio=0.004)]
    labels = {"job-1/row-0": "blank"}
    md = render_blank_report(records, labels, {"fetched_at": "t"})
    assert "한쪽" in md
    assert "보강" in md


def test_render_blank_report_flags_overlapping_distributions():
    # M2: 겹침(gap <= 0)은 조건부로만 경고해야 한다 — 선례(warp_gate_report)와 동일.
    records = [
        _rec("job-1/row-0", ratio=0.050),
        _rec("job-1/row-1", ratio=0.010),
    ]
    labels = {"job-1/row-0": "blank", "job-1/row-1": "nonblank"}
    md = render_blank_report(records, labels, {"fetched_at": "t"})
    assert "겹친다" in md


def test_render_blank_report_omits_overlap_warning_when_gap_positive():
    records = [
        _rec("job-1/row-0", ratio=0.004),
        _rec("job-1/row-1", ratio=0.040),
    ]
    labels = {"job-1/row-0": "blank", "job-1/row-1": "nonblank"}
    md = render_blank_report(records, labels, {"fetched_at": "t"})
    assert "겹친다" not in md


# --- apply 계획: §6 불변식 2×2 전수 ---
# 측정 축은 crop_status(ok/crop_missing/crop_unreadable), DB 축은 pair_status(included/excluded).
# 보류 게이트는 crop_status로, 조건부 UPDATE의 WHERE는 pair_status로 판단해야 한다.


def test_plan_updates_excludes_blank_pair_in_clean_state():
    # 2×2 네 번째 칸: (included, NULL) = 정상 후보 → 새로 배제 가능.
    updates, counts = plan_updates([_rec(ratio=0.001)], THRESHOLD)
    assert [(u.new_status, u.new_reason) for u in updates] == [("excluded", "blank_crop")]
    assert counts == {"protected": 0, "unchanged": 0}


def test_plan_updates_carries_db_pair_status_into_seen_state():
    # WHERE에 실리는 것은 DB 축(pair_status)이지 측정 축(crop_status='ok')이 아니다 —
    # 한 글자 차이로 뒤집히면 사람 PATCH 충돌 검출이 통째로 무의미해진다.
    updates, _ = plan_updates([_rec(ratio=0.001, pair_status="included", reason=None)], THRESHOLD)
    assert (updates[0].seen_status, updates[0].seen_reason) == ("included", None)


def test_plan_updates_reverts_machine_exclusion_and_clears_reason():
    # 2×2 두 번째 칸: 기계가 자기 판정을 취소할 때 사유를 반드시 NULL로 지운다 —
    # 남기면 세 번째 칸(사람이 되돌림)과 구분되지 않아 자기 정정 결과를 영구 보호해버린다.
    rec = _rec(ratio=0.5, pair_status="excluded", reason="blank_crop")
    updates, counts = plan_updates([rec], THRESHOLD)
    assert [(u.new_status, u.new_reason) for u in updates] == [("included", None)]
    assert (updates[0].seen_status, updates[0].seen_reason) == ("excluded", "blank_crop")
    assert counts == {"protected": 0, "unchanged": 0}


def test_plan_updates_never_touches_human_excluded_pair():
    # 2×2 첫 번째 칸: (excluded, NULL) = 사람이 배제 → 어떤 경우에도 덮지 않는다.
    rec = _rec(ratio=0.001, pair_status="excluded", reason=None)
    updates, counts = plan_updates([rec], THRESHOLD)
    assert updates == []
    assert counts["protected"] == 1
    assert counts["unchanged"] == 0


def test_plan_updates_never_touches_human_reverted_pair():
    # 2×2 세 번째 칸: (included, blank_crop) = 사람이 되돌림 → 영구 보호(오탐 관측치).
    rec = _rec(ratio=0.001, pair_status="included", reason="blank_crop")
    updates, counts = plan_updates([rec], THRESHOLD)
    assert updates == []
    assert counts["protected"] == 1
    assert counts["unchanged"] == 0


def test_plan_updates_counts_human_reverted_pair_as_protected_not_unchanged():
    # 사람이 되돌린 쌍은 목표 상태(included, NULL)와 사유가 달라 '불변'이 아니다.
    # 보호가 불변으로 집계되면 사람 판정을 덮지 않았다는 증거가 리포트에서 사라진다.
    rec = _rec(ratio=0.5, pair_status="included", reason="blank_crop")
    updates, counts = plan_updates([rec], THRESHOLD)
    assert updates == []
    assert counts == {"protected": 1, "unchanged": 0}


def test_plan_updates_skips_pairs_already_at_target():
    # 이미 목표 상태인 쌍에는 쏘지 않는다 — 쏘면 affected 0이 충돌과 구분되지 않는다.
    rec = _rec(ratio=0.001, pair_status="excluded", reason="blank_crop")
    updates, counts = plan_updates([rec], THRESHOLD)
    assert updates == []
    assert counts == {"protected": 0, "unchanged": 1}


def test_plan_updates_skips_included_pair_that_is_not_blank():
    rec = _rec(ratio=0.5, pair_status="included", reason=None)
    updates, counts = plan_updates([rec], THRESHOLD)
    assert updates == []
    assert counts == {"protected": 0, "unchanged": 1}


def test_plan_updates_treats_ratio_at_threshold_as_blank():
    # is_blank는 '이하'다 — 경계값이 반대로 붙으면 임계 근거(마진)와 어긋난다.
    updates, _ = plan_updates([_rec(ratio=THRESHOLD)], THRESHOLD)
    assert [(u.new_status, u.new_reason) for u in updates] == [("excluded", "blank_crop")]


@pytest.mark.parametrize("hold", [STATUS_CROP_MISSING, STATUS_CROP_UNREADABLE])
def test_plan_updates_skips_hold_records(hold):
    # 보류는 계획에서 빠지고 DB에서 기존 상태 그대로 남는다 — 보호도 불변도 아니다.
    rec = _rec(ratio=None, crop_status=hold, pair_status="included", reason=None)
    updates, counts = plan_updates([rec], THRESHOLD)
    assert updates == []
    assert counts == {"protected": 0, "unchanged": 0}


def test_plan_updates_maps_pair_and_job_ids():
    rec = _rec(ratio=0.001, pair_id=42, job_id=7)
    updates, _ = plan_updates([rec], THRESHOLD)
    assert (updates[0].pair_id, updates[0].job_id) == (42, 7)


def test_plan_updates_handles_mixed_records_asymmetrically():
    # 배제 1 · 보호 2 · 불변 1 · 보류 1 — 개수가 서로 달라야 축이 뒤바뀐 구현을 잡는다.
    records = [
        _rec("job-1/row-0", ratio=0.001, pair_id=1),
        _rec("job-1/row-1", ratio=0.001, pair_status="excluded", reason=None, pair_id=2),
        _rec("job-1/row-2", ratio=0.001, pair_status="included", reason="blank_crop", pair_id=3),
        _rec("job-1/row-3", ratio=0.5, pair_status="included", reason=None, pair_id=4),
        _rec("job-1/row-4", ratio=None, crop_status=STATUS_CROP_MISSING, pair_id=5),
    ]
    updates, counts = plan_updates(records, THRESHOLD)
    assert [u.pair_id for u in updates] == [1]
    assert counts == {"protected": 2, "unchanged": 1}


def test_plan_updates_is_idempotent_on_second_pass():
    rec = _rec(ratio=0.001)
    updates, _ = plan_updates([rec], THRESHOLD)
    applied = {
        **rec,
        "pair_status": updates[0].new_status,
        "exclusion_reason": updates[0].new_reason,
    }
    # 소소한 것: counts도 함께 고정해 "안 쏜다"의 근거(unchanged=1)를 완결한다 —
    # updates == []만 보면 protected로 빠졌을 가능성과 구분되지 않는다.
    updates2, counts2 = plan_updates([applied], THRESHOLD)
    assert updates2 == []
    assert counts2 == {"protected": 0, "unchanged": 1}


# --- 잡 단위 가드 ---


def test_select_targets_skips_reviewed_jobs_by_default():
    # 비대칭(미검수 2 · 검수완료 1)으로 두어 '전부 통과'가 통과하지 않게 한다.
    recs = [
        _rec("job-1/row-0"),
        _rec("job-2/row-0", job_id=2) | {"curation_reviewed": True},
        _rec("job-3/row-0", job_id=3),
    ]
    assert [r["crop_ref"] for r in select_targets(recs, recheck_reviewed=False)] == [
        "job-1/row-0",
        "job-3/row-0",
    ]


def test_select_targets_includes_reviewed_jobs_when_rechecking():
    recs = [
        _rec("job-1/row-0"),
        _rec("job-2/row-0", job_id=2) | {"curation_reviewed": True},
        _rec("job-3/row-0", job_id=3),
    ]
    assert len(select_targets(recs, recheck_reviewed=True)) == 3


# --- 조건부 UPDATE SQL 조립 ---


def _u(**over):
    base = {
        "pair_id": 7,
        "job_id": 3,
        "seen_status": "included",
        "seen_reason": None,
        "new_status": "excluded",
        "new_reason": "blank_crop",
    }
    return PairUpdate(**{**base, **over})


_REVERT = {
    "pair_id": 8,
    "job_id": 5,
    "seen_status": "excluded",
    "seen_reason": "blank_crop",
    "new_status": "included",
    "new_reason": None,
}


def test_build_apply_script_emits_full_transaction_in_order():
    # 전문 고정 — ROW_COUNT() 프로브는 자기 UPDATE 바로 뒤에 와야 하고(다른 문이 끼면
    # 엉뚱한 문의 행수를 읽는다), 잡 표식 되돌림은 모든 쌍 UPDATE 뒤에 와야 한다.
    sql = build_apply_script([_u(), _u(**_REVERT)])
    assert sql == (
        "START TRANSACTION;\n"
        "UPDATE training_pairs SET status = 'excluded', exclusion_reason = 'blank_crop', "
        "reviewed_at = NULL "
        "WHERE id = 7 AND status = 'included' AND exclusion_reason <=> NULL;\n"
        "SELECT 7 AS pair_id, ROW_COUNT() AS affected;\n"
        "UPDATE training_pairs SET status = 'included', exclusion_reason = NULL, "
        "reviewed_at = NULL "
        "WHERE id = 8 AND status = 'excluded' AND exclusion_reason <=> 'blank_crop';\n"
        "SELECT 8 AS pair_id, ROW_COUNT() AS affected;\n"
        "UPDATE ocr_jobs SET curation_reviewed = FALSE WHERE id IN (3, 5) AND EXISTS ("
        "SELECT 1 FROM training_pairs tp "
        "WHERE tp.job_id = ocr_jobs.id AND tp.reviewed_at IS NULL);\n"
        "COMMIT;\n"
    )


def test_build_apply_script_carries_seen_state_in_where():
    sql = build_apply_script([_u()])
    assert "WHERE id = 7 AND status = 'included'" in sql
    assert "exclusion_reason <=> NULL" in sql  # `= NULL`은 항상 거짓이라 조용히 0행이 된다


def test_build_apply_script_never_compares_reason_with_plain_equals():
    # `AND exclusion_reason = NULL`이 한 번이라도 나오면 사유 NULL 쌍이 전부 0행이 된다.
    sql = build_apply_script([_u(), _u(**_REVERT)])
    assert "AND exclusion_reason = " not in sql


def test_build_apply_script_uses_null_safe_compare_for_non_null_reason():
    sql = build_apply_script([_u(seen_status="excluded", seen_reason="blank_crop")])
    assert "exclusion_reason <=> 'blank_crop'" in sql


def test_build_apply_script_nulls_reviewed_at_on_changed_pair():
    # 잡 표식만 되돌리면 큐레이션 큐의 미처리 수(reviewed_at IS NULL)가 0으로 보여
    # 사람이 '볼 것 없음'으로 읽는다 — 전수 재검사가 그대로 망가진다.
    assert "reviewed_at = NULL " in build_apply_script([_u()])


def test_build_apply_script_reverts_job_review_flag_only_for_jobs_with_unreviewed_pairs():
    # 충돌만 있고 변경 0인 잡은 EXISTS가 거짓 → 표식 유지 → '미검수 + 미처리 0'이 안 생긴다.
    sql = build_apply_script([_u(job_id=3), _u(pair_id=8, job_id=5)])
    assert sql.startswith("START TRANSACTION;")
    assert "UPDATE ocr_jobs SET curation_reviewed = FALSE WHERE id IN (3, 5) AND EXISTS (" in sql
    assert "tp.reviewed_at IS NULL" in sql
    assert sql.rstrip().endswith("COMMIT;")


def test_build_apply_script_dedupes_and_sorts_job_ids():
    sql = build_apply_script([_u(pair_id=7, job_id=5), _u(pair_id=8, job_id=3), _u(pair_id=9)])
    assert "WHERE id IN (3, 5) AND EXISTS (" in sql


def test_build_apply_script_emits_row_count_probe_per_pair():
    sql = build_apply_script([_u()])
    assert "SELECT 7 AS pair_id, ROW_COUNT() AS affected;" in sql


def test_build_apply_script_is_empty_without_updates():
    assert build_apply_script([]) == ""


@pytest.mark.parametrize("field", ["new_status", "seen_status"])
def test_build_apply_script_rejects_unknown_status_value(field):
    with pytest.raises(ValueError):
        build_apply_script([_u(**{field: "deleted"})])


@pytest.mark.parametrize("field", ["new_status", "seen_status"])
def test_build_apply_script_rejects_reason_value_in_status_field(field):
    # 닫힌 집합은 축별로 닫혀야 한다 — 사유 값이 status에 들어가면 SQL은 통과하지만
    # 학습쌍 상태가 알 수 없는 값으로 뒤집힌다.
    with pytest.raises(ValueError):
        build_apply_script([_u(**{field: "blank_crop"})])


@pytest.mark.parametrize("field", ["new_reason", "seen_reason"])
def test_build_apply_script_rejects_unknown_reason_value(field):
    with pytest.raises(ValueError):
        build_apply_script([_u(**{field: "bad_warp"})])


def test_build_apply_script_rejects_non_int_pair_id():
    # H1: pair_id는 status/reason과 달리 화이트리스트가 없어 f-string으로 그대로
    # SQL에 실렸다 — "7 OR 1=1"이 WHERE 절 우선순위상 (included, NULL) 쌍 전수를
    # 일괄 배제시킨다.
    with pytest.raises(ValueError):
        build_apply_script([_u(pair_id="7 OR 1=1")])


def test_build_apply_script_rejects_non_int_job_id():
    with pytest.raises(ValueError):
        build_apply_script([_u(job_id="0; DROP TABLE training_pairs")])


def test_build_apply_script_rejects_a_script_beyond_the_remote_argument_limit():
    # M5: 조립된 스크립트는 통째로 `mysql ... -e <one arg>`에 실려 ssh argv로 나간다.
    # 첫 실전 회차가 전 이력 일괄 배제라 쌍 수가 수천이면 ARG_MAX(1MiB)에 닿는다 —
    # 청크 분할은 트랜잭션 원자성을 깨므로, 알 수 없는 E2BIG 대신 여기서 먼저 멈춘다.
    updates = [_u(pair_id=i, job_id=1) for i in range(1, 4001)]
    with pytest.raises(ValueError, match="상한"):
        build_apply_script(updates)


def test_build_apply_script_allows_a_realistic_batch():
    # 상한이 현실적인 회차까지 막으면 도구가 첫 실전에서 못 쓰인다.
    updates = [_u(pair_id=i, job_id=1) for i in range(1, 1001)]
    assert build_apply_script(updates).startswith("START TRANSACTION;")


# --- affected row 해석 ---


def test_parse_apply_output_maps_pair_to_affected():
    # mysql --batch는 결과셋마다 헤더를 다시 찍는다. 변경 2 · 충돌 1로 비대칭을 둔다.
    header = "pair_id\taffected"
    text = f"{header}\n7\t1\n{header}\n8\t0\n{header}\n9\t1\n"
    assert parse_apply_output(text) == {7: 1, 8: 0, 9: 1}


def test_parse_apply_output_ignores_blank_lines():
    assert parse_apply_output("pair_id\taffected\n\n7\t1\n\n") == {7: 1}


def test_parse_apply_output_is_empty_for_empty_text():
    assert parse_apply_output("") == {}


def test_parse_apply_output_raises_clear_error_on_unparseable_line():
    # M1: 경고 줄이 섞이거나 --batch가 아니면 `int()`의 원문 에러만 나와 원인(원격
    # mysql이 --batch가 아님 / 경고가 stdout으로 샘)을 못 짚는다 — 원문 줄을 메시지에 싣는다.
    text = "pair_id\taffected\nWarning: Using a password on the command line is insecure\n7\t1\n"
    with pytest.raises(ValueError, match="apply 출력 해석 실패"):
        parse_apply_output(text)


def test_parse_apply_output_rejects_duplicate_pair_id():
    # M2: 중복 pair_id를 마지막 값으로 덮으면 stale/잘린 출력이 섞였을 때 조용히 사라진다.
    text = "pair_id\taffected\n7\t1\n7\t0\n"
    with pytest.raises(ValueError, match="중복"):
        parse_apply_output(text)


def test_classify_affected_splits_changed_and_conflict():
    updates = [_u(pair_id=7), _u(pair_id=8), _u(pair_id=9)]
    out = classify_affected(updates, {7: 1, 8: 0, 9: 0})
    assert out == {"changed": [7], "conflict": [8, 9], "unknown": []}


def test_classify_affected_treats_missing_probe_as_conflict():
    # 프로브 자체가 없으면(스크립트 중단 등) 적용됐다고 볼 근거가 없다.
    assert classify_affected([_u(pair_id=7)], {}) == {
        "changed": [],
        "conflict": [7],
        "unknown": [],
    }


def test_classify_affected_flags_ids_not_in_plan():
    # M2: 계획에 없는 id가 프로브에 섞이면 지금까진 조용히 버려져 "충돌 0"이라는
    # 거짓 안심이 나왔다 — stale/잘린 출력 혼입을 unknown으로 드러낸다.
    updates = [_u(pair_id=1)]
    out = classify_affected(updates, {1: 1, 999: 1})
    assert out == {"changed": [1], "conflict": [], "unknown": [999]}


# --- 보류·충돌 게이트 (spec §8) ---


def test_apply_exit_code_is_non_zero_when_holds_exist():
    assert apply_exit_code([_rec(crop_status=STATUS_CROP_MISSING)], [], allow_holds=False) != 0


def test_apply_exit_code_is_zero_with_allow_holds():
    assert apply_exit_code([_rec(crop_status=STATUS_CROP_MISSING)], [], allow_holds=True) == 0


def test_apply_exit_code_is_non_zero_when_conflicts_exist():
    # 충돌에는 우회 플래그가 없다 — 부분 적용 상태로 bank_update에 넘어가면 안 된다.
    assert apply_exit_code([], [7], allow_holds=True) != 0


def test_apply_exit_code_is_non_zero_when_unknown_ids_present():
    # M2: 계획 밖 id(unknown)도 충돌과 동격으로 다룬다 — 우회 플래그를 두지 않는다.
    assert apply_exit_code([], [], allow_holds=True, unknown=[999]) != 0


def test_apply_exit_code_is_non_zero_for_conflicts_even_with_holds_allowed():
    holds = [_rec(crop_status=STATUS_CROP_MISSING)]
    assert apply_exit_code(holds, [7, 8], allow_holds=True) != 0


def test_apply_exit_code_is_zero_when_clean():
    assert apply_exit_code([], [], allow_holds=False) == 0
