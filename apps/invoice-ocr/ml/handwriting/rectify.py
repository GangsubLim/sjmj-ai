"""행정렬 derisk v3 — 양식 전체 평탄화(deskew) → 격자선 재검출.

사용자 통찰: 4-코너 워프는 모서리만 맞춰 내부 격자선의 잔여 기울기/전단을 못 편다
(inv_001=0선). 양식은 고정이므로 '모든 파란 선이 평탄'해지도록 보정하면 행 y가
고정좌표로 떨어진다. 1차 검증: Hough로 수평 파랑선 각도→회전 평탄화 후
strict hline 재검출이 살아나는지 + y 분포가 전표간 일관되는지.

usage: rectify.py            # 36장 측정 + 몽타주
       rectify.py inv_001    # 단건
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from grid_v4 import (load_bgr, blue_mask, form_quad, warp, WARP_W, WARP_H, DATA_Y,  # noqa: E402
                     hline_ys, _edge_balance, _order)

HERE = Path(__file__).parent
OUT = Path("/Users/gangsub/projects/sjmj-ai/apps/invoice-ocr/ml/report")
ITEM_X = (100, 392)
# 적응적 검출 ladder: 기본→느슨. 밝은 노출로 폼 하단 파랑이 옅어 contour가 조각나면
# (inv_001: area 0.10) 느슨한 임계+큰 CLOSE로 폼 전체를 한 덩어리로 잡는다.
_QUAD_LADDER = [(10, 55, 25), (8, 45, 35), (6, 40, 45)]
_MIN_AREA = 0.25
_RECT_W = 2.5   # 직사각성 점수에서 각도분산(직진성) 가중 — 직진성을 선완성도보다 우대


def _cmask(bgr, brt, vt):
    b, g, r = (c.astype(np.int16) for c in cv2.split(bgr))
    v = np.maximum(np.maximum(b, g), r)
    return (((b - r) > brt) & (v > vt)).astype(np.uint8) * 255


def _quad_extreme(bgr):
    """축정렬 극점(min/max of x±y) 코너검출 — 폼이 프레임에 거의 정렬됐을 때 정밀.
    contour가 기대면적(>=25%)을 못 채우면 임계를 단계적으로 완화. 회전·원근이 크면
    극점이 변 위 엉뚱한 점을 집어 워프 후 사다리꼴 잔여가 남는다(→ 후보 중 하나로만 사용)."""
    A = bgr.shape[0] * bgr.shape[1]
    best, best_area = None, -1.0
    for brt, vt, ck in _QUAD_LADDER:
        m = _cmask(bgr, brt, vt)
        big = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((ck, ck), np.uint8))
        big = cv2.dilate(big, np.ones((9, 9), np.uint8), iterations=2)
        cnts, _ = cv2.findContours(big, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            continue
        c = max(cnts, key=cv2.contourArea)
        pts = c.reshape(-1, 2).astype(np.float32)
        s, d = pts[:, 0] + pts[:, 1], pts[:, 0] - pts[:, 1]
        extreme = np.array([pts[s.argmin()], pts[d.argmax()], pts[s.argmax()], pts[d.argmin()]], np.float32)
        eb = _edge_balance(extreme)
        quad = extreme if eb >= 0.72 else _order(cv2.boxPoints(cv2.minAreaRect(c)))
        area = cv2.contourArea(c) / A
        if area > best_area:
            best, best_area = quad, area
        if area >= _MIN_AREA and eb >= 0.72:
            return quad
    return best


def _form_blob(bgr, dilate):
    """파랑마스크 ladder에서 최대 면적 contour(폼 덩어리). dilate=False면 부풀림 없이
    tight — 코너가 배경/인접지로 끌려가지 않아 회전 폼에서 approx/minrect가 정확해진다
    (기존 dilate 9x9x2는 코너를 ~18px 바깥으로 밀어 오선택 유발)."""
    A = bgr.shape[0] * bgr.shape[1]
    best, best_area = None, -1.0
    for brt, vt, ck in _QUAD_LADDER:
        m = _cmask(bgr, brt, vt)
        big = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((ck, ck), np.uint8))
        if dilate:
            big = cv2.dilate(big, np.ones((9, 9), np.uint8), iterations=2)
        cnts, _ = cv2.findContours(big, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            continue
        c = max(cnts, key=cv2.contourArea)
        a = cv2.contourArea(c) / A
        if a > best_area:
            best, best_area = c, a
    return best


def _quad_approx(c):
    """convex hull → approxPolyDP로 epsilon 키우며 정확히 4꼭짓점 폴리곤. 축정렬 극점과
    달리 실제 코너를 찾으므로 회전·원근 불변. 4-gon 근사 실패 시 None."""
    if c is None:
        return None
    hull = cv2.convexHull(c)
    peri = cv2.arcLength(hull, True)
    for k in np.linspace(0.01, 0.10, 19):
        ap = cv2.approxPolyDP(hull, k * peri, True)
        if len(ap) == 4:
            return _order(ap.reshape(4, 2).astype(np.float32))
    return None


def _quad_derot(c):
    """minAreaRect 각도로 contour를 de-rotate → 축정렬 프레임에서 극점법(min/max x±y)으로
    코너 검출 → 역회전해 되돌린다. 극점법은 축정렬일 때만 유효하므로 회전 폼을 먼저 펴서
    적용 — minrect와 달리 원근을 보존하고, 그냥 extreme과 달리 회전에 강건하다. 회전+원근
    동시 케이스(inv050: hstd 2.0→0.4)의 사다리꼴 코너를 정확히 잡는다."""
    if c is None:
        return None
    (cx, cy), _, ang = cv2.minAreaRect(c)
    M = cv2.getRotationMatrix2D((cx, cy), ang, 1.0)
    pts = c.reshape(-1, 2).astype(np.float32)
    rot = cv2.transform(pts[None], M)[0]
    s, d = rot[:, 0] + rot[:, 1], rot[:, 0] - rot[:, 1]
    corners = np.array([rot[s.argmin()], rot[d.argmax()], rot[s.argmax()], rot[d.argmin()]], np.float32)
    back = cv2.transform(corners[None], cv2.invertAffineTransform(M))[0]
    return _order(back.astype(np.float32))


def _candidate_quads(bgr):
    """코너검출 후보 셋. 어느 단일 방식도 보편적이지 않다(near-axis는 극점이, 회전 폼은
    approx/minrect가 유리) → 여럿 만들어 결과로 고른다(form_quad_robust). extreme를 항상
    첫 후보로 둬 동점 시 구버전 동작을 보존(회귀 방지)."""
    cd, ct = _form_blob(bgr, True), _form_blob(bgr, False)
    quads = {"extreme": _quad_extreme(bgr)}
    qa = _quad_approx(cd)
    if qa is not None:
        quads["approxD"] = qa
    qt = _quad_approx(ct)
    if qt is not None:
        quads["approxT"] = qt
    if ct is not None:
        quads["minrectT"] = _order(cv2.boxPoints(cv2.minAreaRect(ct)))
    qdd = _quad_derot(cd)
    if qdd is not None:
        quads["derotD"] = qdd
    qdt = _quad_derot(ct)
    if qdt is not None:
        quads["derotT"] = qdt
    return {k: q for k, q in quads.items() if q is not None}


def _line_spread(warped):
    """워프+deskew 후 근수평 파랑선 각도의 표준편차(도). 0=완벽 평행(직사각), 크면
    사다리꼴 수렴(원근 잔여). 검출선 부족 시 6.0(불량 페널티)."""
    m = blue_mask(warped)
    lines = cv2.HoughLinesP(m, 1, np.pi / 360, threshold=120,
                            minLineLength=WARP_W // 4, maxLineGap=30)
    if lines is None:
        return 6.0
    angs = [np.degrees(np.arctan2(y2 - y1, x2 - x1)) for x1, y1, x2, y2 in lines[:, 0]]
    angs = [a for a in angs if abs(a) < 25]
    return float(np.std(angs)) if len(angs) >= 3 else 6.0


def _rect_score(bgr, quad):
    """직사각성 점수 = DATA_Y 범위 full-width 수평 격자선 수 − _RECT_W·각도분산. 선완성도
    (다운스트림 global_pitch/fit_phase가 쓰는 신호)와 직진성을 동시에 최적화한다."""
    w0 = warp(bgr, quad)
    w = rotate(w0, deskew_angle(w0))
    n = len([y for y in hline_ys(w) if DATA_Y[0] - 40 <= y <= DATA_Y[1] + 40])
    return n - _RECT_W * _line_spread(w)


def form_quad_robust(bgr):
    """회전·원근 불변 best-of-candidates 코너검출. 여러 전략(극점·approxPolyDP·minAreaRect·
    de-rotated 극점)으로 각각 워프해보고 결과가 가장 직사각인 quad를 채택한다. homography는
    직선을 보존하므로 코너만 맞으면 격자가 완벽한 직사각이 된다 — 핵심은 '올바른 코너'이고
    그것을 결과(직사각성)로 검증해 고른다. 잔여 미해결은 동종 파랑격자 종이더미 위에 겹친
    가림(occlusion) 케이스로 blob이 번지는 마스킹 한계(코너검출의 범위 밖)."""
    quads = _candidate_quads(bgr)
    if not quads:
        return None
    return max(quads.values(), key=lambda q: _rect_score(bgr, q))


def deskew_angle(warped):
    """Hough로 긴 근수평 파랑선들의 중앙 각도(도). 양수=시계방향 기울기."""
    m = blue_mask(warped)
    lines = cv2.HoughLinesP(m, 1, np.pi / 360, threshold=150,
                            minLineLength=WARP_W // 3, maxLineGap=30)
    if lines is None:
        return 0.0
    angs = []
    for x1, y1, x2, y2 in lines[:, 0]:
        a = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        if abs(a) < 25:                 # 근수평만
            angs.append(a)
    return float(np.median(angs)) if angs else 0.0


def rotate(img, ang):
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), ang, 1.0)
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))


def rectified(inv):
    w = warp(load_bgr(inv), form_quad_robust(load_bgr(inv)))
    ang = deskew_angle(w)
    return w, rotate(w, ang), ang


def hlines_in_data(img):
    return [y for y in hline_ys(img) if DATA_Y[0] - 40 <= y <= DATA_Y[1] + 40]


def main(ids, gt):
    panels = []
    all_ys = []
    print(f"{'inv':<9}{'ang°':>7}{'before':>7}{'after':>7}{'DBn':>5}")
    for inv in ids:
        w0, w1, ang = rectified(inv)
        b, a = len(hlines_in_data(w0)), hlines_in_data(w1)
        all_ys.append(a)
        print(f"{inv:<9}{ang:>7.2f}{len(hlines_in_data(w0)):>7}{len(a):>7}{len(gt[inv]):>5}")
        ov = w1.copy()
        for y in a:
            cv2.line(ov, (0, y), (WARP_W, y), (0, 0, 255), 1)
        cv2.rectangle(ov, (ITEM_X[0], DATA_Y[0]), (ITEM_X[1], DATA_Y[1]), (0, 180, 0), 2)
        cv2.putText(ov, f"{inv} {ang:+.1f} {len(a)}/{len(gt[inv])}", (8, 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        hh = int(ov.shape[0] * 240 / ov.shape[1])
        panels.append(cv2.resize(ov, (240, hh)))
    # 몽타주
    h = max(p.shape[0] for p in panels)
    rows = [panels[i:i + 9] for i in range(0, len(panels), 9)]
    rimg = []
    for r in rows:
        r = [cv2.copyMakeBorder(p, 0, h - p.shape[0], 0, 6, cv2.BORDER_CONSTANT, value=(255, 255, 255)) for p in r]
        rimg.append(np.hstack(r))
    wmax = max(x.shape[1] for x in rimg)
    rimg = [cv2.copyMakeBorder(r, 0, 6, 0, wmax - r.shape[1], cv2.BORDER_CONSTANT, value=(255, 255, 255)) for r in rimg]
    cv2.imwrite(str(OUT / "rectify_montage.png"), np.vstack(rimg))
    print("\nmontage →", OUT / "rectify_montage.png")


if __name__ == "__main__":
    gt = json.load(open(HERE / "item_gt.json"))
    ids = sys.argv[1:] or sorted(gt)
    main(ids, gt)
