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

from handwriting.grid_v4 import _order
from handwriting.rectify import form_quad_robust

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
    경로로 계속 돈다. 적재 성공도 대칭으로 stderr 1줄을 남긴다 — 그렇지 않으면 "DL 적재
    성공"과 "aligner=None(모델 미배포)"이 로그상(둘 다 [corner-dl] 0줄) 구분 불가능해져
    배포 검증이 "없어야 할 라인이 없다"는 이중 부정으로만 가능해진다. 잡마다 로그를 남기지
    않는 이유가 이것이다 — 부팅 신호(부재 경고·적재 성공)는 여기 1줄뿐이다.

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
        model = CornerModel(path)
    except Exception as e:
        print(
            f"[corner-dl] 적재 실패({path}, {type(e).__name__}: {e}) — 색 경로로만 동작",
            file=sys.stderr,
            flush=True,
        )
        return None
    print(f"[corner-dl] 적재 성공({path})", file=sys.stderr, flush=True)
    return model


def log_fallback(job_id, reason):
    """색 경로 폴백 사유를 stdout 1줄로 남긴다(로그 계약의 단일 소유자).

    stdout·flush=True는 launchd 상시 폴링 워커 규약이다 — 한 잡의 게이트 로그
    (`[warp-gate]`, infer_job._warp_gate_passes)와 같은 창구에 시간순으로 쌓여야
    "DL이 왜 안 쓰였나"를 로그만으로 재구성할 수 있다. 부팅 진단(load_or_none)이
    stderr인 것과 축이 다르다(잡별 진단 = stdout).

    Args:
        job_id: 잡 id. None이면 태그를 생략한다(데모 CLI 경로).
        reason: no-detection | invalid-quad | error:{예외타입} | gate-demoted.
    """
    tag = f"job={job_id} " if job_id is not None else ""
    print(f"[corner-dl] {tag}fallback reason={reason}", flush=True)


def _ordered_or_none(quad):
    """DL quad를 warp 계약(TL→TR→BR→BL) 순서로 정규화한다. 형상 불량·비유한값이면 None.

    NaN/Inf quad는 예외를 내지 않고 cv2가 전-0 워프를 만든다(실측) — 운영 경로는 게이트가
    강등해 흡수하지만 데모 CLI에는 게이트가 없다. 공급자 단에서 닫는 이유다. `_order`는
    (3, 2) 입력에도 예외 없이 퇴화 사각형을 돌려주므로 형상도 여기서 본다.
    """
    if quad is None:
        return None
    pts = np.asarray(quad, dtype=np.float32)
    if pts.shape != (4, 2) or not np.isfinite(pts).all():
        return None
    return _order(pts)


def quad_candidates(bgr, aligner, job_id=None):
    """Quad 공급자 후보를 우선순위대로 지연 산출한다 — DL 1순위, 색 2순위.

    지연 산출이 설계의 핵심이다. 색 경로(form_quad_robust)는 실측 ~0.96s/장이라 DL이
    채택되면 아예 계산하지 않는다. 후보 '거부' 판정(게이트 강등)은 소비자가 소유한다 —
    게이트는 운영 경로(result_json.warp_ok) 전용 계약이라 공급자가 알 필요가 없고,
    알면 corner_dl → infer_job 순환 의존이 생긴다.

    aligner가 None이면 색 후보 하나만 낸다 — 로그도 남기지 않는다(현행 100% 동일).

    Args:
        bgr: EXIF 정위치 BGR 원본.
        aligner: CornerModel 또는 None.
        job_id: 로그 태그용 잡 id. None이면 태그를 생략한다(데모 CLI 경로).

    Yields:
        (source, quad) — source는 "dl" | "color". quad는 (4, 2) float32.
        색 후보는 재정렬하지 않는다 — _candidate_quads가 이미 _order로 정렬해 돌려주고
        (rectify.py:113·135·154, _quad_extreme은 극점 구성 자체가 TL→TR→BR→BL),
        재적용하면 퇴화 quad에서 현행 동작과 갈릴 수 있다.
    """
    if aligner is not None:
        reason = "no-detection"
        try:
            raw = aligner.quad(bgr)
            dl_quad = _ordered_or_none(raw)
            if raw is not None and dl_quad is None:
                reason = "invalid-quad"
        except Exception as e:  # 어댑터 계약 위반도 추론 경로를 죽이지 않는다
            dl_quad, reason = None, f"error:{type(e).__name__}"
        if dl_quad is not None:
            yield "dl", dl_quad
        else:
            log_fallback(job_id, reason)
    color_quad = form_quad_robust(bgr)
    if color_quad is not None:
        yield "color", color_quad


def form_quad_best(bgr, aligner, job_id=None):
    """게이트가 없는 호출부(데모 CLI)용 quad 공급 — 첫 후보를 그대로 고른다.

    운영 경로(infer_job._gated_warp)는 게이트 인지형 선택을 쓴다. 데모는 warp_ok 계약이
    없어(모든 행을 그려 눈으로 본다) 게이트를 소비할 자리가 없고, 넣으면 강등 잡이 빈
    리포트가 되어 QA 도구의 목적이 깨진다 — 비대칭은 의도다.

    Args:
        bgr: EXIF 정위치 BGR 원본.
        aligner: CornerModel 또는 None.
        job_id: 로그 태그용 잡 id.

    Returns:
        (4, 2) float32 quad. 후보가 하나도 없으면 None(현행 색 경로 None 계약과 동일).
    """
    for _src, quad in quad_candidates(bgr, aligner, job_id=job_id):
        return quad
    return None
