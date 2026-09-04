"""infer_job의 warp 게이트 분기 — 실패 시 행 추론을 건너뛰고 rows=[]를 반환한다.

infer_photo는 모듈 최상단에서 torch를 import해 CI에 없다. 그래서 그 모듈만 가짜로 갈아끼우고
cv2·numpy·grid_v4·warp_gate는 진짜를 쓴다 — 게이트 배선 자체를 실제로 실행해 검증한다.
"""

import sys
import types
from pathlib import Path

import pytest

from tests.conftest import import_scopes

pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

import handwriting  # noqa: E402
import handwriting.corner_dl as corner_dl  # noqa: E402
from handwriting.grid_v4 import WARP_H, WARP_W  # noqa: E402

FULL_QUAD = np.array([[0, 0], [WARP_W, 0], [WARP_W, WARP_H], [0, WARP_H]], np.float32)


def _install_fake_infer_photo(monkeypatch, warped, calls):
    """handwriting.infer_photo를 가짜로 교체한다(deskew=0 → warp는 항등).

    운영 경로의 quad 공급은 corner_dl.quad_candidates가 소유한다(_gated_warp가 그걸 순회하며
    게이트 인지형으로 고른다) — form_quad_best는 게이트 없는 데모 CLI 전용 래퍼일 뿐 여기선
    쓰이지 않는다. 색 경로(form_quad_robust)만 전체 캔버스 quad로 고정한다 — rectify 실검출을
    태우면 합성 이미지에 의존한 취약한 기대값이 되고 검증 대상(분기)이 흐려진다.
    """
    m = types.ModuleType("handwriting.infer_photo")
    m.TOPK = 5
    m.load_bgr_path = lambda path: warped
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
    monkeypatch.setattr(corner_dl, "form_quad_robust", lambda bgr: FULL_QUAD)


def _models(retrieval_version="a1b2c3d4e5f6", aligner=None):
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
        aligner=aligner,
    )


def test_gate_failure_returns_empty_rows_and_skips_extraction(
    monkeypatch, tmp_path, make_warped, capsys
):
    from handwriting.infer_job import infer_job

    calls = []
    _install_fake_infer_photo(monkeypatch, make_warped(n_lines=0), calls)  # 백지 = 격자 없음

    out = infer_job("ignored.jpg", _models(), tmp_path, 7, None)

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
    # 앵커에 블록 접두사(선행 공백 포함)를 넣는다: `min_hlines=<값>`만 쓰면 두 단언이 값이
    # 다르다는 우연에만 기댄다. ENH_MIN_HLINES = max(MIN_HLINES, floor(...)) 도출식상 두 값이
    # 같아지는 재캘리브가 설계상 정상 도달 가능하고, 그 순간 두 단언이 같은 부분문자열로
    # 붕괴해 "표준 블록이 통째로 지워져도 통과"라는 M2 변이가 되살아난다.
    # 재캘리브 후에도 과거 로그를 그 시점 기준으로 해석 가능해야 하므로 값도 함께 고정한다.
    assert f" thresholds=(min_hlines={MIN_HLINES}," in logged
    assert f" enh_thresholds=(min_hlines={ENH_MIN_HLINES}," in logged


def test_gate_quad_missing_logs_marker(monkeypatch, tmp_path, capsys):
    """쿼드 자체를 못 찾은 경우도 마커를 남겨 강등 원인(쿼드 미검출 vs 격자 부정합)을 구분한다."""
    from handwriting.infer_job import infer_job

    m = types.ModuleType("handwriting.infer_photo")
    m.load_bgr_path = lambda path: None
    monkeypatch.setattr(handwriting, "infer_photo", m, raising=False)
    monkeypatch.setitem(sys.modules, "handwriting.infer_photo", m)
    # _models()가 aligner=None이므로 후보는 색 경로 하나뿐이다 — 그 색 경로도 None이면
    # 후보 0개가 되어 quad_missing 경로가 그대로 성립한다.
    monkeypatch.setattr(corner_dl, "form_quad_robust", lambda bgr: None)

    out = infer_job("ignored.jpg", _models(), tmp_path, 99, None)

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

    out = infer_job("ignored.jpg", _models(), tmp_path, 39, None)

    assert out["warp_ok"] is False
    assert out["rows"] == []
    assert calls == []


