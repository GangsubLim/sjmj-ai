"""corner_dl 어댑터 — 전처리·후처리 순수함수, 적재 fail-safe, 폴백 조합.

실모델(onnxruntime)에 의존하지 않는다 — 세션은 Fake로 갈아끼우고 SHA 상수는 합성 파일의
다이제스트로 바꾼다(conftest 규약: 합성 데이터 + Fake 어댑터). CI는 dl extra 없이 도는
worker+cv 조합이라, 이 파일이 실행된다는 사실 자체가 "onnxruntime 없이 import 가능"의 증거다.
"""

import hashlib
import sys
import types
from pathlib import Path

import pytest

from tests.conftest import import_scopes

pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from handwriting import corner_dl  # noqa: E402

SRC = Path(__file__).resolve().parents[1] / "handwriting" / "corner_dl.py"


def test_preprocess_returns_a_normalized_nchw_tensor():
    img = np.zeros((480, 640, 3), np.uint8)
    img[:, :] = (10, 20, 30)  # BGR 상수 — 채널 순서가 뒤집히면 값으로 드러난다

    x = corner_dl.preprocess(img)

    assert x.shape == (1, 3, 256, 256)
    assert x.dtype == np.float32
    assert np.allclose(x[0, 0], 10 / 255)
    assert np.allclose(x[0, 1], 20 / 255)
    assert np.allclose(x[0, 2], 30 / 255)


def test_preprocess_matches_the_docaligner_resize_arithmetic():
    """docaligner 1.1.1 point_reg preprocess(do_center_crop=False)와 산술 동일해야 한다.

    capybara.imresize는 cv2.resize(INTER_LINEAR) 래퍼다(capybara/vision/geometric.py). 보간
    상수나 축 순서가 갈리면 스파이크 정답 quads.json과 좌표가 어긋난다 — 로컬 동일성 검증이
    잡기 전에 CI에서 상수 회귀를 먼저 잡는다.
    """
    import cv2

    rng = np.random.default_rng(0)
    img = rng.integers(0, 255, (137, 251, 3), dtype=np.uint8)
    expected = (
        np.transpose(
            cv2.resize(img, (256, 256), interpolation=cv2.INTER_LINEAR), axes=(2, 0, 1)
        ).astype("float32")[None]
        / 255
    )

    assert np.array_equal(corner_dl.preprocess(img), expected)


def test_preprocess_does_not_mutate_the_input():
    img = np.full((40, 30, 3), 7, np.uint8)

    corner_dl.preprocess(img)

    assert np.all(img == 7)


def test_postprocess_scales_the_unit_square_to_the_original_size():
    pts = np.array([[0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]], np.float32)

    q = corner_dl.postprocess(pts, np.array([[0.9]], np.float32), h=2100, w=900)

    assert q.shape == (4, 2)
    assert q.dtype == np.float32
    assert np.allclose(q, [[0, 0], [900, 0], [900, 2100], [0, 2100]])


@pytest.mark.parametrize("conf", [0.5, 0.49, 0.0, float("nan")])
def test_postprocess_returns_none_when_the_model_reports_no_object(conf):
    # 경계 0.5는 미검출이다(docaligner postprocess의 `has_obj > 0.5`와 동일). NaN도 닫는다 —
    # NaN 비교는 항상 False라 `>`로 썼다면 통과했다(warp_gate.py와 같은 fail-closed 관용구).
    pts = np.zeros((1, 8), np.float32)

    assert corner_dl.postprocess(pts, np.array([[conf]], np.float32), h=100, w=100) is None


class _FakeSession:
    """onnxruntime InferenceSession 대역 — run 입력을 기록하고 고정 출력을 돌려준다."""

    def __init__(self, points, has_obj):
        self.points, self.has_obj, self.seen = points, has_obj, []

    def get_inputs(self):
        return [types.SimpleNamespace(name="img")]

    def get_outputs(self):
        return [types.SimpleNamespace(name="points"), types.SimpleNamespace(name="has_obj")]

    def run(self, names, feed):
        self.seen.append((tuple(names), feed))
        out = {"points": self.points, "has_obj": self.has_obj}
        return [out[n] for n in names]


