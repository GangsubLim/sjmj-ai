"""tools.warp_gate_calib — 순수 계층(라벨 분할·마진·대조·렌더). 합성 데이터만."""

from dataclasses import fields

import pytest

from handwriting.warp_gate import WarpGateMetrics  # stdlib만 쓰는 모듈이라 코어 venv에서도 안전
from tools.warp_gate_calib import (
    LABEL_NORMAL,
    LABEL_SUSPECT,
    LABEL_UNLABELED,
    METRIC_KEYS,
    axis_margins,
    classify_flip,
    flip_table,
    label_of,
    render_rewarp_report,
    stored_vs_rewarp,
)


def _rec(
    job_id,
    label,
    *,
    std_hline=17,
    enh_hline=20,
    std_pitch=0.02,
    enh_pitch=0.03,
    stored_hline=None,
    status="ok",
):
    return {
        "job_id": job_id,
        "label": label,
        "prev_warp_ok": True,
        "status": status,
        "metrics": {
            "std": {
                "hline_count": std_hline,
                "pitch_dev": std_pitch,
                "blue_ratio_left": 0.2,
                "blue_ratio_right": 0.2,
            },
            "enh": {
                "hline_count": enh_hline,
                "pitch_dev": enh_pitch,
                "blue_ratio_left": 0.3,
                "blue_ratio_right": 0.3,
            },
        },
        "stored_metrics": (
            None
            if stored_hline is None
            else {
                "hline_count": stored_hline,
                "pitch_dev": 0.02,
                "blue_ratio_left": 0.2,
                "blue_ratio_right": 0.2,
            }
        ),
    }


def test_label_of_defaults_to_normal():
    assert label_of(50, suspects=set(), unlabeled=set()) == LABEL_NORMAL
    assert label_of(24, suspects={24}, unlabeled=set()) == LABEL_SUSPECT
    assert label_of(5, suspects=set(), unlabeled={5}) == LABEL_UNLABELED


def test_label_of_prefers_suspect_over_unlabeled():
    # 육안으로 파손 판정이 나면 미라벨 구간 잡도 파손군에 편입된다(spec §7).
    assert label_of(5, suspects={5}, unlabeled={5}) == LABEL_SUSPECT


def test_axis_margins_excludes_unlabeled_from_both_groups():
    # 잡 2~21은 미라벨이다(spec §4.1). 정상군에 섞으면 정상군 '최악값'이 미확인 잡에서
    # 나와 임계 근거가 오염된다 — 그 오염이 바로 spec §7이 경고한 실패 모드다.
    records = [
        _rec(50, LABEL_NORMAL, enh_hline=20),
        _rec(51, LABEL_NORMAL, enh_hline=19),
        _rec(5, LABEL_UNLABELED, enh_hline=3),  # 정상군에 섞이면 최악값이 3으로 무너진다
        _rec(24, LABEL_SUSPECT, enh_hline=6),
    ]
    m = axis_margins(records, "enh")
    assert m["hline_count"]["worst_normal"] == 19
    assert m["hline_count"]["best_suspect"] == 6


def test_axis_margins_reads_the_requested_axis():
    records = [_rec(50, LABEL_NORMAL), _rec(24, LABEL_SUSPECT, std_hline=6, enh_hline=9)]
    assert axis_margins(records, "std")["hline_count"]["best_suspect"] == 6
    assert axis_margins(records, "enh")["hline_count"]["best_suspect"] == 9


def test_axis_margins_returns_none_when_a_group_is_empty():
    assert axis_margins([_rec(50, LABEL_NORMAL)], "enh")["hline_count"] is None


def test_axis_margins_excludes_records_with_missing_metrics():
    # metrics=None인 레코드(재워프 실패)가 정상군에 섞이면 usable 가드가 없을 때 최악값이
    # 왜곡된다 — 가드가 실제로 걸러내는지 고정한다(M4).
    records = [
        _rec(50, LABEL_NORMAL, enh_hline=20),
        {**_rec(52, LABEL_NORMAL, enh_hline=1), "metrics": None},
        _rec(24, LABEL_SUSPECT, enh_hline=6),
    ]
    m = axis_margins(records, "enh")
    assert m["hline_count"]["worst_normal"] == 20


def test_axis_margins_computes_gap_toward_normal_for_higher_is_better_metric():
    # hline_count는 클수록 좋다 — gap 부호가 반전되면(best_suspect - worst_normal) 표의
    # 마진이 실제와 반대로 읽힌다(H1).
    records = [_rec(50, LABEL_NORMAL, enh_hline=19), _rec(24, LABEL_SUSPECT, enh_hline=6)]
    assert axis_margins(records, "enh")["hline_count"]["gap"] == 13


