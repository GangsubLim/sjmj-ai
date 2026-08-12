"""DL 4-코너 회귀(DocsaidLab/DocAligner point, lcnet050 ONNX/CPU) 어댑터.

색 기반 경계 검출(rectify.form_quad_robust)은 같은 파랑 격자 전표가 인접하거나 배경이
섞이면 blob이 번져 파손 quad를 낸다(rectify.form_quad_robust docstring이 명시한 한계).
확정 37잡 중 14잡이 그 유형이며, 스파이크에서 이 모델이 파손 12/12를 회복했다
(docs/work/2026-08/2026-08-11-warp-boundary-diagnosis/analysis.md 「실측 5」).

⚠️ 모듈 레벨 의존은 cv2·numpy와 handwriting 내부 모듈뿐이다 — `onnxruntime` import는
   `_make_session()` 안으로 지연한다. CI(worker+cv 조합)와 미동기화 worker venv에는
   onnxruntime이 없고, 그 환경에서도 이 모듈 import와 전처리·후처리 단위테스트가 동작해야
   한다(infer_job.py 상단의 지연 import 규약과 같은 취지).

전처리·후처리는 docaligner 1.1.1 point_reg/infer.py(do_center_crop=False)와 산술 동일하다 —
로컬 동일성 검증(docs/work/2026-08/2026-08-12-warp-dl-corner-integration/data/verify_integration.py
1단계)이 스파이크 정답과 88장 전수 대조로 증명한다.
"""

import hashlib
import sys
from pathlib import Path

import cv2
import numpy as np

MODEL_FILENAME = "lcnet050_p_multi_decoder_l3_d64_256_fp32.onnx"
# 스파이크 시점 다운로드 사본(PyPI docaligner-docsaid 1.1.1의 point_reg ckpt, Apache 2.0)의
# 다이제스트. 불일치는 적재 거부다 — 모델 교체는 이 상수 변경(코드 리뷰 게이트)을 요구한다.
EXPECTED_SHA256 = "32d186080ce16442674d4c0eaaaaac878eea289b56a8d1284f05fff1ff42e220"
INFER_SIZE = (256, 256)  # (w, h) — 정방이라 cv2/capybara의 축 순서 차이가 무해하다
HAS_OBJ_MIN = 0.5  # docaligner postprocess의 `has_obj > 0.5`와 동일(경계값은 미검출)
_SHA_CHUNK = 1 << 20  # 해시 스트리밍 청크(모델 ~수 MB, 전량 적재 회피)


def preprocess(bgr):
    """BGR 원본을 모델 입력 텐서 (1, 3, 256, 256) float32로 만든다.

    docaligner point_reg preprocess(do_center_crop=False)와 산술 동일하다:
    cb.imresize(size=(256, 256)) = cv2.resize(..., INTER_LINEAR) → HWC→CHW → 배치축 → /255.

    Args:
        bgr: EXIF 정위치 BGR 원본(임의 크기).

    Returns:
        (1, 3, 256, 256) float32 텐서. 입력 배열은 변경하지 않는다.
    """
    resized = cv2.resize(bgr, INFER_SIZE, interpolation=cv2.INTER_LINEAR)
    chw = np.transpose(resized, axes=(2, 0, 1)).astype("float32")
    return chw[None] / 255


def postprocess(points, has_obj, *, h: int, w: int):
    """모델 출력을 원본 좌표계 (4, 2) float32 코너로 되돌린다.

    docaligner postprocess와 동일한 산술(`points.reshape(4, 2) * (w, h)`)이다. 원 구현은
    미검출에 빈 배열을 돌려주지만 여기서는 호출부 분기가 명확하도록 None을 쓴다.
    h/w는 키워드 전용이다 — 뒤바뀌면 좌표가 조용히 어긋난다.

    Args:
        points: 모델 출력 좌표(정규화 [0, 1], 원소 8개면 형상 무관).
        has_obj: 문서 존재 확신도.
        h: 원본 높이.
        w: 원본 너비.

    Returns:
        (4, 2) float32 코너, 미검출(임계 이하·NaN)이면 None.
    """
    conf = float(np.asarray(has_obj).reshape(-1)[0])
    if not conf > HAS_OBJ_MIN:  # NaN도 미검출로 닫는다(NaN 비교는 항상 False)
        return None
    pts = np.asarray(points, dtype=np.float32).reshape(4, 2)
    return (pts * np.array([w, h], np.float32)).astype(np.float32)


