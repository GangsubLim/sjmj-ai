"""신규 사진 1장 추론 — 운영 경로 그대로(warp→그룹핑→crop→파인튜닝 임베딩→뱅크 retrieval).

infer_demo.py는 뱅크 내부 crop만 채점(leave-one-invoice-out). 이 스크립트는 뱅크에 없는
'진짜 신규 사진'을 추론 시점과 동일 공정으로 처리한다:
  사진 → form_quad 워프+deskew → φ그리드 행 → 이중신호 분류(new/cont/empty)
       → new 행 품목 crop → ft_prod 임베딩(projection) → bank.npz retrieval top-5
       + 같은 행의 금액칸(공급대가)을 Qwen3-VL-8B(MLX)로 숫자 전사.

품목명은 작성자-특화 retrieval(뱅크 어휘 한정), 금액은 손글씨 VLM(Qwen3-VL)로 푼다.
금액 인식기 선택 근거: SP1 stock PP-OCRv5는 손글씨 금액 ~10%(단일 병목)이라 폐기,
findings 채택안 Qwen3-VL-8B(공급가 열 end-to-end recall 84.8%)를 그대로 쓴다(bench.py 동일 호출).
DB GT가 없으므로 정답 비교는 사람이 한다. 금액 합도 표시해 총액과 대조 가능.

usage:
  poc/bin/python item/infer_photo.py "/path/to/photo.jpg" ["/path/B.jpg" ...]
"""

import base64
import re
import sys
from pathlib import Path

import cv2
import numpy as np
import torch
from torchvision.transforms import v2 as T

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from canon import global_pitch  # noqa: E402
from dataset_build import load_bgr_path  # noqa: E402
from fewshot import square  # noqa: E402
from grid_v4 import AMOUNT_X, DATA_Y, hline_ys, warp  # noqa: E402
from group import build_proposal  # noqa: E402
from grouping import AMT_MIN, ITEM_MIN, PAD  # noqa: E402
from rectify import deskew_angle, form_quad_robust, rotate  # noqa: E402
from rows import ITEM_X, band_features, detect_grid_rows  # noqa: E402
from train_contrastive import EVAL_TF, build_model  # noqa: E402

ML = HERE.parents[2]
BANK = HERE / "runs/bank.npz"
PROD = HERE / "runs/ft_prod.pt"
OUT = ML / "review/infer_photo.html"
Y0, Y1 = DATA_Y
TOPK = 5
COLOR = {
    "new": (0, 170, 0),
    "cont": (220, 130, 0),
    "empty": (190, 190, 190),
    "total": (0, 140, 255),
}


def b64(rgb_or_bgr, w=170, is_bgr=False):
    img = rgb_or_bgr if is_bgr else cv2.cvtColor(rgb_or_bgr, cv2.COLOR_RGB2BGR)
    img = cv2.resize(img, (w, max(28, int(img.shape[0] * w / img.shape[1]))))
    return base64.b64encode(cv2.imencode(".png", img)[1]).decode()


def load_model_from(path, device):
    """품목 인코더 적재 — path 명시 버전. 데모는 load_model_from(PROD, device)로 호출."""
    model = build_model(device)
    ck = torch.load(path, map_location=device)
    model.load_state_dict(ck["model"])
    model.eval()
    return model


AMT_PROMPT = (
    "이 이미지는 손으로 쓴 거래명세서의 금액칸 한 칸입니다. "
    "연한 파란색은 인쇄된 격자선이고 진한 글씨가 손으로 쓴 금액입니다. "
    "그 금액 숫자만 콤마 없이 정수로 한 줄로 답하세요. 끝의 대시(—)·빈칸은 무시. "
    "숫자가 없으면 0. 예: 364"
)


def load_ocr():
    """Qwen3-VL-8B(MLX) — 손글씨 금액 인식기(findings 채택, 공급가 열 84.8%).
    SP1 stock PP-OCRv5(~10%·인쇄체용)가 아니라 손글씨 벤치 우승 모델.
    """
    from mlx_vlm import load

    model, proc = load("mlx-community/Qwen3-VL-8B-Instruct-4bit")
    return model, proc, model.config


def read_amount(qwen, cell_bgr, tmp_dir, idx):
    """금액칸 BGR → (정수|None, 원문). Qwen3-VL per-cell 전사(bench.py와 동일 호출).
    천원곱 미적용(전표 내 액면 합으로 검증). MLX generate는 파일경로 입력 — 칸마다
    '고유' 임시 PNG를 써야 한다(같은 경로 재사용 시 MLX-VLM이 degenerate 출력 '!!!').
    """
    from mlx_vlm import generate
    from mlx_vlm.prompt_utils import apply_chat_template

    model, proc, cfg = qwen
    png = tmp_dir / f"amt_{idx}.png"
    cv2.imwrite(str(png), cell_bgr)
    fp = apply_chat_template(proc, cfg, AMT_PROMPT, num_images=1)
    out = generate(model, proc, fp, [str(png)], max_tokens=32, temperature=0.0, verbose=False)
    txt = out if isinstance(out, str) else getattr(out, "text", str(out))
    digits = "".join(re.findall(r"\d+", txt))
    return (int(digits) if digits else None), txt.strip()