def test_axis_margins_computes_gap_toward_normal_for_lower_is_better_metric():
    # pitch_dev는 작을수록 좋다 — 방향이 higher-is-better와 반대여야 한다(H1).
    records = [
        _rec(50, LABEL_NORMAL, std_pitch=0.10),
        _rec(24, LABEL_SUSPECT, std_pitch=0.50),
    ]
    assert axis_margins(records, "std")["pitch_dev"]["gap"] == pytest.approx(0.40)


def test_axis_margins_computes_margin_pct_against_normal_denominator():
    # margin_pct = 100 * gap / |worst_normal| — 분모가 gap이나 상수 100으로 바뀌어도
    # 이 값 하나로는 안 잡힌다(H1). 정상 분모 경로를 고정한다.
    records = [_rec(50, LABEL_NORMAL, enh_hline=20), _rec(24, LABEL_SUSPECT, enh_hline=10)]
    m = axis_margins(records, "enh")["hline_count"]
    assert m["margin_pct"] == pytest.approx(50.0)
    assert m["denom_fallback"] is False


def test_axis_margins_falls_back_margin_pct_denominator_when_worst_normal_is_zero():
    # worst_normal이 0이면 분모가 없어 1.0으로 대체한다 — 그때 margin_pct는 백분율이 아니라
    # gap의 절대값이다(H1, denom_fallback이 상수 False로 꺼져도 기존 테스트는 못 잡았다).
    records = [
        _rec(50, LABEL_NORMAL, std_pitch=0.0),
        _rec(24, LABEL_SUSPECT, std_pitch=0.30),
    ]
    m = axis_margins(records, "std")["pitch_dev"]
    assert m["denom_fallback"] is True
    assert m["margin_pct"] == pytest.approx(30.0)


def _blue_rec(job_id, label, left, right):
    """좌우 파랑 비율이 서로 다른 레코드.

    `_rec`는 좌우를 같은 값으로 고정해 blue_ratio_min의 min↔max 뒤바뀜도, blue_asym의
    상수 0 뭉갬도 구분하지 못한다 — 파생 지표의 값 자체를 단언하려면 좌우가 달라야 한다.
    """
    base = _rec(job_id, label)
    std = {**base["metrics"]["std"], "blue_ratio_left": left, "blue_ratio_right": right}
    return {**base, "metrics": {**base["metrics"], "std": std}}


def test_axis_margins_derives_blue_ratio_min_from_the_weaker_side():
    # blue_ratio_min은 게이트 술어가 실제로 보는 축이다 — min이 max로 뒤집히면 분리 마진이
    # 낙관적으로 읽힌다. 정상 (0.5, 0.3) → 0.3, 파손 (0.2, 0.1) → 0.1.
    records = [_blue_rec(50, LABEL_NORMAL, 0.5, 0.3), _blue_rec(24, LABEL_SUSPECT, 0.2, 0.1)]
    m = axis_margins(records, "std")["blue_ratio_min"]
    assert m["worst_normal"] == pytest.approx(0.3)
    assert m["best_suspect"] == pytest.approx(0.1)


def test_axis_margins_derives_blue_asymmetry_from_both_sides():
    # blue_asym이 상수 0으로 뭉개져도 좌우가 같은 픽스처에서는 아무 테스트도 못 잡는다.
    # 정상 (0.5, 0.3) → 0.4(작을수록 좋으므로 정상군 최악값), 파손 (0.2, 0.1) → 0.5.
    records = [_blue_rec(50, LABEL_NORMAL, 0.5, 0.3), _blue_rec(24, LABEL_SUSPECT, 0.2, 0.1)]
    m = axis_margins(records, "std")["blue_asym"]
    assert m["worst_normal"] == pytest.approx(0.4)
    assert m["best_suspect"] == pytest.approx(0.5)


def test_every_metric_key_declares_a_margin_direction():
    # RAW_METRIC_KEYS는 DTO 필드에서 자동 파생되는데 방향 집합만 수동 리터럴이다 — DTO에
    # 필드가 늘면 METRIC_KEYS는 따라가지만 방향은 '작을수록 좋다'로 조용히 기본값 처리돼
    # _margins가 worst_normal/best_suspect를 반대로 고르고 gap 부호까지 뒤집힌다.
    # 프로덕션에 로드 시점 예외를 배선하는 대신, 방향 누락을 여기서 RED로 드러낸다.
    from tools.warp_gate_calib import HIGHER_IS_BETTER

    lower_is_better = {"pitch_dev", "blue_asym"}

    assert HIGHER_IS_BETTER & lower_is_better == set(), "한 지표가 두 방향을 동시에 가진다"
    assert HIGHER_IS_BETTER | lower_is_better == set(METRIC_KEYS), (
        "방향이 정해지지 않은 지표가 있다"
    )


