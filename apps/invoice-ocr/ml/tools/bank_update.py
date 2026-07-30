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

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, get_args

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


def _is_included(pair: dict) -> bool:
    """쌍의 status가 included인지 판정한다(migration_008 기본값 술어의 단일 진실원).

    안전 관련 술어라 select_desired·select_scoped 양쪽에서 반드시 이 함수를 통해야
    한다 — 문자열 리터럴을 각자 들고 있으면 한쪽만 갱신되는 드리프트가 모집단
    오염으로 이어진다.
    """
    return pair["status"] == "included"


def select_desired(pairs: list[dict], reviewed_job_ids: set[int]) -> list[dict]:
    """ADR 0004 게이트 — 검수 완료 잡의 included 쌍만 뱅크 대상으로 남긴다.

    status 기본값이 included(migration_008)라 status만 보면 미검수 쌍이 전부 통과한다.
    """
    return [p for p in pairs if p["job_id"] in reviewed_job_ids and _is_included(p)]


# 모집단 축(spec §3-B). AXES(제외 축)와 다른 축이며 CLI 플래그는 score에만 붙는다.
# Scope가 닫힌 집합의 진실원 — SCOPES는 get_args로 그로부터 도출해 둘이 구조적으로
# 드리프트할 수 없다(새 scope를 추가하려면 Literal부터 고쳐야 SCOPES도 따라온다).
Scope = Literal["reviewed", "all"]
SCOPES = get_args(Scope)
# plan 경로의 모집단은 reviewed 고정 — ADR 0004 검수 게이트다. 상수로 못 박아 args가
# 우연히 흘러들어오지 못하게 한다(누수의 결과는 되돌리기 어려운 뱅크 오염이다).
PLAN_SCOPE: Scope = "reviewed"


def select_scoped(pairs: list[dict], reviewed_job_ids: set[int], scope: Scope) -> list[dict]:
    """모집단 축에 따라 included 쌍을 고른다 — 분기를 명시하고 끝에서 던진다(_axis_excluded 관용구).

    Args:
        pairs: training_pairs 전량.
        reviewed_job_ids: 검수 완료 잡 id 집합.
        scope: "reviewed"(ADR 0004 게이트 적용) 또는 "all"(검수 여부 무관, 채점 전용).

    Raises:
        ValueError: 분기가 배선되지 않은 scope(미지의 이름 · SCOPES에만 추가된 이름 모두).
            `scope not in SCOPES`만 걸러 reviewed로 흘려보내면, 새 scope가 reviewed 숫자를
            다른 이름표로 달고 조용히 산출된다.
    """
    if scope == "reviewed":
        return select_desired(pairs, reviewed_job_ids)
    if scope == "all":
        return [p for p in pairs if _is_included(p)]
    raise ValueError(f"미지의 모집단 scope {scope!r} — 분기가 배선되지 않았다(SCOPES={SCOPES})")


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
    """crop_ref('job-42/row-0')에서 전표 식별자('job-42')를 얻는다 — 뱅크 inv 열.

    crop_ref 형식이 아닌 key(부트스트랩 '2025-08-18_inv011_0' 등)는 거부한다. 그런 key는
    슬래시가 없어 split이 key 자신을 돌려주고, 그 값으로 만든 전표 제외 집합은 어떤 뱅크
    항목과도 일치하지 않아 조용히 비어버린다. 그 경우의 정답은 언제나 뱅크 inv 열이다.
    """
    if not is_crop_ref(crop_ref):
        raise ValueError(
            f"crop_ref 형식이 아님 {crop_ref!r} — 부트스트랩 key의 전표는 뱅크 inv 열에서 읽는다"
        )
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


def partition_crop_ref(pairs: list[dict]) -> tuple[list[dict], list[dict]]:
    """crop_ref 형식이 아닌 쌍을 분리해 보고 가능하게 한다(M3).

    plan_records/diff_from_records는 crop_ref 형식을 뱅크 key 계약으로 전제한다.
    형식 불량 쌍을 여기서 걸러야 apply 단계(diff_from_records의 형식 검증)에서
    plan.jsonl 전체가 죽는 대신, plan 단계에서 개별 사유로 보고하고 배제할 수 있다.
    """
    valid, invalid = [], []
    for p in pairs:
        if is_crop_ref(p["crop_ref"]):
            valid.append(p)
        else:
            invalid.append({**p, "reason": "bad_crop_ref"})
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


ACTIONS = ("add", "replace", "remove")
LABEL_REQUIRED_ACTIONS = ("add", "replace")


def plan_records(diff: BankDiff, label_by_ref: dict[str, str]) -> list[dict]:
    """diff를 plan.jsonl 레코드로 직렬화한다(plan과 apply 사이의 유일한 계약)."""
    return [
        {"action": action, "crop_ref": ref, "label": label_by_ref.get(ref)}
        for action, refs in zip(ACTIONS, (diff.add, diff.replace, diff.remove), strict=True)
        for ref in refs
    ]


