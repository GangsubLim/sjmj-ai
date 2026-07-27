"""tools.warp_gate_report 순수 계층 단위테스트(ssh/cv2 비의존, 합성 데이터만)."""

import json
from dataclasses import fields

import pytest

from handwriting.warp_gate import WarpGateMetrics  # stdlib만 쓰는 모듈이라 코어 venv에서도 안전
from tools.warp_gate_report import (
    METRIC_KEYS,
    evaluate_cached,
    job_status,
    main,
    parse_job_rows_tsv,
    render_gate_report,
    summarize_gate,
)


def _rec(job_id=1, status="gate_target", gate_pass=True, suspect=False, prev=True):
    return {
        "job_id": job_id,
        "status": status,
        "prev_warp_ok": prev,
        "suspect": suspect,
        "metrics": (
            None
            if status != "gate_target"
            else {
                "hline_count": 15,
                "pitch_dev": 0.02,
                "blue_ratio_left": 0.031,
                "blue_ratio_right": 0.030,
            }
        ),
        "gate_pass": gate_pass if status == "gate_target" else None,
    }


# --- TSV 파싱 ---


def test_parse_job_rows_tsv_maps_boolean_strings():
    # SQL이 result_json->>'$.warp_ok'로 값 하나만 뽑으므로 로컬 JSON 파싱이 없다.
    text = "id\tresult_json->>'$.warp_ok'\n1\ttrue\n2\tfalse\n"
    assert parse_job_rows_tsv(text) == [
        {"job_id": 1, "warp_ok": True},
        {"job_id": 2, "warp_ok": False},
    ]


def test_parse_job_rows_tsv_treats_null_as_unknown():
    # result_json이 NULL이거나 warp_ok 키가 없는 잡(미처리·failed) — 둘 다 SQL이 NULL을 준다.
    text = "id\tresult_json->>'$.warp_ok'\n3\tNULL\n4\t\n"
    assert parse_job_rows_tsv(text) == [
        {"job_id": 3, "warp_ok": None},
        {"job_id": 4, "warp_ok": None},
    ]


def test_parse_job_rows_tsv_rejects_unexpected_value():
    # 예상 밖 표현을 None으로 흡수하면 무회귀 분모가 조용히 줄어 캘리브 결론이 왜곡된다.
    with pytest.raises(ValueError, match="warp_ok"):
        parse_job_rows_tsv("id\tresult_json->>'$.warp_ok'\n7\t{\"rows\": []}\n")


# --- 분모 분류 ---


def test_job_status_gate_target_when_warped_png_exists():
    assert job_status(True, has_warped=True) == "gate_target"
    assert job_status(None, has_warped=True) == "gate_target"
    # warp_ok=False라도 warped.png가 남아 있으면 gate_target이다 — 이전에 게이트가
    # 강등시킨 잡도 (임계 재캘리브 등으로) 재평가 대상에 포함돼야 한다는 뜻이다.
    # has_warped가 warp_ok보다 우선하는 분기 순서를 고정한다.
    assert job_status(False, has_warped=True) == "gate_target"


def test_job_status_quad_missing_when_no_warp_and_already_false():
    assert job_status(False, has_warped=False) == "quad_missing"


def test_job_status_warp_missing_when_no_image_but_claimed_ok():
    assert job_status(True, has_warped=False) == "warp_missing"
    assert job_status(None, has_warped=False) == "warp_missing"


# --- 집계 ---


def test_summarize_gate_counts_denominator_and_outcomes():
    recs = [
        _rec(1),
        _rec(2),
        _rec(34, gate_pass=False, suspect=True),
        _rec(39, gate_pass=False, suspect=True),
        _rec(40, gate_pass=False),  # 정상 잡인데 실패 = 회귀 후보
        _rec(50, status="quad_missing", prev=False),
        _rec(51, status="warp_missing"),
    ]
    s = summarize_gate(recs)
    assert s["n_total"] == 7
    assert s["n_gate_target"] == 5
    assert s["n_quad_missing"] == 1
    assert s["n_warp_missing"] == 1
    assert s["n_pass"] == 2
    assert s["n_fail"] == 3
    assert s["n_suspect_demoted"] == 2  # 잡 34·39 강등 성공
    assert s["regressions"] == [40]  # 무회귀 분모에서 실패한 잡


def test_summarize_gate_regressions_empty_when_only_suspects_fail():
    s = summarize_gate([_rec(1), _rec(39, gate_pass=False, suspect=True)])
    assert s["regressions"] == []


