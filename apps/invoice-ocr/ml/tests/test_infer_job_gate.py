"""infer_job의 warp 게이트 분기 — 실패 시 행 추론을 건너뛰고 rows=[]를 반환한다.

infer_photo는 모듈 최상단에서 torch를 import해 CI에 없다. 그래서 그 모듈만 가짜로 갈아끼우고
cv2·numpy·grid_v4·warp_gate는 진짜를 쓴다 — 게이트 배선 자체를 실제로 실행해 검증한다.
"""

import sys
import types

import pytest

pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

import handwriting  # noqa: E402
from handwriting.grid_v4 import WARP_H, WARP_W  # noqa: E402


def _install_fake_infer_photo(monkeypatch, warped, calls):
    """handwriting.infer_photo를 가짜로 교체한다(quad=전체영역, deskew=0 → warp는 항등)."""
    m = types.ModuleType("handwriting.infer_photo")
    m.TOPK = 5
    m.load_bgr_path = lambda path: warped
    m.form_quad_robust = lambda bgr: np.array(
        [[0, 0], [WARP_W, 0], [WARP_W, WARP_H], [0, WARP_H]], np.float32
    )
    m.deskew_angle = lambda w: 0.0
    m.rotate = lambda img, ang: img
    m.topk = lambda sims, lab, k: [(lab[0], float(sims[0]))]

    def extract_rows_for_job(w, model, qwen, tmp_dir, counter, device):
        # 반환 arity(8)는 실제 handwriting.infer_photo.extract_rows_for_job 시그니처
        # (news, crops, queries, amounts, prop, ys, P, bands) 그대로다 — 정본은 infer_photo.py.
        calls.append("extract_rows_for_job")
        return (
            [object()],
            [np.zeros((10, 10, 3), np.uint8)],
            np.ones((1, 2), np.float32),
            [(364, "364")],
            None,
            None,
            None,
            None,
        )

    m.extract_rows_for_job = extract_rows_for_job
    monkeypatch.setattr(handwriting, "infer_photo", m, raising=False)
    monkeypatch.setitem(sys.modules, "handwriting.infer_photo", m)


def _models(retrieval_version="a1b2c3d4e5f6"):
    # E @ queries[i]만 실제로 쓰인다. retrieval_version은 스탬프 배선 검증용.
    # ModelBundle은 속성으로 읽는 계약이라 생성도 키워드로 한다(위치 인자 6개는 순서 실수가
    # 조용히 통과하는 바로 그 형태다).
    from worker.main import ModelBundle

    return ModelBundle(
        item_model=None,
        emb=np.ones((1, 2), np.float32),
        labs=["삼겹살"],
        qwen=None,
        device="cpu",
        retrieval_version=retrieval_version,
    )


def test_gate_failure_returns_empty_rows_and_skips_extraction(
    monkeypatch, tmp_path, make_warped, capsys
):
    from handwriting.infer_job import infer_job

    calls = []
    _install_fake_infer_photo(monkeypatch, make_warped(n_lines=0), calls)  # 백지 = 격자 없음

    out = infer_job("ignored.jpg", _models(), tmp_path, 7)

    from handwriting.infer_job import ITEM_CONF_THRESHOLD

    assert out == {
        "rows": [],
        "supply_sum": 0,
        "warp_ok": False,
        "item_conf_threshold": ITEM_CONF_THRESHOLD,
        "retrieval_version": "a1b2c3d4e5f6",
    }
    assert calls == []  # 오검출 워프 기반 행 추론·크롭을 아예 하지 않는다
    assert (tmp_path / "warped.png").exists()  # 진단용 워프는 남긴다
    assert not (tmp_path / "row-0.png").exists()
    logged = capsys.readouterr().out
    assert "[warp-gate] job=7 demoted std=" in logged  # 강등 원인을 로그만으로 재구성 가능해야 함
    from handwriting.warp_gate import ENH_MIN_HLINES, MIN_HLINES

    # "thresholds=" 단독 단언은 "enh_thresholds="의 부분문자열이라 표준 임계 블록이 통째로
    # 지워지거나 enh 값으로 치환돼도 통과한다(M2) — 표준·enh 블록을 각자 앵커로 구분한다.
    assert (
        f"min_hlines={MIN_HLINES}" in logged
    )  # 재캘리브 후에도 과거 로그를 그 시점 기준으로 해석 가능해야 함
    assert f"min_hlines={ENH_MIN_HLINES}" in logged


