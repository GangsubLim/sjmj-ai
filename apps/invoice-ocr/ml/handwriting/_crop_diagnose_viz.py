"""품목 크롭 확장 + 행검출 진단 검수 HTML (모델·뱅크 불필요).

검수 피드백 3종을 이슈로 체크·귀속한다:
  1) 확장 미흡 의심 — new 행에서 실제 잉크가 경계 너머로 뻗는데 gap-stop/세로선억제가
     잘라낸 경우 (== 이 브랜치가 바꾼 크롭 기능의 튜닝 후보).
  2) 맨 위/기타 품목 미검출 — 품목칸에 손글씨가 있으나 new로 분류 안 된 밴드
     (== classify_types/trim 기존 행검출 동작. 이 브랜치는 검출 로직을 안 바꿨으므로 회귀 아님).
  3) 품목 0개 — 사진 전체에서 new 행이 하나도 안 잡힌 경우.

각 데이터 밴드의 품목칸 전체 셀(x0..상한)을 렌더하고 빨강(기존 392+4)·파랑(신규 경계)·
초록(실제 최우측 잉크) 수직선을 겹쳐, 경계가 손글씨 대비 어디서 잘렸는지 눈으로 확인한다.

Usage: poc/bin/python handwriting/_crop_diagnose_viz.py <이미지 폴더 또는 파일...>
"""

import base64
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from canon import global_pitch  # noqa: E402
from dataset_build import load_bgr_path  # noqa: E402
from grid_v4 import AMOUNT_X, DATA_Y, hline_ys, warp  # noqa: E402
from group import build_proposal  # noqa: E402
from grouping import AMT_MIN, ITEM_MIN, PAD  # noqa: E402
from rectify import deskew_angle, form_quad_robust, rotate  # noqa: E402
from rows import (  # noqa: E402
    COL_INK_ON,
    ITEM_GAP_PX,
    ITEM_MARGIN_PX,
    ITEM_X,
    _ink_mask,
    _remove_vlines,
    _right_bound_from_col_ink,
    band_features,
    detect_grid_rows,
    item_crop_right_bound,
)

Y0, Y1 = DATA_Y
OUT = HERE.parent / "review/crop_diagnose.html"
X0, X_FLOOR = ITEM_X
LIMIT = max(AMOUNT_X[0] - ITEM_MARGIN_PX, X_FLOOR)  # 확장 상한


def b64(bgr, w, q=75):
    """BGR을 너비 w JPEG base64 data-URI로."""
    h = max(16, int(bgr.shape[0] * w / bgr.shape[1]))
    img = cv2.resize(bgr, (w, h))
    enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])[1]
    return "data:image/jpeg;base64," + base64.b64encode(enc).decode()


def raw_rightmost_ink_x(w, band):
    """세로선 억제 후 COL_INK_ON 이상인 최우측 잉크 열의 절대 x (없으면 None).

    gap-stop을 적용하지 않은 '잉크가 존재하는 최우측'(옆칸 포함). 초록선으로 표시해
    경계 너머 잉크가 같은 품목명인지 옆칸(규격/수량/단가)인지 육안 판단용.
    """
    a, b = band
    cell = w[a:b, X0:LIMIT]
    if cell.size == 0:
        return None
    mask = _remove_vlines(_ink_mask(cell))
    col_ink = mask.mean(axis=0) >= COL_INK_ON
    idx = np.flatnonzero(col_ink)
    return int(X0 + idx[-1]) if idx.size else None


def bound_without_vsuppress(w, band):
    """세로선 억제를 끄고 계산한 gap-stop 경계 (억제가 경계를 단축했는지 비교용).

    item_crop_right_bound와 동일하되 _remove_vlines만 생략. 이 값이 실제 경계보다
    크게 앞서면 _remove_vlines가 (실제 획이든 인쇄선이든) 잉크를 지워 경계를 당긴 것.
    """
    a, b = band
    cell = w[a:b, X0:LIMIT]
    if cell.size == 0:
        return X_FLOOR
    col_ink = _ink_mask(cell).mean(axis=0) >= COL_INK_ON
    return _right_bound_from_col_ink(col_ink, item_x1=X_FLOOR, x_start=X0, gap_px=ITEM_GAP_PX)