@torch.no_grad()
def embed_crops(model, crops_bgr, device):
    """crop(BGR) 리스트 → projection 임베딩(뱅크와 동일 전처리)."""
    embs = []
    for c in crops_bgr:
        sq = square(cv2.cvtColor(c, cv2.COLOR_BGR2RGB))  # uint8 RGB 224
        t = T.functional.to_dtype(T.functional.to_image(sq), torch.float32, scale=True)
        x = EVAL_TF(t).unsqueeze(0).to(device)
        z, _ = model(x)  # projection
        embs.append(z.cpu().numpy()[0])
    return np.array(embs)


def topk(sims, lab, k):
    out, seen = [], set()
    for j in np.argsort(-sims):
        L = lab[j]
        if L in seen:
            continue
        seen.add(L)
        out.append((L, float(sims[j])))
        if len(out) >= k:
            break
    return out


def extract_rows_for_job(w, model, qwen, tmp_dir, counter, device):
    """워프된 양식 w → 추론 산출. process_one(데모)과 infer_job(운영)이 공유하는 단일 경로.

    행검출 → new 행 품목 crop → ft 임베딩(queries) → 같은 행 금액칸 Qwen3-VL 전사.
    반환: (news, crops, queries, amounts, prop, ys, P, bands).
      · 앞 4개(news/crops/queries/amounts) = infer_job result_json 입력
      · 뒤 4개(prop/ys/P/bands) = process_one 데모 HTML 오버레이/요약 컨텍스트
    HTML 조립은 process_one에만 남긴다(추론은 여기서 한 번만 한다 — DRY).
    """
    ys = [y for y in hline_ys(w) if Y0 - 40 <= y <= Y1 + 40]
    P = global_pitch({"x": ys})

    # φ그리드 행 + 이중신호 분류 (DB 없음 → db_names=[])
    bands = detect_grid_rows(w, P)
    item_inks, amt_inks, stroke_rows = band_features(w, bands)
    prop = build_proposal(
        bands, item_inks, amt_inks, stroke_rows, [], item_min=ITEM_MIN, amt_min=AMT_MIN, pad=PAD
    )
    news = [r for r in prop.rows if r.rtype == "new" and r.box]

    # new 행 품목 crop → 임베딩 → 뱅크 retrieval 쿼리
    x1, x2 = ITEM_X
    ax0, ax1 = AMOUNT_X
    crops = [w[r.box[0] : r.box[1], x1 - 4 : x2 + 4] for r in news]
    queries = embed_crops(model, crops, device) if crops else np.zeros((0, 0))

    # 같은 행 금액칸 → Qwen3-VL 전사 (칸마다 고유 idx로 임시파일 분리)
    amounts = [
        read_amount(qwen, w[r.band[0] : r.band[1], ax0:ax1], tmp_dir, next(counter)) for r in news
    ]
    return news, crops, queries, amounts, prop, ys, P, bands


def process_one(src, model, E, lab, qwen, tmp_dir, counter, device):
    """사진 1장 → section_html. 품목 retrieval + 금액칸 OCR. 운영 경로 그대로."""
    # 1) 워프 + deskew
    bgr = load_bgr_path(src)
    q = form_quad_robust(bgr)
    w = rotate(warp(bgr, q), deskew_angle(warp(bgr, q)))

    # 2) 추론(행검출~crop~retrieval임베딩~금액 OCR) — infer_job와 공유하는 단일 경로
    news, crops, Q, amounts, prop, ys, P, bands = extract_rows_for_job(
        w, model, qwen, tmp_dir, counter, device
    )

    # 3) 오버레이(행/타입 색칠) + 카드
    x1, x2 = ITEM_X
    ax0, ax1 = AMOUNT_X
    ov = w.copy()
    for r in prop.rows:
        a, b = r.band
        col = COLOR[r.rtype]
        cv2.rectangle(ov, (x1, a), (x2, b), col, 2 if r.rtype in ("new", "total") else 1)
        cv2.rectangle(ov, (ax0, a), (ax1, b), col, 1)
        if r.rtype == "new" and r.box:
            cv2.rectangle(ov, (x1 - 4, r.box[0]), (x2 + 4, r.box[1]), (0, 0, 255), 1)

    rows_html, preds_dump = [], []
    for i, (r, c) in enumerate(zip(news, crops)):
        preds = topk(E @ Q[i], lab, TOPK)
        preds_dump.append([p[0] for p in preds])
        amt, raw = amounts[i]
        chips = "".join(f"<span class=chip>{nm} <small>{s:.2f}</small></span>" for nm, s in preds)
        amt_html = (
            f"<span class=amt>{amt:,}</span>"
            if amt is not None
            else '<span class="amt none">—</span>'
        )
        rows_html.append(
            f'<div class=row><img src="data:image/png;base64,{b64(c, is_bgr=True)}">'
            f"<div class=info><div class=hd>품목 #{i + 1} {amt_html} "
            f'<small>금액칸 OCR "{raw}" · item_ink {r.item_ink:.3f}</small></div>'
            f"<div class=preds>{chips}</div></div></div>"
        )

    vals = [a for a, _ in amounts if a is not None]
    total = sum(vals)
    section = (
        f"<section class=card><h3>{Path(src).name}</h3>"
        f"<div class=sub>검출 품목 {len(news)}행 · 행선 {len(ys)} · P={P:.1f} · "
        f"밴드 {len(bands)}(new {len(news)} · cont {sum(r.rtype == 'cont' for r in prop.rows)} · "
        f"empty {sum(r.rtype == 'empty' for r in prop.rows)}) · "
        f"금액 합 <b>{total:,}</b>({len(vals)}행)</div>"
        f"<div class=wrap><div class=ov>"
        f'<img src="data:image/png;base64,{b64(ov, is_bgr=True, w=460)}"></div>'
        f"<div class=list>{''.join(rows_html)}</div></div></section>"
    )
    print(
        f"\n■ {Path(src).name} · 행선 {len(ys)} · P={P:.1f} · 품목 {len(news)}행 · 금액합 {total:,}"
    )
    for i, preds in enumerate(preds_dump):
        amt, raw = amounts[i]
        print(
            f"  #{i + 1} [금액 {amt if amt is not None else '—'} / OCR'{raw}']: "
            + " | ".join(preds)
        )
    return section


