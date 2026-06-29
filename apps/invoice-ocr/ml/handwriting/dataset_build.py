"""확정 매칭 → 통합 원본정리 + 라벨셋 빌드 (두 소스 통합, 경로 기반 로드).

소스 2개를 하나의 학습 세트로 통합한다:
  - 신규: data/image/Resized_*  ← confirmed_matches.json {파일명:invoice_id} (검수 확정)
  - 기존: OneDrive inv_NNN.jpg  ← reviewed_dates.csv (date+total_supply DB 유니크)
모든 라벨(GT)은 DB invoice_items 를 단일 출처로 사용.

산출:
  1) 정리: data/image_dataset/<date>_inv<id>.jpg (날짜형 통일) + manifest.json
  2) 라벨셋: rectify(form_quad_robust+deskew) → 고정 그리드 → select_items(연속블록 trust)
     → report/dataset_v2/<정식명>/<date>_inv<id>_<k>.png + dataset_v2_review.html
  3) 고아: data/image_dataset/_unmatched/ 에 원본명 보관(DB 커버리지 이전, 별도)

grid_v4(OneDrive {id}.jpg 하드코딩)는 건드리지 않고 경로 기반 동일 1차 파이프라인 적용.

usage:
  dataset_build.py                       # confirmed_matches.json + 기존 36장 통합
  dataset_build.py --new-only            # 신규 확정분만
  dataset_build.py --auto-only           # 신규 단일후보 자동확정분만(파이프라인 검증)
"""

import csv
import json
import re
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

HERE = Path(__file__).parent
ML = Path("/Users/gangsub/projects/sjmj-ai/apps/invoice-ocr/ml")
SP2 = ML / "report/sp2_spike"
sys.path.insert(0, str(SP2))
sys.path.insert(0, str(HERE))
from canon import ITEM_X, Y0, Y1, fit_phase, global_pitch, grid_rows  # noqa: E402
from grid_v4 import SRC, hline_ys, warp  # noqa: E402
from rectify import deskew_angle, form_quad_robust, rotate  # noqa: E402

# labelset(select_items/safe/b64)·photomatch(db_invoices/IMGDIR)는 DB+이미지디렉터리
# 의존을 끌어오므로 모듈 레벨이 아닌 사용하는 함수 본문에서 지연 import한다
# (load_bgr_path만 쓰는 임포터가 그 의존을 끌지 않도록 — T9-A 디커플).

ORG = ML / "data/image_dataset"
DSV2 = ML / "report/dataset_v2"


def faint_set():
    """흐림 회수 대상 cname 집합 — review_flags.json 'faint'. 이 전표는 set-aside 하지 않고
    grid_v4.faint_on 아래 대비향상 hline으로 처리(과노출 격자 복구). 비어 있으면 빈 집합.
    """
    fp = HERE / "review_flags.json"
    return set(json.load(open(fp)).get("faint", [])) if fp.exists() else set()


def load_bgr_path(path):
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def rect_and_lines_path(path):
    bgr = load_bgr_path(path)
    w = warp(bgr, form_quad_robust(bgr))
    w = rotate(w, deskew_angle(w))
    ys = [y for y in hline_ys(w) if Y0 - 40 <= y <= Y1 + 40]
    return w, sorted(ys)


def new_sources():
    """신규 data/image 확정분 → [(src_path, invoice_id, origin)]."""
    from photomatch import IMGDIR  # noqa: E402  (지연 import — T9-A)

    if "--auto-only" in sys.argv:
        dflt = json.load(open(HERE / "photo_match_default.json"))
        m = {fn: v["pick"] for fn, v in dflt.items() if v["auto"]}
    else:
        m = json.load(open(HERE / "confirmed_matches.json"))
    return [(IMGDIR / fn, int(i), "new") for fn, i in m.items()]


