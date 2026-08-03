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


def test_axis_margins_ignores_jobs_above_the_snapshot_bound():
    # 캘리브 도중 생긴 신규 잡이 검증 없이 정상군 최악값을 움직이면 임계 근거가 오염된다.
    records = [
        _rec(50, LABEL_NORMAL, enh_hline=20),
        _rec(64, LABEL_NORMAL, enh_hline=3),
        _rec(24, LABEL_SUSPECT, enh_hline=6),
    ]
    m = axis_margins([r for r in records if r["job_id"] <= 63], "enh")
    assert m["hline_count"]["worst_normal"] == 20


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
