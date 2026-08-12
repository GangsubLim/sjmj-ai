"""corner_dl 어댑터 — 전처리·후처리 순수함수, 적재 fail-safe, 폴백 조합.

실모델(onnxruntime)에 의존하지 않는다 — 세션은 Fake로 갈아끼우고 SHA 상수는 합성 파일의
다이제스트로 바꾼다(conftest 규약: 합성 데이터 + Fake 어댑터). CI는 dl extra 없이 도는
worker+cv 조합이라, 이 파일이 실행된다는 사실 자체가 "onnxruntime 없이 import 가능"의 증거다.
"""

import ast
import hashlib
import sys
import types
from pathlib import Path

import pytest

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


def test_onnxruntime_is_never_imported_at_module_level():
    """dl extra 없는 조합(CI worker+cv, 미동기화 worker venv)에서 import 가능해야 한다.

    로컬에 dl extra가 깔린 개발자 환경에서는 모듈 레벨 import가 조용히 통과하므로,
    소스 구조 자체를 고정한다.
    """
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    top_level = {
        alias.name for node in tree.body if isinstance(node, ast.Import) for alias in node.names
    } | {node.module for node in tree.body if isinstance(node, ast.ImportFrom)}

    assert "onnxruntime" not in top_level


def test_corner_model_rejects_a_hash_mismatch_before_opening_a_session(monkeypatch, tmp_path):
    # 무결성 검증이 세션 생성보다 먼저다 — 변조 파일을 onnxruntime에 먹이지 않는다.
    path = tmp_path / corner_dl.MODEL_FILENAME
    path.write_bytes(b"tampered")
    monkeypatch.setattr(corner_dl, "_make_session", lambda p: pytest.fail("세션을 열면 안 된다"))

    with pytest.raises(ValueError):
        corner_dl.CornerModel(path)


def test_load_or_none_swallows_a_missing_model_file(tmp_path, capsys):
    assert corner_dl.load_or_none(tmp_path) is None
    assert "[corner-dl]" in capsys.readouterr().err


def test_load_or_none_swallows_a_hash_mismatch(tmp_path, capsys):
    (tmp_path / corner_dl.MODEL_FILENAME).write_bytes(b"tampered")

    assert corner_dl.load_or_none(tmp_path) is None
    assert "[corner-dl]" in capsys.readouterr().err


def test_load_or_none_returns_none_without_a_models_dir(capsys):
    # env 미설정(SJMJ_ML_MODELS_DIR 없음) — 데모 CLI가 그대로 넘긴다.
    assert corner_dl.load_or_none(None) is None
    assert "[corner-dl]" in capsys.readouterr().err


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


def test_load_or_none_returns_the_model_when_the_hash_matches(monkeypatch, tmp_path):
    _install_model_file(monkeypatch, tmp_path)
    monkeypatch.setattr(
        corner_dl, "_make_session", lambda p: _FakeSession(np.zeros((1, 8), np.float32), 0.9)
    )

    assert isinstance(corner_dl.load_or_none(tmp_path), corner_dl.CornerModel)


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


def test_corner_model_rejects_a_model_with_unexpected_output_names(monkeypatch, tmp_path):
    """출력 이름 계약 위반은 잡별 조용한 퇴행이 아니라 부팅 실패여야 한다."""
    path = _install_model_file(monkeypatch, tmp_path)
    session = _FakeSession(np.zeros((1, 8), np.float32), 0.9)
    session.get_outputs = lambda: [types.SimpleNamespace(name="pts")]
    monkeypatch.setattr(corner_dl, "_make_session", lambda p: session)

    with pytest.raises(ValueError):
        corner_dl.CornerModel(path)