def old_sources(inv):
    """기존 OneDrive inv_NNN → date+total_supply DB 유니크 → [(src_path, invoice_id, origin)]."""
    idx = {}
    for v in inv.values():
        idx.setdefault((v["date"], v["total_supply"]), []).append(v["id"])
    out = []
    csv_path = ML / "results/reviewed_dates.csv"
    for r in csv.DictReader(open(csv_path)):
        if r["status"] != "unique":
            continue
        c = idx.get((r["extracted_date"], int(r["total_supply"])), [])
        src = SRC / f"{r['image_id']}.jpg"
        if len(c) == 1 and src.exists():
            out.append((src, c[0], "old"))
    return out


def build_sources(inv):
    src = new_sources()
    if not ("--new-only" in sys.argv or "--auto-only" in sys.argv):
        src += old_sources(inv)
    # invoice_id 중복 제거(날짜 disjoint라 정상은 없음; 안전망)
    seen, uniq = set(), []
    for s, iid, og in src:
        if iid not in seen:
            seen.add(iid)
            uniq.append((s, iid, og))
    return uniq


def stash_orphans():
    """DB 커버리지 이전 고아 사진 → _unmatched/ 원본명 보관."""
    from photomatch import IMGDIR, db_invoices  # noqa: E402  (지연 import — T9-A)

    inv_dates = {v["date"] for v in db_invoices().values()}
    dst = ORG / "_unmatched"
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in sorted(IMGDIR.iterdir()):
        m = re.search(r"(\d{8})", f.name)
        if m and f.suffix.lower() in (".jpg", ".jpeg"):
            d = f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}"
            if d not in inv_dates:
                shutil.copy2(f, dst / f.name)
                n += 1
    return n