def _sha256(path) -> str:
    """파일의 SHA-256 16진 다이제스트를 스트리밍으로 계산한다."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_SHA_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _make_session(model_path):
    """CPU 전용 onnxruntime 세션을 연다 — onnxruntime import는 이 함수 안에서만 일어난다.

    테스트는 이 함수를 Fake로 갈아끼워 onnxruntime 없이 CornerModel 글루를 실행한다.
    추가 세션 옵션을 주지 않는다(스파이크와 동일 조건 — 기본 그래프 최적화).

    `SessionOptions`를 주지 않아 intra-op 스레드가 코어 수로 잡힌다(스파이크 동일 조건).
    워커가 torch(CPU)·MLX(Metal)와 한 프로세스라 경합 여지는 있으나 추론이 ~0.004s라 실효
    영향이 미미해 튜닝하지 않는다 — 배포 후 관찰 항목.

    Args:
        model_path: ONNX 파일 경로.

    Returns:
        onnxruntime.InferenceSession.

    Raises:
        ImportError: dl extra가 없는 환경(CI·미동기화 worker venv).
    """
    import onnxruntime as ort  # noqa: PLC0415 — 모듈 레벨 금지(상단 규약)

    return ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])


class CornerModel:
    """DL 4-코너 검출기. 적재 실패는 예외로 던지고, 추론 실패는 None으로 닫는다."""

    def __init__(self, model_path):
        """SHA-256을 검증한 뒤 CPU 세션을 연다.

        Args:
            model_path: lcnet050 ONNX 파일 경로.

        Raises:
            FileNotFoundError: 파일이 없을 때.
            ValueError: SHA-256 불일치 또는 출력 이름 계약 위반.
            ImportError: onnxruntime이 없을 때.
        """
        path = Path(model_path)
        digest = _sha256(path)
        if digest != EXPECTED_SHA256:
            raise ValueError(f"모델 SHA-256 불일치: {path} (실측 {digest})")
        self._session = _make_session(path)
        self._input_name = self._session.get_inputs()[0].name
        self._output_names = [o.name for o in self._session.get_outputs()]
        # 이름이 어긋나면 quad()가 잡마다 KeyError→None으로 조용히 색 경로 퇴행한다 —
        # 부팅 1줄 신호(load_or_none)로 승격한다.
        missing = {"points", "has_obj"} - set(self._output_names)
        if missing:
            raise ValueError(f"모델 출력 이름 계약 위반: {sorted(missing)} 없음 ({path})")

    def quad(self, bgr):
        """BGR 원본 → 원본 좌표계 (4, 2) float32 코너. 검출 실패·예외는 전부 None.

        추론 경로 다운 금지 불변식 — 어떤 예외도 밖으로 내지 않는다(호출부가 색 경로로
        폴백한다). 실패 모드가 안전하다는 것이 이 모델 채택의 근거이기도 하다: 틀린 quad
        대신 빈 결과를 낸다(analysis.md 「실측 5」).

        Args:
            bgr: EXIF 정위치 BGR 원본.

        Returns:
            (4, 2) float32 코너 또는 None.
        """
        try:
            h, w = bgr.shape[:2]
            outs = self._session.run(self._output_names, {self._input_name: preprocess(bgr)})
            named = dict(zip(self._output_names, outs, strict=True))
            return postprocess(named["points"], named["has_obj"], h=h, w=w)
        except Exception as e:
            print(f"[corner-dl] 추론 실패({type(e).__name__}: {e})", file=sys.stderr, flush=True)
            return None


def load_or_none(models_dir):
    """모델 디렉터리에서 CornerModel을 적재한다. 어떤 실패도 밖으로 내지 않는다.

    worker(load_models)와 demo CLI(infer_photo.main)가 공유하는 단일 로더다. 모델 부재·해시
    불일치·onnxruntime 부재·기타 예외 전부 None + stderr 경고 1줄로 닫고, 호출부는 현행 색
    경로로 계속 돈다. 잡마다 로그를 남기지 않는 이유가 이것이다 — 부재 경고는 여기 1줄이다.

    Args:
        models_dir: 모델이 사는 디렉터리(SJMJ_ML_MODELS_DIR). None·빈 값이면 None을 반환한다.

    Returns:
        CornerModel 또는 None.
    """
    if not models_dir:
        print("[corner-dl] 모델 디렉터리 미지정 — 색 경로로만 동작", file=sys.stderr, flush=True)
        return None
    path = Path(models_dir) / MODEL_FILENAME
    try:
        return CornerModel(path)
    except Exception as e:
        print(
            f"[corner-dl] 적재 실패({path}, {type(e).__name__}: {e}) — 색 경로로만 동작",
            file=sys.stderr,
            flush=True,
        )
        return None