def test_metric_keys_track_dto_fields_plus_derived():
    # 원시 지표는 DTO에서 파생한다 — WarpGateMetrics에 필드가 늘면 표가 자동으로 따라간다.
    raw = tuple(f.name for f in fields(WarpGateMetrics))
    assert METRIC_KEYS[: len(raw)] == raw
    assert set(METRIC_KEYS) - set(raw) == {"blue_ratio_min", "blue_asym"}


def test_stored_vs_rewarp_lists_only_jobs_whose_metrics_moved():
    # 재워프가 저장분과 달라지는 잡은 숨은 가정이 아니라 기록해야 할 데이터다(spec §4.1) —
    # 그 잡의 #18 승계 라벨은 무효이므로 Phase 1 육안 대상에 추가된다.
    rows = stored_vs_rewarp(
        [
            _rec(30, LABEL_SUSPECT, std_hline=17, stored_hline=17),
            _rec(31, LABEL_SUSPECT, std_hline=17, stored_hline=6),
        ]
    )
    assert [r["job_id"] for r in rows] == [31]
    assert rows[0]["hline_count"] == {"rewarp": 17, "stored": 6}


def test_stored_vs_rewarp_ignores_float_noise_within_tolerance():
    # float 재계산은 1e-12 수준 오차가 흔하다 — 완전일치(!=) 비교면 그 오차도 drift 행이 돼
    # 육안 대상이 과대 편입된다(L1).
    base = _rec(31, LABEL_SUSPECT)
    std = {**base["metrics"]["std"], "pitch_dev": 0.02 + 1e-12}
    stored = {**base["metrics"]["std"], "pitch_dev": 0.02}
    rec = {**base, "metrics": {**base["metrics"], "std": std}, "stored_metrics": stored}
    assert stored_vs_rewarp([rec]) == []


def test_stored_vs_rewarp_still_flags_float_drift_beyond_tolerance():
    base = _rec(31, LABEL_SUSPECT)
    std = {**base["metrics"]["std"], "pitch_dev": 0.05}
    stored = {**base["metrics"]["std"], "pitch_dev": 0.02}
    rec = {**base, "metrics": {**base["metrics"], "std": std}, "stored_metrics": stored}
    rows = stored_vs_rewarp([rec])
    assert rows and rows[0]["pitch_dev"] == {"rewarp": 0.05, "stored": 0.02}


# --- flip 4분류(Task 12) ---


def _m(hline, pitch, blue):
    return {
        "hline_count": hline,
        "pitch_dev": pitch,
        "blue_ratio_left": blue,
        "blue_ratio_right": blue,
    }


def test_classify_flip_marks_standard_pass_without_touching_enh():
    from handwriting.warp_gate import MAX_PITCH_DEV, MIN_BLUE_RATIO, MIN_HLINES

    good = _m(MIN_HLINES + 3, MAX_PITCH_DEV / 2, MIN_BLUE_RATIO * 3)
    assert classify_flip({"std": good, "enh": _m(0, 1.0, 0.0)}) == "pass→pass"


def test_classify_flip_marks_rescue_when_only_enh_passes():
    from handwriting.warp_gate import ENH_MAX_PITCH_DEV, ENH_MIN_BLUE_RATIO, ENH_MIN_HLINES

    enh_good = _m(ENH_MIN_HLINES + 3, ENH_MAX_PITCH_DEV / 2, ENH_MIN_BLUE_RATIO * 3)
    assert classify_flip({"std": _m(0, 1.0, 0.0), "enh": enh_good}) == "fail→pass"


def test_classify_flip_judges_the_enh_axis_by_enh_thresholds_not_the_standard_ones():
    # 위 픽스처 enh_good은 표준 임계도 전부 통과하는 값이라, enh 축을 표준 술어로 판정하는
    # 오배선(evaluate_warp_enh → evaluate_warp)이 기존 테스트 전량에서 생존한다. 이는
    # handwriting.warp_gate가 타입 분리로 막으려던 fail-open과 같은 실패 모드인데 리포트 쪽
    # 복제 경로에는 그 가드가 없다 — 표준은 통과하지만 enh는 실패하는 밴드를 enh 축에 놓아
    # 오배선이 fail→pass로 새는 것을 막는다.
    from handwriting.warp_gate import ENH_MIN_HLINES, MAX_PITCH_DEV, MIN_BLUE_RATIO, MIN_HLINES

    # 재캘리브로 두 임계가 붙어 밴드가 사라지면 이 테스트가 원인을 먼저 말하게 한다.
    assert MIN_HLINES <= ENH_MIN_HLINES - 1, "표준 통과·enh 실패 밴드가 존재하지 않는다"
    enh_only_fails = _m(ENH_MIN_HLINES - 1, MAX_PITCH_DEV / 2, MIN_BLUE_RATIO * 3)

    assert classify_flip({"std": _m(0, 1.0, 0.0), "enh": enh_only_fails}) == "fail→fail"