def _install_model_file(monkeypatch, tmp_path, content=b"fake-onnx"):
    """해시가 맞는 합성 모델 파일을 놓는다(상수를 그 파일의 다이제스트로 교체)."""
    path = tmp_path / corner_dl.MODEL_FILENAME
    path.write_bytes(content)
    monkeypatch.setattr(corner_dl, "EXPECTED_SHA256", hashlib.sha256(content).hexdigest())
    return path


def _model(monkeypatch, tmp_path, points, has_obj):
    """Fake 세션을 문 CornerModel과 그 세션을 돌려준다."""
    path = _install_model_file(monkeypatch, tmp_path)
    session = _FakeSession(points, has_obj)
    monkeypatch.setattr(corner_dl, "_make_session", lambda p: session)
    return corner_dl.CornerModel(path), session


def test_sha256_streams_content_longer_than_one_chunk(monkeypatch, tmp_path):
    """청크 루프가 실제로 여러 번 돌아야 한다 — 모든 픽스처가 한 청크에 들어가면 미검증이다.

    단발 `f.read(_SHA_CHUNK)`로 퇴행하면 첫 청크만 해시해 모델 뒷부분 변조를 통과시킨다.
    """
    monkeypatch.setattr(corner_dl, "_SHA_CHUNK", 8)
    content = bytes(range(256)) * 5  # 청크(8B)의 160배 — 다청크 경로 확정
    path = tmp_path / "multi-chunk.bin"
    path.write_bytes(content)

    assert corner_dl._sha256(path) == hashlib.sha256(content).hexdigest()


def test_onnxruntime_is_never_imported_at_module_level():
    """dl extra 없는 조합(CI worker+cv, 미동기화 worker venv)에서 import 가능해야 한다.

    로컬에 dl extra가 깔린 개발자 환경에서는 모듈 레벨 import가 조용히 통과하므로,
    소스 구조 자체를 고정한다. 모듈 레벨 판정은 import_scopes가 정규화한다 —
    `tree.body` 직계만 보면 `try: import onnxruntime / except ImportError:` 같은
    모듈 레벨 블록 안의 import가 그대로 새어나간다.
    """
    module_level, _ = import_scopes(SRC)

    assert "onnxruntime" not in module_level


def test_corner_model_rejects_a_hash_mismatch_before_opening_a_session(monkeypatch, tmp_path):
    # 무결성 검증이 세션 생성보다 먼저다 — 변조 파일을 onnxruntime에 먹이지 않는다.
    path = tmp_path / corner_dl.MODEL_FILENAME
    path.write_bytes(b"tampered")
    monkeypatch.setattr(corner_dl, "_make_session", lambda p: pytest.fail("세션을 열면 안 된다"))

    with pytest.raises(ValueError):
        corner_dl.CornerModel(path)


# 세 실패 사유는 배포 진단에서 서로 다른 조치로 이어진다(env 미설정 / 모델 미배포 / 파일 변조).
# `[corner-dl]` 하나만 단언하면 셋이 한 메시지로 붕괴해도 전부 통과하므로 판별 토큰을 건다.


def test_load_or_none_swallows_a_missing_model_file(tmp_path, capsys):
    assert corner_dl.load_or_none(tmp_path) is None
    err = capsys.readouterr().err
    assert "[corner-dl] 적재 실패" in err
    assert "FileNotFoundError" in err  # 미배포 — 해시 불일치(ValueError)와 구분된다


def test_load_or_none_swallows_a_hash_mismatch(tmp_path, capsys):
    (tmp_path / corner_dl.MODEL_FILENAME).write_bytes(b"tampered")

    assert corner_dl.load_or_none(tmp_path) is None
    err = capsys.readouterr().err
    assert "[corner-dl] 적재 실패" in err
    assert "모델 SHA-256 불일치" in err  # 변조·모델 교체 — 파일 부재와 구분된다


