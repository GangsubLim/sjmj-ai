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
    assert (
        "[warp-gate] job=7 demoted metrics=" in logged
    )  # 강등 원인을 로그만으로 재구성 가능해야 함
    assert (
        "thresholds=" in logged
    )  # 재캘리브 후에도 과거 로그 라인을 그 시점 기준으로 해석 가능해야 함


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