def _validate_plan_record(index: int, record: dict) -> None:
    """레코드 1건의 타입·필수 키·action·crop_ref 형식·label 요건을 검증한다.

    dict 검사가 먼저인 이유: plan.jsonl 한 줄이 JSON 문자열/배열이면 아래 `k not in record`가
    부분문자열·원소 검사로 통과해 버려, 뒤의 인덱싱에서 어느 레코드가 문제인지 알 수 없는
    원시 TypeError가 난다.
    """
    if not isinstance(record, dict):
        raise ValueError(f"plan 레코드 {index}가 JSON 객체가 아님: {record!r}")
    missing = [k for k in ("action", "crop_ref") if k not in record]
    if missing:
        raise ValueError(f"plan 레코드 {index}에 필수 키 누락 {missing}: {record}")
    if record["action"] not in ACTIONS:
        raise ValueError(f"plan 레코드 {index}에 미지의 action {record['action']!r}: {record}")
    if not is_crop_ref(record["crop_ref"]):
        raise ValueError(
            f"plan 레코드 {index}의 crop_ref 형식 불량 {record['crop_ref']!r}: {record}"
        )
    if record["action"] in LABEL_REQUIRED_ACTIONS:
        label = record.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(
                f"plan 레코드 {index}에 유효한 label 없음(action={record['action']!r}): {record}"
            )


def diff_from_records(records: list[dict]) -> BankDiff:
    """plan.jsonl 레코드를 BankDiff로 되돌린다(unchanged는 기록하지 않으므로 공집합).

    plan.jsonl은 외부 입력이라 신뢰하지 않는다 — 필수 키 누락·미지의 action·crop_ref
    형식 불량·add/replace의 label 누락을 발견하면 조용히 버리지 않고 어느 레코드가
    문제인지 담아 즉시 실패한다. crop_ref 중복도 거부한다(뱅크 keys UNIQUE 전제와 동일 사유).
    """
    for i, record in enumerate(records):
        _validate_plan_record(i, record)

    duplicate_refs = sorted(
        ref for ref, count in Counter(r["crop_ref"] for r in records).items() if count > 1
    )
    if duplicate_refs:
        raise ValueError(f"plan 레코드에 중복 crop_ref: {duplicate_refs}")

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


def excluded_indices(
    keys: list[str],
    invs: list[str] | None,
    *,
    self_ref: str | None = None,
    self_inv: str | None = None,
) -> set[int]:
    """채점에서 뺄 뱅크 항목의 인덱스 — '무엇을 뺄지' 판단의 유일한 지점.

    축은 모드 스위치가 아니라 데이터(인덱스 집합)로 표현된다. 채점(topk_dedup)과 peer
    분모(has_peer_sample)가 같은 집합을 공유하므로 두 곳이 각자 판단해 어긋나는 일이
    구조적으로 불가능해진다.

    전표 판정은 key 파싱이 아니라 뱅크 inv 열로 한다 — 부트스트랩 key에는 슬래시가 없어
    파싱이 조용히 실패한다(spec §2). 회귀 평가셋 러너처럼 crop_ref가 아예 없는 소비자는
    self_inv를 직접 준다.

    Args:
        keys: 뱅크 key 열(self_ref 대조용).
        invs: 뱅크 inv 열(self_inv 대조용). 전표 축을 쓰지 않으면 None이어도 된다.
        self_ref: 제외할 쿼리 자신의 key.
        self_inv: 제외할 전표 식별자. 같은 전표의 모든 항목이 빠진다.

    Returns:
        제외할 인덱스 집합. 뱅크에 자기도 동일 전표도 없으면 빈 집합(hold-out 정상 경로).

    Raises:
        ValueError: self_ref·self_inv가 둘 다 None이거나, self_inv를 줬는데 invs가 None
            이거나, keys와 invs의 길이가 다를 때.
    """
    if self_ref is None and self_inv is None:
        raise ValueError(
            "self_ref/self_inv가 모두 None — 제외 없이 채점하면 자기 자신이 항상 1등이다"
        )
    if self_inv is not None and invs is None:
        raise ValueError(f"self_inv={self_inv!r}를 줬는데 invs가 None — 전표 축 판단 재료가 없다")
    if invs is not None and len(keys) != len(invs):
        raise ValueError(f"keys/invs 길이 불일치: {len(keys)}/{len(invs)}")

    out = {i for i, k in enumerate(keys) if k == self_ref} if self_ref is not None else set()
    if self_inv is not None:
        out |= {i for i, v in enumerate(invs) if v == self_inv}
    return out


# 직접 미러링 대상은 운영 retrieval인 handwriting/infer_photo.py의 TOPK다 — '운영과 같은
# 기준으로 채점한다'(topk_dedup)는 전제가 여기 걸려 있어, 어긋나면 운영과 다른
# 기준으로 산출된 유사도가 임계 캘리브레이션 근거가 된다. 그 TOPK는 다시 backend/app/
# schemas/ocr.py의 TOP_K/LABEL_SOURCES와 frontend/src/utils/label-source.ts의 TOP_K에
# 물려 있다. ml 쪽 2곳은 tests/test_topk_sync.py(ml/tests)가 api-spec.json enum과 대조한다.
TOPK = 5


