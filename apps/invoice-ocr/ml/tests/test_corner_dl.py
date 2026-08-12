"""corner_dl 어댑터 — 전처리·후처리 순수함수, 적재 fail-safe, 폴백 조합.

실모델(onnxruntime)에 의존하지 않는다 — 세션은 Fake로 갈아끼우고 SHA 상수는 합성 파일의
다이제스트로 바꾼다(conftest 규약: 합성 데이터 + Fake 어댑터). CI는 dl extra 없이 도는
worker+cv 조합이라, 이 파일이 실행된다는 사실 자체가 "onnxruntime 없이 import 가능"의 증거다.
"""

import pytest

pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from handwriting import corner_dl  # noqa: E402


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