def test_summarize_gate_excludes_never_true_jobs_from_regressions():
    # result_json이 NULL이었거나 이미 warp_ok=false였던 잡은 '무회귀' 판단 대상이 아니다.
    recs = [
        _rec(1),
        _rec(60, gate_pass=False, prev=None),
        _rec(61, gate_pass=False, prev=False),
    ]
    s = summarize_gate(recs)
    assert s["regressions"] == []
    assert s["unknown_fail"] == [60, 61]
    assert s["n_prev_true"] == 1


def test_metric_margins_reports_separation_for_each_metric():
    normal = _rec(1)
    weak = _rec(34, gate_pass=False, suspect=True)
    weak["metrics"] = {
        "hline_count": 6,
        "pitch_dev": 0.52,
        "blue_ratio_left": 0.010,
        "blue_ratio_right": 0.0,
    }
    m = summarize_gate([normal, weak])["margins"]
    assert m["hline_count"]["worst_normal"] == 15
    assert m["hline_count"]["best_suspect"] == 6
    assert m["hline_count"]["gap"] == 9
    assert m["pitch_dev"]["gap"] > 0  # 작을수록 좋은 지표는 방향이 반대


def test_summarize_gate_regression_denominator_excludes_suspects():
    # 무회귀 문장의 분모는 regressions(분자)와 같은 집합이어야 한다 — suspect를 포함하는
    # n_prev_true를 분모로 쓰면 분자·분모가 다른 집합이라 무회귀 주장이 성립하지 않는다.
    recs = [_rec(1), _rec(34, gate_pass=False, suspect=True), _rec(40, gate_pass=False)]
    s = summarize_gate(recs)
    assert s["n_prev_true"] == 3  # 원시 집계는 suspect 포함(그대로 유지)
    assert s["n_regression_denom"] == 2
    assert s["regressions"] == [40]


def test_metric_keys_track_dto_fields_plus_derived():
    # 원시 지표는 DTO에서 파생한다 — WarpGateMetrics에 필드가 늘면 표가 자동으로 따라간다.
    raw = tuple(f.name for f in fields(WarpGateMetrics))
    assert METRIC_KEYS[: len(raw)] == raw
    assert set(METRIC_KEYS) - set(raw) == {"blue_ratio_min", "blue_asym"}


def test_metric_margins_covers_gate_predicate_metrics():
    # 게이트는 원시 L·R 각각이 아니라 min(L,R)과 좌우 비대칭도를 검사한다(③·④ 규칙).
    normal = _rec(1)
    weak = _rec(39, gate_pass=False, suspect=True)
    weak["metrics"] = {
        "hline_count": 12,
        "pitch_dev": 0.30,
        "blue_ratio_left": 0.033,  # 좌측만 멀쩡 — 원시 L만 보면 정상군과 겹쳐 분리가 안 된다
        "blue_ratio_right": 0.0,
    }
    m = summarize_gate([normal, weak])["margins"]
    assert m["blue_ratio_left"]["gap"] < 0  # 원시 L 단독으로는 분리 실패
    assert m["blue_ratio_min"]["worst_normal"] == 0.030
    assert m["blue_ratio_min"]["best_suspect"] == 0.0
    assert m["blue_ratio_min"]["gap"] > 0
    # blue_asym은 작을수록 좋은 지표 — 의심군 최선값이 1.0(한쪽 전무)이라 gap이 양수다.
    assert m["blue_asym"]["best_suspect"] == pytest.approx(1.0)
    assert m["blue_asym"]["gap"] > 0


def test_summarize_gate_counts_unreadable_warps():
    s = summarize_gate([_rec(1), _rec(70, status="warp_unreadable")])
    assert s["n_unreadable"] == 1
    assert s["n_gate_target"] == 1


# --- 렌더 ---


def test_render_gate_report_contains_denominator_and_job_table():
    md = render_gate_report(
        [_rec(1), _rec(39, gate_pass=False, suspect=True), _rec(50, status="quad_missing")],
        {"fetched_at": "2026-07-27T00:00:00", "host": "macmini"},
    )
    assert "게이트 평가 대상" in md
    assert "quad_missing" in md
    assert "hline" in md
    assert "| 39 |" in md
    assert "회귀" in md
    assert "분리 마진" in md


def test_render_gate_report_denominator_identity_includes_unreadable():
    # 항등식이 성립해야 한다 — warp_unreadable 항이 빠지면 산술이 깨져 리포트가 자기모순이다.
    recs = [
        _rec(1),
        _rec(50, status="quad_missing", prev=False),
        _rec(51, status="warp_missing"),
        _rec(70, status="warp_unreadable"),
    ]
    md = render_gate_report(recs, {})
    assert (
        "전체 잡 4 = 게이트 평가 대상 1 + quad_missing 1 + warp_missing 1 + warp_unreadable 1" in md
    )