def test_gate_pass_keeps_existing_row_extraction(monkeypatch, tmp_path, make_warped):
    from handwriting.infer_job import infer_job

    calls = []
    _install_fake_infer_photo(monkeypatch, make_warped(), calls)  # 정상 격자

    out = infer_job("ignored.jpg", _models(), tmp_path, 42, None)

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

    out = infer_job("ignored.jpg", _models(retrieval_version=None), tmp_path, 42, None)

    assert out["warp_ok"] is True
    assert "retrieval_version" not in out


def test_bundle_without_a_fingerprint_omits_the_key_on_the_gate_failure_path(
    monkeypatch, tmp_path, make_warped
):
    from handwriting.infer_job import infer_job

    _install_fake_infer_photo(monkeypatch, make_warped(n_lines=0), [])

    out = infer_job("ignored.jpg", _models(retrieval_version=None), tmp_path, 7, None)

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
        calls.append((args, kwargs))

    monkeypatch.setattr("builtins.print", spy)

    assert ij._warp_gate_passes(make_warped(color=FAINT_BLUE), job_id=59) is True  # rescue 경로
    assert ij._warp_gate_passes(make_warped(n_lines=0), job_id=24) is False  # demote 경로

    # args까지 모아 `[warp-gate]` 라인만 걸러 단언한다. 전체 print 개수를 고정하면 (1) 그 두
    # 번이 실제로 구제·강등 라인이었는지 확인할 수단이 없고 (2) 경유하는 grid_v4/canon에
    # 무관한 진단 print가 하나만 늘어도 flush와 무관한 이유로 RED가 된다.
    gate_calls = [
        kwargs
        for args, kwargs in calls
        if args and isinstance(args[0], str) and args[0].startswith("[warp-gate]")
    ]
    assert len(gate_calls) == 2  # 구제 1 + 강등 1
    assert all(kwargs.get("flush") is True for kwargs in gate_calls)


def test_rescued_faint_sheet_reaches_row_extraction_through_infer_job(
    monkeypatch, tmp_path, make_warped
):
    # 위 폴백 테스트 4종은 전부 private _warp_gate_passes만 직접 불러 True/로그까지만 본다 —
    # 구제된 워프가 실제로 warp_ok=True로 직렬화되고 extract_rows_for_job·크롭 저장까지
    # 도달하는지는 아무 테스트도 고정하지 않는다. 호출부에 표준 판정 재확인 같은 조건이
    # 덧붙어 구제가 무력화돼도 신규 테스트는 전부 초록이다. 통과 경로의
    # test_gate_pass_keeps_existing_row_extraction과 같은 강도로 사용자 가시 계약을 건다.
    from handwriting.infer_job import infer_job

    calls = []
    _install_fake_infer_photo(monkeypatch, make_warped(color=FAINT_BLUE), calls)

    out = infer_job("ignored.jpg", _models(), tmp_path, 59, None)

    assert out["warp_ok"] is True
    assert calls == ["extract_rows_for_job"]
    assert out["rows"][0]["crop_ref"] == "job-59/row-0"
    assert out["supply_sum"] == 364000
    assert (tmp_path / "row-0.png").exists()


# ── DL 코너검출 quad 공급 (게이트 인지형 선택) ──────────────────────────


class _Aligner:
    """CornerModel 대역 — 고정 quad를 돌려주고 받은 이미지 배열 객체를 기록한다."""

    def __init__(self, quad):
        self._quad, self.seen = quad, []

    def quad(self, bgr):
        # 형상(WARP_H, WARP_W, 3)은 워프 결과와도 같아 '원본을 받았다'를 가르지 못한다 —
        # 객체 자체를 기록해 identity로 못 박는다.
        self.seen.append(bgr)
        return self._quad


