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
