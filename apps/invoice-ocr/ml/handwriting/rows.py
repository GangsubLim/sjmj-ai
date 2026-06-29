"""행검출 + 밴드 특징(이미지→배열) — Path B(검증된 φ-그리드 행 + 이중신호).

행 위치: canon의 deskew+고정피치 φ-그리드(인쇄선 사이 셀, labelset 68/74 입증)를 쓴다.
헤더 HEADER_ROWS행(품목/규격/… 인쇄 타이틀) skip. 각 셀에서 품목칸·금액칸 손글씨 ink를
재 셀의 new/cont/empty 분류(group.build_proposal)에 넘긴다.

ink는 국소대비 마스크에서 '전폭 인쇄 가로선'을 형태학적으로 제거해 손글씨만 남긴다
(셀 경계 격자선이 ink·박스스냅을 오염시키는 것을 막음 — 금액칸 격자선 오검출 교정).

stroke_profile_col/segment_rows/detect_amount_rows: 금액칸 1D 프로파일 유틸(순수
segment_rows는 TDD). Path B 주경로에선 쓰지 않으나 §6 금액행 정렬 보조용으로 보존.
"""

import sys
from pathlib import Path

import cv2
import numpy as np

SP2 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SP2))
sys.path.insert(0, str(Path(__file__).parent))
from canon import fit_phase, grid_rows  # noqa: E402
from grid_v4 import AMOUNT_X, DATA_Y, hline_ys  # noqa: E402

ITEM_X = (100, 392)
Y0, Y1 = DATA_Y
HEADER_ROWS = 3  # 헤더(품목/규격/수량/단가/공급가 인쇄 타이틀) 행 skip — labelset 검증값
ROW_STROKE_ON = 0.02  # 셀 내 한 행이 '획 있음'으로 칠 국소대비 coverage 최소(snap_box_v용)


def _ink_mask(cell):
    """국소대비 손글씨 획 마스크(uint8 0/1). 균일 그림자·옅은 파랑 인쇄선 배제."""
    if cell.size == 0:
        return np.zeros((max(cell.shape[0], 1), max(cell.shape[1], 1)), np.uint8)
    gray = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY).astype(np.int16)
    blur = cv2.GaussianBlur(gray, (0, 0), 11).astype(np.int16)
    return ((gray - blur) < -28).astype(np.uint8)


def _remove_hlines(mask):
    """전폭 인쇄 가로선 제거 — 가로로 긴 수평성분을 open으로 추출해 뺀다."""
    w = mask.shape[1]
    if w < 6:
        return mask
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, w // 3), 1))
    lines = cv2.morphologyEx(mask, cv2.MORPH_OPEN, hk)
    return cv2.subtract(mask, lines)


def detect_grid_rows(warp, P, *, header_rows=HEADER_ROWS):
    """검증된 φ-그리드 데이터 셀 밴드[(a,b)] — 헤더 행 skip."""
    ys = [y for y in hline_ys(warp) if Y0 - 40 <= y <= Y1 + 40]
    phi = fit_phase(ys, P)
    cells = grid_rows(phi, P)
    return cells[header_rows:]


def band_features(warp, bands):
    """밴드별 (item_ink, amt_ink, 품목칸 행별 획 bool)을 측정한다 — build_proposal 입력.

    국소대비 마스크에서 인쇄 가로선 제거 후 측정(손글씨만).
    """
    item_inks, amt_inks, stroke_rows = [], [], []
    ix0, ix1 = ITEM_X
    ax0, ax1 = AMOUNT_X
    for a, b in bands:
        im = _remove_hlines(_ink_mask(warp[a:b, ix0:ix1]))
        am = _remove_hlines(_ink_mask(warp[a:b, ax0:ax1]))
        item_inks.append(float(im.mean()))
        amt_inks.append(float(am.mean()))
        stroke_rows.append((im.mean(axis=1) > ROW_STROKE_ON).tolist())
    return item_inks, amt_inks, stroke_rows


# ── 금액칸 1D 프로파일 유틸(보조; Path B 주경로 미사용) ─────────────────────
def stroke_profile_col(warp, x0, x1):
    """열 [x0,x1)에서 y축 국소대비 획 비율(1D). 전역 그림자·인쇄선 배제."""
    col = warp[:, x0:x1]
    gray = cv2.cvtColor(col, cv2.COLOR_BGR2GRAY).astype(np.int16)
    blur = cv2.GaussianBlur(gray, (0, 0), 11).astype(np.int16)
    return ((gray - blur) < -28).mean(axis=1)


def segment_rows(profile, P, y0, y1, on, min_gap):
    """1D 프로파일을 행 밴드[(a,b)]로 분할한다.

    런 중심을 피치 P 폭 밴드로, min_gap 이내 인접 런은 한 행으로 병합.
    """
    mask = profile >= on
    mask[:y0] = False
    mask[y1:] = False
    centers, i, n = [], 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            w = profile[i:j]
            c = i + (int(np.average(np.arange(j - i), weights=w)) if w.sum() > 0 else (j - i) // 2)
            centers.append(c)
            i = j
        else:
            i += 1
    merged = []
    for c in centers:
        if merged and c - merged[-1] < min_gap:
            merged[-1] = (merged[-1] + c) // 2
        else:
            merged.append(c)
    half = int(P / 2)
    return [(max(y0, c - half), min(y1, c + half)) for c in merged]


def detect_amount_rows(warp, P, *, on=0.20, min_gap=None):
    """금액칸 1D 프로파일에서 행 밴드를 검출한다."""
    prof = stroke_profile_col(warp, AMOUNT_X[0], AMOUNT_X[1])
    return segment_rows(prof, P, Y0, Y1, on, min_gap or int(P * 0.6))