def test_infer_job_prefers_the_dl_quad_over_the_color_path(monkeypatch, tmp_path, make_warped):
    from handwriting.infer_job import infer_job

    calls = []
    bgr = make_warped()
    _install_fake_infer_photo(monkeypatch, bgr, calls)
    monkeypatch.setattr(
        corner_dl, "form_quad_robust", lambda bgr: pytest.fail("DL 성공 시 색 경로 금지")
    )
    aligner = _Aligner(FULL_QUAD)

    out = infer_job("ignored.jpg", _models(aligner=aligner), tmp_path, 34, None)

    assert len(aligner.seen) == 1
    assert aligner.seen[0] is bgr  # 워프 결과가 아니라 EXIF 정위치 원본을 그대로 받는다
    assert out["warp_ok"] is True
    assert calls == ["extract_rows_for_job"]


def test_infer_job_falls_back_to_the_color_path_when_the_dl_quad_is_missing(
    monkeypatch, tmp_path, make_warped, capsys
):
    # 스파이크 실패 3건(54·86·89)의 실물 시나리오 — 색 경로가 정상 처리하던 잡들이다.
    from handwriting.infer_job import infer_job

    calls = []
    _install_fake_infer_photo(monkeypatch, make_warped(), calls)

    out = infer_job("ignored.jpg", _models(aligner=_Aligner(None)), tmp_path, 54, None)

    assert out["warp_ok"] is True
    assert calls == ["extract_rows_for_job"]
    assert "[corner-dl] job=54 fallback reason=no-detection" in capsys.readouterr().out


def test_infer_job_retries_with_the_color_quad_when_the_dl_warp_is_gate_demoted(
    monkeypatch, tmp_path, make_warped, capsys
):
    # DL quad가 격자 없는 상단 띠만 잡은 경우(잡 41·69 유형) — 게이트가 강등하면 색으로 재시도한다.
    from handwriting.infer_job import infer_job

    top_strip = np.array([[0, 0], [WARP_W, 0], [WARP_W, 300], [0, 300]], np.float32)
    calls = []
    _install_fake_infer_photo(monkeypatch, make_warped(), calls)

    out = infer_job("ignored.jpg", _models(aligner=_Aligner(top_strip)), tmp_path, 41, None)

    assert out["warp_ok"] is True  # 색 재시도로 회수
    assert calls == ["extract_rows_for_job"]
    logged = capsys.readouterr().out
    assert "[warp-gate] job=41 demoted" in logged
    assert "[corner-dl] job=41 fallback reason=gate-demoted" in logged
    # 실행 확인: 합성 워프 n_lines=16 기준 FULL_QUAD → std pass·hline 16,
    # top_strip → std/enh 모두 fail·hline 0. 결정론적으로 RED→GREEN이 갈린다.


def test_infer_job_demotes_when_both_quads_fail_the_gate(monkeypatch, tmp_path, make_warped):
    """전량 강등 — warp_ok=False, 그리고 warped.png는 **마지막(색)** 후보의 워프여야 한다.

    두 후보에 같은 quad를 주면 워프 결과가 같아 "마지막 후보로 닫는다"(_gated_warp 반환 규칙)가
    고정되지 않는다 — 첫 후보를 남기도록 회귀해도 통과한다. 그래서 구분 가능한 실패 quad를
    준다: DL은 격자 위 백지 띠(하라인 0), 색은 격자를 4선만 담는 띠(MIN_HLINES=14 미달).
    둘 다 표준·enh 양쪽에서 강등되지만 픽셀 내용은 확실히 다르다.
    """
    import cv2

    from handwriting.grid_v4 import warp
    from handwriting.infer_job import infer_job

    dl_strip = np.array([[0, 0], [WARP_W, 0], [WARP_W, 300], [0, 300]], np.float32)  # 백지
    color_band = np.array(
        [[0, 500], [WARP_W, 500], [WARP_W, 1000], [0, 1000]], np.float32
    )  # 격자 일부 포함
    calls = []
    bgr = make_warped()
    _install_fake_infer_photo(monkeypatch, bgr, calls)
    monkeypatch.setattr(corner_dl, "form_quad_robust", lambda b: color_band)

    out = infer_job("ignored.jpg", _models(aligner=_Aligner(dl_strip)), tmp_path, 99, None)

    assert out["warp_ok"] is False
    assert calls == []
    saved = cv2.imread(str(tmp_path / "warped.png"))
    assert np.array_equal(saved, warp(bgr, color_band))
    assert not np.array_equal(saved, warp(bgr, dl_strip))  # 첫 후보를 남기는 회귀를 배제