def topk_dedup(
    sims: list[float], labs: list[str], excluded: set[int], k: int = TOPK
) -> list[tuple[str, float]]:
    """제외 집합 밖에서 라벨 중복 제거 top-k를 고른다 — '무엇을 뺄지'는 판단하지 않는다.

    중복 제거 규칙은 handwriting/infer_photo.py의 topk와 동일 — 운영 retrieval과 같은
    기준으로 채점한다. 단, 정렬은 여기서 `sorted`(안정 정렬)를 쓰므로 동점 시 항상 같은
    순서가 나온다 — 운영 argsort(불안정 정렬)와 다르며 채점 결정론을 위한 선택이다.

    Raises:
        ValueError: sims/labs 길이가 다르거나(긴 labs는 조용히 꼬리가 버려진다), excluded가
            sims 범위를 벗어날 때(다른 뱅크에서 만든 집합을 적용하는 사고).
    """
    if len(sims) != len(labs):
        raise ValueError(f"sims/labs 길이 불일치: {len(sims)}/{len(labs)}")
    if excluded and max(excluded) >= len(sims):
        raise ValueError(f"제외 인덱스가 뱅크 범위를 벗어남: max={max(excluded)} >= {len(sims)}")
    out: list[tuple[str, float]] = []
    seen: set[str] = set()
    for j in sorted(range(len(sims)), key=lambda i: -sims[i]):
        if j in excluded or labs[j] in seen:
            continue
        seen.add(labs[j])
        out.append((labs[j], float(sims[j])))
        if len(out) >= k:
            break
    return out


def has_peer_sample(label: str, labs: list[str], excluded: set[int]) -> bool:
    """제외 집합 밖에 같은 라벨의 다른 크롭이 있는지 — 단일 샘플 라벨의 분모 분리 신호.

    채점(topk_dedup)과 같은 excluded를 받는다. 제외 축을 바꿔도 두 지표가 함께 움직인다.

    Raises:
        ValueError: excluded가 labs 범위를 벗어날 때(다른 뱅크에서 만든 집합을 적용하는 사고).
            score_one 안에서는 topk_dedup이 먼저 이를 걸러주지만, 이 함수를 단독으로 부르는
            소비자는 그 방어를 못 받으므로 여기서도 별도로 검증한다.
    """
    if excluded and max(excluded) >= len(labs):
        raise ValueError(f"제외 인덱스가 뱅크 범위를 벗어남: max={max(excluded)} >= {len(labs)}")
    return any(lb == label for i, lb in enumerate(labs) if i not in excluded)


def score_one(
    sims: list[float],
    labs: list[str],
    excluded: set[int],
    self_ref: str,
    label: str,
) -> dict:
    """쌍 1건을 채점한다 — 커버리지(제외 무관)와 제외 후 top-1/top-5를 분리 산출.

    제외 축은 호출자가 excluded_indices로 정해서 넘긴다 — 이 함수는 축을 모른다.

    Returns:
        다음 키를 가진 dict — ``crop_ref``(쿼리 자신의 key), ``label``(정답 라벨),
        ``in_bank``(뱅크 커버리지, 제외 항목 포함), ``top1``/``top5``(제외 후 적중 여부),
        ``has_peer``(제외 집합 밖에 동일 라벨 존재 여부), ``preds``(topk 예측 라벨 목록),
        ``top1_sim``(제외 후 top-1 유사도. 후보가 없으면 ``None``).
    """
    ranked = topk_dedup(sims, labs, excluded, TOPK)
    preds = [lb for lb, _ in ranked]
    return {
        "crop_ref": self_ref,
        "label": label,
        "in_bank": label in labs,
        "top1": bool(preds) and preds[0] == label,
        "top5": label in preds,
        "has_peer": has_peer_sample(label, labs, excluded),
        "preds": preds,
        "top1_sim": ranked[0][1] if ranked else None,
    }


def score_summary(records: list[dict]) -> dict:
    """커버리지·retrieval 지표를 집계한다(동일 라벨 타 샘플 존재 쌍 한정 분모 병기)."""
    peers = [r for r in records if r["has_peer"]]
    return {
        "n": len(records),
        "in_bank": sum(r["in_bank"] for r in records),
        "out_of_bank": sum(not r["in_bank"] for r in records),
        "top1": sum(r["top1"] for r in records),
        "top5": sum(r["top5"] for r in records),
        "peer_n": len(peers),
        "peer_top1": sum(r["top1"] for r in peers),
        "peer_top5": sum(r["top5"] for r in peers),
    }


def _pct(k: int, n: int) -> str:
    return f"{k}/{n} ({100 * k / n:.1f}%)" if n else "0/0 (—)"


# 제외 축 — cmd_score는 항상 둘을 병기한다(CLI 플래그를 두지 않는다, spec D4).
# 이름이 축을 다 말하지 못하므로(무엇을 제외하는 축인지) 표 제목에서 풀어 쓴다.
AXES = ("crop_ref", "invoice")
AXIS_TITLES = {
    "crop_ref": "crop_ref (쿼리 자신만 제외)",
    # 전표 판정은 뱅크 inv 열이며 key와 inv는 네임스페이스가 둘이다(부트스트랩
    # '2025-08-18_inv011.jpg' vs 큐레이션 'job-42'). 큐레이션 쿼리의 inv는 부트스트랩 항목과
    # 절대 일치하지 않으므로 "전표 전체"로 읽히면 과대 약속이다 — 제목은 기준을 그대로 적는다.
    "invoice": "invoice (뱅크 inv 열이 같은 항목 전체 제외)",
}