def test_load_or_none_returns_none_without_a_models_dir(capsys):
    # env 미설정(SJMJ_ML_MODELS_DIR 없음) — 데모 CLI가 그대로 넘긴다.
    assert corner_dl.load_or_none(None) is None
    # 적재를 시도조차 하지 않은 경로다 — "적재 실패"로 뭉개지면 조치가 갈린다(env 주입 vs 배포).
    assert "[corner-dl] 모델 디렉터리 미지정" in capsys.readouterr().err


def test_load_or_none_survives_a_venv_without_onnxruntime(monkeypatch, tmp_path, capsys):
    """CI(worker+cv)와 미동기화 worker venv에는 onnxruntime이 없다 — 기동은 계속돼야 한다.

    `sys.modules['x']=None`은 `ImportError`가 아니라 하위클래스 `ModuleNotFoundError`를 낸다
    (실행 확인) — 타입명을 단언하면 GREEN 불가.
    """
    _install_model_file(monkeypatch, tmp_path)
    monkeypatch.setitem(sys.modules, "onnxruntime", None)  # import 시 ModuleNotFoundError

    assert corner_dl.load_or_none(tmp_path) is None
    err = capsys.readouterr().err
    assert (
        "[corner-dl]" in err
    )  # 예외 타입명(ModuleNotFoundError)은 로그 포맷 세부사항이라 고정하지 않는다


def test_load_or_none_returns_the_model_when_the_hash_matches(monkeypatch, tmp_path, capsys):
    _install_model_file(monkeypatch, tmp_path)
    monkeypatch.setattr(
        corner_dl, "_make_session", lambda p: _FakeSession(np.zeros((1, 8), np.float32), 0.9)
    )

    model = corner_dl.load_or_none(tmp_path)

    assert isinstance(model, corner_dl.CornerModel)
    # 적재 성공도 실패와 대칭으로 stderr 1줄을 남긴다 — 그렇지 않으면 "적재 성공"과
    # "aligner=None(모델 미배포)"이 로그상 구분 불가능해진다(배포 검증이 이중 부정으로만 가능).
    err = capsys.readouterr().err
    assert "[corner-dl]" in err
    assert str(tmp_path / corner_dl.MODEL_FILENAME) in err


def test_quad_feeds_the_preprocessed_tensor_and_scales_back_to_the_original(monkeypatch, tmp_path):
    pts = np.array([[0.1, 0.2, 0.9, 0.2, 0.9, 0.8, 0.1, 0.8]], np.float32)
    model, session = _model(monkeypatch, tmp_path, pts, np.array([[0.99]], np.float32))

    q = model.quad(np.zeros((1000, 800, 3), np.uint8))

    assert session.seen[0][1]["img"].shape == (1, 3, 256, 256)
    assert np.allclose(q, [[80, 200], [720, 200], [720, 800], [80, 800]])


def test_quad_returns_none_when_the_model_reports_no_object(monkeypatch, tmp_path):
    model, _ = _model(
        monkeypatch, tmp_path, np.zeros((1, 8), np.float32), np.array([[0.1]], np.float32)
    )

    assert model.quad(np.zeros((100, 100, 3), np.uint8)) is None


def test_quad_returns_none_when_the_session_raises(monkeypatch, tmp_path, capsys):
    """추론 경로 다운 금지 — 세션 예외가 워커까지 올라가면 잡 하나가 프로세스를 죽인다."""
    model, session = _model(
        monkeypatch, tmp_path, np.zeros((1, 8), np.float32), np.array([[0.9]], np.float32)
    )

    def boom(names, feed):
        raise RuntimeError("ONNX 런타임 실패")

    monkeypatch.setattr(session, "run", boom)

    assert model.quad(np.zeros((100, 100, 3), np.uint8)) is None
    assert "[corner-dl]" in capsys.readouterr().err
    assert model.last_failure == "RuntimeError"