def test_classify_flip_marks_double_failure():
    assert classify_flip({"std": _m(0, 1.0, 0.0), "enh": _m(0, 1.0, 0.0)}) == "fail→fail"


def test_classify_flip_delegates_to_the_operational_predicate():
    # M5: 별도 판정식을 리포트 쪽에 복제하지 않고 handwriting.warp_gate.evaluate_warp를 그대로
    # 호출해야 한다 — 임계 상수를 바꾸면 이 분류도 따라 움직여야 근거가 코드로 재현 가능하다.
    import tools.warp_gate_calib as calib

    original = calib.evaluate_warp
    calib.evaluate_warp = lambda metrics: True
    try:
        assert classify_flip({"std": _m(0, 1.0, 0.0), "enh": _m(0, 1.0, 0.0)}) == "pass→pass"
    finally:
        calib.evaluate_warp = original


def test_flip_table_groups_jobs_by_transition():
    records = [
        {"job_id": 57, "metrics": {"std": _m(20, 0.02, 0.2), "enh": _m(20, 0.02, 0.2)}},
        {"job_id": 59, "metrics": {"std": _m(0, 1.0, 0.0), "enh": _m(20, 0.02, 0.3)}},
    ]
    assert flip_table(records) == {
        "pass→pass": [57],
        "fail→pass": [59],
        "fail→fail": [],
        "pass→fail": [],
    }


def test_flip_table_skips_records_without_metrics():
    records = [
        {"job_id": 51, "metrics": None},
        {"job_id": 57, "metrics": {"std": _m(20, 0.02, 0.2), "enh": _m(20, 0.02, 0.2)}},
    ]
    assert flip_table(records)["pass→pass"] == [57]


def test_rescue_and_damaged_job_manifests_are_pinned_to_spec():
    # 두 상수는 spec §4.2에서 손으로 옮겨 적은 값이다 — 드리프트가 나면 DoD 2·3이
    # 조용히 다른 기준으로 판정된다(#60 리뷰 M3).
    from tools.warp_gate_calib import DAMAGED_JOBS, RESCUE_JOBS

    assert frozenset({59, 60, 61, 62, 63, 65, 66, 67}) == RESCUE_JOBS
    assert frozenset({17, 21, 24, 29, 30, 31, 39}) == DAMAGED_JOBS


def test_dod2_stays_unsatisfied_when_rescue_manifest_is_emptied(monkeypatch):
    # RESCUE_JOBS가 빈 집합이면 `set() >= frozenset()`이 항상 참이라 DoD 2가 데이터와
    # 무관하게 ✅가 된다 — 모집단 없는 침묵 붕괴를 가드가 막아야 한다(#60 리뷰 M3).
    import tools.warp_gate_calib as calib

    monkeypatch.setattr(calib, "RESCUE_JOBS", frozenset())
    table = {
        calib.FLIP_PASS_PASS: [],
        calib.FLIP_FAIL_PASS: [],
        calib.FLIP_FAIL_FAIL: [],
        calib.FLIP_PASS_FAIL: [],
    }
    assert calib._dod_marks(table)["dod2_rescue_all_fail_to_pass"] is False


def test_dod3_stays_unsatisfied_when_damaged_manifest_is_emptied(monkeypatch):
    import tools.warp_gate_calib as calib

    monkeypatch.setattr(calib, "DAMAGED_JOBS", frozenset())
    table = {
        calib.FLIP_PASS_PASS: [],
        calib.FLIP_FAIL_PASS: [],
        calib.FLIP_FAIL_FAIL: [],
        calib.FLIP_PASS_FAIL: [],
    }
    assert calib._dod_marks(table)["dod3_damaged_all_fail_to_fail"] is False


def test_classify_flip_fails_fast_when_enh_axis_is_missing_even_if_std_passes():
    # std가 통과하면 enh를 참조하지 않으므로 enh 키가 통째로 빠져도 조용히 pass→pass가
    # 나온다 — std 실패 시에만 KeyError로 드러나는 비대칭을 없앤다(#60 리뷰 L1).
    from handwriting.warp_gate import MAX_PITCH_DEV, MIN_BLUE_RATIO, MIN_HLINES

    good = _m(MIN_HLINES + 3, MAX_PITCH_DEV / 2, MIN_BLUE_RATIO * 3)
    with pytest.raises(KeyError):
        classify_flip({"std": good})