def render_score_md(summaries: dict[tuple[str, str], dict], meta: dict) -> str:
    """(side, axis) → 요약 맵을 제외 축별 표로 렌더한다(표마다 before/after 2열 유지).

    4열(crop_ref before/after · invoice before/after) 한 표로 만들지 않는다 — 읽는 사람이
    비교해야 할 축은 before↔after이지 crop_ref↔invoice가 아니다(spec §4).
    """
    rows = [
        # 두 축이 같은 값이다(in_bank은 제외와 무관하게 label in labs). "self 포함"은 축이
        # crop_ref뿐이던 시절의 표현이라 invoice 표에서 실제보다 좁게 읽힌다.
        ("커버리지 in-bank(제외 무관)", "in_bank", "n"),
        ("커버리지 out_of_bank", "out_of_bank", "n"),
        ("제외 후 top-1", "top1", "n"),
        ("제외 후 top-5", "top5", "n"),
        ("peer 존재 한정 top-1", "peer_top1", "peer_n"),
        ("peer 존재 한정 top-5", "peer_top5", "peer_n"),
    ]
    lines = [
        "# 뱅크 증분 갱신 전/후 비교",
        "",
        f"- 뱅크 크기: {meta.get('bank_before', '?')} → {meta.get('bank_after', '?')}",
        f"- 채점 대상(desired 쌍): {summaries[('after', AXES[0])]['n']}건 · "
        "동일 채점기로 before/after 산출",
        "- 표본 수는 두 축이 같다(같은 쿼리 쌍을 제외 축만 바꿔 채점). 축마다 달라지는 것은",
        "  후보에서 빠지는 뱅크 항목이며, 그 여파가 peer 분모에 드러난다.",
    ]
    for axis in AXES:
        before, after = summaries[("before", axis)], summaries[("after", axis)]
        lines += [
            "",
            f"## 제외 축: {AXIS_TITLES[axis]}",
            "",
            "| 지표 | before | after |",
            "| --- | --- | --- |",
        ]
        lines += [
            f"| {name} | {_pct(before[num], before[den])} | {_pct(after[num], after[den])} |"
            for name, num, den in rows
        ]
    lines += [
        "",
        "단일 샘플 라벨은 제외 후 후보가 0이라 구조적으로 미스이므로, out_of_bank 해소는",
        "커버리지 행으로 판단하고 retrieval 개선은 peer 존재 한정 행으로 판단한다.",
    ]
    return "\n".join(lines) + "\n"


def score_meta(
    *,
    generated_at: str,
    scope: str,
    n_pairs: int,
    fingerprints: dict[str, str | None],
    score_jsonl_sha256: str,
) -> dict:
    """재평가 산출물의 유효성 게이트 메타를 만든다(리포트가 §3-C에서 대조한다).

    axes를 단수 axis가 아니라 목록으로 적는 이유: 산출물은 항상 두 축을 함께 담으므로
    단수로 적으면 나머지 한 축이 없는 것처럼 읽힌다. n_pairs는 축과 무관하다(같은 쿼리 쌍을
    두 축으로 채점하므로 분모가 같다). 레코드 수는 n_pairs × 2 × len(axes)로 리포트가 직접
    계산해 대조하므로 여기 중복 기록하지 않는다.
    """
    return {
        "generated_at": generated_at,
        "scope": scope,
        "axes": list(AXES),
        "n_pairs": n_pairs,
        "retrieval_version": fingerprints,
        "score_jsonl_sha256": score_jsonl_sha256,
    }


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
    """뱅크 정합 검증 — 4배열 길이·임베딩 차원·유한값·crop_ref key 유일성.

    적재 직후(apply_sync·cmd_score)와 저장 직전(save_bank_atomic) 3곳에서 부른다. 워커
    (worker/main.py)는 시작 시 emb/lab만 적재하므로 구조 불량이 추론 시점까지 잠복한다 —
    쓰기 전에 차단한다. crop_ref 형식 key의 중복은 sync 멱등성(keys=UNIQUE 가정)을 깨므로
    함께 막는다. 길이 정합뿐 아니라 emb 차원·유한값·crop_ref key 중복까지 강제하므로,
    호출부가 "길이 정합"만 기대하고 불러도 그보다 넓게 검증된다.
    """
    import numpy as np

    lengths = {"emb": len(emb), "lab": len(labs), "inv": len(invs), "keys": len(keys)}
    if len(set(lengths.values())) != 1:
        raise RuntimeError(f"뱅크 배열 길이 불일치: {lengths}")
    if emb.ndim != 2 or emb.shape[1] != EMB_DIM:
        raise RuntimeError(f"임베딩 차원 이상: shape={emb.shape} (기대 (n, {EMB_DIM}))")
    if not np.isfinite(emb).all():
        raise RuntimeError("임베딩에 NaN/inf가 있습니다 — 중단합니다.")
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