def test_quad_clears_last_failure_on_the_next_successful_call(monkeypatch, tmp_path):
    """삼킨 예외 표식은 호출 단위다 — 다음 잡이 성공하면 지워져야 이전 잡의 사유가 새지 않는다."""
    model, session = _model(
        monkeypatch, tmp_path, np.zeros((1, 8), np.float32), np.array([[0.1]], np.float32)
    )
    original_run = session.run

    def boom(names, feed):
        raise RuntimeError("일시 실패")

    monkeypatch.setattr(session, "run", boom)
    model.quad(np.zeros((100, 100, 3), np.uint8))
    assert model.last_failure == "RuntimeError"

    monkeypatch.setattr(session, "run", original_run)
    assert model.quad(np.zeros((100, 100, 3), np.uint8)) is None  # 미검출(has_obj 0.1)
    assert model.last_failure is None


def test_corner_model_rejects_a_model_with_unexpected_output_names(monkeypatch, tmp_path):
    """출력 이름 계약 위반은 잡별 조용한 퇴행이 아니라 부팅 실패여야 한다."""
    path = _install_model_file(monkeypatch, tmp_path)
    session = _FakeSession(np.zeros((1, 8), np.float32), 0.9)
    session.get_outputs = lambda: [types.SimpleNamespace(name="pts")]
    monkeypatch.setattr(corner_dl, "_make_session", lambda p: session)

    with pytest.raises(ValueError):
        corner_dl.CornerModel(path)


# ── quad_candidates 후보 제너레이터 + 폴백 로그 계약 ────────────────────
# 색 경로(rectify.form_quad_robust)는 전부 monkeypatch로 고정한다 — 실검출을 태우면
# 합성 이미지에 의존한 취약한 기대값이 되고, 검증 대상(분기)이 흐려진다.

DL_QUAD = np.array([[1, 1], [11, 1], [11, 21], [1, 21]], np.float32)
COLOR_QUAD = np.array([[0, 0], [10, 0], [10, 20], [0, 20]], np.float32)
# 위 둘은 이미 TL→TR→BR→BL이라 _order 적용 여부가 값으로 드러나지 않는다 — 코너를 섞은
# 이 quad만이 "DL은 재정렬한다 / 색은 재정렬하지 않는다"를 양방향으로 고정할 수 있다.
SCRAMBLED_QUAD = np.array([[11, 21], [1, 1], [1, 21], [11, 1]], np.float32)  # BR, TL, BL, TR


class _FakeAligner:
    """CornerModel 대역 — 고정 quad(또는 None)를 돌려주고 받은 배열 객체를 기록한다."""

    def __init__(self, quad):
        self._quad, self.calls = quad, []

    def quad(self, bgr):
        self.calls.append(bgr)  # 형상이 아니라 객체 자체 — 색공간 변환·복사를 identity로 잡는다
        return self._quad


class _RaisingAligner:
    """CornerModel 대역 — 어댑터 계약 위반(예외)을 재현한다."""

    def quad(self, bgr):
        raise RuntimeError("어댑터 계약 위반")


def _bgr():
    return np.zeros((30, 20, 3), np.uint8)


def test_quad_candidates_yields_the_dl_quad_first_and_never_computes_the_color_path(
    monkeypatch,
):
    monkeypatch.setattr(
        corner_dl,
        "form_quad_robust",
        lambda bgr: pytest.fail("색 경로가 계산되면 안 된다"),
    )
    aligner = _FakeAligner(DL_QUAD)

    source, quad = next(corner_dl.quad_candidates(_bgr(), aligner, job_id=1))

    assert source == "dl"
    assert np.allclose(quad, DL_QUAD)


