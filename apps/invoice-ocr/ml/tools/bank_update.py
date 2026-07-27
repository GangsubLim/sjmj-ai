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

import re
from collections.abc import Callable
from dataclasses import dataclass
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
