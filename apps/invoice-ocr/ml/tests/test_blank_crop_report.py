"""tools.blank_crop_report 순수 계층 단위테스트(ssh/cv2 비의존, 합성 데이터만)."""

import pytest

from tools.blank_crop_report import (
    STATUS_CROP_MISSING,
    STATUS_CROP_UNREADABLE,
    STATUS_OK,
    count_exact_zero_ink,
    crop_status,
    label_margin,
    load_labels,
    parse_pairs_tsv,
    read_labels_csv,
    render_blank_report,
)


def _rec(
    crop_ref="job-1/row-0",
    *,
    ratio=0.5,
    crop_status=STATUS_OK,
    pair_status="included",
    reason=None,
):
    return {
        "id": 1,
        "crop_ref": crop_ref,
        "job_id": 1,
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