def test_quad_candidates_feeds_the_untouched_original_bgr_to_the_aligner(monkeypatch):
    """어댑터는 EXIF 정위치 BGR 원본을 그대로 받는다 — 채널 순서·복사 개입이 없어야 한다.

    형상만 기록하면 BGR→RGB 뒤집기처럼 형상이 보존되는 변환이 그대로 통과한다(모델은
    BGR을 전제로 preprocess한다 — corner_dl.preprocess). 객체 identity로 못 박는다.
    """
    monkeypatch.setattr(corner_dl, "form_quad_robust", lambda bgr: COLOR_QUAD)
    aligner = _FakeAligner(DL_QUAD)
    bgr = _bgr()

    list(corner_dl.quad_candidates(bgr, aligner, job_id=1))

    assert len(aligner.calls) == 1
    assert aligner.calls[0] is bgr


def test_quad_candidates_normalizes_the_dl_quad_to_the_warp_corner_order(monkeypatch):
    # DL 출력 순서는 warp 계약(TL→TR→BR→BL)과 무관하다 — 공급자가 _order로 정규화한다.
    monkeypatch.setattr(
        corner_dl, "form_quad_robust", lambda bgr: pytest.fail("색 경로가 계산되면 안 된다")
    )
    aligner = _FakeAligner(SCRAMBLED_QUAD)

    _source, quad = next(corner_dl.quad_candidates(_bgr(), aligner, job_id=1))

    assert np.array_equal(quad, DL_QUAD)


def test_quad_candidates_does_not_reorder_the_color_quad(monkeypatch):
    # 색 후보는 _candidate_quads가 이미 정렬해 돌려주므로 재적용하지 않는다(docstring 계약) —
    # 퇴화 quad에서 현행 동작과 갈리는 것을 막는 규칙이라 방향까지 고정한다.
    monkeypatch.setattr(corner_dl, "form_quad_robust", lambda bgr: SCRAMBLED_QUAD)

    candidates = list(corner_dl.quad_candidates(_bgr(), None, job_id=1))

    assert len(candidates) == 1
    assert candidates[0][0] == "color"
    assert np.array_equal(candidates[0][1], SCRAMBLED_QUAD)


def test_quad_candidates_falls_back_to_the_color_path_after_the_dl_quad_is_consumed(
    monkeypatch,
):
    monkeypatch.setattr(corner_dl, "form_quad_robust", lambda bgr: COLOR_QUAD)
    aligner = _FakeAligner(DL_QUAD)

    candidates = list(corner_dl.quad_candidates(_bgr(), aligner, job_id=1))

    assert len(candidates) == 2
    assert candidates[0][0] == "dl"
    assert np.allclose(candidates[0][1], DL_QUAD)
    assert candidates[1][0] == "color"
    assert np.allclose(candidates[1][1], COLOR_QUAD)


def test_quad_candidates_logs_no_detection_and_yields_only_the_color_quad(monkeypatch, capsys):
    monkeypatch.setattr(corner_dl, "form_quad_robust", lambda bgr: COLOR_QUAD)
    aligner = _FakeAligner(None)

    candidates = list(corner_dl.quad_candidates(_bgr(), aligner, job_id=7))

    assert len(candidates) == 1
    assert candidates[0][0] == "color"
    assert np.allclose(candidates[0][1], COLOR_QUAD)
    assert "[corner-dl] job=7 fallback reason=no-detection" in capsys.readouterr().out


class _SwallowingAligner:
    """CornerModel 대역 — 추론 예외를 삼켜 None을 내고 last_failure만 남긴다(#120)."""

    last_failure = "KeyError"

    def quad(self, bgr):
        return None


def test_quad_candidates_labels_a_swallowed_inference_error_as_error(monkeypatch, capsys):
    """삼켜진 추론 예외는 no-detection이 아니라 error:{타입}으로 잡 로그에 남아야 한다(#120)."""
    monkeypatch.setattr(corner_dl, "form_quad_robust", lambda bgr: COLOR_QUAD)

    candidates = list(corner_dl.quad_candidates(_bgr(), _SwallowingAligner(), job_id=9))

    assert len(candidates) == 1
    assert candidates[0][0] == "color"
    assert "[corner-dl] job=9 fallback reason=error:KeyError" in capsys.readouterr().out


