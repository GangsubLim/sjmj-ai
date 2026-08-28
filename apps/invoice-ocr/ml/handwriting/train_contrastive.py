"""작성자-특화 품목 인식기 — contrastive 메트릭 러닝 파인튜닝 (SP2).

목표: ddobokki/ko-trocr ViT 인코더를 베이스로, 작성자의 손글씨 crop을 DB 정식명 라벨로
군집시키는 임베딩을 학습한다. 운영 시나리오 = 신규 전표 crop을 과거(학습된) crop 뱅크에
few-shot retrieval. 따라서 평가도 '전표 단위 hold-out → 학습 뱅크에 retrieval'로 한다
(fewshot.py의 leave-one-invoice-out과 동일 정신, 단 train/val 누수 없음).

데이터(#133부터): tools.train_input이 구성한다 — 부트스트랩 크롭 npz(clean_crops.npz) +
        큐레이션 학습쌍 크롭(ocr_crops/job-N/row-K.png)을 curve 모드가 한 벌 TrainSet으로
        병합한다. 정답 라벨 = 큐레이션 쌍의 canonical_label(검수 확정) 또는 부트스트랩 lab.
        축약(엔→엔진오일)은 그대로 정답(작성자 특화의 핵심).

학습: ViT embeddings+layer[0:FREEZE] 동결, 마지막 (12-FREEZE)층 + layernorm + 128d
      projection head만 SupCon(2-view)으로 미세조정. 강한 손글씨-안전 증강 + 전표분할 early stop.
      과적합 방지: 소수 파라미터만, 낮은 LR, val retrieval로 조기종료.

평가/게이트: 동일 val split에서 '동결 베이스(backbone mean-pool)' vs '파인튜닝(projection)'
            top-1/top-3 비교. 베이스라인 대비 상승이 본질. (역사적 기준 few-shot 47.3/58.7%)

usage:
  uv run python -m handwriting.train_contrastive curve \
      --bootstrap-npz report/sp2_spike/item/clean_crops.npz \
      --pairs-jsonl results/bank_update/pairs.jsonl \
      --reviewed-json results/bank_update/reviewed_jobs.json \
      --crops-root "$SJMJ_DATA_DIR/ocr_crops" --folds 4 --baseline-ckpt runs/ft_prod.pt
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision.transforms import v2 as T

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
ML = HERE.parents[2]
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


# ---------------- 데이터 분할 ----------------
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
    """ViT 백본 + projection head 품목 인코더."""

    def __init__(self, vit, proj_dim=128):
        """ViT 백본에 projection head를 붙인다."""
        super().__init__()
        self.enc = vit
        self.head = nn.Sequential(nn.Linear(768, 256), nn.GELU(), nn.Linear(256, proj_dim))

    def forward(self, x):
        """(projection, backbone) 정규화 임베딩 쌍을 반환한다."""
        h = self.enc(pixel_values=x).last_hidden_state.mean(1)  # [B,768] backbone
        z = self.head(h)
        return F.normalize(z, dim=1), F.normalize(h, dim=1)  # (projection, backbone)


def build_model(device):
    """ko-trocr ViT 인코더를 부분 동결해 ItemEncoder로 빌드한다."""
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
        """temperature를 받아 손실을 초기화한다."""
        super().__init__()
        self.t = temp

    def forward(self, feats, labels):
        """feats·labels로 supervised contrastive 손실을 계산한다."""
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
    """텐서들을 배치로 인코딩해 (projection, backbone) 임베딩을 반환한다."""
    model.eval()
    Z, H = [], []
    for i in range(0, len(tensors), bs):
        x = torch.stack([eval_tf(t) for t in tensors[i : i + bs]]).to(device)
        z, h = model(x)
        Z.append(z.cpu().numpy())
        H.append(h.cpu().numpy())
    return np.concatenate(Z), np.concatenate(H)


def retrieval(emb_q, lab_q, inv_q, emb_b, lab_b, inv_b, recurring):
    """query(val) → bank(train) 개방 retrieval로 운영 메트릭을 계산한다.

    추론 시 DB는 비어 있어 후보 narrowing 없음(작성자 어휘 전체가 후보). 재현라벨·bank에 정답
    존재 query만 채점. 반환 dict: t1/t3/t5/n 카운트 + pairs[(top1정답?, top1유사도)]
    (신뢰도 게이팅용).
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
    """신뢰도 게이팅: top1 유사도 순 자동채움, running precision ≥ target_p 인 최대 prefix.

    반환 (coverage=자동채움 비율, precision, tau=임계유사도). 나머지(1-coverage)는 사용자
    검수/드롭다운.
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
    """한 split을 학습해 카운트를 반환한다.

    base=동결 backbone 베이스라인, best=projection 기준 best epoch. 개방 retrieval top-1/3/5 +
    신뢰도 게이팅용 pairs 수집(운영: 자동채움 + 사용자 검수 드롭다운).
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
    """전체 clean crop 학습(고정 epoch·no early-stop)으로 배포 모델 + 뱅크를 export한다.

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


# ---------------- 학습곡선 (#133 AC2) ----------------
def load_curve_data(args):
    """부트스트랩 npz + 큐레이션 크롭을 한 벌 TrainSet으로 모은다.

    Args:
        args: curve 모드 파싱 결과(bootstrap_npz·pairs_jsonl·reviewed_json·crops_root).

    Returns:
        (merged TrainSet, 부트스트랩 key 목록, 큐레이션 key 목록).
    """
    from tools import train_input as ti

    pairs = [
        json.loads(ln)
        for ln in Path(args.pairs_jsonl).read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    reviewed = set(json.loads(Path(args.reviewed_json).read_text(encoding="utf-8")))
    curated = ti.load_curated(ti.select_curated(pairs, reviewed), args.crops_root)
    sets = [curated]
    boot_keys: list[str] = []
    if args.bootstrap_npz:
        boot = ti.load_bootstrap(args.bootstrap_npz)
        boot_keys = list(boot.keys)
        sets.insert(0, boot)
    merged = ti.merge(*sets)
    print(
        f"학습 입력: 부트스트랩 {len(boot_keys)} + 큐레이션 {len(curated.keys)} "
        f"= {len(merged.keys)} crop · 라벨 {len(set(merged.lab))} · "
        f"큐레이션 잡 {len(set(curated.inv))}"
    )
    return merged, boot_keys, list(curated.keys)


def train_fixed(base, ids, tr_idx, args, device):
    """고정 epoch로 학습만 한다 — 채점·조기종료·체크포인트 저장 없음.

    train_split과 갈라지는 유일한 이유가 hold-out 오염이다. train_split은 매 epoch val을
    채점해 best를 고르므로 그 val은 더 이상 hold-out이 아니다(선택에 쓰인 순간 학습 신호다).
    학습곡선은 epoch를 사전 고정하고 종료 후 1회만 채점한다.

    루프 본문은 train_production과 같은 모양이지만 공유하지 않는다 — train_production은
    ADR 0001의 production 재학습 경로이고 이번 범위는 그 경로 무접촉이다(AC3 소관).
    """
    model = build_model(device)
    crit = SupConLoss()
    enc_p = [p for p in model.enc.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(
        [{"params": enc_p, "lr": 2e-5}, {"params": model.head.parameters(), "lr": 5e-4}],
        weight_decay=1e-4,
    )
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs)
    idx_all = np.array(tr_idx)
    steps = max(1, len(idx_all) // args.batch)
    for _ep in range(1, args.epochs + 1):
        model.train()
        perm = np.random.permutation(idx_all)
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
        sched.step()
    return model


def load_ckpt_model(path, device):
    """동결 기준선 — 지정 체크포인트를 적재한다(infer_photo.load_model_from과 같은 계약).

    infer_photo를 import하지 않는다 — 그쪽이 이 모듈을 import하므로 순환이 된다.
    """
    model = build_model(device)
    model.load_state_dict(torch.load(path, map_location=device)["model"])
    model.eval()
    return model


def score_pair(model, base, tr_idx, qa_idx, cell, ds, device):
    """한 모델로 train 뱅크·hold-out 쿼리를 임베딩해 셀의 고정 cohort로 채점한다."""
    from tools import train_input as ti

    emb_b, _ = embed(model, [base[i] for i in tr_idx], EVAL_TF, device)
    emb_q, _ = embed(model, [base[i] for i in qa_idx], EVAL_TF, device)
    return ti.score_cell(
        emb_q=emb_q,
        q_keys=[ds.keys[i] for i in qa_idx],
        q_labs=[ds.lab[i] for i in qa_idx],
        q_invs=[ds.inv[i] for i in qa_idx],
        emb_b=emb_b,
        b_keys=[ds.keys[i] for i in tr_idx],
        b_labs=[ds.lab[i] for i in tr_idx],
        b_invs=[ds.inv[i] for i in tr_idx],
        cell=cell,
    )


def _rate(num, den):
    """카운트 쌍을 백분율 문자열로 만든다(분모 0이면 —)."""
    return f"{100 * num / den:.1f}%" if den else "—"


def _acc(agg, key, rec):
    """N별 micro-average 누적기 — 카운트를 그대로 더한다."""
    empty = {"t1": 0, "t5": 0, "n_cohort": 0, "n_covered": 0, "pairs": 0, "jobs": 0}
    slot = agg.setdefault(key, empty)
    for k in ("t1", "t5", "n_cohort", "n_covered"):
        slot[k] += rec[k]
    return slot


def run_curve(args, device):
    """학습곡선 격자를 돌려 (재학습, 동결) 쌍의 cohort 고정 채점표를 stdout에 낸다."""
    from tools import train_input as ti

    ds, boot_keys, cur_keys = load_curve_data(args)
    idx_of = {k: i for i, k in enumerate(ds.keys)}
    lab_of = dict(zip(ds.keys, ds.lab, strict=True))
    inv_of_key = dict(zip(ds.keys, ds.inv, strict=True))
    lab2id = {L: k for k, L in enumerate(sorted(set(ds.lab)))}
    ids = np.array([lab2id[L] for L in ds.lab])
    base = T.functional.to_dtype(
        torch.stack([T.functional.to_image(s) for s in ds.sq]), torch.float32, scale=True
    )
    n_grid = args.curated_n if args.curated_n else [0, 25, 50, len(cur_keys)]
    cells = ti.plan_cells(
        curated_keys=cur_keys,
        curated_invs=[inv_of_key[k] for k in cur_keys],
        curated_labs=[lab_of[k] for k in cur_keys],
        bootstrap_keys=boot_keys,
        bootstrap_labs=[lab_of[k] for k in boot_keys],
        n_grid=n_grid,
        k=args.folds,
        seed=ti.SEED,
    )
    print(f"\n격자 {len(cells)}셀 (N {n_grid} × fold {args.folds}) · epoch {args.epochs} 고정\n")
    print(
        "| N요청 | fold | holdout | train쌍 | train잡 | cohort "
        "| ft t1 | ft t5 | ft cov | bl t1 | bl t5 |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    agg_ft: dict[int, dict] = {}
    agg_bl: dict[int, dict] = {}
    # 기준선은 셀마다 재학습되지 않으므로 루프 밖에서 1회만 적재한다 — 루프 안에서 매번
    # 적재하면 build_model 초기화가 전역 torch RNG를 소비해 재학습 셀의 초기화까지
    # --baseline-ckpt 지정 여부로 바뀐다(N3, E6).
    bl_model = load_ckpt_model(args.baseline_ckpt, device) if args.baseline_ckpt else None
    for cell in cells:
        if args.holdout_fold is not None and cell.fold != args.holdout_fold:
            continue
        tr_idx = [idx_of[k] for k in cell.train_keys]
        qa_idx = [idx_of[k] for k in cell.holdout_keys]
        # 부트스트랩을 생략(--bootstrap-npz 없음)하면 N=0 셀의 train 뱅크가 통째로 빈다.
        # 그대로 두면 embed의 np.concatenate([])가 불투명한 ValueError로 죽으므로 사유를
        # 남기고 건너뛴다(집계에서도 빠진다).
        if not tr_idx:
            print(
                f"⏭️ 셀 건너뜀 — N={cell.n_requested} fold={cell.fold}는 train 뱅크 0건"
                " (--bootstrap-npz 없이 N=0)"
            )
            continue
        if cell.n_requested and not cell.n_actual_pairs:
            print(f"⚠️ 격자점 붕괴 — N={cell.n_requested} fold={cell.fold}는 쌍 0건으로 실행됨")
        # 셀별로 재시드하되 seed는 fold에만 의존한다 — 같은 fold의 모든 N이 동일 초기화·동일
        # 셔플에서 출발해야(common random numbers) 곡선의 셀 간 차이가 N 차이만 남는다. N을
        # seed에 섞으면 격자점마다 난수가 갈려 그 분산이 N 효과에 섞인다(H3, E5). fold가 seed에
        # 남으므로 --holdout-fold로 재개해도 같은 셀은 같은 수치를 낸다.
        torch.manual_seed(SEED + cell.fold * 1000)
        np.random.seed(SEED + cell.fold * 1000)
        ft_model = train_fixed(base, ids, tr_idx, args, device)
        ft = score_pair(ft_model, base, tr_idx, qa_idx, cell, ds, device)
        slot = _acc(agg_ft, cell.n_requested, ft)
        slot["pairs"] += cell.n_actual_pairs
        slot["jobs"] += cell.n_actual_jobs
        bl = score_pair(bl_model, base, tr_idx, qa_idx, cell, ds, device) if bl_model else None
        if bl:
            _acc(agg_bl, cell.n_requested, bl)
        print(
            f"| {cell.n_requested} | {cell.fold} | {len(cell.holdout_keys)} "
            f"| {cell.n_actual_pairs} | {cell.n_actual_jobs} | {len(cell.eval_cohort)} "
            f"| {_rate(ft['t1'], ft['n_cohort'])} | {_rate(ft['t5'], ft['n_cohort'])} "
            f"| {_rate(ft['n_covered'], ft['n_cohort'])} "
            f"| {_rate(bl['t1'], bl['n_cohort']) if bl else '—'} "
            f"| {_rate(bl['t5'], bl['n_cohort']) if bl else '—'} |"
        )
    print("\n=== fold micro-average (x축 = 실측 train 쌍·잡 수의 fold 평균) ===")
    print(
        "| N요청 | train쌍(평균) | train잡(평균) | cohort | cov "
        "| 재학습 t1 | 재학습 t5 | 재학습 t1_covered "
        "| 동결 t1 | 동결 t5 | 동결 t1_covered | Δt1 |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    n_folds = 1 if args.holdout_fold is not None else args.folds
    for n in sorted(agg_ft):
        f, b = agg_ft[n], agg_bl.get(n)
        # n_covered는 라벨·제외집합만으로 정해져 재학습·동결 모델이 항상 같은 값을 낸다 —
        # coverage는 1열로 충분하다(M3, E8).
        d = (
            f"{100 * (f['t1'] / f['n_cohort'] - b['t1'] / b['n_cohort']):+.1f}%p"
            if b and f["n_cohort"] and b["n_cohort"]
            else "—"
        )
        print(
            f"| {n} | {f['pairs'] / n_folds:.1f} | {f['jobs'] / n_folds:.1f} | {f['n_cohort']} "
            f"| {_rate(f['n_covered'], f['n_cohort'])} "
            f"| {_rate(f['t1'], f['n_cohort'])} | {_rate(f['t5'], f['n_cohort'])} "
            f"| {_rate(f['t1'], f['n_covered'])} "
            f"| {_rate(b['t1'], b['n_cohort']) if b else '—'} "
            f"| {_rate(b['t5'], b['n_cohort']) if b else '—'} "
            f"| {_rate(b['t1'], b['n_covered']) if b else '—'} | {d} |"
        )


def main():
    """CLI 인자를 파싱해 학습곡선(curve)을 실행한다."""
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "mode",
        nargs="?",
        choices=["curve"],
        default=None,
        help="curve: 학습곡선 실측(#133 AC2). 생략 시 구 CV/production 경로 — AC3까지 비활성",
    )
    ap.add_argument(
        "--epochs", type=int, default=None, help="curve: 셀별 고정 epoch(기본 PROD_EPOCHS)"
    )
    ap.add_argument("--batch", type=int, default=24)
    ap.add_argument("--folds", type=int, default=1, help="curve: 큐레이션 잡을 K개 fold로 분할")
    ap.add_argument("--production", action="store_true", help="전체 데이터 학습 → 배포 모델+뱅크")
    ap.add_argument("--smoke", action="store_true", help="2 epoch 스모크")
    ap.add_argument("--bootstrap-npz", type=Path, help="부트스트랩 크롭 npz(clean_crops.npz)")
    ap.add_argument("--pairs-jsonl", type=Path, help="bank_update export-pairs의 pairs.jsonl")
    ap.add_argument("--reviewed-json", type=Path, help="같은 반출의 reviewed_jobs.json")
    ap.add_argument("--crops-root", type=Path, help="큐레이션 크롭 루트(SJMJ_DATA_DIR/ocr_crops)")
    ap.add_argument(
        "--curated-n",
        type=int,
        nargs="*",
        default=None,
        help="학습곡선 격자의 큐레이션 쌍 수(기본 0 25 50 + 전량)",
    )
    ap.add_argument("--holdout-fold", type=int, help="지정 fold 하나만 실행")
    ap.add_argument("--baseline-ckpt", type=Path, help="동결 기준선 체크포인트(ft_prod.pt)")
    args = ap.parse_args()

    if args.mode != "curve":
        sys.exit(
            "이 진입점의 학습 입력은 #133 AC1에서 train_input으로 옮겨졌고 curve 모드만 배선됐다.\n"
            "구 경로(--production · K-fold)는 레포에 없는 SP2 라벨셋 walk에 묶여 이미 실행이"
            " 불가했고, 재배선은 AC3 소관이다(모델·뱅크 원자 활성화 설계 포함).\n"
            "사용: python -m handwriting.train_contrastive curve --pairs-jsonl … "
            "--reviewed-json … --crops-root … --folds 4"
        )
    if args.folds < 2:
        sys.exit("curve 모드는 --folds 2 이상이 필요하다(큐레이션 잡을 K개 fold로 나눈다).")
    if args.holdout_fold is not None and not (1 <= args.holdout_fold <= args.folds):
        sys.exit(f"--holdout-fold은 1..{args.folds} 범위여야 한다(받은 값: {args.holdout_fold}).")
    for name in ("pairs_jsonl", "reviewed_json", "crops_root"):
        if getattr(args, name) is None:
            sys.exit(f"curve 모드 필수 인자 누락: --{name.replace('_', '-')}")
    args.epochs = 2 if args.smoke else (args.epochs or PROD_EPOCHS)

    torch.manual_seed(SEED)
    np.random.seed(SEED)
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"device {device} · epoch {args.epochs} 고정 · 실험계획 seed는 train_input.SEED")
    run_curve(args, device)


if __name__ == "__main__":
    main()