def test_gate_quad_missing_logs_marker(monkeypatch, tmp_path, capsys):
    """쿼드 자체를 못 찾은 경우도 마커를 남겨 강등 원인(쿼드 미검출 vs 격자 부정합)을 구분한다."""
    from handwriting.infer_job import infer_job

    m = types.ModuleType("handwriting.infer_photo")
    m.load_bgr_path = lambda path: None
    m.form_quad_robust = lambda bgr: None
    monkeypatch.setattr(handwriting, "infer_photo", m, raising=False)
    monkeypatch.setitem(sys.modules, "handwriting.infer_photo", m)

    out = infer_job("ignored.jpg", _models(), tmp_path, 99)

    from handwriting.infer_job import ITEM_CONF_THRESHOLD

    assert out == {
        "rows": [],
        "supply_sum": 0,
        "warp_ok": False,
        "item_conf_threshold": ITEM_CONF_THRESHOLD,
        "retrieval_version": "a1b2c3d4e5f6",
    }
    assert "[warp-gate] job=99 quad_missing" in capsys.readouterr().out


def test_gate_failure_on_half_width_grid(monkeypatch, tmp_path, make_warped):
    # 잡 39 유형 — 격자가 좌반에만 있는 워프.
    from handwriting.infer_job import infer_job

    calls = []
    _install_fake_infer_photo(monkeypatch, make_warped(x_end=450), calls)

    out = infer_job("ignored.jpg", _models(), tmp_path, 39)

    assert out["warp_ok"] is False
    assert out["rows"] == []
    assert calls == []


def test_gate_pass_keeps_existing_row_extraction(monkeypatch, tmp_path, make_warped):
    from handwriting.infer_job import infer_job

    calls = []
    _install_fake_infer_photo(monkeypatch, make_warped(), calls)  # 정상 격자

    out = infer_job("ignored.jpg", _models(), tmp_path, 42)

    assert out["warp_ok"] is True
    assert calls == ["extract_rows_for_job"]
    assert out["rows"][0]["crop_ref"] == "job-42/row-0"
    assert out["supply_sum"] == 364000
    assert out["retrieval_version"] == "a1b2c3d4e5f6"
    assert (tmp_path / "row-0.png").exists()


def test_bundle_without_a_fingerprint_omits_the_key_on_the_gate_pass_path(
    monkeypatch, tmp_path, make_warped
):
    """지문을 못 얻은 워커 세션(code_version 실패 등)은 키 자체를 넣지 않는다.

    자리표시자("unknown")를 넣으면 서로 다른 retrieval 상태가 한 코호트로 합쳐진다(Issue #49).
    지문 있는 번들만 테스트하면 infer_job 배선이 `stamp or "unknown"`으로 회귀해도 아무
    테스트가 빨개지지 않는다 — assemble 단위 테스트는 assemble만 검증한다.
    """
    from handwriting.infer_job import infer_job

    _install_fake_infer_photo(monkeypatch, make_warped(), [])

    out = infer_job("ignored.jpg", _models(retrieval_version=None), tmp_path, 42)

    assert out["warp_ok"] is True
    assert "retrieval_version" not in out


def test_bundle_without_a_fingerprint_omits_the_key_on_the_gate_failure_path(
    monkeypatch, tmp_path, make_warped
):
    from handwriting.infer_job import infer_job

    _install_fake_infer_photo(monkeypatch, make_warped(n_lines=0), [])

    out = infer_job("ignored.jpg", _models(retrieval_version=None), tmp_path, 7)

    assert out["warp_ok"] is False
    assert "retrieval_version" not in out