def test_quad_candidates_logs_error_when_the_aligner_raises(monkeypatch, capsys):
    monkeypatch.setattr(corner_dl, "form_quad_robust", lambda bgr: COLOR_QUAD)
    aligner = _RaisingAligner()

    candidates = list(corner_dl.quad_candidates(_bgr(), aligner, job_id=3))

    assert len(candidates) == 1
    assert candidates[0][0] == "color"
    assert "[corner-dl] job=3 fallback reason=error:RuntimeError" in capsys.readouterr().out


def test_quad_candidates_rejects_a_non_finite_dl_quad(monkeypatch, capsys):
    monkeypatch.setattr(corner_dl, "form_quad_robust", lambda bgr: COLOR_QUAD)
    nan_quad = np.array([[np.nan, 0], [10, 0], [10, 20], [0, 20]], np.float32)
    aligner = _FakeAligner(nan_quad)

    candidates = list(corner_dl.quad_candidates(_bgr(), aligner, job_id=2))

    assert len(candidates) == 1
    assert candidates[0][0] == "color"
    assert "[corner-dl] job=2 fallback reason=invalid-quad" in capsys.readouterr().out


def test_quad_candidates_rejects_a_malformed_dl_quad(monkeypatch, capsys):
    monkeypatch.setattr(corner_dl, "form_quad_robust", lambda bgr: COLOR_QUAD)
    malformed_quad = np.array([[0, 0], [10, 0], [10, 20]], np.float32)
    aligner = _FakeAligner(malformed_quad)

    candidates = list(corner_dl.quad_candidates(_bgr(), aligner, job_id=4))

    assert len(candidates) == 1
    assert candidates[0][0] == "color"
    assert "[corner-dl] job=4 fallback reason=invalid-quad" in capsys.readouterr().out


def test_quad_candidates_without_an_aligner_is_the_untouched_color_path(monkeypatch, capsys):
    monkeypatch.setattr(corner_dl, "form_quad_robust", lambda bgr: COLOR_QUAD)

    candidates = list(corner_dl.quad_candidates(_bgr(), None, job_id=9))

    assert len(candidates) == 1
    assert candidates[0][0] == "color"
    assert np.allclose(candidates[0][1], COLOR_QUAD)
    assert capsys.readouterr().out == ""


def test_quad_candidates_yields_nothing_when_both_paths_fail(monkeypatch):
    monkeypatch.setattr(corner_dl, "form_quad_robust", lambda bgr: None)
    aligner = _FakeAligner(None)

    candidates = list(corner_dl.quad_candidates(_bgr(), aligner, job_id=5))

    assert candidates == []


def test_form_quad_best_returns_the_first_candidate(monkeypatch):
    monkeypatch.setattr(
        corner_dl,
        "form_quad_robust",
        lambda bgr: pytest.fail("색 경로가 계산되면 안 된다"),
    )
    aligner = _FakeAligner(DL_QUAD)

    quad = corner_dl.form_quad_best(_bgr(), aligner, job_id=6)

    assert np.allclose(quad, DL_QUAD)


def test_form_quad_best_returns_none_when_there_is_no_candidate(monkeypatch):
    monkeypatch.setattr(corner_dl, "form_quad_robust", lambda bgr: None)
    aligner = _FakeAligner(None)

    assert corner_dl.form_quad_best(_bgr(), aligner, job_id=8) is None


def test_log_fallback_omits_the_job_tag_in_the_demo_path(capsys):
    corner_dl.log_fallback(None, "no-detection")

    out = capsys.readouterr().out
    assert out == "[corner-dl] fallback reason=no-detection\n"
