"""품목 인코더 재학습의 학습 입력 구성·실험계획·채점 계층(#133 AC1·AC2).

부트스트랩 크롭 npz(SP2 잔재)와 큐레이션 학습쌍 크롭(ocr_crops/job-N/row-K.png)을 한 벌의
TrainSet으로 모으고, 잡 단위 hold-out 격자를 torch 없이 계획·채점한다. 모집단 규칙(ADR 0004
검수 게이트)과 제외 축·top-k 규칙은 tools.bank_update의 순수함수를 그대로 호출한다 —
재구현하면 뱅크 갱신과 학습 입력이 서로 다른 모집단을 보게 된다.

코어 규약 준수: 모듈 레벨은 stdlib + bank_update(stdlib 전용)까지. numpy/cv2/fewshot(torch)은
함수 본문 지연 import라 paddle-free venv에서도 import가 성공하고 CI가 이 모듈을 테스트한다.
"""

import random
import re
from dataclasses import dataclass
from pathlib import Path

from tools.bank_update import inv_of, partition_crop_ref, partition_valid, select_desired

CROP_SIZE = 224
# 실험계획 재현 seed — 학습 seed(train_contrastive.SEED)와 별개 축이다.
SEED = 20260828
# 부트스트랩 npz의 필수 배열. 실파일에는 prepare()가 남긴 캐시 `key`가 하나 더 있으므로
# 상등이 아니라 부분집합으로 검증한다(2026-08-28 실측: files=['key','sq','lab','inv','keys']).
BOOTSTRAP_ARRAYS = ("sq", "lab", "inv", "keys")


@dataclass(frozen=True)
class TrainSet:
    """학습 입력 한 벌 — 다섯 열의 길이가 항상 같다.

    sq는 224² RGB uint8 배열의 튜플이고, origin은 항목별 출처(bootstrap|curated)다.
    출처를 집합 단위가 아니라 항목 단위로 두는 이유는 merge 이후에도 어느 항목이
    부트스트랩인지 남아야 실험계획이 '부트스트랩은 항상 train' 규칙을 세울 수 있기 때문이다.
    """

    sq: tuple
    lab: tuple[str, ...]
    inv: tuple[str, ...]
    keys: tuple[str, ...]
    origin: tuple[str, ...]


def load_bootstrap(npz_path) -> TrainSet:
    """부트스트랩 크롭 npz를 적재한다 — 배열 누락·크롭 규격 불일치는 즉시 실패.

    `allow_pickle=True`가 필요한 이유는 `lab`/`inv`/`keys`가 object dtype(가변 길이 문자열)
    이기 때문이다(`bank_update.load_bank`와 동일 패턴). 입력은 1st-party 산출물(SP2 스파이크가
    만든 npz) 전제이며 임의 외부 파일을 신뢰 경계 없이 적재한다.
    """
    import numpy as np

    with np.load(Path(npz_path), allow_pickle=True) as z:
        missing = [k for k in BOOTSTRAP_ARRAYS if k not in z.files]
        if missing:
            raise ValueError(f"부트스트랩 npz 배열 누락 {missing} — 있는 배열: {sorted(z.files)}")
        sq = z["sq"]
        if sq.ndim != 4 or tuple(sq.shape[1:]) != (CROP_SIZE, CROP_SIZE, 3):
            raise ValueError(
                f"크롭 규격 불일치 {sq.shape} — (N,{CROP_SIZE},{CROP_SIZE},3)이어야 한다"
            )
        lab, inv, keys = z["lab"].tolist(), z["inv"].tolist(), z["keys"].tolist()
        if not len(sq) == len(lab) == len(inv) == len(keys):
            raise ValueError(
                f"열 길이 불일치: sq{len(sq)}/lab{len(lab)}/inv{len(inv)}/keys{len(keys)}"
            )
        sq = tuple(sq)
    return TrainSet(
        sq=sq,
        lab=tuple(lab),
        inv=tuple(inv),
        keys=tuple(keys),
        origin=("bootstrap",) * len(keys),
    )


def select_curated(pairs: list[dict], reviewed_job_ids: set[int]) -> list[dict]:
    """학습 대상 큐레이션 쌍을 고른다 — 게이트 순서는 bank_update._desired_pairs와 동일."""
    crop_ref_ok, _ = partition_crop_ref(select_desired(pairs, reviewed_job_ids))
    valid, _ = partition_valid(crop_ref_ok)
    return valid


