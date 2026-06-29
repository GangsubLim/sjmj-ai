"""few-shot leave-one-invoice-out — 작성자-특화 품목 인식 가설 검증.

가설: 단일 작성자 손글씨 품목 crop을 off-the-shelf 비전 인코더로 임베딩하면,
다른 전표의 같은 품목 exemplar에 최근접해 DB 정식명을 retrieval할 수 있다.

평가: 각 crop을 query로, 같은 전표 crop은 bank에서 제외(leave-one-invoice-out).
재현 어휘(2개 이상 '서로 다른 전표'에 등장한 라벨)만 채점 — 그래야 bank에 정답 후보 존재.
지표: top-1 / top-3 정확도 + 고유품명 커버리지 + 전표당 미보유(사람개입) 행 수.

인코더: facebook/dinov2-small (CLS 임베딩). crop은 정사각 패딩 후 224.
"""

import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
import torch
from transformers import AutoImageProcessor, AutoModel, TrOCRProcessor, VisionEncoderDecoderModel

MODEL = sys.argv[1] if len(sys.argv) > 1 else "facebook/dinov2-small"
DS = (
    Path(sys.argv[2])
    if len(sys.argv) > 2
    else Path("/Users/gangsub/projects/sjmj-ai/apps/invoice-ocr/ml/report/dataset")
)


def tight_crop(bgr, margin=8):
    """손글씨 획 bbox로 타이트 크롭 — 격자선·여백 제거해 글씨가 프레임을 채우게."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.int16)
    blur = cv2.GaussianBlur(gray, (0, 0), 11).astype(np.int16)
    stroke = (gray - blur) < -28
    ys, xs = np.where(stroke)
    if len(xs) < 20:
        return bgr
    x0, x1 = max(xs.min() - margin, 0), min(xs.max() + margin, bgr.shape[1])
    y0, y1 = max(ys.min() - margin, 0), min(ys.max() + margin, bgr.shape[0])
    return bgr[y0:y1, x0:x1]


def square(img, size=224):
    img = tight_crop(img)
    h, w = img.shape[:2]
    s = max(h, w)
    pad = np.full((s, s, 3), 255, np.uint8)
    pad[(s - h) // 2 : (s - h) // 2 + h, (s - w) // 2 : (s - w) // 2 + w] = img
    return cv2.resize(pad, (size, size))


def load_crops():
    items = []  # (label, invoice, path)
    for d in sorted(DS.glob("*")):
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.png")):
            inv = f.stem.rsplit("_", 1)[0]
            items.append((d.name, inv, f))
    return items


def embed(items, proc, model, device, is_trocr):
    embs = []
    for _, _, f in items:
        img = cv2.cvtColor(cv2.imread(str(f)), cv2.COLOR_BGR2RGB)
        x = (
            proc(images=square(img), return_tensors="pt").to(device)
            if not is_trocr
            else proc(images=square(img), return_tensors="pt").pixel_values.to(device)
        )
        with torch.no_grad():
            if is_trocr:
                hs = model.encoder(x).last_hidden_state
                v = hs.mean(1).cpu().numpy()[0]  # 패치 평균
            else:
                v = model(**x).last_hidden_state[:, 0].cpu().numpy()[0]  # CLS
        embs.append(v / (np.linalg.norm(v) + 1e-8))
    return np.array(embs)


def main():
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    is_trocr = "trocr" in MODEL.lower()
    print(f"device={device}  loading {MODEL} ...", flush=True)
    if is_trocr:
        proc = TrOCRProcessor.from_pretrained(MODEL)
        model = VisionEncoderDecoderModel.from_pretrained(MODEL).to(device).eval()
    else:
        proc = AutoImageProcessor.from_pretrained(MODEL)
        model = AutoModel.from_pretrained(MODEL).to(device).eval()
    items = load_crops()
    labels = [x[0] for x in items]
    invs = [x[1] for x in items]
    E = embed(items, proc, model, device, is_trocr)
    print(f"crops={len(items)}  고유라벨={len(set(labels))}")

    # 라벨이 등장한 '서로 다른 전표' 수
    lab_invs = defaultdict(set)
    for lab, inv, _ in items:
        lab_invs[lab].add(inv)
    recurring = {l for l, s in lab_invs.items() if len(s) >= 2}

    top1 = top3 = evaln = 0
    miss_by_inv = defaultdict(int)
    eval_labels = [
        labels[i]
        for i in range(len(items))
        if labels[i] in recurring
        and any(labels[j] == labels[i] and invs[j] != invs[i] for j in range(len(items)))
    ]
    from collections import Counter

    cnt = Counter(eval_labels)
    base_major = max(cnt.values()) / len(eval_labels) if eval_labels else 0
    for i in range(len(items)):
        lab, inv = labels[i], invs[i]
        bank = [j for j in range(len(items)) if invs[j] != inv]
        if lab not in recurring:
            # bank에 같은 라벨이 (다른 전표에) 있는지로 커버리지/개입 집계
            if not any(labels[j] == lab for j in bank):
                miss_by_inv[inv] += 1
            continue
        sims = E[bank] @ E[i]
        order = np.argsort(-sims)
        ranked = []
        for j in order:
            lj = labels[bank[j]]
            if lj not in ranked:
                ranked.append(lj)
            if len(ranked) >= 3:
                break
        top1 += ranked[0] == lab
        top3 += lab in ranked[:3]
        evaln += 1

    cov = len(recurring) / len(set(labels))
    n_inv = len(set(invs))
    print(f"\n=== few-shot leave-one-invoice-out ({MODEL}) ===")
    print(f"재현 라벨(≥2 전표): {len(recurring)} / 고유 {len(set(labels))}  (커버리지 {cov:.1%})")
    print(f"채점 crop(재현): {evaln}")
    print(
        f"top-1: {top1}/{evaln} = {top1 / evaln:.1%}  (다수라벨 베이스라인 {base_major:.1%})"
        if evaln
        else "top-1: n/a"
    )
    print(f"top-3: {top3}/{evaln} = {top3 / evaln:.1%}" if evaln else "top-3: n/a")
    print(f"전표당 미보유(신규=사람개입) 행 평균: {sum(miss_by_inv.values()) / n_inv:.2f}")


if __name__ == "__main__":
    main()