def cell_with_lines(w, band, old_r, new_r, raw_x):
    """품목칸 전체 셀에 기존(빨강)·신규(파랑)·실잉크끝(초록) 수직선을 그린 crop."""
    a, b = band
    lo = X0 - 4
    cell = w[a:b, lo:LIMIT].copy()
    for x_abs, color in ((old_r, (0, 0, 255)), (new_r, (255, 0, 0))):
        rx = x_abs - lo
        if 0 <= rx < cell.shape[1]:
            cv2.line(cell, (rx, 0), (rx, cell.shape[0] - 1), color, 1)
    if raw_x is not None:
        rx = raw_x - lo
        if 0 <= rx < cell.shape[1]:
            cv2.line(cell, (rx, 0), (rx, cell.shape[0] - 1), (0, 200, 0), 1)
    return cell


def analyze(src):
    """사진 1장 → (overlay, [band_dict...], per-image flags)."""
    bgr = load_bgr_path(src)
    q = form_quad_robust(bgr)
    w = rotate(warp(bgr, q), deskew_angle(warp(bgr, q)))

    ys = [y for y in hline_ys(w) if Y0 - 40 <= y <= Y1 + 40]
    p = global_pitch({"x": ys})
    bands = detect_grid_rows(w, p)
    item_inks, amt_inks, stroke_rows = band_features(w, bands)
    prop = build_proposal(
        bands, item_inks, amt_inks, stroke_rows, [], item_min=ITEM_MIN, amt_min=AMT_MIN, pad=PAD
    )

    x1, x2 = ITEM_X
    ov = w.copy()
    color = {
        "new": (0, 170, 0),
        "cont": (220, 130, 0),
        "empty": (190, 190, 190),
        "total": (0, 140, 255),
    }
    recs = []
    first_data_missed = False
    seen_new = False
    for r in prop.rows:
        a, b = r.band
        cv2.rectangle(ov, (x1, a), (x2, b), color.get(r.rtype, (150, 150, 150)), 1)
        box = r.box if (r.rtype == "new" and r.box) else r.band
        raw_x = raw_rightmost_ink_x(w, box)
        rec = {
            "rtype": r.rtype,
            "item_ink": r.item_ink,
            "amt_ink": r.amt_ink,
            "flag": None,
            "attr": None,
        }
        if r.rtype == "new" and r.box:
            seen_new = True
            rb = item_crop_right_bound(w, r.box)
            old_r, new_r = x2 + 4, rb + 4
            cv2.rectangle(ov, (x1 - 4, r.box[0]), (x2 + 4, r.box[1]), (0, 0, 255), 2)
            cv2.rectangle(ov, (x1 - 4, r.box[0]), (rb + 4, r.box[1]), (255, 0, 0), 2)
            rec["old_r"], rec["new_r"], rec["raw_x"], rec["rb"] = old_r, new_r, raw_x, rb
            rec["cell"] = cell_with_lines(w, r.box, old_r, new_r, raw_x)
            delta = rb - x2
            width = rb + 4 - (x1 - 4)
            # feature 결함: 과확장 — 상한 도달 또는 +150px↑ = 옆칸(규격/수량/단가) 잠식 → 크롭 오염
            if rb >= LIMIT - 4 or delta >= 150:
                rec["flag"] = f"과확장 — 옆칸 잠식 의심 (+{delta}px, 폭 {width}px, 상한 x{LIMIT})"
                rec["attr"] = "feature"
            elif delta > 0:
                rec["flag"] = f"정상 확장 (+{delta}px, 폭 {width}px)"
                rec["attr"] = "ok"
            else:
                rec["flag"] = "확장 없음 (기존 폭 유지)"
                rec["attr"] = "ok"
        else:
            rec["cell"] = cell_with_lines(w, r.band, x2 + 4, x2 + 4, raw_x)
            # 미검출 의심: new 아닌데 품목칸 손글씨가 ITEM_MIN 이상 존재
            if r.item_ink >= ITEM_MIN:
                why = (
                    "금액칸 부족(amt<0.045)" if r.amt_ink < AMT_MIN else "trim/분류(상단·하단 절단)"
                )
                rec["flag"] = f"품목 손글씨 있으나 미검출 — {why}"
                rec["attr"] = "detect"
                if not seen_new:
                    first_data_missed = True
        recs.append(rec)

    n_new = sum(1 for r in prop.rows if r.rtype == "new" and r.box)
    flags = {"no_new": n_new == 0, "top_missed": first_data_missed, "n_new": n_new}
    return ov, recs, flags