def test_flip_table_fails_fast_when_metrics_key_is_absent():
    # axis_margins의 fail-fast 관용(#60 리뷰 M5)과 통일한다 — `.get()`으로 흡수하면
    # 상류 배선 버그로 빠진 레코드가 조용히 분모에서 사라진다.
    with pytest.raises(KeyError):
        flip_table([{"job_id": 1, "label": "normal"}])


def test_flip_table_never_produces_pass_to_fail_by_construction():
    # 폴백은 표준 통과를 뒤집지 않는다 — 표준이 통과하면 enh는 판정에 참여하지 않는다.
    # 이 성질이 코드로 보장됨을 고정한다(spec §4.2: pass→fail 1건이라도 나오면 설계 결함).
    from handwriting.warp_gate import MAX_PITCH_DEV, MIN_BLUE_RATIO, MIN_HLINES

    good = _m(MIN_HLINES + 3, MAX_PITCH_DEV / 2, MIN_BLUE_RATIO * 3)
    table = flip_table(
        [{"job_id": 1, "label": "normal", "metrics": {"std": good, "enh": _m(0, 1.0, 0.0)}}]
    )
    assert table["pass→fail"] == []


# --- 렌더 ---


def test_render_rewarp_report_includes_axis_margin_tables_and_label_distribution():
    records = [
        _rec(50, LABEL_NORMAL),
        _rec(24, LABEL_SUSPECT, std_hline=6, enh_hline=9),
        _rec(5, LABEL_UNLABELED),
    ]
    margins = {"std": axis_margins(records, "std"), "enh": axis_margins(records, "enh")}
    md = render_rewarp_report(records, margins, [], {"fetched_at": "t", "host": "h"})
    assert "표준 마스크 기준 분리 마진" in md
    assert "enh 마스크 기준 분리 마진" in md
    assert "normal 1" in md
    assert "suspect 1" in md
    assert "unlabeled 1" in md
    assert "| 24 |" in md  # 지표 전수표에 잡이 실린다


def test_render_rewarp_report_shows_no_difference_when_drift_is_empty():
    md = render_rewarp_report([_rec(50, LABEL_NORMAL)], {"std": {}, "enh": {}}, [], {})
    assert "차이 없음" in md


def test_render_rewarp_report_lists_drift_rows():
    drift = [{"job_id": 31, "hline_count": {"rewarp": 17, "stored": 6}}]
    md = render_rewarp_report([_rec(50, LABEL_NORMAL)], {"std": {}, "enh": {}}, drift, {})
    assert "| 31 | hline_count | 17 | 6 |" in md
    assert "차이 없음" not in md


def test_render_rewarp_report_shows_status_denominator_breakdown():
    records = [
        _rec(50, LABEL_NORMAL),
        {**_rec(51, LABEL_NORMAL), "status": "upload_missing", "metrics": None},
    ]
    md = render_rewarp_report(records, {"std": {}, "enh": {}}, [], {})
    assert "upload_missing 1" in md
    assert "job 51: upload_missing" in md


def test_render_rewarp_report_renders_exact_margin_row_values():
    # worst_normal/best_suspect 열이 서로 바뀌어도(H2) 렌더 테스트가 제목 문자열과 잡 id
    # 존재만 보면 통과했다 — 사람이 이 표를 읽고 임계를 정하므로 행 전체를 문자열로 고정한다.
    records = [_rec(50, LABEL_NORMAL, std_hline=17), _rec(24, LABEL_SUSPECT, std_hline=6)]
    margins = {"std": axis_margins(records, "std"), "enh": {}}
    md = render_rewarp_report(records, margins, [], {})
    assert "| hline_count | 17.0000 | 6.0000 | 11.0000 | 64.7% |" in md


def test_render_rewarp_report_footnotes_zero_baseline_margin():
    # 정상군 최악값이 0이면 마진% 분모가 1.0으로 대체된다 — 백분율이 아님을 각주로 밝힌다.
    records = [
        _rec(50, LABEL_NORMAL, std_pitch=0.0),
        _rec(24, LABEL_SUSPECT, std_pitch=0.30),
    ]
    margins = {"std": axis_margins(records, "std"), "enh": {}}
    md = render_rewarp_report(records, margins, [], {})
    assert "마진% 분모를 1.0으로 대체" in md


