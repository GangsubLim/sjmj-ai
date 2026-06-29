"""템플릿 정합 v4 — 전체워프 수평 파랑선으로 행분할 → 공급가 열 1셀=1금액.

v3 교훈: 잉크투영은 인접 금액을 병합. 행 경계는 '전체 표를 가로지르는 긴
수평 격자선'이 안정적(옅어도 가로합이 큼). 헤더는 데이터 y범위로 배제,
빈 행은 잉크 없음으로 스킵.
"""

import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

SRC = Path("/Users/gangsub/Library/CloudStorage/OneDrive-개인/Documents/sjmj image/images")
OUT = Path("/Users/gangsub/projects/sjmj-ai/apps/invoice-ocr/ml/report")
CROPS = Path(__file__).parent / "crops"
WARP_W, WARP_H = 900, 2100
AMOUNT_X = (612, 896)  # 공급가 열 (템플릿)
DATA_Y = (612, 1948)  # 데이터 행 범위 (공급내역 헤더 아래 ~ 합계 위)


def load_bgr(img_id):
    img = ImageOps.exif_transpose(Image.open(SRC / f"{img_id}.jpg")).convert("RGB")
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def blue_mask(bgr):
    b, g, r = (c.astype(np.int16) for c in cv2.split(bgr))
    v = np.maximum(np.maximum(b, g), r)
    return (((b - r) > 10) & (v > 55)).astype(np.uint8) * 255


def blue_mask_enh(bgr):
    """대비향상 파랑마스크 — 과노출로 (b−r)이 옅어진 흐린 격자 복구. 표준 blue_mask의
    고정 임계(b−r>10)가 바랜 청색 격자를 놓치는 것을, (b−r) 양수부를 per-image 정규화 +
    CLAHE + 적응 임계로 '상대적' 파랑까지 살린다. 노이즈는 hline_ys의 가로 morphology가
    걸러 격자선만 남는다(흐린격자 1→14선 실측). per-sheet 흐림 회수에서만 쓴다(아래 _FAINT).
    """
    b, g, r = (c.astype(np.float32) for c in cv2.split(bgr))
    diff = np.clip(b - r, 0, None)
    mx = float(diff.max())
    if mx < 1:
        return np.zeros(bgr.shape[:2], np.uint8)
    norm = cv2.createCLAHE(2.0, (8, 8)).apply((diff / mx * 255).astype(np.uint8))
    return cv2.adaptiveThreshold(norm, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 31, -5)


# ── per-sheet 흐림 회수 토글 ─────────────────────────────────────────────
# 전역 hline 변경은 정상 전표 위상 교란(trusted 66→63·crop 265→244 실측 회귀)이라,
# 흐림 회수는 명시적으로 플래그된 전표를 처리하는 동안에만 켠다. 기본 OFF = 무회귀.
_FAINT = False


class faint_on:
    """with faint_on(cn in FAINT_SET): ... — 블록 안에서만 hline_ys가 대비향상 마스크 사용."""

    def __init__(self, on):
        self.on = bool(on)

    def __enter__(self):
        global _FAINT
        self.prev = _FAINT
        _FAINT = self.on

    def __exit__(self, *exc):
        global _FAINT
        _FAINT = self.prev


def _order(pts):
    pts = pts.astype(np.float32)
    s, d = pts.sum(1), pts[:, 0] - pts[:, 1]
    return np.array(
        [pts[s.argmin()], pts[d.argmax()], pts[s.argmax()], pts[d.argmin()]], np.float32
    )


def _edge_balance(q):
    """마주보는 변 길이비(0~1). 1에 가까울수록 온전한 사각형."""
    import numpy as _np  # noqa: PLC0415

    e = [float(_np.linalg.norm(q[i] - q[(i + 1) % 4])) for i in range(4)]
    lr = min(e[1], e[3]) / max(e[1], e[3])  # 우변 vs 좌변
    tb = min(e[0], e[2]) / max(e[0], e[2])  # 상변 vs 하변
    return min(lr, tb)