def apply_sync(
    bank_path: str | Path,
    records: list[dict],
    crops_root: str | Path,
    embed_fn: Callable[[list[Path]], object],
) -> dict:
    """plan 레코드대로 뱅크를 sync한다 — 크롭 존재 확인 → 임베딩 → 백업 → 병합 → 원자적 저장.

    크롭 존재 확인을 백업·임베딩보다 먼저 두는 이유: 어차피 실패할 apply를 위해 백업
    파일(고아 .bak)을 만들거나 모델을 로딩하지 않기 위함이다.

    embed_fn: crop PNG 경로 리스트 → (n, 128) 임베딩. 운영은 prod_embed_fn(운영 추론과
    동일 경로), 테스트는 Fake를 주입한다(worker/poll.py의 infer_fn 주입 선례).
    """
    import numpy as np

    emb, labs, invs, keys = load_bank(bank_path)
    validate_bank_arrays(emb, labs, invs, keys)

    if not records:
        # no-op이면 운영 파일을 아예 건드리지 않는다(백업·재작성 생략 = 멱등성의 관측 가능한 형태).
        return {
            "backup": None,
            "added": 0,
            "replaced": 0,
            "removed": 0,
            "before": len(keys),
            "after": len(keys),
        }

    diff = diff_from_records(records)
    plan = merge_plan(keys, diff)
    label_by_ref = {
        r["crop_ref"]: r["label"] for r in records if r["action"] in LABEL_REQUIRED_ACTIONS
    }

    crops_root = Path(crops_root)
    paths = [crops_root / f"{ref}.png" for ref in plan.append_refs]
    missing_crops = [str(p) for p in paths if not p.exists()]
    if missing_crops:
        raise RuntimeError(f"크롭 파일 없음(백업·임베딩 전 중단): {missing_crops}")

    new_emb = np.asarray(embed_fn(paths), dtype=emb.dtype) if paths else emb[:0]
    expected_shape = (len(plan.append_refs), EMB_DIM)
    if new_emb.shape != expected_shape:
        raise RuntimeError(f"임베딩 shape 불일치: {new_emb.shape} != 기대 {expected_shape}")

    # 임베딩까지 성공을 확인한 뒤에만 백업한다 — 실패로 끝날 apply가 고아 .bak을 남기지 않도록.
    backup = backup_bank(bank_path)

    keep = np.array(plan.keep_indices, dtype=int)
    merged_emb = np.concatenate([emb[keep], new_emb])
    merged_labs = [labs[i] for i in plan.keep_indices] + [label_by_ref[r] for r in plan.append_refs]
    merged_invs = [invs[i] for i in plan.keep_indices] + [inv_of(r) for r in plan.append_refs]
    merged_keys = [keys[i] for i in plan.keep_indices] + list(plan.append_refs)

    save_bank_atomic(bank_path, merged_emb, merged_labs, merged_invs, merged_keys)
    # diff_from_records가 crop_ref 중복을 거부하므로 add/replace/remove 길이가 실제 병합
    # 결과와 항상 일치한다 — summary는 diff 길이를 그대로 쓴다.
    return {
        "backup": str(backup),
        "added": len(diff.add),
        "replaced": len(diff.replace),
        "removed": len(diff.remove),
        "before": len(keys),
        "after": len(merged_keys),
    }


# ---------------------------------------------------------------------------
# DB / 모델 글루 (macmini 로컬 실행 — 단위테스트 비대상)
# ---------------------------------------------------------------------------

ENV_BACKEND = ("SJMJ_BACKEND_ENV", "~/.sjmj-ai/backend.env")
REVIEWED_SQL = "SELECT id FROM ocr_jobs WHERE curation_reviewed = 1"


def require_env(name: str) -> str:
    """필수 env를 읽는다. 미설정이면 즉시 실패한다(경로 하드코딩 금지 규약)."""
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(
            f"{name} 미설정 — ml-worker env(~/.sjmj-ai/ml-worker.env)를 로드한 뒤 실행하세요."
        )
    return val


def parse_reviewed_job_ids(text: str) -> set[int]:
    """`SELECT id FROM ocr_jobs WHERE curation_reviewed=1`의 --batch TSV를 집합으로 파싱한다.

    헤더('id')는 숫자가 아니라 자연히 걸러진다 — 헤더 유무에 의존하지 않는다(0행이면 빈 집합).
    """
    return {int(ln) for ln in (line.strip() for line in text.split("\n")) if ln.isdigit()}


def _mysql(backend_env: str, sql: str) -> str:
    """macmini 로컬 mysql CLI로 질의해 --batch TSV를 얻는다(접속값은 env 파일에서만).

    backend_env는 `os.path.expanduser`로 먼저 `~`를 전개한 뒤 quote한다 — shlex.quote를
    전개 없이 단독 적용하면 작은따옴표로 감싸져 bash의 `~` 확장이 막힌다.

    H1: 파일이 없으면 셸을 띄우기 전에 즉시 fail-fast한다 — bash `source`가 실패해도
    `set -a; source ...`만으로는 스크립트가 계속 진행돼(DB_* 미설정인 채로) mysql이
    엉뚱한 접속값(빈 host 등)으로 조용히 실패하거나, 최악의 경우 다른 프로세스의 잔여
    env를 오인해 조용히 성공한 것처럼 보일 위험이 있다. `|| exit 91`로 셸 내부에서도
    한 번 더 막는다(방어 중복이지만 비용이 없다).
    """
    backend_env = os.path.expanduser(backend_env)
    if not Path(backend_env).exists():
        raise RuntimeError(f"backend env 파일 없음: {backend_env}")
    script = (
        f"set -a; source {shlex.quote(backend_env)} || exit 91; set +a; "
        'export MYSQL_PWD="$DB_PASS"; '
        'MYSQL_BIN="$(command -v mysql || echo /opt/homebrew/opt/mysql/bin/mysql)"; '
        f'"$MYSQL_BIN" -h"$DB_HOST" -P"$DB_PORT" -u"$DB_USER" "$DB_NAME" --batch -e {shlex.quote(sql)}'
    )
    proc = subprocess.run(["bash", "-c", script], capture_output=True, check=False)
    if proc.returncode != 0:
        # M2: stderr 전체(비밀번호 파편 등 유출 표면)가 아닌 마지막 2줄만 예외 메시지에 담는다.
        stderr_tail = "\n".join(proc.stderr.decode().splitlines()[-2:])
        raise RuntimeError(f"mysql 질의 실패(exit {proc.returncode}): {stderr_tail}")
    return proc.stdout.decode()