def test_infer_job_keeps_the_demoted_dl_warp_when_the_color_path_finds_nothing(
    monkeypatch, tmp_path, make_warped, capsys
):
    """혼합 케이스 — DL quad는 나왔지만 게이트에 강등되고, 색 경로는 아예 검출되지 않는다.

    후보가 DL 하나뿐이라 강등된 DL 워프가 마지막(유일한) 후보로 warped.png에 남는다 —
    quad_missing 마커(w=None)는 후보가 하나도 없을 때만 찍히므로 여기선 찍히지 않는다.
    warp_ok=False는 quad_missing 경로와 동일해 result_json 계약 회귀는 아니지만, 마커의
    의미가 이 경로에서는 좁아진다(_gated_warp docstring 참조).
    """
    from handwriting.infer_job import infer_job

    top_strip = np.array([[0, 0], [WARP_W, 0], [WARP_W, 300], [0, 300]], np.float32)
    calls = []
    _install_fake_infer_photo(monkeypatch, make_warped(), calls)
    monkeypatch.setattr(corner_dl, "form_quad_robust", lambda bgr: None)

    out = infer_job("ignored.jpg", _models(aligner=_Aligner(top_strip)), tmp_path, 71, None)

    assert out["warp_ok"] is False
    assert calls == []
    assert (tmp_path / "warped.png").exists()  # 강등된 DL 워프가 진단용으로 남는다
    logged = capsys.readouterr().out
    assert "[corner-dl] job=71 fallback reason=gate-demoted" in logged
    assert "quad_missing" not in logged  # 후보가 있었으므로(강등일 뿐) 미검출 마커는 아니다


def test_infer_job_without_an_aligner_keeps_the_current_color_path(
    monkeypatch, tmp_path, make_warped, capsys
):
    """모델 배포 전 상태 — 현행과 100% 동일해야 하고 잡별 로그도 늘지 않는다."""
    from handwriting.infer_job import infer_job

    calls = []
    _install_fake_infer_photo(monkeypatch, make_warped(), calls)

    out = infer_job("ignored.jpg", _models(), tmp_path, 42, None)

    assert out["warp_ok"] is True
    assert calls == ["extract_rows_for_job"]
    assert "[corner-dl]" not in capsys.readouterr().out


def test_infer_job_imports_corner_dl_lazily():
    """infer_job.py 상단 규약 — corner_dl은 cv2를 끌어오므로 모듈 레벨 import가 금지다.

    그 규약이 깨져도 CI(cv2 있음)는 초록이라, 소스 구조를 직접 고정한다. 술어는
    import_scopes가 정규화한다 — 모듈 레벨 `try:`/`if:` 안의 import 누락, `from handwriting
    import corner_dl` 표기 누락, 함수 안 `import handwriting.corner_dl`의 거짓 RED를
    한꺼번에 닫는다.
    """
    src = Path(__file__).resolve().parents[1] / "handwriting" / "infer_job.py"
    module_level, in_functions = import_scopes(src)

    assert "handwriting.corner_dl" not in module_level
    assert "handwriting.corner_dl" in in_functions


def _warp_spy(monkeypatch):
    """grid_v4.warp 호출을 세는 spy를 건다(원 계산은 그대로 수행)."""
    import handwriting.grid_v4 as g4

    original = g4.warp
    seen = []

    def spy(bgr, quad):
        seen.append(quad)
        return original(bgr, quad)

    monkeypatch.setattr(g4, "warp", spy)
    return seen


