"""warp 게이트 회귀 하네스의 cv2 글루 — 원본 재워프 + 행·크롭 재현.

주 기준은 저장 warped.png가 아니라 원본 사진 전량 재워프다(spec §4.1) — 저장분은 처리 시점
코드의 산출이고 그 뒤로 grid_v4·infer_photo가 여러 번 바뀌었다. 게이트가 검증해야 할 대상은
'운영이 앞으로 실제로 낼 워프'다.

이 모듈은 모델 무의존이다 — 재워프 경로도, `replicate_rows`가 재현하는 크롭 경계 산출 경로도
전부 cv2/numpy 전용이다. `replicate_rows`는 `handwriting.infer_photo.extract_rows_for_job`에서
모델 의존 구간(임베딩·금액 전사)만 뺀 접두를 그대로 옮겨 적은 것이다(아래 함수 docstring 참조).

⚠️ quad 공급(`_form_quad`)은 **색 경로(`rectify.form_quad_robust`)만** 재현한다. 운영 워커는
`infer_job._gated_warp`(DL 코너검출 후보 → 게이트 채점 → 색 폴백, #117)를 타지만, DL 모델이
배치되지 않아 `aligner=None`인 동안은 두 경로가 동일하다(#118 재측정으로 DL 비활성 유지 확정,
2026-08-28 기준). **DL을 활성화(모델 배치)하는 시점에는 이 공급을 `_gated_warp` 경로로 정렬해야
한다** — 정렬 전까지 이 하네스의 산출은 활성화 이후 운영 워프의 기준선이 아니다(#119).

⚠️ `hline_ys`는 `handwriting.grid_v4` 모듈 전역 `_FAINT`(기본 False)를 읽는다. `infer_photo`는
`sys.path` 트릭으로 `grid_v4`를 별도 모듈 객체로 다시 import하지만(위 경고 참조), 양쪽 모두
`_FAINT`의 초기값은 False이고 이 하네스는 `FaintOn`을 쓰지 않으므로 ambient False로 돈다 —
운영 `infer_photo`와 동일 조건이다.

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
# rewarp(=cv2 글루 전량) 자체가 터진 잡. quad 미검출(정상적인 '못 찾았다')과 구분해야
# 원본이 병리적인 잡을 리포트에서 골라낼 수 있다 — 뭉뚱그리면 진단 신호가 사라진다.
STATUS_REWARP_FAILED = "rewarp_failed"

# 품목칸 크롭의 좌우 여유(px) — infer_photo.extract_rows_for_job의 `x1 - 4 : x2 + 4`와 같은 값.
ITEM_CROP_PAD = 4
# 잉크로 세는 밝기 상한(BGR 채널 최대값). 이 값 '미만'인 픽셀만 어두운 것으로 본다.
CROP_INK_MAX_LEVEL = 120
# crop_sha에 남기는 16진 문자 수 — 스냅샷 JSON 크기와 충돌 확률의 절충(64비트).
CROP_DIGEST_CHARS = 16


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
    # 운영 워프 호출부(handwriting.infer_job._gated_warp)는 후보당 warp를 1회만 부른다
    # (raw = warp(bgr, quad)가 호이스트됨) — 이 하네스의 1회 호출과 직접 대응한다.
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
    if bgr is None:
        # cv2.imread 관용구 로더는 디코딩 실패를 예외가 아니라 None으로 알린다(같은 레포
        # blank_crop_calib.py가 이미 인정한 실패 모드) — 그대로 rewarp에 흘리면 위 계약이
        # loader 구현체에 따라 샌다.
        return STATUS_UPLOAD_UNREADABLE, None
    import cv2

    try:
        w = rewarp(bgr)
    except cv2.error:
        # rewarp는 전부 cv2 글루다 — 병리적 원본 1장의 cv2.error가 전수 순회를 통째로
        # 중단시키면 이 계약이 막으려던 시나리오 그 자체가 된다. 광범위한 예외 삼킴이
        # 아니라 cv2.error만 잡아 그 잡 하나를 분모 밖으로 강등한다.
        return STATUS_REWARP_FAILED, None
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


def _fake_read(_row):
    """금액 전사 대역 — block_amounts의 new행 선별은 build_proposal 분류만 보므로 값은 무의미하다."""
    return None, ""


def item_crop(warped, box):
    """new행 하나의 품목칸 크롭(ITEM_X ± ITEM_CROP_PAD)을 돌려준다.

    해시(`replicate_rows`)와 육안검수 PNG 덤프(`warp_gate_report._dump_crop_pngs`)가 이 함수
    하나만 쓴다 — 기하를 각자 소유하면 '해시한 픽셀'과 '눈으로 본 PNG'가 조용히 갈라진다.

    Args:
        warped: 재워프된 BGR 배열.
        box: new행의 `[y0, y1]`(`replicate_rows`의 boxes 항목).
    """
    from handwriting.rows import ITEM_X

    x1, x2 = ITEM_X
    return warped[box[0] : box[1], x1 - ITEM_CROP_PAD : x2 + ITEM_CROP_PAD]


def crop_digest(crop) -> str:
    """크롭의 sha256 앞 CROP_DIGEST_CHARS자. shape·dtype을 픽셀과 함께 해시한다.

    바이트열만 해시하면 같은 픽셀 수의 다른 shape(예: (6,2,3) vs (4,3,3))가 같은 값이 돼
    크롭 경계가 밀린 것을 '무변경'으로 보고한다. 슬라이스(비연속 view)를 그대로 받아도 안전하다.
    """
    import hashlib

    import numpy as np

    arr = np.ascontiguousarray(crop)
    h = hashlib.sha256(f"{arr.shape}|{arr.dtype}|".encode())
    h.update(arr.tobytes())
    return h.hexdigest()[:CROP_DIGEST_CHARS]


def crop_ink(crop) -> float:
    """크롭의 잉크 비율 — 채널 최대값이 CROP_INK_MAX_LEVEL 미만인 픽셀의 비율(축 ②-b 신호)."""
    return float((crop.max(2) < CROP_INK_MAX_LEVEL).mean())


def replicate_rows(warped) -> dict:
    """워프 → 밴드·new행·크롭 좌표/해시를 모델 없이 재현한다(infer_photo.extract_rows_for_job 대응).

    extract_rows_for_job에서 embed_crops(torch)만 뺀 접두다 — infer_photo는 모듈 최상단
    torch import 때문에 여기서 import할 수 없어 호출 순서를 그대로 옮겨 적는다. 드리프트는
    tests/test_warp_gate_rows.py의 AST 배선 가드가 잡는다.
    """
    from handwriting.canon import global_pitch
    from handwriting.grid_v4 import DATA_Y, amount_crop_left, hline_ys
    from handwriting.group import block_amounts, build_proposal
    from handwriting.grouping import AMT_MIN, ITEM_MIN, PAD
    from handwriting.rows import band_features, detect_grid_rows

    y0, y1 = DATA_Y
    ys = [y for y in hline_ys(warped) if y0 - 40 <= y <= y1 + 40]
    pitch = global_pitch({"x": ys})
    bands = detect_grid_rows(warped, pitch)
    item_inks, amt_inks, stroke_rows = band_features(warped, bands)
    prop = build_proposal(
        bands, item_inks, amt_inks, stroke_rows, [], item_min=ITEM_MIN, amt_min=AMT_MIN, pad=PAD
    )
    # 금액 크롭 좌측 실측(#50) — 운영은 read_fn 크롭에만 쓰고 여기선 전사가 없으므로 값만 기록
    amount_left = amount_crop_left(warped)
    news, _amounts = block_amounts(prop.rows, _fake_read)
    boxes = [[int(r.box[0]), int(r.box[1])] for r in news]
    crops = [item_crop(warped, b) for b in boxes]
    return {
        "amount_left": amount_left,
        "n_bands": len(bands),
        "n_new": len(news),
        "boxes": boxes,
        "crop_sha": [crop_digest(c) for c in crops],
        # 크롭이 '비어있지 않은지'의 정량 신호(축 ②-b). identity가 아니라 진단 신호다 —
        # 무변경 판정(warp_gate_calib.snapshot_diff)은 이 값을 비교에서 뺀다.
        "crop_ink": [crop_ink(c) for c in crops],
    }