def fetch_pairs(backend_env: str) -> list[dict]:
    """training_pairs 전량을 조회한다(파서는 curation_report와 공유 — 컬럼 계약 단일화)."""
    from tools.curation_report import PAIRS_SQL, parse_pairs_tsv

    return parse_pairs_tsv(_mysql(backend_env, PAIRS_SQL))


def fetch_reviewed_job_ids(backend_env: str) -> set[int]:
    """검수 완료(curation_reviewed=TRUE) 잡 id를 조회한다 — ADR 0004 게이트의 입력."""
    return parse_reviewed_job_ids(_mysql(backend_env, REVIEWED_SQL))


def prod_embed_fn(models_dir):
    """운영 추론과 동일 경로(square → EVAL_TF → ItemEncoder projection) 임베딩 함수를 만든다.

    cv2/torch/handwriting.infer_photo는 여기서 지연 import한다(handwriting/infer_job.py 규약)
    — 그래야 paddle-free venv에서도 `python -m tools.bank_update --help`가 성공한다.
    """

    def embed(paths):
        import cv2

        from handwriting import infer_photo as ip

        device = "cpu"  # ADR 0002 — MPS/MLX 동시 사용 회피
        model = ip.load_model_from(Path(models_dir) / "ft_prod.pt", device)
        crops = []
        for p in paths:
            img = cv2.imread(str(p))
            if img is None:
                raise RuntimeError(f"크롭 이미지를 읽을 수 없습니다: {p}")
            crops.append(img)
        return ip.embed_crops(model, crops, device)

    return embed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _desired_pairs(backend_env: str, scope: Scope) -> tuple[list[dict], list[dict]]:
    """모집단 쌍을 조회해 라벨/crop_ref 유효 여부로 나눈다(plan·score 공용 입구).

    scope는 호출자가 명시한다 — 기본값을 두지 않는다. 기본값이 있으면 plan 경로가 실수로
    넓은 모집단을 물려받는 경로가 생긴다(ADR 0004).

    M3: crop_ref 형식 게이트를 라벨 게이트보다 먼저 적용한다 — 둘 다 뱅크에 넣을 수
    없는 사유이므로 같은 invalid 목록에 합류시켜, 형식 불량 쌍이 plan.jsonl까지
    흘러가 apply에서야(diff_from_records) 늦게 발각되는 것을 막는다.
    """
    pairs = fetch_pairs(backend_env)
    reviewed = fetch_reviewed_job_ids(backend_env)
    desired = select_scoped(pairs, reviewed, scope)
    crop_ref_ok, bad_crop_ref = partition_crop_ref(desired)
    valid, invalid = partition_valid(crop_ref_ok)
    return valid, invalid + bad_crop_ref