def render_band(rec):
    """밴드 1개 → HTML 카드."""
    attr_cls = {"feature": "f-feature", "detect": "f-detect", "ok": "f-ok"}.get(rec["attr"], "")
    metrics = f"item {rec['item_ink']:.3f} · amt {rec['amt_ink']:.3f}"
    flag = f'<div class="flag {attr_cls}">{rec["flag"]}</div>' if rec["flag"] else ""
    return (
        f'<div class="band {rec["rtype"]}">'
        f"<div class=meta><b>{rec['rtype']}</b><br><small>{metrics}</small>{flag}</div>"
        f'<img src="{b64(rec["cell"], 420)}">'
        f"</div>"
    )


def main(args):
    """이미지들을 진단해 HTML을 쓴다."""
    paths = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            paths += sorted(x for x in p.iterdir() if x.suffix.lower() in (".jpg", ".jpeg", ".png"))
        elif p.is_file():
            paths.append(p)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    sections = []
    tot = {"img": 0, "new": 0, "underexp": 0, "missed": 0, "no_new_imgs": [], "top_missed_imgs": []}
    for src in paths:
        try:
            ov, recs, fl = analyze(src)
        except Exception as e:  # noqa: BLE001 — 검수: 개별 실패 격리
            sections.append(f"<section><h2>{src.name}</h2><p class=err>실패: {e}</p></section>")
            continue
        tot["img"] += 1
        tot["new"] += fl["n_new"]
        tot["underexp"] += sum(1 for r in recs if r["attr"] == "feature")
        tot["missed"] += sum(1 for r in recs if r["attr"] == "detect")
        if fl["no_new"]:
            tot["no_new_imgs"].append(src.name)
        if fl["top_missed"]:
            tot["top_missed_imgs"].append(src.name)
        badges = []
        if fl["no_new"]:
            badges.append('<span class="ib no">품목 0개 검출</span>')
        if fl["top_missed"]:
            badges.append('<span class="ib top">맨 위 품목 미검출 의심</span>')
        nu = sum(1 for r in recs if r["attr"] == "feature")
        nm = sum(1 for r in recs if r["attr"] == "detect")
        if nu:
            badges.append(f'<span class="ib fe">과확장 {nu}</span>')
        if nm:
            badges.append(f'<span class="ib de">미검출 {nm}</span>')
        sections.append(
            f"<section><h2>{src.name} <small>new {fl['n_new']}</small> {''.join(badges)}</h2>"
            f'<img class=ov src="{b64(ov, 640)}">'
            f"<div class=bands>{''.join(render_band(r) for r in recs)}</div></section>"
        )

    head = (
        "<style>body{font:14px system-ui;margin:22px;background:#faf9f7;color:#222}"
        "section{margin:0 0 34px;padding:14px;background:#fff;border:1px solid #e5e3df;border-radius:8px}"
        "h2{margin:0 0 10px;font-size:15px}small{color:#999;font-weight:400}"
        ".ov{max-width:100%;border:1px solid #ccc}"
        ".bands{margin-top:12px;display:grid;gap:6px}"
        ".band{display:flex;gap:12px;align-items:center;padding:5px 8px;border-radius:5px;background:#fafafa}"
        ".band.new{background:#f2fbf3}.band.total{background:#fff6e9}"
        ".meta{min-width:190px;font-size:12px}.meta b{text-transform:uppercase;font-size:11px;color:#555}"
        ".band img{display:block;border:1px solid #ddd;flex:1}"
        ".flag{margin-top:4px;font-size:12px;font-weight:600}"
        ".f-feature{color:#c47f00}.f-detect{color:#c0272d}.f-ok{color:#2a8a3e}"
        ".ib{display:inline-block;margin-left:6px;padding:1px 8px;border-radius:10px;font-size:12px;font-weight:600;color:#fff}"
        ".ib.no{background:#c0272d}.ib.top{background:#d0451b}.ib.fe{background:#c47f00}.ib.de{background:#a13} "
        ".lead{margin:0 0 22px;padding:14px 18px;background:#eef4ff;border-radius:8px;line-height:1.7}"
        "b.r{color:#d00}b.b{color:#1560d6}b.g{color:#2a8a3e}code{background:#eee;padding:1px 5px;border-radius:3px}</style>"
    )
    lead = (
        "<div class=lead><b>품목 크롭 확장 · 행검출 진단</b><br>"
        "셀 이미지 수직선: <b class=r>빨강</b>=기존 고정폭(392+4) · <b class=b>파랑</b>=신규 확장 경계 · "
        "<b class=g>초록</b>=실제 최우측 잉크.<br>"
        '<span style="color:#c47f00;font-weight:600">■ 과확장</span>(크롭 기능 결함 — 상한 도달/+150px↑, 옆칸 규격·수량·단가 잠식 → tight_crop 후에도 품목이 잡음에 묻힘) · '
        '<span style="color:#c0272d;font-weight:600">■ 미검출</span>(품목 손글씨 있으나 new 분류 실패 — 기존 행검출/FaintOn 발산, 이 브랜치 범위 밖) · '
        '<span style="color:#2a8a3e;font-weight:600">■ 정상</span><br>'
        f"이미지 <b>{tot['img']}</b>장 · new <b>{tot['new']}</b> · "
        f"<span style='color:#c47f00'>과확장 <b>{tot['underexp']}</b></span> · "
        f"<span style='color:#c0272d'>미검출 밴드 <b>{tot['missed']}</b></span> · "
        f"품목 0개 이미지 <b>{len(tot['no_new_imgs'])}</b> · 맨위 미검출 의심 <b>{len(tot['top_missed_imgs'])}</b><br>"
        f"<small>임계: ITEM_MIN={ITEM_MIN} AMT_MIN={AMT_MIN} · 크롭: ITEM_GAP_PX={ITEM_GAP_PX} "
        f"ITEM_MARGIN_PX={ITEM_MARGIN_PX} COL_INK_ON={COL_INK_ON} 상한 x{LIMIT}</small></div>"
    )
    if tot["no_new_imgs"]:
        lead += (
            f"<div class=lead><b>품목 0개 검출 이미지</b>: {', '.join(tot['no_new_imgs'])}</div>"
        )
    OUT.write_text(
        f"<!doctype html><meta charset=utf-8><title>crop 진단</title>{head}{lead}{''.join(sections)}"
    )
    print(f"wrote {OUT}")
    print(
        f"img {tot['img']} · new {tot['new']} · 과확장 {tot['underexp']} · "
        f"미검출밴드 {tot['missed']} · 품목0개 {len(tot['no_new_imgs'])} · "
        f"맨위미검출 {len(tot['top_missed_imgs'])}"
    )


if __name__ == "__main__":
    main(sys.argv[1:] or [str(HERE.parent / "data/image_dataset")])