def test_render_rewarp_report_includes_flip_transition_section():
    records = [
        _rec(50, LABEL_NORMAL),  # std 기본값이 통과 임계를 넘는다 → pass→pass
        _rec(24, LABEL_SUSPECT, std_hline=6),  # std 실패, enh 기본값이 통과 → fail→pass
    ]
    margins = {"std": axis_margins(records, "std"), "enh": axis_margins(records, "enh")}
    md = render_rewarp_report(records, margins, [], {})
    assert "## 판정 전이 4분류" in md
    assert "- pass→pass: [50]" in md
    assert "- fail→pass: [24]" in md
    assert "- pass→fail: []" in md


def test_render_flip_section_states_its_own_denominator():
    # metrics=None인 잡은 flip 절에서 조용히 빠진다 — 아래 "분모 제외" 절과 교차대조하지
    # 않아도 되게, 절 머리에서 합계/모집단/제외 건수를 바로 밝힌다(#60 리뷰 M4).
    records = [
        _rec(50, LABEL_NORMAL),
        {**_rec(51, LABEL_NORMAL), "status": "upload_missing", "metrics": None},
    ]
    margins = {"std": axis_margins(records, "std"), "enh": axis_margins(records, "enh")}
    md = render_rewarp_report(records, margins, [], {})
    assert "- 합계 1 / 모집단 2 (지표 없음 1)" in md


def test_render_flip_section_sorts_job_ids_ascending_within_bucket():
    # `sorted()`를 제거하는 변이가 생존하지 않도록 잡 2건을 역순으로 넣어 오름차순 렌더를
    # 고정한다(#60 리뷰 L2, snapshot_diff의 순서 고정과 동일 취지).
    records = [
        _rec(24, LABEL_SUSPECT, std_hline=6),
        _rec(5, LABEL_SUSPECT, std_hline=6),
    ]
    margins = {"std": axis_margins(records, "std"), "enh": axis_margins(records, "enh")}
    md = render_rewarp_report(records, margins, [], {})
    assert "- fail→pass: [5, 24]" in md


def test_render_rewarp_report_marks_dod1_satisfied_by_construction():
    md = render_rewarp_report([_rec(50, LABEL_NORMAL)], {"std": {}, "enh": {}}, [], {})
    assert "DoD 1(`pass→fail` 0건): ✅ (구성상 보장" in md


def test_render_rewarp_report_marks_dod2_and_dod3_unsatisfied_when_incomplete():
    # 정상 잡 1건뿐이면 오강등 구제 8건·파손 확정 7건이 하나도 안 채워졌다. 마크는 각
    # DoD 줄 전체(잡 목록 + 기호)로 고정한다 — "문서 어딘가에 ❌가 있다"만 보면 DoD 2·3
    # 각각의 판정을 서로 대신 충족시키는 변이가 생존한다(#60 리뷰 H1).
    md = render_rewarp_report([_rec(50, LABEL_NORMAL)], {"std": {}, "enh": {}}, [], {})
    assert "DoD 2(오강등 구제 [59, 60, 61, 62, 63, 65, 66, 67] 전부 fail→pass): ❌" in md
    assert "DoD 3(파손 확정 [17, 21, 24, 29, 30, 31, 39] 전부 fail→fail): ❌" in md


def test_render_rewarp_report_marks_dod2_and_dod3_satisfied_when_all_jobs_present():
    from tools.warp_gate_calib import DAMAGED_JOBS, RESCUE_JOBS

    records = [_rec(50, LABEL_NORMAL)]
    records += [_rec(j, LABEL_SUSPECT, std_hline=6) for j in sorted(RESCUE_JOBS)]
    records += [_rec(j, LABEL_SUSPECT, std_hline=6, enh_hline=3) for j in sorted(DAMAGED_JOBS)]
    margins = {"std": axis_margins(records, "std"), "enh": axis_margins(records, "enh")}
    md = render_rewarp_report(records, margins, [], {})
    # 전역 부정("❌" not in md) 대신 DoD 2·3 각 줄의 ✅를 직접 확인한다(#60 리뷰 H1).
    assert "DoD 2(오강등 구제 [59, 60, 61, 62, 63, 65, 66, 67] 전부 fail→pass): ✅" in md
    assert "DoD 3(파손 확정 [17, 21, 24, 29, 30, 31, 39] 전부 fail→fail): ✅" in md


# --- crop-identity 스냅샷 대조(Task 7) ---


def test_snapshot_diff_flags_jobs_whose_crops_moved():
    from tools.warp_gate_calib import snapshot_diff

    before = {"59": {"n_new": 5, "boxes": [[10, 20]], "crop_sha": ["aa"]}}
    after = {"59": {"n_new": 5, "boxes": [[10, 21]], "crop_sha": ["aa"]}}
    assert snapshot_diff(before, after)["changed"] == ["59"]


