"""품목·금액 결합 그룹핑 오케스트레이터 — rectify→금액척추 행검출→이중신호 proposal.

임계(ITEM_MIN/AMT_MIN/PAD/ON_THRESH)는 교정 GT(grouping_corrections.json)로 튜닝하는
설정값이다. group.py는 임계 무지(인자로만 받음).
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from canon import global_pitch  # noqa: E402
from dataset_build import ORG, faint_set, load_bgr_path  # noqa: E402
from grid_v4 import FaintOn, warp  # noqa: E402
from group import build_proposal  # noqa: E402
from rectify import deskew_angle, form_quad_robust, rotate  # noqa: E402
from rows import band_features, detect_grid_rows  # noqa: E402

FAINT = faint_set()  # 흐림 회수 대상(review_flags 'faint') — 이 전표만 대비향상 hline

ITEM_MIN = 0.04  # 품목칸 손글씨 ink 최소(채워진 셀=new ↔ 빈/연속 셀) — 74장 스윕 최적
AMT_MIN = 0.045  # 금액칸 손글씨 ink 최소(데이터 행 ↔ 빈행, trim 절단 기준) — 74장 스윕 최적
PAD = 3


def rectify_warp(cname):
    """전표를 정합·워프한 뒤 deskew 회전까지 적용해 반환한다."""
    bgr = load_bgr_path(ORG / cname)
    w0 = warp(bgr, form_quad_robust(bgr))
    return rotate(w0, deskew_angle(w0))


def propose(warp_img, db_names, P):
    """워프 이미지에서 행밴드·ink 특징을 뽑아 결합 proposal을 만든다."""
    bands = detect_grid_rows(warp_img, P)
    item_inks, amt_inks, stroke_rows = band_features(warp_img, bands)
    return build_proposal(
        bands,
        item_inks,
        amt_inks,
        stroke_rows,
        db_names,
        item_min=ITEM_MIN,
        amt_min=AMT_MIN,
        pad=PAD,
    )


def all_warps_and_pitch():
    """74장 워프 + 전역 피치를 계산한다(cname -> warp, P, manifest)."""
    from rows import stroke_profile_col  # noqa

    with open(ORG / "manifest.json") as f:
        manifest = json.load(f)
    cnames = sorted(manifest)
    warps, ys_all = {}, {}
    for cn in cnames:
        with FaintOn(cn in FAINT):
            w = rectify_warp(cn)
            warps[cn] = w
            # 피치 추정용 금액칸 행선 근사(detect 전이라 임시로 hline 대신 amount run 사용 안 함)
            from canon import Y0 as _Y0
            from canon import Y1 as _Y1
            from grid_v4 import hline_ys

            ys_all[cn] = [y for y in hline_ys(w) if _Y0 - 40 <= y <= _Y1 + 40]
    P = global_pitch(ys_all)
    return cnames, warps, manifest, P


def main():
    """전체 전표에 그룹핑을 돌려 행수·블록수·상태를 출력한다."""
    cnames, warps, manifest, P = all_warps_and_pitch()
    ok = 0
    print(f"P={P:.1f} ITEM_MIN={ITEM_MIN} AMT_MIN={AMT_MIN}\n")
    print(f"{'cname':<26}{'rows':>5}{'blk':>5}{'DBn':>5}  status")
    for cn in cnames:
        names = manifest[cn]["items"]
        with FaintOn(cn in FAINT):
            p = propose(warps[cn], names, P)
        ok += p.status == "ok"
        ndata = sum(1 for r in p.rows if r.rtype != "empty")
        print(f"{cn:<26}{ndata:>5}{p.n_blocks:>5}{len(names):>5}  {p.status}")
    print(f"\nstatus==ok : {ok}/{len(cnames)}")


if __name__ == "__main__":
    main()
