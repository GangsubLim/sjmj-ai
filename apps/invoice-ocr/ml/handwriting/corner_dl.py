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

import cv2
import numpy as np

MODEL_FILENAME = "lcnet050_p_multi_decoder_l3_d64_256_fp32.onnx"
# 스파이크 시점 다운로드 사본(PyPI docaligner-docsaid 1.1.1의 point_reg ckpt, Apache 2.0)의
# 다이제스트. 불일치는 적재 거부다 — 모델 교체는 이 상수 변경(코드 리뷰 게이트)을 요구한다.
EXPECTED_SHA256 = "32d186080ce16442674d4c0eaaaaac878eea289b56a8d1284f05fff1ff42e220"
INFER_SIZE = (256, 256)  # (w, h) — 정방이라 cv2/capybara의 축 순서 차이가 무해하다
HAS_OBJ_MIN = 0.5  # docaligner postprocess의 `has_obj > 0.5`와 동일(경계값은 미검출)


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