def test_snapshot_diff_is_empty_when_nothing_moved():
    from tools.warp_gate_calib import snapshot_diff

    snap = {"57": {"n_new": 5, "boxes": [[10, 20]], "crop_sha": ["aa"]}}
    assert snapshot_diff(snap, dict(snap)) == {"changed": [], "missing": [], "added": []}


def test_snapshot_diff_reports_jobs_that_appeared_or_vanished():
    from tools.warp_gate_calib import snapshot_diff

    d = snapshot_diff({"1": {"n_new": 1}}, {"2": {"n_new": 1}})
    assert d["missing"] == ["1"]
    assert d["added"] == ["2"]


def test_snapshot_diff_orders_jobs_numerically_not_lexically():
    # 산출 순서가 실행마다 흔들리면 리포트 diff를 사람이 읽을 수 없다 — 잡 10이 잡 9보다
    # 앞서는 사전순 정렬로 되돌아가는 회귀를 고정한다.
    from tools.warp_gate_calib import snapshot_diff

    assert snapshot_diff({}, {"10": {}, "9": {}})["added"] == ["9", "10"]


def test_snapshot_diff_rejects_a_snapshot_key_that_is_not_a_job_id():
    # --baseline은 사용자가 지정하는 외부 파일이다 — 키가 숫자가 아니면 int()가 맨
    # 스택트레이스로 죽는 대신 무엇이 잘못됐는지 말해야 한다.
    from tools.warp_gate_calib import snapshot_diff

    with pytest.raises(ValueError, match="job_id"):
        snapshot_diff({"job-1": {}}, {})


def test_snapshot_diff_ignores_crop_ink_drift():
    # crop_ink는 축 ②-b의 진단 신호이지 identity가 아니다 — 로컬과 macmini의 부동소수
    # 1 ULP 차이로 DoD 4 게이트가 빨개지면 안 된다.
    from tools.warp_gate_calib import snapshot_diff

    before = {"59": {"boxes": [[10, 20]], "crop_sha": ["aa"], "crop_ink": [0.1234567890123]}}
    after = {"59": {"boxes": [[10, 20]], "crop_sha": ["aa"], "crop_ink": [0.1234567890124]}}
    assert snapshot_diff(before, after)["changed"] == []


def test_pair_rows_keeps_only_included_pairs():
    # 축 ②-a의 모집단은 included 44건이다(excluded 14건은 학습에 안 쓰인다).
    from tools.warp_gate_calib import pair_rows

    pairs = [
        {"crop_ref": "job-23/row-0", "job_id": 23, "row_index": 0, "status": "included"},
        {"crop_ref": "job-23/row-1", "job_id": 23, "row_index": 1, "status": "excluded"},
    ]
    assert pair_rows(pairs) == {(23, 0)}


def test_changed_pairs_reports_only_included_rows_that_moved():
    from tools.warp_gate_calib import changed_pairs, pair_rows

    before = {"23": {"boxes": [[10, 20], [30, 40]], "crop_sha": ["aa", "bb"]}}
    after = {"23": {"boxes": [[10, 20], [31, 41]], "crop_sha": ["aa", "cc"]}}
    pairs = [
        {"job_id": 23, "row_index": 0, "status": "included"},
        {"job_id": 23, "row_index": 1, "status": "included"},
    ]
    assert changed_pairs(before, after, pair_rows(pairs)) == {"moved": [(23, 1)], "vanished": []}


def test_changed_pairs_flags_a_row_when_only_the_box_moved_but_the_hash_did_not():
    # boxes만 밀리고 crop_sha가 우연히 같아도 움직인 것으로 봐야 한다 — moved 판정이
    # (박스 변화) and (해시 변화)로 바뀌면 한쪽만 변한 케이스를 놓친다.
    from tools.warp_gate_calib import changed_pairs

    before = {"23": {"boxes": [[10, 20]], "crop_sha": ["aa"]}}
    after = {"23": {"boxes": [[11, 21]], "crop_sha": ["aa"]}}
    assert changed_pairs(before, after, {(23, 0)})["moved"] == [(23, 0)]


def test_changed_pairs_flags_a_row_when_only_the_crop_hash_moved():
    # 박스는 그대로인데 픽셀만 바뀐 경우 = 폴백이 워프 자체를 바꿨을 때의 시나리오이고,
    # 그것이 crop_sha가 스냅샷에 있는 이유다 — 해시 비교가 빠져도 박스 축 테스트는 초록이다.
    from tools.warp_gate_calib import changed_pairs

    before = {"23": {"boxes": [[10, 20]], "crop_sha": ["aa"]}}
    after = {"23": {"boxes": [[10, 20]], "crop_sha": ["zz"]}}
    assert changed_pairs(before, after, {(23, 0)})["moved"] == [(23, 0)]


