"""warp 게이트 회귀 하네스의 cv2 글루 — 원본 재워프(구현됨) + 행·크롭 재현(예정, Task 6/7).

주 기준은 저장 warped.png가 아니라 원본 사진 전량 재워프다(spec §4.1) — 저장분은 처리 시점
코드의 산출이고 그 뒤로 grid_v4·infer_photo가 여러 번 바뀌었다. 게이트가 검증해야 할 대상은
'운영이 앞으로 실제로 낼 워프'다.

이 모듈은 모델 무의존이다 — 지금 들어 있는 재워프 경로는 전부 cv2/numpy 전용이다.
(예정) 크롭 경계 산출, block_amounts의 new행 선별 술어, Fake read_fn 주입으로 밴드 수·
new 수·크롭 좌표까지 모델 없이 재현하는 것은 아직 이 파일에 없다(Task 6/7에서 추가된다).

⚠️ 기본 loader(`handwriting.dataset_build.load_bgr_path`)를 import하면 그 모듈이 로드
시점 부작용으로 `sys.path`에 report/sp2_spike·handwriting 경로를 끼워 넣는다
(`handwriting/dataset_build.py`가 원인 — 이 모듈이 만든 부작용이 아니라 손대지 않는다).
"""

from dataclasses import asdict
from pathlib import Path

STATUS_OK = "ok"
STATUS_UPLOAD_MISSING = "upload_missing"
STATUS_UPLOAD_UNREADABLE = "upload_unreadable"
STATUS_QUAD_MISSING = "quad_missing"


def _form_quad(bgr):
    from handwriting.rectify import form_quad_robust

    return form_quad_robust(bgr)


def rewarp(bgr):
    """원본 BGR을 운영과 동일 공정으로 워프·deskew한다. quad 미검출이면 None."""
    from handwriting.grid_v4 import warp
    from handwriting.rectify import deskew_angle, rotate

    quad = _form_quad(bgr)
    if quad is None:
        return None
    # infer_job.py:147은 warp를 두 번 부르지만 warp는 결정론적 순수 변환이라 1회로 동치다.
    w = warp(bgr, quad)
    return rotate(w, deskew_angle(w))


def rewarp_job(path, *, loader=None):
    """원본 사진 경로 → (status, warped|None). 예외를 던지지 않는다(전수 리포트 보호)."""
    path = Path(path)
    if not path.exists():
        return STATUS_UPLOAD_MISSING, None
    if loader is None:
        from handwriting.dataset_build import load_bgr_path

        loader = load_bgr_path
    from PIL import Image

    try:
        bgr = loader(path)
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        # 손상 파일 종류가 열려 있다 — PIL은 PIL.UnidentifiedImageError(OSError 서브클래스)를
        # 던지고, cv2 기반 로더는 ValueError/디코딩 실패를 낼 수 있다. SyntaxError는 PIL의
        # JPEG 플러그인이 깨진 마커 세그먼트를 만났을 때 낸다(Pillow의 알려진 관용구).
        # DecompressionBombError는 Exception 직계라 OSError로 안 잡힌다 — 초대형 업로드
        # 1장이 있어도 전수 리포트가 중간에 죽지 않게 하려면 반드시 여기 포함해야 한다.
        return STATUS_UPLOAD_UNREADABLE, None
    w = rewarp(bgr)
    if w is None:
        return STATUS_QUAD_MISSING, None
    return STATUS_OK, w


def job_metrics(warped) -> dict:
    """한 워프에서 표준·enh 두 축의 지표 4종을 뽑는다."""
    from handwriting.warp_gate import compute_metrics

    return {
        "std": asdict(compute_metrics(warped)),
        "enh": asdict(compute_metrics(warped, enhanced=True)),
    }