def _atomic_write_text(path: Path, text: str) -> None:
    """tmp에 쓰고 rename한다(save_bank_atomic의 기존 관용구).

    부분 기록된 산출물이 유효한 것처럼 읽히는 것을 막는다 — 중단된 재실행이 새 score.jsonl과
    이전 meta를 짝지으면, 같은 뱅크로 재채점하는 흔한 경우엔 지문이 일치해 stale 방어도
    이를 못 잡는다(spec §3-B).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(text)
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    # 직렬화를 먼저 끝내므로 실패 시 기존 파일이 그대로 남는다.
    _atomic_write_text(path, "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")


def _write_json(path: Path, obj: dict) -> None:
    _atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=1) + "\n")


def cmd_plan(args) -> None:
    """desired 대비 뱅크 diff를 계산해 plan.jsonl과 요약을 낸다."""
    crops_root = Path(require_env("SJMJ_DATA_DIR")) / "ocr_crops"
    bank_path = Path(require_env("SJMJ_ML_MODELS_DIR")) / "bank.npz"
    valid, invalid = _desired_pairs(args.backend_env, PLAN_SCOPE)

    _, labs, _, keys = load_bank(bank_path)
    desired = {p["crop_ref"]: p["canonical_label"] for p in valid}
    # 크롭 존재 검사는 diff 이후 추가·교체에만 — desired에서 미리 빼면 기존 뱅크 항목이 remove된다.
    diff, missing = prune_missing_crops(
        diff_bank(bank_current_map(labs=labs, keys=keys), desired),
        lambda ref: (crops_root / f"{ref}.png").exists(),
    )
    records = plan_records(diff, desired)

    out = args.out / "plan.jsonl"
    _write_jsonl(out, records)
    print(
        f"desired {len(valid)}쌍(제외 {len(invalid)}) · 뱅크 {len(keys)}항목\n"
        f"추가 {len(diff.add)} · 교체 {len(diff.replace)} · 제거 {len(diff.remove)} · "
        f"불변 {len(diff.unchanged)}\n저장: {out}"
    )
    for p in invalid:
        print(f"  제외 {p['crop_ref']}: {p['reason']} (label={p.get('canonical_label')!r})")
    for ref in missing:
        print(f"  보류 {ref}: missing_crop (추가·교체만 보류 — 기존 뱅크 항목은 유지)")


REMOVE_PREVIEW = 5
RESTART_HINT = (
    "ml-worker는 기동 시 1회만 뱅크를 적재한다(worker/main.py) — 재시작해야 추론에 반영된다:\n"
    "  launchctl kickstart -k gui/$(id -u)/ai.sjmj.ml-worker"
)


def require_removal_confirmation(records: list[dict], *, confirmed: bool) -> None:
    """제거가 포함된 plan은 명시 승인(--yes) 없이는 거부한다 — 대량 삭제 안전장치.

    apply는 plan.jsonl만 신뢰하므로 plan 산출 시점의 DB/뱅크 상태를 재확인하지 않는다.
    그래서 ① plan 이후 다시 desired가 된 항목을 스테일 plan이 지우거나, ② 잘못된
    `--backend-env`로 만든 plan(reviewed 0건 → 뱅크 crop_ref 전량 remove)이 그대로
    실행될 수 있다. 추가·교체는 재실행이 멱등이라 그대로 통과시키고(merge_plan 참조),
    되돌릴 수 없는 제거에만 사람의 확인을 요구한다.

    Args:
        records: plan.jsonl에서 읽은 레코드 목록. 여기서 형식 검증도 함께 수행된다.
        confirmed: `--yes`로 제거를 승인했는지 여부.

    Raises:
        ValueError: 레코드 형식이 불량할 때(diff_from_records 계약).
        RuntimeError: 제거가 있는데 승인되지 않았을 때.
    """
    removes = diff_from_records(records).remove
    if not removes or confirmed:
        return
    preview = ", ".join(removes[:REMOVE_PREVIEW])
    more = f" 외 {len(removes) - REMOVE_PREVIEW}건" if len(removes) > REMOVE_PREVIEW else ""
    raise RuntimeError(
        f"plan에 제거 {len(removes)}건이 있습니다 — 되돌릴 수 없으므로 --yes 없이는 "
        f"실행하지 않습니다. 대상이 맞는지 plan을 재산출해 확인하세요: {preview}{more}"
    )


def cmd_apply(args) -> None:
    """plan.jsonl대로 뱅크를 sync한다(백업 자동 생성 · 제거는 --yes 필요)."""
    crops_root = Path(require_env("SJMJ_DATA_DIR")) / "ocr_crops"
    models_dir = Path(require_env("SJMJ_ML_MODELS_DIR"))
    records = [json.loads(ln) for ln in args.plan.read_text().splitlines() if ln.strip()]
    require_removal_confirmation(records, confirmed=args.yes)
    summary = apply_sync(models_dir / "bank.npz", records, crops_root, prod_embed_fn(models_dir))
    print(
        f"백업: {summary['backup'] or '생략(변경 없음)'}\n"
        f"추가 {summary['added']} · 교체 {summary['replaced']} · "
        f"제거 {summary['removed']} · 뱅크 {summary['before']} → {summary['after']}"
    )
    if summary["backup"]:
        print(RESTART_HINT)


def _axis_excluded(axis: str, keys: list[str], invs: list[str], self_ref: str) -> set[int]:
    """축 이름 → 제외 집합. invoice 축에도 self_ref를 함께 넘겨 자기 제외를 보장한다(D4).

    분기를 축마다 명시하고 끝에서 던진다 — `axis not in AXES`만 걸러 crop_ref로 흘려보내면
    AXES에 세 번째 축을 추가하고 여기 분기를 빠뜨린 경우를 못 막는다(그 축은 crop_ref 숫자를
    다른 이름표로 달고 조용히 산출되며, 레코드 수 side×axis×쌍은 그대로 맞아 §4 유일키·개수
    단언도 통과한다).

    Raises:
        ValueError: 분기가 배선되지 않은 축 이름(미지의 축 · AXES에만 추가된 축 모두).
    """
    if axis == "crop_ref":
        return excluded_indices(keys, invs, self_ref=self_ref)
    if axis == "invoice":
        return excluded_indices(keys, invs, self_ref=self_ref, self_inv=inv_of(self_ref))
    raise ValueError(f"미지의 제외 축 {axis!r} — 분기가 배선되지 않았다(AXES={AXES})")


def _side_fingerprints(models_dir: Path, bank_arrays: dict[str, tuple]) -> dict[str, str | None]:
    """before/after 뱅크의 retrieval 지문을 계산한다.

    모델 다이제스트·코드 SHA는 side와 무관하므로 1회만 계산해 공유한다 — ft_prod.pt는
    347MB이고 sha256이 수 초다.
    """
    from handwriting import bank_id

    model_digest = bank_id.file_digest(models_dir / "ft_prod.pt")
    code_sha = bank_id.code_version()
    return {
        side: (
            None
            if code_sha is None
            else bank_id.retrieval_fingerprint(
                bank_id.bank_rows(keys, labs, emb), model_digest, code_sha
            )
        )
        for side, (keys, labs, emb) in bank_arrays.items()
    }


def _write_score_artifacts(
    *,
    out: Path,
    summaries: dict[tuple[str, str], dict],
    meta: dict[str, int],
    per_pair: dict[tuple[str, str], list[dict]],
    scope: str,
    n_pairs: int,
    fingerprints: dict[str, str | None],
) -> tuple[str, Path, Path, Path]:
    """score.md·score.jsonl·score_meta.json을 계약된 순서로 쓴다(meta는 항상 마지막).

    §4 산출물 계약 — score.jsonl의 유일키는 (side, axis, crop_ref). meta를 마지막에 쓰는
    이유는 _atomic_write_text 문서 참조 — 중단된 재실행이 새 jsonl과 이전 meta를 짝짓는
    것을 막는다(같은 뱅크 재채점 시 지문이 일치해 stale 방어도 이를 못 잡는 경우의 방어).

    Returns:
        (md, score_md_path, score_jsonl_path, score_meta_path) — 호출부가 출력에 쓴다.
    """
    from handwriting import bank_id

    md = render_score_md(summaries, meta)
    md_path = out / "score.md"
    _atomic_write_text(md_path, md)
    score_path = out / "score.jsonl"
    _write_jsonl(
        score_path,
        [
            {"side": side, "axis": axis, **r}
            for side in ("before", "after")
            for axis in AXES
            for r in per_pair[(side, axis)]
        ],
    )
    meta_path = out / "score_meta.json"
    _write_json(
        meta_path,
        score_meta(
            generated_at=datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
            scope=scope,
            n_pairs=n_pairs,
            fingerprints=fingerprints,
            score_jsonl_sha256=bank_id.file_digest(score_path),
        ),
    )
    return md, md_path, score_path, meta_path


def cmd_score(args) -> None:
    """before/after 뱅크를 동일 채점기로 비교한다(임베딩은 1회만 계산해 공정 비교)."""
    crops_root = Path(require_env("SJMJ_DATA_DIR")) / "ocr_crops"
    models_dir = Path(require_env("SJMJ_ML_MODELS_DIR"))
    valid, _ = _desired_pairs(args.backend_env, args.scope)
    # 크롭이 없는 쌍은 임베딩할 수 없으므로 채점 대상에서 뺀다(뱅크 항목은 그대로 둔다).
    valid = [p for p in valid if (crops_root / f"{p['crop_ref']}.png").exists()]
    queries = prod_embed_fn(models_dir)([crops_root / f"{p['crop_ref']}.png" for p in valid])

    summaries: dict[tuple[str, str], dict] = {}
    per_pair: dict[tuple[str, str], list[dict]] = {}
    meta: dict[str, int] = {}
    bank_arrays: dict[str, tuple] = {}
    for side, path in (("before", args.before), ("after", args.after)):
        emb, labs, invs, keys = load_bank(path)
        # 4배열 길이 정합 — 분리 전 topk_excluding_self(커밋 cb9b1c7)가 sims/labs/keys를
        # 한자리에서 검증하던 것을 여기서 잇는다. excluded_indices는 keys↔invs, topk_dedup은
        # sims↔labs만 보므로 emb/lab만 n이고 inv/keys가 n-1인 뱅크는 두 검사를 모두 통과한다.
        validate_bank_arrays(emb, labs, invs, keys)
        # 유사도는 축과 무관하므로 한 번만 계산해 두 축이 공유한다(임베딩도 여전히 1회).
        sims = [(emb @ queries[i]).tolist() for i in range(len(valid))]
        for axis in AXES:
            recs = [
                score_one(
                    sims[i],
                    labs,
                    _axis_excluded(axis, keys, invs, p["crop_ref"]),
                    p["crop_ref"],
                    p["canonical_label"],
                )
                for i, p in enumerate(valid)
            ]
            summaries[(side, axis)] = score_summary(recs)
            per_pair[(side, axis)] = recs
        meta[f"bank_{side}"] = len(keys)
        bank_arrays[side] = (keys, labs, emb)

    md, md_path, score_path, meta_path = _write_score_artifacts(
        out=args.out,
        summaries=summaries,
        meta=meta,
        per_pair=per_pair,
        scope=args.scope,
        n_pairs=len(valid),
        fingerprints=_side_fingerprints(models_dir, bank_arrays),
    )
    print(md)
    print(f"저장: {md_path}\n저장: {score_path}\n저장: {meta_path}")


def main(argv: list[str] | None = None) -> None:
    """서브커맨드(plan/apply/score)를 파싱해 실행한다."""
    ap = argparse.ArgumentParser(prog="bank_update", description=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--backend-env",
        default=os.environ.get(ENV_BACKEND[0], ENV_BACKEND[1]),
        help="운영 DB 접속값 env 파일",
    )
    common.add_argument("--out", type=Path, default=DEFAULT_OUT, help="산출물 디렉터리")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan", parents=[common], help="diff 계산 → plan.jsonl")
    # M6: apply는 backend_env/out을 쓰지 않으므로(plan.jsonl만 소비) common을 상속하지 않는다
    # — 조용히 무시되던 옵션을 파서 단계에서 거부한다.
    p_apply = sub.add_parser("apply", help="plan.jsonl대로 뱅크 sync(백업 자동)")
    p_apply.add_argument("--plan", type=Path, required=True, help="plan.jsonl 경로")
    p_apply.add_argument(
        "--yes",
        action="store_true",
        help="제거가 포함된 plan 실행을 승인한다(되돌릴 수 없는 삭제 — 기본은 거부)",
    )
    p_score = sub.add_parser("score", parents=[common], help="before/after 뱅크 동일 채점기 비교")
    p_score.add_argument("--before", type=Path, required=True, help="갱신 전 뱅크(.npz.bak)")
    p_score.add_argument("--after", type=Path, required=True, help="갱신 후 뱅크(bank.npz)")
    p_score.add_argument(
        "--scope",
        choices=list(SCOPES),
        default="reviewed",
        help="채점 모집단 — reviewed(검수 완료만, 기본) | all(미검수 포함, 채점 전용)",
    )
    args = ap.parse_args(argv)

    {"plan": cmd_plan, "apply": cmd_apply, "score": cmd_score}[args.cmd](args)


if __name__ == "__main__":
    main()