def test_changed_pairs_reports_a_row_that_the_after_snapshot_no_longer_has():
    # before 3행 → after 1행. 조용히 건너뛰면 폴백이 included 학습쌍 행을 **없앤** 회귀가
    # "변화 0건"으로 보고된다(축 ②-a의 침묵 붕괴).
    from tools.warp_gate_calib import changed_pairs

    before = {"23": {"boxes": [[10, 20], [30, 40], [50, 60]], "crop_sha": ["a", "b", "c"]}}
    after = {"23": {"boxes": [[10, 20]], "crop_sha": ["a"]}}
    result = changed_pairs(before, after, {(23, 0), (23, 1), (23, 2)})
    assert result == {"moved": [], "vanished": [(23, 1), (23, 2)]}


def test_changed_pairs_reports_every_row_of_a_job_that_vanished_entirely():
    from tools.warp_gate_calib import changed_pairs

    before = {"23": {"boxes": [[10, 20], [30, 40]], "crop_sha": ["a", "b"]}}
    assert changed_pairs(before, {}, {(23, 0), (23, 1)})["vanished"] == [(23, 0), (23, 1)]


def test_changed_pairs_rejects_a_snapshot_entry_without_crop_sha():
    # --baseline은 사용자가 지정하는 외부 JSON이다(_job_key와 같은 이유로 신뢰 불가) —
    # 스키마가 어긋나면 맥락 없는 IndexError/KeyError 대신 무엇이 잘못됐는지 말해야 한다.
    from tools.warp_gate_calib import changed_pairs

    before = {"23": {"boxes": [[10, 20]]}}
    with pytest.raises(ValueError, match="crop_sha"):
        changed_pairs(before, {"23": {"boxes": [[10, 20]], "crop_sha": ["aa"]}}, {(23, 0)})


def test_changed_pairs_rejects_a_snapshot_entry_whose_arrays_disagree_in_length():
    # boxes 2행 / crop_sha 1행이면 row_index 1에서 crop_sha만 IndexError로 터진다.
    from tools.warp_gate_calib import changed_pairs

    before = {"23": {"boxes": [[10, 20], [30, 40]], "crop_sha": ["aa"]}}
    after = {"23": {"boxes": [[10, 20], [30, 40]], "crop_sha": ["aa", "bb"]}}
    with pytest.raises(ValueError, match="길이"):
        changed_pairs(before, after, {(23, 1)})


def test_changed_pairs_rejects_a_snapshot_entry_without_boxes():
    # `.get("boxes", [])`로 흡수하면 boxes가 통째로 빠진 항목의 included 행 전부가
    # vanished도 moved도 아닌 채 조용히 스킵돼 '변화 0건'으로 보고된다.
    from tools.warp_gate_calib import changed_pairs

    before = {"23": {"crop_sha": ["aa"]}}
    with pytest.raises(ValueError, match="boxes"):
        changed_pairs(before, {"23": {"boxes": [[10, 20]], "crop_sha": ["aa"]}}, {(23, 0)})


def test_changed_pairs_orders_rows_deterministically():
    # set 순회 순서에 산출이 끌려가면 같은 입력에서도 리포트 행 순서가 실행마다 달라진다.
    from tools.warp_gate_calib import changed_pairs

    before = {str(j): {"boxes": [[10, 20]] * 3, "crop_sha": ["a"] * 3} for j in (4, 7, 23)}
    after = {str(j): {"boxes": [[11, 21]] * 3, "crop_sha": ["a"] * 3} for j in (4, 7, 23)}
    pairs = {(23, 1), (7, 0), (23, 0), (4, 2)}  # 실측: 이 set의 순회 순서는 정렬 순서가 아니다

    assert changed_pairs(before, after, pairs)["moved"] == [(4, 2), (7, 0), (23, 0), (23, 1)]


def test_snapshot_diff_ignores_amount_left_added_by_issue50():
    # amount_left는 금액 크롭 좌측 실측값(#50)으로, 이 게이트가 지키는 품목 크롭 identity
    # (boxes·crop_sha, 축 ②-a)가 아니다 — 패치 이전 베이스라인에는 키 자체가 없으므로
    # identity에 넣으면 실제 경계가 612 그대로인 잡까지 전부 changed로 오판된다.
    from tools.warp_gate_calib import snapshot_diff

    before = {"59": {"boxes": [[10, 20]], "crop_sha": ["aa"]}}
    after = {"59": {"boxes": [[10, 20]], "crop_sha": ["aa"], "amount_left": 636}}
    assert snapshot_diff(before, after)["changed"] == []