def load_curated(pairs: list[dict], crops_root, *, square_fn=None) -> TrainSet:
    """큐레이션 크롭 PNG를 적재한다 — 누락은 전량 목록과 함께 즉시 실패.

    square_fn 기본값은 운영 임베딩과 같은 handwriting.fewshot.square다. 그 모듈은 모듈 레벨
    torch 의존이라 주입 슬롯을 둬야 CI(torch 없음)가 이 함수를 테스트할 수 있다
    (bank_update의 embed_fn 주입과 같은 관용구).
    """
    import cv2
    import numpy as np

    if square_fn is None:
        from handwriting.fewshot import square as square_fn

    root = Path(crops_root)
    missing = sorted({p["crop_ref"] for p in pairs if not (root / f"{p['crop_ref']}.png").exists()})
    if missing:
        raise FileNotFoundError(f"크롭 PNG 없음 {len(missing)}건: {missing}")

    sq, lab, inv, keys = [], [], [], []
    for p in pairs:
        ref = p["crop_ref"]
        img = cv2.imread(str(root / f"{ref}.png"))
        if img is None:
            raise RuntimeError(f"크롭 이미지를 읽을 수 없습니다: {ref}")
        sq_i = square_fn(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        if getattr(sq_i, "shape", None) != (CROP_SIZE, CROP_SIZE, 3) or sq_i.dtype != np.uint8:
            raise ValueError(f"square_fn 산출 규격 불일치 {ref}: {getattr(sq_i, 'shape', None)}")
        sq.append(sq_i)
        lab.append(p["canonical_label"])
        inv.append(inv_of(ref))
        keys.append(ref)
    return TrainSet(
        sq=tuple(sq),
        lab=tuple(lab),
        inv=tuple(inv),
        keys=tuple(keys),
        origin=("curated",) * len(keys),
    )


def merge(*sets: TrainSet) -> TrainSet:
    """TrainSet들을 이어 붙인다 — key 중복은 거부(같은 크롭이 두 번 학습되는 사고 차단)."""
    seen: set[str] = set()
    for s in sets:
        dup_within = {k for k in s.keys if s.keys.count(k) > 1}
        if dup_within:
            raise ValueError(f"TrainSet 내부 key 중복 {sorted(dup_within)}")
        dup = seen & set(s.keys)
        if dup:
            raise ValueError(f"key 중복 {sorted(dup)} — 같은 크롭이 두 벌 들어왔다")
        seen |= set(s.keys)
    return TrainSet(
        sq=tuple(x for s in sets for x in s.sq),
        lab=tuple(x for s in sets for x in s.lab),
        inv=tuple(x for s in sets for x in s.inv),
        keys=tuple(x for s in sets for x in s.keys),
        origin=tuple(x for s in sets for x in s.origin),
    )


# 큐레이션 전표 식별자 — bank_update.inv_of의 산출 형식. 부트스트랩 inv
# ('2025-08-18_inv011.jpg')는 매칭되지 않으므로 fold 축 오염을 여기서 잡는다.
JOB_INV_RE = re.compile(r"^job-\d+$")


@dataclass(frozen=True)
class Cell:
    """학습곡선 격자의 한 칸 — (요청 N, fold) 하나에 대응하는 완결된 실험 정의.

    n_actual_pairs·n_actual_jobs는 큐레이션만 센다(부트스트랩은 모든 셀에서 동일 상수라
    x축이 되지 못한다). eval_cohort는 fold별로 고정돼 N이 달라도 같은 쿼리 집합을 채점한다 —
    작은 N에서 peer가 사라진 쿼리는 분모에서 빠지는 대신 miss로 잡힌다.
    """

    n_requested: int
    fold: int
    train_keys: tuple[str, ...]
    holdout_keys: tuple[str, ...]
    n_actual_pairs: int
    n_actual_jobs: int
    eval_cohort: tuple[str, ...]


def job_folds(curated_invs, k: int, seed: int) -> list[set[str]]:
    """큐레이션 잡을 K개 fold로 나눈다(잡 단위·seed 재현).

    부트스트랩 전표는 fold에 넣지 않는다 — 그 크롭은 모든 셀에서 train 고정이므로
    hold-out 축이 아니다. 섞여 들어오면 즉시 실패시킨다.

    Raises:
        ValueError: 큐레이션 형식이 아닌 inv가 섞였거나 k가 1 미만·잡 수 초과일 때.
    """
    bad = sorted({iv for iv in curated_invs if not JOB_INV_RE.fullmatch(iv)})
    if bad:
        raise ValueError(f"큐레이션 전표 형식이 아님 {bad} — 부트스트랩 inv는 fold 축이 아니다")
    jobs = sorted(set(curated_invs))
    if k < 1 or k > len(jobs):
        raise ValueError(f"fold 수 {k}가 잡 수 {len(jobs)} 범위를 벗어남")
    random.Random(seed).shuffle(jobs)
    return [set(jobs[i::k]) for i in range(k)]


def limit_curated(curated_keys, curated_invs, n: int, seed: int) -> set[str]:
    """잡 단위로 셔플해 누적 쌍 수가 n을 넘기 직전까지 포함한다(잡 경계 절단).

    한도를 넘는 잡에서 continue가 아니라 break하는 이유는 중첩성이다 — prefix로 자르면
    작은 N의 포함 집합이 큰 N의 부분집합이 되어 곡선의 x축이 '같은 데이터 + 추가분'이 된다.
    건너뛰기(greedy packing)는 N마다 다른 잡 조합을 만들어 곡선을 데이터 교체와 뒤섞는다.
    """
    by_job: dict[str, list[str]] = {}
    for key, iv in zip(curated_keys, curated_invs, strict=True):
        by_job.setdefault(iv, []).append(key)
    jobs = sorted(by_job)
    random.Random(seed).shuffle(jobs)
    out: set[str] = set()
    total = 0
    for iv in jobs:
        if total + len(by_job[iv]) > n:
            break
        out |= set(by_job[iv])
        total += len(by_job[iv])
    return out


def plan_cells(
    *,
    curated_keys,
    curated_invs,
    curated_labs,
    bootstrap_keys,
    bootstrap_labs,
    n_grid,
    k: int,
    seed: int,
) -> list[Cell]:
    """(요청 N × fold) 격자를 계획한다 — torch 없이 도는 순수 실험계획.

    부트스트랩은 모든 셀의 train에 전량 들어가고, 큐레이션은 hold-out fold를 뺀 나머지에
    limit_curated를 적용한다. eval_cohort는 'N=전량 train 뱅크'에 정답 peer가 있는 hold-out
    쿼리로 fold마다 한 번만 정해져 N 전체에서 공유된다.
    """
    lab_of = dict(zip(curated_keys, curated_labs, strict=True))
    inv_by_key = dict(zip(curated_keys, curated_invs, strict=True))
    cells: list[Cell] = []
    for fold_no, fold_jobs in enumerate(job_folds(curated_invs, k, seed), start=1):
        holdout = tuple(key for key in curated_keys if inv_by_key[key] in fold_jobs)
        pool_keys = [key for key in curated_keys if inv_by_key[key] not in fold_jobs]
        pool_invs = [inv_by_key[key] for key in pool_keys]
        full_labels = set(bootstrap_labs) | {lab_of[key] for key in pool_keys}
        cohort = tuple(q for q in holdout if lab_of[q] in full_labels)
        for n in n_grid:
            keep = limit_curated(pool_keys, pool_invs, n, seed)
            kept = [key for key in pool_keys if key in keep]
            cells.append(
                Cell(
                    n_requested=n,
                    fold=fold_no,
                    train_keys=tuple(bootstrap_keys) + tuple(kept),
                    holdout_keys=holdout,
                    n_actual_pairs=len(kept),
                    n_actual_jobs=len({inv_by_key[key] for key in kept}),
                    eval_cohort=cohort,
                )
            )
    return cells


def score_cell(
    *,
    emb_q,
    q_keys,
    q_labs,
    q_invs,
    emb_b,
    b_keys,
    b_labs,
    b_invs,
    cell: Cell,
) -> dict:
    """셀의 고정 cohort로 hold-out retrieval을 채점한다 — 분모는 언제나 cohort 크기다.

    bank_update.retrieval 계열과 분모 규칙이 다르다. retrieval()은 정답 peer가 없는 쿼리를
    아예 건너뛰지만(분모에서 제외) 학습곡선은 그러면 안 된다 — 작은 N에서 peer가 사라진
    쿼리가 조용히 분모에서 빠지면 데이터가 줄수록 정확도가 오르는 착시가 생긴다. 여기서는
    peer 부재를 miss로 계산하고, 커버리지 효과를 분리해 보고 싶은 소비자를 위해 n_covered를
    함께 돌려준다.

    제외 축·top-k 규칙은 bank_update를 그대로 부른다(운영 채점과 같은 기준).

    Args:
        emb_q: hold-out 쿼리 임베딩 [Nq, D](L2 정규화 전제).
        q_keys/q_labs/q_invs: 쿼리 열. cell.eval_cohort의 key가 q_keys에 있어야 한다.
        emb_b: train 뱅크 임베딩 [Nb, D].
        b_keys/b_labs/b_invs: 뱅크 열.
        cell: 채점할 셀(eval_cohort가 분모).

    Returns:
        카운트 dict — ``t1``·``t5``(적중 수) · ``n_cohort``(분모) · ``n_covered``(제외 후
        정답 peer가 있던 쿼리 수).

    Raises:
        KeyError: cohort key가 쿼리 열에 없을 때(셀과 임베딩이 어긋난 사고).
    """
    from tools.bank_update import TOPK, excluded_indices, has_peer_sample, topk_dedup

    idx_of = {key: i for i, key in enumerate(q_keys)}
    b_keys, b_labs, b_invs = list(b_keys), list(b_labs), list(b_invs)
    t1 = t5 = covered = 0
    for query in cell.eval_cohort:
        if query not in idx_of:
            raise KeyError(f"cohort key가 쿼리 열에 없음: {query}")
        i = idx_of[query]
        excluded = excluded_indices(b_keys, b_invs, self_inv=q_invs[i])
        if not has_peer_sample(q_labs[i], b_labs, excluded):
            continue
        covered += 1
        sims = (emb_b @ emb_q[i]).tolist()
        preds = [lb for lb, _ in topk_dedup(sims, b_labs, excluded, TOPK)]
        t1 += bool(preds) and preds[0] == q_labs[i]
        t5 += q_labs[i] in preds
    return {"t1": int(t1), "t5": int(t5), "n_cohort": len(cell.eval_cohort), "n_covered": covered}
