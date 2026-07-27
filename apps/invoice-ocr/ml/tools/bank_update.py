"""큐레이션 학습쌍 기반 품목 뱅크(bank.npz) 증분 갱신 도구.

검수 완료(ocr_jobs.curation_reviewed=TRUE) 잡의 included 학습쌍을 '원하는 상태'로 보고,
운영 뱅크를 그 상태로 수렴시키는 멱등 sync를 수행한다(추가/교체/제거). 구세대 key
(crop_ref 형식이 아닌 항목)는 절대 건드리지 않는다. macmini에서 직접 실행한다(ADR 0001).

코어 규약 준수: 순수 로직은 stdlib 전용, numpy/torch/cv2는 함수 본문 지연 import
(handwriting/infer_job.py 규약). 임베딩은 embed_fn 주입이라 테스트가 모델 없이 돈다.

Usage:
    uv run python -m tools.bank_update plan
    uv run python -m tools.bank_update apply --plan results/bank_update/plan.jsonl
    uv run python -m tools.bank_update score --before <bank.bak> --after <bank.npz>
"""

import os
import re
import shutil
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ML_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ML_ROOT / "results" / "bank_update"

# 뱅크 key가 큐레이션 crop_ref('job-42/row-0')인지 — 구세대 학습 key와 구분하는 유일 신호.
CROP_REF_RE = re.compile(r"^job-\d+/row-\d+$")


# ---------------------------------------------------------------------------
# 순수 로직 계층 (IO 없음 — 단위테스트 대상)
# ---------------------------------------------------------------------------


def is_crop_ref(key: str) -> bool:
    """뱅크 key가 큐레이션 crop_ref 형식인지 판정한다."""
    return bool(CROP_REF_RE.fullmatch(key))


def select_desired(pairs: list[dict], reviewed_job_ids: set[int]) -> list[dict]:
    """ADR 0004 게이트 — 검수 완료 잡의 included 쌍만 뱅크 대상으로 남긴다.

    status 기본값이 included(migration_008)라 status만 보면 미검수 쌍이 전부 통과한다.
    """
    return [p for p in pairs if p["job_id"] in reviewed_job_ids and p["status"] == "included"]


def bank_current_map(*, labs: list[str], keys: list[str]) -> dict[str, str]:
    """뱅크에서 crop_ref 형식 key만 뽑아 {crop_ref: label}로 만든다(구세대 key 제외)."""
    return {k: lb for k, lb in zip(keys, labs, strict=True) if is_crop_ref(k)}


@dataclass(frozen=True)
class BankDiff:
    """원하는 상태(desired) 대비 뱅크 current의 sync 차이."""

    add: tuple[str, ...]
    replace: tuple[str, ...]
    remove: tuple[str, ...]
    unchanged: tuple[str, ...]


def diff_bank(current: dict[str, str], desired: dict[str, str]) -> BankDiff:
    """crop_ref 집합 연산으로 추가/교체/제거/불변을 계산한다(재실행 시 공집합 = 멱등)."""
    cur, des = set(current), set(desired)
    both = cur & des
    return BankDiff(
        add=tuple(sorted(des - cur)),
        replace=tuple(sorted(r for r in both if current[r] != desired[r])),
        remove=tuple(sorted(cur - des)),
        unchanged=tuple(sorted(r for r in both if current[r] == desired[r])),
    )


def inv_of(crop_ref: str) -> str:
    """crop_ref('job-42/row-0')에서 전표 식별자('job-42')를 얻는다 — 뱅크 inv 열."""
    return crop_ref.split("/", 1)[0]


def partition_valid(desired: list[dict]) -> tuple[list[dict], list[dict]]:
    """뱅크에 넣을 수 없는 쌍(빈 canonical_label)을 분리해 보고 가능하게 한다."""
    valid, invalid = [], []
    for p in desired:
        label = (p.get("canonical_label") or "").strip()
        if not label:
            invalid.append({**p, "reason": "empty_label"})
        else:
            valid.append({**p, "canonical_label": label})
    return valid, invalid


def prune_missing_crops(
    diff: BankDiff, crop_exists: Callable[[str], bool]
) -> tuple[BankDiff, tuple[str, ...]]:
    """크롭 PNG가 없는 ref를 추가·교체 계획에서만 뺀다(spec §3 plan 3단계).

    제거(remove) 대상에는 적용하지 않는다 — 이미 임베딩돼 뱅크에 든 항목에 원본 PNG는
    필요 없고, 여기서 떨어뜨리면 멀쩡한 뱅크 항목이 조용히 지워진다.
    """
    missing = tuple(sorted({r for r in diff.add + diff.replace if not crop_exists(r)}))
    skip = set(missing)
    pruned = BankDiff(
        add=tuple(r for r in diff.add if r not in skip),
        replace=tuple(r for r in diff.replace if r not in skip),
        remove=diff.remove,
        unchanged=diff.unchanged,
    )
    return pruned, missing


def plan_records(diff: BankDiff, label_by_ref: dict[str, str]) -> list[dict]:
    """diff를 plan.jsonl 레코드로 직렬화한다(plan과 apply 사이의 유일한 계약)."""
    return [
        {"action": action, "crop_ref": ref, "label": label_by_ref.get(ref)}
        for action, refs in (
            ("add", diff.add),
            ("replace", diff.replace),
            ("remove", diff.remove),
        )
        for ref in refs
    ]


