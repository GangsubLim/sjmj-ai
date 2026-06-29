"""작성자-특화 품목 인식기 — contrastive 메트릭 러닝 파인튜닝 (SP2).

목표: ddobokki/ko-trocr ViT 인코더를 베이스로, 작성자의 손글씨 crop을 DB 정식명 라벨로
군집시키는 임베딩을 학습한다. 운영 시나리오 = 신규 전표 crop을 과거(학습된) crop 뱅크에
few-shot retrieval. 따라서 평가도 '전표 단위 hold-out → 학습 뱅크에 retrieval'로 한다
(fewshot.py의 leave-one-invoice-out과 동일 정신, 단 train/val 누수 없음).

데이터: build_labelset_grouped와 동일 walk(label_inspect.build_rows) 280 crop
        → review/dataset_corrections.json 적용:
          drop/ditto 제외 → relabel(crop별 오답 교정) → merge(라벨 변형 통합, transitive).
정답 라벨 = 교정 후 DB 정식명. 축약(엔→엔진오일)은 그대로 정답(작성자 특화의 핵심).

학습: ViT embeddings+layer[0:FREEZE] 동결, 마지막 (12-FREEZE)층 + layernorm + 128d
      projection head만 SupCon(2-view)으로 미세조정. 강한 손글씨-안전 증강 + 전표분할 early stop.
      과적합 방지: 소수 파라미터만, 낮은 LR, val retrieval로 조기종료.

평가/게이트: 동일 val split에서 '동결 베이스(backbone mean-pool)' vs '파인튜닝(projection)'
            top-1/top-3 비교. 베이스라인 대비 상승이 본질. (역사적 기준 few-shot 47.3/58.7%)

usage:
  poc/bin/python item/train_contrastive.py            # 학습(증강·early stop)
  poc/bin/python item/train_contrastive.py --epochs 40 --smoke
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision.transforms import v2 as T

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from fewshot import square  # noqa: E402  (tight_crop+224 정사각 — 베이스라인과 동일 전처리)

ML = HERE.parents[2]
CORR = ML / "review/dataset_corrections.json"
CACHE = HERE / "clean_crops.npz"
RUNS = HERE / "runs"
PROD_CKPT = RUNS / "ft_prod.pt"  # 배포 모델(전체 데이터 학습)
BANK = RUNS / "bank.npz"  # 배포 뱅크(전체 crop projection 임베딩 + 라벨)
REPORT = ML / "review/finetune_report.json"
PROD_EPOCHS = 22  # 전체-데이터 production 고정 epoch(CV best 구간, no early-stop)

SEED = 20260626
IMG = 384
MEAN = STD = (0.5, 0.5, 0.5)  # ViT/TrOCR 기본 — proc와 동일
FREEZE = 10  # ViT 12층 중 앞 10층 동결, 마지막 2층만 학습

# 손글씨-안전 증강: 회전·이동·스케일·shear·밝기/대비·블러. 좌우/상하 반전 없음(글자 정체성 보존).
TRAIN_TF = T.Compose(
    [
        T.RandomAffine(degrees=6, translate=(0.04, 0.04), scale=(0.92, 1.08), shear=5, fill=1.0),
        T.RandomApply([T.GaussianBlur(3, (0.1, 1.5))], p=0.3),
        T.ColorJitter(0.2, 0.2),
        T.Resize((IMG, IMG), antialias=True),
        T.Normalize(MEAN, STD),
    ]
)
EVAL_TF = T.Compose([T.Resize((IMG, IMG), antialias=True), T.Normalize(MEAN, STD)])


# ---------------- 데이터 준비 (교정 적용) ----------------
def merge_resolver(merge):
    """transitive: 변형→정식 체인을 끝까지 따라간다(루프 방지)."""

    def canon(lbl):
        seen = set()
        while lbl in merge and lbl not in seen:
            seen.add(lbl)
            lbl = merge[lbl]
        return lbl

    return canon


def prepare(corr):
    """build_rows(동일 walk) → 교정 적용 → (square_rgb[uint8], label, invoice, key) 리스트. 캐시."""
    key = hashlib.md5(
        ("v2" + json.dumps(corr, sort_keys=True, ensure_ascii=False)).encode()
    ).hexdigest()[:10]
    if CACHE.exists():
        z = np.load(CACHE, allow_pickle=True)
        if str(z["key"]) == key and "keys" in z.files:
            print(f"clean 캐시 사용 ({CACHE.name})")
            return list(z["sq"]), list(z["lab"]), list(z["inv"]), list(z["keys"])
    from label_inspect import build_rows

    rows = build_rows()
    drop, ditto = set(corr.get("drop", [])), set(corr.get("ditto", []))
    relabel, canon = corr.get("relabel", {}), merge_resolver(corr.get("merge", {}))
    sq, lab, inv, keys = [], [], [], []
    for r in rows:
        k = r["key"]
        if k in drop or k in ditto:
            continue
        sq.append(square(cv2.cvtColor(r["std"], cv2.COLOR_BGR2RGB)))
        lab.append(canon(relabel.get(k, r["name"])))
        inv.append(r["cn"])
        keys.append(k)
    np.savez(
        CACHE,
        key=key,
        sq=np.array(sq),
        lab=np.array(lab, object),
        inv=np.array(inv, object),
        keys=np.array(keys, object),
    )
    print(f"clean {len(sq)} crop 캐시 저장 → {CACHE.name}")
    return sq, lab, inv, keys


def split_invoices(inv, labels, recurring, val_frac=0.25, rng=None):
    """전표 단위 train(bank)/val(query) 분할. 재현라벨마다 train에 ≥1전표 남겨 bank 커버리지 보장."""
    rng = rng or np.random.default_rng(SEED)
    invs = sorted(set(inv))
    rng.shuffle(invs)
    # 전표별 보유 재현라벨
    inv_labs = {
        iv: {labels[i] for i in range(len(inv)) if inv[i] == iv and labels[i] in recurring}
        for iv in invs
    }
    target = round(val_frac * len(invs))
    train_cov = {}  # 재현라벨 → train 전표 수
    for iv in invs:
        for L in inv_labs[iv]:
            train_cov[L] = train_cov.get(L, 0) + 1
    val = set()
    for iv in invs:
        if len(val) >= target:
            break
        # iv를 val로 빼도 모든 재현라벨이 train에 ≥1 남는가
        if all(train_cov[L] - 1 >= 1 for L in inv_labs[iv]):
            val.add(iv)
            for L in inv_labs[iv]:
                train_cov[L] -= 1
    train = [iv for iv in invs if iv not in val]
    return set(train), val


# ---------------- 모델 ----------------
class ItemEncoder(nn.Module):
    def __init__(self, vit, proj_dim=128):
        super().__init__()
        self.enc = vit
        self.head = nn.Sequential(nn.Linear(768, 256), nn.GELU(), nn.Linear(256, proj_dim))

    def forward(self, x):
        h = self.enc(pixel_values=x).last_hidden_state.mean(1)  # [B,768] backbone
        z = self.head(h)
        return F.normalize(z, dim=1), F.normalize(h, dim=1)  # (projection, backbone)


def build_model(device):
    from transformers import VisionEncoderDecoderModel

    # 기본 로드는 fp16 → fp32 head와 MPS matmul dtype 충돌. fp32로 통일(학습 안정성도 ↑).
    vit = VisionEncoderDecoderModel.from_pretrained(
        "ddobokki/ko-trocr", torch_dtype=torch.float32
    ).encoder
    for p in vit.parameters():
        p.requires_grad_(False)
    for i in range(FREEZE, len(vit.layers)):  # transformers 5.x: ViTModel.layers[i]
        for p in vit.layers[i].parameters():
            p.requires_grad_(True)
    for p in vit.layernorm.parameters():
        p.requires_grad_(True)
    return ItemEncoder(vit).to(device)


class SupConLoss(nn.Module):
    """Supervised Contrastive — 같은 라벨(+같은 이미지의 2번째 증강뷰)을 positive로 당긴다."""

    def __init__(self, temp=0.07):
        super().__init__()
        self.t = temp

    def forward(self, feats, labels):
        n = feats.shape[0]
        sim = (feats @ feats.T) / self.t
        sim = sim - sim.max(1, keepdim=True).values.detach()
        labels = labels.view(-1, 1)
        eye = torch.eye(n, device=feats.device)
        pos = (labels == labels.T).float() * (1 - eye)
        exp = torch.exp(sim) * (1 - eye)
        log_prob = sim - torch.log(exp.sum(1, keepdim=True) + 1e-12)
        ppos = pos.sum(1)
        loss = -(pos * log_prob).sum(1) / ppos.clamp(min=1)
        return loss[ppos > 0].mean()


# ---------------- 평가 (val→train retrieval) ----------------
@torch.no_grad()
def embed(model, tensors, eval_tf, device, bs=64):
    model.eval()
    Z, H = [], []
    for i in range(0, len(tensors), bs):
        x = torch.stack([eval_tf(t) for t in tensors[i : i + bs]]).to(device)
        z, h = model(x)
        Z.append(z.cpu().numpy())
        H.append(h.cpu().numpy())
    return np.concatenate(Z), np.concatenate(H)


def retrieval(emb_q, lab_q, inv_q, emb_b, lab_b, inv_b, recurring):
    """query(val) → bank(train=작성자 과거 crop) 개방 retrieval = 운영 메트릭(추론 시 DB는 비어
    있어 후보 narrowing 없음; 작성자 어휘 전체가 후보). 재현라벨·bank에 정답 존재 query만 채점.
    반환 dict: t1/t3/t5/n 카운트 + pairs[(top1정답?, top1유사도)] (신뢰도 게이팅용).
    """
    t1 = t3 = t5 = n = 0
    pairs = []
    for i in range(len(emb_q)):
        if lab_q[i] not in recurring:
            continue
        bk = [j for j in range(len(emb_b)) if inv_b[j] != inv_q[i]]
        if not any(lab_b[j] == lab_q[i] for j in bk):
            continue
        sims = emb_b[bk] @ emb_q[i]
        ranked = []
        for j in np.argsort(-sims):
            L = lab_b[bk[j]]
            if L not in ranked:
                ranked.append(L)
            if len(ranked) >= 5:
                break
        c1 = ranked[0] == lab_q[i]
        t1 += c1
        t3 += lab_q[i] in ranked[:3]
        t5 += lab_q[i] in ranked[:5]
        n += 1
        pairs.append((bool(c1), float(sims.max())))
    return {"t1": t1, "t3": t3, "t5": t5, "n": n, "pairs": pairs}


def conf_gate(pairs, target_p):
    """신뢰도 게이팅: top1 유사도 높은 순으로 자동채움, running precision ≥ target_p 인 최대 prefix.
    반환 (coverage=자동채움 비율, precision, tau=임계유사도). 나머지(1-coverage)는 사용자 검수/드롭다운.
    """
    if not pairs:
        return 0.0, 0.0, 1.0
    s = sorted(pairs, key=lambda x: -x[1])
    best_k = 0
    corr = 0
    for k, (c, _) in enumerate(s, 1):
        corr += c
        if corr / k >= target_p:
            best_k = k
    if not best_k:
        return 0.0, 0.0, 1.0
    prec = sum(c for c, _ in s[:best_k]) / best_k
    return best_k / len(s), prec, s[best_k - 1][1]


# ---------------- 학습 (단일 split) ----------------
def train_split(
    base, ids, lab_arr, inv_arr, recurring, tr, va, lab2id, args, device, ckpt=None, verbose=True
):
    """한 split 학습 → 카운트 반환. base=동결 backbone 베이스라인, best=projection 기준 best epoch.
    개방 retrieval top-1/3/5 + 신뢰도 게이팅용 pairs 수집(운영: 자동채움 + 사용자 검수 드롭다운).
    """
    model = build_model(device)
    qa, ba = [base[i] for i in va], [base[i] for i in tr]
    _, hbq = embed(model, qa, EVAL_TF, device)
    _, hbb = embed(model, ba, EVAL_TF, device)
    ob = retrieval(hbq, lab_arr[va], inv_arr[va], hbb, lab_arr[tr], inv_arr[tr], recurring)
    if verbose:
        bn = ob["n"]
        print(
            f"  베이스라인 backbone: top1 {ob['t1'] / max(bn, 1):.0%} top3 {ob['t3'] / max(bn, 1):.0%} "
            f"top5 {ob['t5'] / max(bn, 1):.0%} (n{bn})"
        )

    crit = SupConLoss()
    enc_p = [p for p in model.enc.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(
        [{"params": enc_p, "lr": 2e-5}, {"params": model.head.parameters(), "lr": 5e-4}],
        weight_decay=1e-4,
    )
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs)
    best = {"p": None, "bb": None, "ep": 0}
    bad = 0
    steps = max(1, len(tr) // args.batch)
    for ep in range(1, args.epochs + 1):
        model.train()
        perm = np.random.permutation(tr)
        tot = 0.0
        for s in range(steps):
            idx = perm[s * args.batch : (s + 1) * args.batch]
            if len(idx) < 2:
                continue
            b = base[idx]
            x = torch.cat(
                [torch.stack([TRAIN_TF(z) for z in b]), torch.stack([TRAIN_TF(z) for z in b])]
            ).to(device)
            y = torch.tensor(np.concatenate([ids[idx], ids[idx]]), device=device)
            z, _ = model(x)
            loss = crit(z, y)
            opt.zero_grad()
            loss.backward()
            opt.step()
            tot += loss.item()
        sched.step()

        zq, hq = embed(model, qa, EVAL_TF, device)
        zb, hb = embed(model, ba, EVAL_TF, device)
        pj = retrieval(
            zq, lab_arr[va], inv_arr[va], zb, lab_arr[tr], inv_arr[tr], recurring
        )  # projection
        bb = retrieval(
            hq, lab_arr[va], inv_arr[va], hb, lab_arr[tr], inv_arr[tr], recurring
        )  # backbone
        mk = ""
        if best["p"] is None or pj["t1"] > best["p"]["t1"]:  # projection top-1 기준 early-stop
            best = {"p": pj, "bb": bb, "ep": ep}
            bad = 0
            mk = " ★"
            if ckpt:
                torch.save({"model": model.state_dict(), "lab2id": lab2id}, ckpt)
        else:
            bad += 1
        if verbose:
            nv = pj["n"]
            print(
                f"  ep{ep:>2} loss {tot / steps:.3f} · proj {pj['t1'] / nv:.0%}/{pj['t3'] / nv:.0%}/{pj['t5'] / nv:.0%} "
                f"· bb {bb['t1'] / nv:.0%} (n{nv}){mk}"
            )
        if bad >= args.patience:
            if verbose:
                print(f"  early stop (patience {args.patience})")
            break
    return {"base": ob, "best": best}


def train_production(base, ids, lab_arr, inv_arr, keys, lab2id, args, device):
    """전체 clean crop 학습(고정 epoch·no early-stop) → 배포 모델 + 뱅크 export.
    뱅크 = 전체 crop의 projection 임베딩 + 라벨/전표/key. 추론은 신규 crop을 임베딩해 뱅크에서 retrieval.
    """
    model = build_model(device)
    crit = SupConLoss()
    enc_p = [p for p in model.enc.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(
        [{"params": enc_p, "lr": 2e-5}, {"params": model.head.parameters(), "lr": 5e-4}],
        weight_decay=1e-4,
    )
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs)
    allidx = np.arange(len(ids))
    steps = max(1, len(allidx) // args.batch)
    print(f"production 학습: 전체 {len(allidx)} crop · {args.epochs} epoch(고정)")
    for ep in range(1, args.epochs + 1):
        model.train()
        perm = np.random.permutation(allidx)
        tot = 0.0
        for s in range(steps):
            idx = perm[s * args.batch : (s + 1) * args.batch]
            if len(idx) < 2:
                continue
            b = base[idx]
            x = torch.cat(
                [torch.stack([TRAIN_TF(z) for z in b]), torch.stack([TRAIN_TF(z) for z in b])]
            ).to(device)
            y = torch.tensor(np.concatenate([ids[idx], ids[idx]]), device=device)
            z, _ = model(x)
            loss = crit(z, y)
            opt.zero_grad()
            loss.backward()
            opt.step()
            tot += loss.item()
        sched.step()
        if ep % 5 == 0 or ep == args.epochs:
            print(f"  ep{ep:>2} loss {tot / steps:.3f}")
    Z, _ = embed(model, [base[i] for i in allidx], EVAL_TF, device)  # 뱅크 = projection 임베딩
    torch.save({"model": model.state_dict(), "lab2id": lab2id}, PROD_CKPT)
    np.savez(
        BANK,
        emb=Z,
        lab=np.array(lab_arr, object),
        inv=np.array(inv_arr, object),
        keys=np.array(keys, object),
    )
    print(f"배포 모델 → {PROD_CKPT}\n배포 뱅크({len(Z)} crop) → {BANK}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--batch", type=int, default=24)
    ap.add_argument("--patience", type=int, default=12)
    ap.add_argument("--folds", type=int, default=1, help=">1 이면 전표 K-fold 교차검증(신뢰 수치)")
    ap.add_argument("--production", action="store_true", help="전체 데이터 학습 → 배포 모델+뱅크")
    ap.add_argument("--smoke", action="store_true", help="2 epoch 스모크")
    args = ap.parse_args()
    if args.smoke:
        args.epochs = 2
    elif args.production:
        args.epochs = PROD_EPOCHS

    torch.manual_seed(SEED)
    np.random.seed(SEED)
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    corr = json.load(open(CORR)) if CORR.exists() else {}
    print(
        f"교정: drop {len(corr.get('drop', []))} · ditto {len(corr.get('ditto', []))} · "
        f"relabel {len(corr.get('relabel', {}))} · merge {len(corr.get('merge', {}))}"
    )

    sq, lab, inv, keys = prepare(corr)
    lab_inv = {}
    for L, iv in zip(lab, inv):
        lab_inv.setdefault(L, set()).add(iv)
    recurring = {L for L, s in lab_inv.items() if len(s) >= 2}
    print(
        f"clean {len(sq)} crop · 라벨 {len(set(lab))} · 재현(≥2전표) {len(recurring)} · 전표 {len(set(inv))}"
    )

    base = T.functional.to_dtype(
        torch.stack([T.functional.to_image(s) for s in sq]), torch.float32, scale=True
    )  # [N,3,224,224] in [0,1]
    lab_arr, inv_arr = np.array(lab, object), np.array(inv, object)
    lab2id = {L: k for k, L in enumerate(sorted(set(lab)))}
    ids = np.array([lab2id[L] for L in lab])
    ntr = sum(p.numel() for p in build_model("cpu").parameters() if p.requires_grad)
    print(f"학습 파라미터 {ntr / 1e6:.1f}M (마지막 {12 - FREEZE}층+head)\n")
    RUNS.mkdir(exist_ok=True)

    if args.production:
        train_production(base, ids, lab_arr, inv_arr, keys, lab2id, args, device)
        return

    # 분할 구성: 단일 split(빠름) 또는 전표 K-fold(각 전표가 정확히 1회 val → micro-average)
    if args.folds <= 1:
        train_inv, val_inv = split_invoices(inv, lab, recurring)
        splits = [(set(val_inv), "single")]
    else:
        invs = sorted(set(inv))
        np.random.default_rng(SEED).shuffle(invs)
        splits = [
            (set(g.tolist()), f"fold{k + 1}")
            for k, g in enumerate(np.array_split(invs, args.folds))
        ]

    results = []
    for val_set, tag in splits:
        tr = [i for i in range(len(sq)) if inv[i] not in val_set]
        va = [i for i in range(len(sq)) if inv[i] in val_set]
        print(f"[{tag}] val {len(va)}crop/{len(val_set)}전표 · train {len(tr)}crop")
        ck = (RUNS / "ft_best.pt") if tag in ("single", "fold1") else None
        results.append(
            train_split(
                base,
                ids,
                lab_arr,
                inv_arr,
                recurring,
                tr,
                va,
                lab2id,
                args,
                device,
                ckpt=ck,
                verbose=True,
            )
        )

    # micro-average (각 crop 1회 채점). n은 baseline·best 동일(skip은 라벨/전표만 의존).
    def rate(sel, key):
        N = sum(r[sel]["n"] if sel == "base" else r["best"][sel]["n"] for r in results)
        c = sum((r[sel] if sel == "base" else r["best"][sel])[key] for r in results)
        return c / max(N, 1), N

    BN = sum(r["base"]["n"] for r in results)
    b1, _ = rate("base", "t1")
    b3, _ = rate("base", "t3")
    b5, _ = rate("base", "t5")
    p1, _ = rate("p", "t1")
    p3, _ = rate("p", "t3")
    p5, _ = rate("p", "t5")
    bb1, _ = rate("bb", "t1")
    # 신뢰도 게이팅(운영 자동채움): 전 fold projection pairs 합산
    pairs = [pr for r in results for pr in r["best"]["p"]["pairs"]]
    cov95, prec95, _ = conf_gate(pairs, 0.95)
    cov90, prec90, _ = conf_gate(pairs, 0.90)
    mode = "단일 split" if args.folds <= 1 else f"{args.folds}-fold 교차검증"

    print(f"\n=== 결과 ({mode} · 채점 {BN} crop · 운영=개방 retrieval, DB 사전지식 없음) ===")
    print(f"베이스라인 동결 backbone : top-1 {b1:.1%} · top-3 {b3:.1%} · top-5 {b5:.1%}")
    print(
        f"파인튜닝 projection     : top-1 {p1:.1%} · top-3 {p3:.1%} · top-5 {p5:.1%}  (backbone top-1 {bb1:.1%})"
    )
    print(f"상승 Δtop-1 {p1 - b1:+.1%} · Δtop-3 {p3 - b3:+.1%} · Δtop-5 {p5 - b5:+.1%}")
    print("운영 시나리오(자동채움 + 사용자 검수 드롭다운):")
    print(
        f"  · 신뢰도 게이팅 자동채움 — 정밀도 ≥95%: 커버리지 {cov95:.0%} (실측 {prec95:.0%}) · ≥90%: {cov90:.0%}"
    )
    print(
        f"  · 나머지 행은 top-{3 if p3 >= p5 else 5} 드롭다운으로 사용자 선택(상위 후보 적중 top-3 {p3:.0%}/top-5 {p5:.0%})"
    )
    REPORT.write_text(
        json.dumps(
            {
                "mode": mode,
                "scored": BN,
                "scenario": "input-assist + HITL (추론 시 DB 비어있음 → 개방 retrieval)",
                "baseline_frozen_backbone": {
                    "top1": round(b1, 4),
                    "top3": round(b3, 4),
                    "top5": round(b5, 4),
                },
                "finetuned_projection": {
                    "top1": round(p1, 4),
                    "top3": round(p3, 4),
                    "top5": round(p5, 4),
                },
                "autofill_conf_gate": {
                    "prec95_coverage": round(cov95, 4),
                    "prec90_coverage": round(cov90, 4),
                },
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print("리포트 →", REPORT, "\n체크포인트 →", RUNS / "ft_best.pt")


if __name__ == "__main__":
    main()
