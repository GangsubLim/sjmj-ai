"""품목 크롭 우측 확장 시각 검수 (모델·뱅크 불필요, 경계 로직만).

infer_photo의 warp+행검출 경로를 그대로 재사용하되 임베딩·금액 OCR(모델 의존)은
건너뛴다. 각 new 행에 기존 고정폭(빨강)·신규 확장(파랑) 경계 박스를 그리고, 카드에
기존/확장 크롭 썸네일을 나란히 둔다. 산출: review/crop_compare.html (로컬 전용).

Usage: poc/bin/python handwriting/_crop_compare_viz.py <이미지 폴더 또는 파일...>
"""

import base64
import sys
from pathlib import Path

import cv2

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from canon import global_pitch  # noqa: E402
from dataset_build import load_bgr_path  # noqa: E402
from grid_v4 import DATA_Y, hline_ys, warp  # noqa: E402
from group import build_proposal  # noqa: E402
from grouping import AMT_MIN, ITEM_MIN, PAD  # noqa: E402
from rectify import deskew_angle, form_quad_robust, rotate  # noqa: E402
from rows import ITEM_X, band_features, detect_grid_rows, item_crop_right_bound  # noqa: E402

Y0, Y1 = DATA_Y
OUT = HERE.parent / "review/crop_compare.html"


def b64(bgr, w=680, q=72):
    """BGR 이미지를 너비 w로 리사이즈해 JPEG base64로(사진 압축률↑ — 용량 절감)."""
    h = max(20, int(bgr.shape[0] * w / bgr.shape[1]))
    img = cv2.resize(bgr, (w, h))
    enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])[1]
    return "data:image/jpeg;base64," + base64.b64encode(enc).decode()


def collect_images(args):
    """인자(폴더/파일)에서 jpg/png 경로 목록을 만든다(재귀 없음, 정렬)."""
    paths = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            paths += sorted(x for x in p.iterdir() if x.suffix.lower() in (".jpg", ".jpeg", ".png"))
        elif p.is_file():
            paths.append(p)
    return paths


def process(src):
    """사진 1장 → (overlay_bgr, [(old_crop, new_crop, old_r, new_r, box)])."""
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
    news = [r for r in prop.rows if r.rtype == "new" and r.box]

    x1, x2 = ITEM_X
    ov = w.copy()
    cards = []
    for r in news:
        rb = item_crop_right_bound(w, r.box)
        old_c = w[r.box[0] : r.box[1], x1 - 4 : x2 + 4]
        new_c = w[r.box[0] : r.box[1], x1 - 4 : rb + 4]
        cv2.rectangle(ov, (x1 - 4, r.box[0]), (x2 + 4, r.box[1]), (0, 0, 255), 2)  # 기존(빨강)
        cv2.rectangle(ov, (x1 - 4, r.box[0]), (rb + 4, r.box[1]), (255, 0, 0), 2)  # 확장(파랑)
        cards.append((old_c, new_c, x2 + 4, rb + 4, r.box))
    return ov, cards


def main(args):
    """이미지들을 처리해 비교 HTML을 쓴다."""
    paths = collect_images(args)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    sections = []
    total_rows = expanded_rows = 0
    max_delta = 0
    for src in paths:
        try:
            ov, cards = process(src)
        except Exception as e:  # noqa: BLE001 — 검수 스크립트: 개별 실패 격리
            sections.append(f"<section><h2>{src.name}</h2><p class=err>실패: {e}</p></section>")
            continue
        rows_html = []
        for i, (old_c, new_c, old_r, new_r, _box) in enumerate(cards):
            total_rows += 1
            delta = new_r - old_r
            if delta > 0:
                expanded_rows += 1
                max_delta = max(max_delta, delta)
            tag = f"+{delta}px 확장" if delta > 0 else "확장 없음"
            cls = "exp" if delta > 0 else "same"
            rows_html.append(
                f"<div class=row><div class='badge {cls}'>#{i + 1} {tag}</div>"
                f"<div class=thumbs><figure><figcaption>기존 고정폭 (→{old_r})</figcaption>"
                f'<img src="{b64(old_c, 260)}"></figure>'
                f"<figure><figcaption>신규 확장 (→{new_r})</figcaption>"
                f'<img src="{b64(new_c, 260)}"></figure></div></div>'
            )
        sections.append(
            f"<section><h2>{src.name} <small>({len(cards)} new 행)</small></h2>"
            f'<img class=ov src="{b64(ov)}">'
            f"<div class=rows>{''.join(rows_html)}</div></section>"
        )
    pct = (100 * expanded_rows / total_rows) if total_rows else 0
    head = (
        "<style>body{font:14px system-ui;margin:24px;background:#faf9f7;color:#222}"
        "section{margin:0 0 40px;padding:16px;background:#fff;border:1px solid #e5e3df;border-radius:8px}"
        "h2{margin:0 0 12px;font-size:16px}small{color:#888;font-weight:400}"
        ".ov{max-width:100%;border:1px solid #ccc}.rows{margin-top:16px;display:grid;gap:12px}"
        ".row{display:flex;gap:16px;align-items:center;padding:8px;background:#fafafa;border-radius:6px}"
        ".badge{min-width:120px;font-weight:600;font-size:13px}.badge.exp{color:#1560d6}.badge.same{color:#999}"
        ".thumbs{display:flex;gap:20px}figure{margin:0}figcaption{font-size:11px;color:#666;margin-bottom:3px}"
        "img{display:block;border:1px solid #ddd}.err{color:#c00}"
        ".lead{margin:0 0 24px;padding:14px 18px;background:#eef4ff;border-radius:8px;line-height:1.6}"
        "b.r{color:#d00}b.b{color:#1560d6}</style>"
    )
    lead = (
        f"<div class=lead><b>품목 크롭 우측 확장 검수</b> — 오버레이 <b class=r>빨강</b>=기존 고정폭 경계(392+4), "
        f"<b class=b>파랑</b>=신규 동적 확장 경계. 카드 왼쪽=기존 크롭, 오른쪽=확장 크롭.<br>"
        f"이미지 {len(paths)}장 · new 행 {total_rows}개 중 <b>{expanded_rows}개({pct:.0f}%) 확장</b> · "
        f"최대 확장폭 {max_delta}px. (모델·뱅크 불필요 — 경계 로직만.)</div>"
    )
    OUT.write_text(
        f"<!doctype html><meta charset=utf-8><title>crop 확장 검수</title>{head}{lead}{''.join(sections)}",
        encoding="utf-8",
    )
    print(f"wrote {OUT}")
    print(
        f"이미지 {len(paths)}장, new 행 {total_rows}개, 확장 {expanded_rows}개({pct:.0f}%), 최대 {max_delta}px"
    )


if __name__ == "__main__":
    main(sys.argv[1:] or [str(HERE.parent / "data/image_dataset")])