def main():
    from labelset import b64, safe, select_items  # noqa: E402  (지연 import — T9-A)
    from photomatch import db_invoices  # noqa: E402  (지연 import — T9-A)

    inv = db_invoices()
    sources = build_sources(inv)
    n_new = sum(og == "new" for _, _, og in sources)
    n_old = sum(og == "old" for _, _, og in sources)
    print(f"통합 소스 {len(sources)}장 (신규 {n_new} + 기존 {n_old})\n")

    # 1) 정리 — 날짜형 통일 복사 + manifest
    ORG.mkdir(exist_ok=True)
    for f in ORG.glob("*.jpg"):
        f.unlink()
    manifest, canon_path = {}, {}
    for src, iid, og in sources:
        v = inv[iid]
        cname = f"{v['date']}_inv{iid:03d}.jpg"
        shutil.copy2(src, ORG / cname)
        canon_path[cname] = ORG / cname
        manifest[cname] = {
            "invoice_id": iid,
            "date": v["date"],
            "origin": og,
            "source": src.name,
            "recipient": v["recipient"],
            "items": [nm for _, nm, _ in v["items"]],
        }
    n_orphan = stash_orphans()
    (ORG / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    # 2) 라벨셋 — rectify → 고정 그리드 → select_items
    warps, perlines = {}, {}
    for cname, p in canon_path.items():
        w, ys = rect_and_lines_path(p)
        warps[cname], perlines[cname] = w, ys
    P = global_pitch(perlines)

    DSV2.mkdir(exist_ok=True)
    for d in DSV2.glob("*"):
        if d.is_dir():
            for f in d.glob("*"):
                f.unlink()

    # 2장 동시촬영(연속 영수증)은 병합워프가 13품목을 오정렬 crop으로 흘려 뱅크를 오염시킨다
    # → 정상 경로에서 제외하고 twoup.py(수동 박스 분할 수확)로 별도 처리.
    twoup_path = HERE / "twoup_split.json"
    TWOUP = set(json.load(open(twoup_path))) if twoup_path.exists() else set()

    cards = []
    trusted_n = labeled = 0
    skips = []
    print(f"P={P:.1f}")
    for cname in sorted(canon_path):
        if cname in TWOUP:
            continue  # 2장 케이스 → twoup.py 전담(병합워프 라벨노이즈 차단)
        iid = manifest[cname]["invoice_id"]
        names = [nm for _, nm, _ in inv[iid]["items"]]
        w = warps[cname]
        rows = grid_rows(fit_phase(perlines[cname], P), P)
        chosen, trusted = select_items(w, rows, len(names))
        trusted_n += trusted
        if not trusted:
            skips.append((cname, len(names), len(chosen)))
        x1, x2 = ITEM_X
        thumbs = []
        for k, (idx, a, b, fr) in enumerate(chosen):
            lbl = names[k] if k < len(names) else "(extra)"
            c = w[max(a - 4, 0) : b + 4, x1 - 4 : x2 + 4]
            if trusted and k < len(names):
                dd = DSV2 / safe(lbl)
                dd.mkdir(exist_ok=True)
                cv2.imwrite(str(dd / f"{cname[:-4]}_{k}.png"), c)
                labeled += 1
            t = cv2.resize(c, (150, max(40, int(c.shape[0] * 150 / c.shape[1]))))
            thumbs.append(
                f'<div class=t><img src="data:image/png;base64,{b64(t)}"><div class=l>{lbl} <small>{fr * 100:.0f}</small></div></div>'
            )
        cards.append(
            f"<div class=card><div class=hd>{cname} [{manifest[cname]['origin']}] — {'✅trusted' if trusted else '◐skip'} DB{len(names)}"
            f"<br><small>{' · '.join(names)}</small></div><div class=row>{''.join(thumbs)}</div></div>"
        )

    bylabel = {d.name: len(list(d.glob("*.png"))) for d in sorted(DSV2.glob("*")) if d.is_dir()}
    multi = {k: v for k, v in bylabel.items() if v >= 2}
    print(
        f"\ntrusted {trusted_n}/{len(sources)} · 라벨 crop {labeled} · 고유라벨 {len(bylabel)} · 2+표본 {len(multi)} · 고아 {n_orphan} 보관"
    )
    print(
        f"skip {len(skips)}장:",
        ", ".join(f"{c}(DB{d}/sel{s})" for c, d, s in skips[:12])
        + (" ..." if len(skips) > 12 else ""),
    )
    (ML / "report/dataset_v2_review.html").write_text(
        "<!doctype html><meta charset=utf-8><style>"
        "body{font:13px sans-serif;background:#eee;margin:0;padding:12px}"
        ".card{background:#fff;border-radius:8px;margin-bottom:10px;padding:8px;box-shadow:0 1px 3px #0002}"
        ".hd{font-weight:600;margin-bottom:6px}.row{display:flex;flex-wrap:wrap;gap:6px}"
        ".t{border:1px solid #ccc}.t img{display:block}.l{font-size:11px;text-align:center;color:#06c;padding:2px}"
        f"</style><h3>dataset_v2 — 통합 {len(sources)}장 · trusted {trusted_n} · crop {labeled} · 2+표본 {len(multi)}</h3>"
        + "".join(cards),
        encoding="utf-8",
    )
    print("정리 →", ORG, "\n라벨셋 →", DSV2, "\nreview →", ML / "report/dataset_v2_review.html")


def build_labelset_grouped():
    """교정 GT(grouping_corrections.json) → ok 전표의 new 박스를 DB명 폴더로 크롭.
    그룹핑·합계·db_skip 반영, 워프불량/제외(review_flags.json) 배제. 별도 dataset_grouped/.
    """
    from grid_v4 import faint_on
    from group import apply_corrections
    from grouping import (  # 지역 import(grouping→dataset_build 순환 회피)
        PAD,
        all_warps_and_pitch,
        propose,
    )
    from labelset import b64, safe  # noqa: E402  (지연 import — T9-A)
    from rows import band_features

    cnames, warps, manifest, P = all_warps_and_pitch()
    cpath = HERE / "grouping_corrections.json"
    corr = json.load(open(cpath)) if cpath.exists() else {}
    fpath = HERE / "review_flags.json"
    flags = json.load(open(fpath)) if fpath.exists() else {}
    setaside = set(flags.get("rewarp", [])) | set(flags.get("exclude", []))
    faint = faint_set()

    DSG = ML / "report/dataset_grouped"
    DSG.mkdir(parents=True, exist_ok=True)
    for d in DSG.glob("*"):
        if d.is_dir():
            for f in d.glob("*"):
                f.unlink()

    x1, x2 = ITEM_X
    cards = []
    trusted = labeled = nr = aside = 0
    print(f"P={P:.1f} · 교정 {len(corr)}전표 · set-aside {len(setaside)}")
    for cn in cnames:
        if cn in setaside:
            aside += 1
            continue
        names = manifest[cn]["items"]
        with faint_on(cn in faint):
            auto = propose(warps[cn], names, P)
            if cn in corr and len(corr[cn].get("types", [])) == len(auto.rows):
                bands = [r.band for r in auto.rows]
                _, _, stroke_rows = band_features(warps[cn], bands)
                p = apply_corrections(
                    auto,
                    corr[cn]["types"],
                    names,
                    stroke_rows,
                    pad=PAD,
                    db_skips=corr[cn].get("db_skips", []),
                )
            else:
                p = auto
        if p.status != "ok":
            nr += 1
            continue
        trusted += 1
        thumbs = []
        for r in p.rows:
            if r.rtype == "new" and r.box and r.db_name:
                a, b = r.box
                crop = warps[cn][a:b, x1 - 4 : x2 + 4]
                dd = DSG / safe(r.db_name)
                dd.mkdir(exist_ok=True)
                cv2.imwrite(str(dd / f"{cn[:-4]}_{r.db_idx}.png"), crop)
                labeled += 1
                t = cv2.resize(crop, (150, max(40, int(crop.shape[0] * 150 / crop.shape[1]))))
                thumbs.append(
                    f'<div class=t><img src="data:image/png;base64,{b64(t)}">'
                    f"<div class=l>{r.db_name}</div></div>"
                )
        cards.append(
            f"<div class=card><div class=hd>{cn} ✅ DB{p.dbn}"
            f"<br><small>{' · '.join(names)}</small></div>"
            f"<div class=row>{''.join(thumbs)}</div></div>"
        )

    bylabel = {d.name: len(list(d.glob("*.png"))) for d in sorted(DSG.glob("*")) if d.is_dir()}
    multi = {k: v for k, v in bylabel.items() if v >= 2}
    print(
        f"[grouped] trusted {trusted} · needs_review {nr} · set-aside {aside} · "
        f"crop {labeled} · 고유라벨 {len(bylabel)} · 2+표본 {len(multi)}"
    )
    rev = ML / "review/grouped_labelset_review.html"
    rev.write_text(
        "<!doctype html><meta charset=utf-8><style>"
        "body{font:13px sans-serif;background:#eee;margin:0;padding:12px}"
        ".card{background:#fff;border-radius:8px;margin-bottom:10px;padding:8px;box-shadow:0 1px 3px #0002}"
        ".hd{font-weight:600;margin-bottom:6px}.row{display:flex;flex-wrap:wrap;gap:6px}"
        ".t{border:1px solid #ccc}.t img{display:block}.l{font-size:11px;text-align:center;color:#06c;padding:2px}"
        f"</style><h3>그룹핑 라벨셋 — trusted {trusted} · crop {labeled} · 고유라벨 {len(bylabel)} · 2+표본 {len(multi)}</h3>"
        + "".join(cards),
        encoding="utf-8",
    )
    print("라벨셋 →", DSG, "\nreview →", rev)


if __name__ == "__main__":
    if "--grouped" in sys.argv:
        build_labelset_grouped()
    else:
        main()