def form_quad(mask):
    """하이브리드: 극점(원근보정)이 기본, quad가 깨지면(마스크 누락·배경오염으로
    한 코너가 안쪽으로 끌림 — inv_001) minrect로 폴백. 극점은 정밀하나 마스크
    누락에 취약, minrect는 강건하나 원근 손실 → 깨짐 판정으로 양쪽 장점만.
    """
    big = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))
    big = cv2.dilate(big, np.ones((9, 9), np.uint8), iterations=2)
    cnts, _ = cv2.findContours(big, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    c = max(cnts, key=cv2.contourArea)
    pts = c.reshape(-1, 2).astype(np.float32)
    s, d = pts[:, 0] + pts[:, 1], pts[:, 0] - pts[:, 1]
    extreme = np.array(
        [pts[s.argmin()], pts[d.argmax()], pts[s.argmax()], pts[d.argmin()]], np.float32
    )
    if _edge_balance(extreme) >= 0.72:
        return extreme
    return _order(cv2.boxPoints(cv2.minAreaRect(c)))


def warp(bgr, quad):
    dst = np.array([[0, 0], [WARP_W, 0], [WARP_W, WARP_H], [0, WARP_H]], dtype=np.float32)
    return cv2.warpPerspective(bgr, cv2.getPerspectiveTransform(quad, dst), (WARP_W, WARP_H))


def _hlines_from_mask(m):
    horiz = cv2.morphologyEx(
        m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (WARP_W // 3, 1))
    )
    prof = horiz.mean(1) / 255.0
    on = prof >= 0.18
    ys, i, n = [], 0, len(on)
    while i < n:
        if on[i]:
            j = i
            while j < n and on[j]:
                j += 1
            ys.append((i + j) // 2)
            i = j
        else:
            i += 1
    return ys


def hline_ys(warped):
    """전체 표를 가로지르는 수평 파랑 격자선의 y들. faint_on 블록 안에서만(흐림 회수)
    대비향상 마스크를 쓰되, 그게 DATA_Y 내 선을 더 줄 때만 채택(아니면 표준).
    """
    ys = _hlines_from_mask(blue_mask(warped))
    if _FAINT:
        in_data = [y for y in ys if DATA_Y[0] - 40 <= y <= DATA_Y[1] + 40]
        ys2 = _hlines_from_mask(blue_mask_enh(warped))
        in_data2 = [y for y in ys2 if DATA_Y[0] - 40 <= y <= DATA_Y[1] + 40]
        if len(in_data2) > len(in_data):
            return ys2
    return ys


def main(img_id):
    CROPS.mkdir(exist_ok=True)
    bgr = load_bgr(img_id)
    warped = warp(bgr, form_quad(blue_mask(bgr)))
    x1, x2 = AMOUNT_X
    ys = [y for y in hline_ys(warped) if DATA_Y[0] - 30 <= y <= DATA_Y[1] + 30]

    ov = warped.copy()
    for y in ys:
        cv2.line(ov, (0, y), (WARP_W, y), (0, 0, 255), 1)
    cells = []
    for a, b in zip(ys, ys[1:]):
        if b - a < 40:  # 너무 얇은 간격(이중선) 스킵
            continue
        cell = warped[a:b, x1:x2]
        if (cell.max(2) < 120).mean() < 0.012:  # 잉크 거의 없음 = 빈 행
            continue
        cv2.rectangle(ov, (x1, a), (x2, b), (0, 200, 0), 2)
        k = len(cells)
        cv2.imwrite(
            str(CROPS / f"{img_id}_amt_{k:02d}.png"), warped[a - 3 : b + 3, x1 - 4 : x2 + 4]
        )
        cells.append((a, b))
    h = int(ov.shape[0] * 560 / ov.shape[1])
    cv2.imwrite(str(OUT / f"g4_{img_id}_overlay.png"), cv2.resize(ov, (560, h)))
    print(f"[{img_id}] hlines={len(ys)} amount_cells={len(cells)}")


if __name__ == "__main__":
    for img_id in sys.argv[1:] or ["inv_003"]:
        main(img_id)
