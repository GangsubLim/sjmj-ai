"""행정렬 derisk v4 — deskew + '고정 피치 행그리드 + 전표별 offset(φ)' 정합.

사용자 제안 구현: 양식 고정 → 행 피치 P 일정. 전표는 deskew로 평탄화 후
φ(상단 offset)만 맞추면 행이 고정좌표로 떨어진다. 그 고정 행으로 품목칸 ink를
세어 '품목-filled == DB 항목수' 일치율을 본다(기존 잉크밴드 4/36 대비).
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from grid_v4 import load_bgr, blue_mask, warp, WARP_W, DATA_Y, hline_ys  # noqa: E402
from rectify import deskew_angle, rotate, form_quad_robust  # noqa: E402

HERE = Path(__file__).parent
OUT = Path("/Users/gangsub/projects/sjmj-ai/apps/invoice-ocr/ml/report")
ITEM_X = (100, 392)
INK = 0.012
Y0, Y1 = DATA_Y


def rect_and_lines(inv):
    w = warp(load_bgr(inv), form_quad_robust(load_bgr(inv)))
    w = rotate(w, deskew_angle(w))
    ys = [y for y in hline_ys(w) if Y0 - 40 <= y <= Y1 + 40]
    return w, sorted(ys)


def global_pitch(per):
    """깨끗한 검출(많고 평탄)에서 인접 행선 간격의 중앙값 = 양식 행 피치."""
    gaps = []
    for ys in per.values():
        if len(ys) >= 14:
            g = np.diff(ys)
            gaps += [x for x in g if 50 <= x <= 130]   # 합리적 행높이만
    return float(np.median(gaps)) if gaps else 83.0


def fit_phase(ys, P):
    """검출 행선들이 φ+kP 격자에 가장 잘 맞는 φ(0~P). 원형 평균으로 추정."""
    if not ys:
        return Y0 % P
    ang = [2 * np.pi * (y % P) / P for y in ys]
    phi = (np.angle(np.mean([np.exp(1j * a) for a in ang])) % (2 * np.pi)) / (2 * np.pi) * P
    return phi


def grid_rows(phi, P):
    """DATA_Y 범위를 덮는 고정 행경계 → (a,b) 셀들."""
    ys = []
    k = 0
    while phi + k * P <= Y1 + 5:
        y = phi + k * P
        if y >= Y0 - 5:
            ys.append(int(round(y)))
        k += 1
    return [(a, b) for a, b in zip(ys, ys[1:])]


def ink_frac(cell):
    return 0.0 if cell.size == 0 else float((cell.max(2) < 120).mean())


def main():
    gt = json.load(open(HERE / "item_gt.json"))
    ids = sorted(gt)
    per = {}
    warps = {}
    for inv in ids:
        w, ys = rect_and_lines(inv)
        per[inv] = ys
        warps[inv] = w
    P = global_pitch(per)
    print(f"전역 행 피치 P = {P:.1f}px\n")
    print(f"{'inv':<9}{'phi':>6}{'rows':>5}{'item+':>7}{'DBn':>5}  match")
    ok = near = 0
    panels = []
    for inv in ids:
        w = warps[inv]
        phi = fit_phase(per[inv], P)
        rows = grid_rows(phi, P)
        item_rows = [(a, b) for a, b in rows if ink_frac(w[a:b, ITEM_X[0]:ITEM_X[1]]) >= INK]
        nf, dbn = len(item_rows), len(gt[inv])
        tag = "OK" if nf == dbn else ("~" if abs(nf - dbn) <= 1 else "X")
        ok += nf == dbn
        near += abs(nf - dbn) <= 1
        print(f"{inv:<9}{phi:>6.0f}{len(rows):>5}{nf:>7}{dbn:>5}  {tag}")
        ov = w.copy()
        for a, b in rows:
            cv2.line(ov, (ITEM_X[0], a), (ITEM_X[1], a), (255, 130, 0), 1)
        for a, b in item_rows:
            cv2.rectangle(ov, (ITEM_X[0], a), (ITEM_X[1], b), (0, 180, 0), 2)
        cv2.putText(ov, f"{inv} {nf}/{dbn}{tag}", (8, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                    (0, 0, 255) if tag == "X" else (0, 150, 0), 2)
        hh = int(ov.shape[0] * 240 / ov.shape[1])
        panels.append(cv2.resize(ov, (240, hh)))
    print(f"\n품목-filled == DBn : {ok}/{len(ids)}  (±1: {near}/{len(ids)})")
    h = max(p.shape[0] for p in panels)
    rows = [panels[i:i + 9] for i in range(0, len(panels), 9)]
    rimg = []
    for r in rows:
        r = [cv2.copyMakeBorder(p, 0, h - p.shape[0], 0, 6, cv2.BORDER_CONSTANT, value=(255, 255, 255)) for p in r]
        rimg.append(np.hstack(r))
    wmax = max(x.shape[1] for x in rimg)
    rimg = [cv2.copyMakeBorder(r, 0, 6, 0, wmax - r.shape[1], cv2.BORDER_CONSTANT, value=(255, 255, 255)) for r in rimg]
    cv2.imwrite(str(OUT / "canon_montage.png"), np.vstack(rimg))
    print("montage →", OUT / "canon_montage.png")


if __name__ == "__main__":
    main()