# ── enh 마스크 2단 폴백(Issue #60) ────────────────────────────────────────
# 옅은 파랑 격자 — b−r=10이라 표준 blue_mask는 격자를 통째로 놓치고 blue_mask_enh만 살린다.
# 잡 59~63 오강등(정상 전표가 청색 채도 하나로 강등된 사건)의 합성 재현이다.
FAINT_BLUE = (250, 120, 240)


def test_enhanced_metrics_are_not_computed_when_the_standard_gate_passes(
    monkeypatch, make_warped, capsys
):
    # 분기 닫힘 — 표준 통과 잡은 폴백 분기에 진입조차 하지 않아야 한다. 진입하면 정상 잡
    # 전량에 enh 측정 비용이 붙고 pass→pass 지표 동일성 전제도 흔들린다.
    import handwriting.infer_job as ij

    seen = []
    original = ij.compute_metrics

    def spy(w, **kw):
        seen.append(kw.get("enhanced", False))
        return original(w, **kw)

    monkeypatch.setattr(ij, "compute_metrics", spy)

    assert ij._warp_gate_passes(make_warped(), job_id=1) is True
    assert seen == [False]
    assert "[warp-gate]" not in capsys.readouterr().out


def test_faint_sheet_is_rescued_by_the_enhanced_mask(make_warped, capsys):
    import handwriting.infer_job as ij

    assert ij._warp_gate_passes(make_warped(color=FAINT_BLUE), job_id=59) is True
    out = capsys.readouterr().out
    assert "rescued-by-enh" in out
    assert "job=59" in out
    # 강등 로그(:225-227)와 미러링 — 구제 경로도 진단에 필요한 지표 두 벌을 실제로 싣는지
    # 실행으로 고정한다(M1). 과거엔 "rescued-by-enh"/"job=59"만 단언해 std=·enh=·
    # _thresholds_text() 3종을 각각 제거해도 생존했다.
    assert "std=WarpGateMetrics(" in out
    assert "enh=WarpGateMetricsEnh(" in out
    assert "enh_thresholds=" in out


def test_broken_warp_is_demoted_with_both_metric_sets_in_the_log(make_warped, capsys):
    # 두 벌 다 로그에 실려야 배포 후 로그만 보고 어느 축에서 걸렸는지 판별할 수 있다.
    import handwriting.infer_job as ij

    assert ij._warp_gate_passes(make_warped(n_lines=0), job_id=24) is False
    out = capsys.readouterr().out
    assert "demoted" in out
    assert "std=WarpGateMetrics(" in out
    assert "enh=WarpGateMetricsEnh(" in out
    assert "enh_thresholds=" in out


def test_enhanced_metrics_are_computed_at_most_once(monkeypatch, make_warped):
    # 재귀·루프 없음 — enh 측정은 표준 실패 시 정확히 1회다.
    import handwriting.infer_job as ij

    seen = []
    original = ij.compute_metrics

    def spy(w, **kw):
        seen.append(kw.get("enhanced", False))
        return original(w, **kw)

    monkeypatch.setattr(ij, "compute_metrics", spy)

    ij._warp_gate_passes(make_warped(n_lines=0), job_id=24)
    assert seen == [False, True]


def test_warp_gate_logs_flush_immediately_on_rescue_and_demote(monkeypatch, make_warped):
    # _warp_gate_passes docstring이 "flush=True 필수(launchd 상시 폴링 프로세스 — 파일
    # 리다이렉트 시 블록 버퍼링에 걸리면 로그가 한참 뒤에야 보인다)"라고 선언한 성질을
    # 실행으로 고정한다(M3). capsys는 블록 버퍼링을 재현 못 하지만
    # `monkeypatch.setattr("builtins.print", spy)`는 kwargs를 그대로 잡는다.
    import handwriting.infer_job as ij

    calls = []

    def spy(*args, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("builtins.print", spy)

    assert ij._warp_gate_passes(make_warped(color=FAINT_BLUE), job_id=59) is True  # rescue 경로
    assert ij._warp_gate_passes(make_warped(n_lines=0), job_id=24) is False  # demote 경로

    assert len(calls) == 2
    assert all(kwargs.get("flush") is True for kwargs in calls)