def test_render_gate_report_regression_sentence_uses_suspect_free_denominator():
    # suspect 34는 이전 warp_ok=true지만 무회귀 분모가 아니다 — 분모는 1이어야 한다.
    md = render_gate_report([_rec(1), _rec(34, gate_pass=False, suspect=True)], {})
    assert "정상 잡(suspect 제외) 1 중 실패(회귀): 없음" in md


def test_render_gate_report_warns_when_cached_warped_count_mismatches():
    # fetch가 센 warped 수와 이미지가 있는 잡 수가 어긋나면 stale 캐시다.
    md = render_gate_report([_rec(1)], {"n_warped": 3})
    assert "캐시 불일치" in md


def test_render_gate_report_has_no_cache_warning_when_counts_match():
    md = render_gate_report([_rec(1), _rec(70, status="warp_unreadable")], {"n_warped": 2})
    assert "캐시 불일치" not in md


def test_render_gate_report_reports_suspect_request_coverage():
    md = render_gate_report(
        [_rec(1), _rec(34, gate_pass=False, suspect=True)], {"suspects": [34, 999]}
    )
    assert "요청 suspect 2건 중 평가 대상 1건" in md
    assert "999" in md  # jobs에 없는 id는 경고로 드러난다(오타·다른 서버)


def test_render_gate_report_marks_suspect_in_excluded_list():
    md = render_gate_report([_rec(38, status="warp_missing", suspect=True)], {"suspects": [38]})
    assert "job 38: warp_missing" in md
    assert "suspect 요청됨" in md


def test_render_gate_report_footnotes_zero_baseline_margin():
    # 정상군 blue_asym 최악값이 0이면 마진% 분모가 1.0으로 대체된다 — 백분율이 아님을 밝힌다.
    normal = _rec(1)
    normal["metrics"] = {**normal["metrics"], "blue_ratio_left": 0.03, "blue_ratio_right": 0.03}
    weak = _rec(39, gate_pass=False, suspect=True)
    weak["metrics"] = {**weak["metrics"], "blue_ratio_left": 0.03, "blue_ratio_right": 0.0}
    md = render_gate_report([normal, weak], {})
    assert "마진% 분모를 1.0으로 대체" in md


# --- 평가 경로 (imread 주입 — Fake 어댑터 관례) ---


def _write_cache(cache, jobs, *, warped_ids=()):
    """fetch가 만드는 캐시 레이아웃을 합성한다(warped.png는 빈 파일 — 내용은 Fake가 준다)."""
    (cache / "jobs.json").write_text(json.dumps(jobs))
    (cache / "meta.json").write_text(json.dumps({"host": "h", "n_warped": len(warped_ids)}))
    for job_id in warped_ids:
        job_dir = cache / "warped" / f"job-{job_id}"
        job_dir.mkdir(parents=True)
        (job_dir / "warped.png").write_bytes(b"")
    return cache


def test_evaluate_cached_demotes_unreadable_image(tmp_path):
    # imread가 None을 주면(손상·권한) 전수 리포트를 죽이지 않고 분모 밖으로 강등한다.
    cache = _write_cache(tmp_path, [{"job_id": 1, "warp_ok": True}], warped_ids=(1,))
    recs = evaluate_cached(cache, set(), imread=lambda _path: None)
    assert recs[0]["status"] == "warp_unreadable"
    assert recs[0]["metrics"] is None
    assert recs[0]["gate_pass"] is None


def test_evaluate_cached_marks_requested_suspects(tmp_path):
    jobs = [{"job_id": 1, "warp_ok": True}, {"job_id": 34, "warp_ok": True}]
    cache = _write_cache(tmp_path, jobs, warped_ids=(34,))
    recs = evaluate_cached(cache, {34}, imread=lambda _path: None)
    assert [r["suspect"] for r in recs] == [False, True]


def test_evaluate_cached_wires_gate_verdict_from_metrics(tmp_path, make_warped):
    # 정상 합성 워프 → compute_metrics → evaluate_warp 배선이 record에 실리는지 고정한다.
    cache = _write_cache(tmp_path, [{"job_id": 1, "warp_ok": True}], warped_ids=(1,))
    warped = make_warped()
    recs = evaluate_cached(cache, set(), imread=lambda _path: warped)
    assert recs[0]["status"] == "gate_target"
    assert recs[0]["gate_pass"] is True
    assert recs[0]["metrics"]["hline_count"] == 16


# --- CLI ---


def test_report_without_cache_exits_with_fetch_guidance(tmp_path):
    # fetch 전에 report를 돌리면 맨 FileNotFoundError 대신 다음 행동을 지시해야 한다.
    with pytest.raises(SystemExit) as excinfo:
        main(["--cache", str(tmp_path), "report"])
    assert "fetch" in str(excinfo.value)