def diff_from_records(records: list[dict]) -> BankDiff:
    """plan.jsonl 레코드를 BankDiff로 되돌린다(unchanged는 기록하지 않으므로 공집합)."""

    def refs(action: str) -> tuple[str, ...]:
        return tuple(r["crop_ref"] for r in records if r["action"] == action)

    return BankDiff(add=refs("add"), replace=refs("replace"), remove=refs("remove"), unchanged=())


@dataclass(frozen=True)
class MergePlan:
    """기존 뱅크에서 유지할 인덱스와, 새로 임베딩해 덧붙일 crop_ref 순서."""

    keep_indices: tuple[int, ...]
    append_refs: tuple[str, ...]


def merge_plan(keys: list[str], diff: BankDiff) -> MergePlan:
    """교체는 '기존 항목 제거 후 재추가'로 처리한다 — 라벨/임베딩을 통째로 새로 쓴다.

    drop에 add도 포함한다: 정상 경로에선 add ∩ 기존 keys = ∅라 no-op이지만, 스테일
    plan.jsonl을 재적용할 때는 add 대상이 이미 keys에 있을 수 있어 이를 빼지 않으면
    append_refs와 중복돼 key가 늘어난다. 포함해두면 '제거 후 재추가'로 수렴해 멱등이 된다.
    """
    drop = set(diff.remove) | set(diff.replace) | set(diff.add)
    return MergePlan(
        keep_indices=tuple(i for i, k in enumerate(keys) if k not in drop),
        append_refs=tuple(sorted(set(diff.add) | set(diff.replace))),
    )


# ---------------------------------------------------------------------------
# npz IO 글루 (numpy 지연 import — 코어는 paddle-free/pillow-only 유지)
# ---------------------------------------------------------------------------

BANK_ARRAY_KEYS = ("emb", "lab", "inv", "keys")
EMB_DIM = 128


def load_bank(path: str | Path):
    """bank.npz를 (emb, labs, invs, keys)로 적재한다. 구조 불일치는 즉시 실패(부분 병합 금지)."""
    import numpy as np

    path = Path(path)
    if not path.exists():
        raise RuntimeError(f"뱅크 파일 없음: {path}")
    with np.load(path, allow_pickle=True) as z:
        missing = [k for k in BANK_ARRAY_KEYS if k not in z.files]
        if missing:
            raise RuntimeError(
                f"뱅크 npz 키 구조 불일치 — 누락 {missing} (기대 {list(BANK_ARRAY_KEYS)})"
            )
        return (
            z["emb"],
            [str(x) for x in z["lab"]],
            [str(x) for x in z["inv"]],
            [str(x) for x in z["keys"]],
        )


def validate_bank_arrays(emb, labs: list[str], invs: list[str], keys: list[str]) -> None:
    """저장 직전 정합 검증 — 4배열 길이·임베딩 차원·유한값·crop_ref key 유일성.

    워커(worker/main.py)는 시작 시 emb/lab만 적재하므로 구조 불량이 추론 시점까지 잠복한다.
    쓰기 전에 차단한다. crop_ref 형식 key의 중복은 sync 멱등성(keys=UNIQUE 가정)을 깨므로
    함께 막는다.
    """
    import numpy as np

    lengths = {"emb": len(emb), "lab": len(labs), "inv": len(invs), "keys": len(keys)}
    if len(set(lengths.values())) != 1:
        raise RuntimeError(f"뱅크 배열 길이 불일치: {lengths}")
    if emb.ndim != 2 or emb.shape[1] != EMB_DIM:
        raise RuntimeError(f"임베딩 차원 이상: shape={emb.shape} (기대 (n, {EMB_DIM}))")
    if not np.isfinite(emb).all():
        raise RuntimeError("임베딩에 NaN/inf가 있습니다 — 저장을 중단합니다.")
    crop_ref_counts = Counter(k for k in keys if is_crop_ref(k))
    duplicates = sorted(k for k, count in crop_ref_counts.items() if count > 1)
    if duplicates:
        raise RuntimeError(f"뱅크 key 중복(crop_ref): {duplicates}")


def backup_bank(path: str | Path) -> Path:
    """bank.npz를 같은 디렉터리에 타임스탬프 백업으로 복사한다(실패 시 예외 → apply 중단).

    같은 초 재실행으로 백업 파일명이 충돌하면 무경고 덮어쓰기 대신 즉시 실패한다
    (spec "백업 실패 시 중단"과 일치).
    """
    path = Path(path)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = path.with_name(f"{path.stem}.{stamp}.npz.bak")
    if dst.exists():
        raise RuntimeError(f"백업 파일이 이미 존재함: {dst}")
    shutil.copy2(path, dst)
    return dst


def save_bank_atomic(
    path: str | Path, emb, labs: list[str], invs: list[str], keys: list[str]
) -> None:
    """검증 통과 시에만 tmp에 쓰고 rename한다 — 부분 쓰기로 운영 뱅크를 깨지 않는다."""
    import numpy as np

    emb = np.asarray(emb, dtype="float32")
    validate_bank_arrays(emb, labs, invs, keys)
    path = Path(path)
    # tmp 파일명이 .npz로 끝나야 np.savez가 확장자를 추가로 덧붙이지 않는다.
    tmp = path.with_name(path.name + ".tmp.npz")
    try:
        np.savez(
            tmp,
            emb=emb,
            lab=np.array(labs, object),
            inv=np.array(invs, object),
            keys=np.array(keys, object),
        )
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)