def test_infer_job_warps_each_candidate_exactly_once(monkeypatch, tmp_path, make_warped):
    """채택된 워프를 하류가 그대로 재사용한다 — 후보당 warpPerspective는 정확히 1회다.

    deskew 인자와 회전 대상이 각자 `warp(bgr, quad)`를 부르면 후보당 2회가 되어 전량 강등
    잡은 4회를 돈다(warpPerspective는 잡당 가장 비싼 단일 연산이다). 산출물은 동일해
    행위 테스트로는 드러나지 않으므로 호출 횟수를 직접 센다.
    """
    from handwriting.infer_job import infer_job

    seen = _warp_spy(monkeypatch)
    _install_fake_infer_photo(monkeypatch, make_warped(), [])

    out = infer_job("ignored.jpg", _models(aligner=_Aligner(FULL_QUAD)), tmp_path, 34, None)

    assert out["warp_ok"] is True
    assert len(seen) == 1  # DL 후보 채택 — 재워프 0회


def test_infer_job_warps_once_per_candidate_when_all_are_demoted(
    monkeypatch, tmp_path, make_warped
):
    from handwriting.infer_job import infer_job

    top_strip = np.array([[0, 0], [WARP_W, 0], [WARP_W, 300], [0, 300]], np.float32)
    seen = _warp_spy(monkeypatch)
    _install_fake_infer_photo(monkeypatch, make_warped(), [])
    monkeypatch.setattr(corner_dl, "form_quad_robust", lambda bgr: top_strip)

    out = infer_job("ignored.jpg", _models(aligner=_Aligner(top_strip)), tmp_path, 99, None)

    assert out["warp_ok"] is False
    assert len(seen) == 2  # 후보 2개 × 1회


# ── _gated_warp 반환 계약(GatedWarp) ────────────────────────────────────────


def test_gated_warp_returns_the_geometry_of_the_passing_candidate(monkeypatch, make_warped):
    """통과한 후보의 quad·공급자·deskew 각을 함께 돌려준다 — 지역변수로 버리지 않는다."""
    from handwriting.infer_job import _gated_warp

    warped = make_warped()
    _install_fake_infer_photo(monkeypatch, warped, [])
    monkeypatch.setattr("handwriting.infer_photo.deskew_angle", lambda w: 0.42, raising=False)

    gw = _gated_warp(warped, None, 7)

    assert gw.passed is True
    assert gw.quad_source == "color"
    assert gw.deskew_deg == pytest.approx(0.42)
    assert np.asarray(gw.quad).shape == (4, 2)


def test_gated_warp_returns_the_last_candidate_geometry_when_all_are_demoted(
    monkeypatch, make_warped
):
    """전량 강등에서도 마지막 후보의 기하를 돌려준다 — warped.png와 같은 규칙(spec §5-2).

    이 기하가 없으면 강등 잡의 부분 문서에 쿼드가 비어, "4점이 전표를 물었나"라는 ② 패널의
    질문 자체가 화면에서 성립하지 않는다.
    """
    from handwriting.infer_job import _gated_warp

    blank = make_warped(n_lines=0)  # 격자 없음 → 게이트 전량 강등
    _install_fake_infer_photo(monkeypatch, blank, [])

    gw = _gated_warp(blank, None, 8)

    assert gw.passed is False
    assert gw.warped is not None
    assert gw.quad is not None
    assert gw.quad_source == "color"
    assert gw.deskew_deg is not None


def test_gated_warp_returns_all_none_when_no_candidate_exists(monkeypatch, make_warped):
    """후보 전무는 기하도 없다 — 부재 자체가 신호이고 호출부가 quad_missing으로 닫는다."""
    import handwriting.corner_dl as cdl
    from handwriting.infer_job import _gated_warp

    warped = make_warped()
    _install_fake_infer_photo(monkeypatch, warped, [])
    monkeypatch.setattr(cdl, "quad_candidates", lambda *a, **k: iter(()))

    gw = _gated_warp(warped, None, 9)

    assert gw == (None, False, None, None, None)