def main():
    srcs = sys.argv[1:]
    miss = [s for s in srcs if not Path(s).exists()]
    if not srcs or miss:
        sys.exit(f"사진 경로 필요/없음: {miss or '(없음)'}")
    if not BANK.exists() or not PROD.exists():
        sys.exit("배포 모델/뱅크 없음 — train_contrastive.py --production 먼저")

    import itertools
    import tempfile

    # 품목 인코더는 CPU로 고정 — PyTorch-MPS와 MLX(Qwen Metal)를 한 프로세스에서 같이 쓰면
    # transformers ViT를 MPS에 상주+forward한 뒤 mlx generate가 '!!!' degenerate로 깨진다(실측).
    # crop 소수라 CPU forward ~1.6s로 충분. 금액 OCR Qwen은 Metal 전용으로 둔다.
    device = "cpu"
    model = load_model_from(PROD, device)
    qwen = load_ocr()
    z = np.load(BANK, allow_pickle=True)
    E, lab = z["emb"], list(z["lab"])

    tmp_dir = Path(tempfile.mkdtemp())
    counter = itertools.count()
    sections = [process_one(s, model, E, lab, qwen, tmp_dir, counter, device) for s in srcs]

    OUT.write_text(
        f"""<!doctype html><meta charset=utf-8><title>신규 사진 추론</title>
<style>
body{{font:13px/1.5 -apple-system,sans-serif;background:#eee;margin:0;padding:14px}}
h2{{margin:0 0 4px}} .lead{{color:#444;margin-bottom:14px}}
.card{{background:#fff;border-radius:10px;margin-bottom:16px;padding:12px 16px;box-shadow:0 1px 3px #0002}}
.card h3{{margin:0 0 2px}} .sub{{color:#888;margin-bottom:10px}}
.wrap{{display:flex;gap:16px;align-items:flex-start}}
.ov img{{max-height:760px;border:1px solid #ccc;border-radius:6px}}
.list{{flex:1}}
.row{{display:flex;gap:12px;align-items:center;background:#fafafa;border:1px solid #eee;border-radius:8px;padding:8px 12px;margin-bottom:8px}}
.row img{{border:1px solid #ddd;border-radius:4px}}
.info{{flex:1}} .hd{{margin-bottom:5px}} .hd small{{color:#999;font-weight:400}}
.preds{{display:flex;flex-wrap:wrap;gap:5px}}
.chip{{border:1px solid #ddd;border-radius:14px;padding:2px 9px;background:#fff}}
.chip:first-child{{background:#dcfce7;border-color:#16a34a;font-weight:600}}
.chip small{{color:#999}}
.amt{{background:#1e293b;color:#fff;padding:1px 8px;border-radius:5px;font-weight:600;font-variant-numeric:tabular-nums}}
.amt.none{{background:#cbd5e1;color:#475569}}
</style>
<h2>신규 사진 추론 — 품목 retrieval + 금액 OCR (운영 경로: 워프→그룹핑→crop→인식)</h2>
<div class=lead>사진 {len(srcs)}장 · 뱅크 {len(lab)} crop · 품목=초록 chip top-1(작성자 어휘 retrieval) ·
검정 배지=금액칸 Qwen3-VL-8B 전사(손글씨 VLM, 천원곱 미적용 액면). DB GT 없음 → 정답은 사람이 crop과 대조 검증.
빨강 박스=ink-snap 품목 crop 영역.</div>
{"".join(sections)}""",
        encoding="utf-8",
    )
    print("\n데모 HTML →", OUT)


if __name__ == "__main__":
    main()
