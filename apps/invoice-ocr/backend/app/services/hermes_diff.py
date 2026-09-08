"""hermes 위임 입력의 초안↔최종본 대조 — 순수 계층(stdlib 전용).

규칙은 ml/tools/agent_report.py의 순수부(norm/compare/summarize)와 같다. import로
공유하지 않고 재구현하는 근거는 spec §8.1(A안) — backend↔ml 결합이 CI 잡과 launchd 운영
venv까지 번지고 최상위 패키지명 `tools`가 백엔드 네임스페이스를 오염시킨다. "되돌릴 수 있는
중복 < 되돌리기 힘든 결합"으로 판단했고, 중복 위험은 공유 골든 픽스처
(apps/invoice-ocr/fixtures/hermes_compare_cases.json)를 두 트리의 테스트가 각자 먹어
CI 두 잡으로 방어한다.

DB·파일시스템에 닿지 않는다 — 입력은 dict, 출력은 NamedTuple/dict뿐이다.
"""

import re
from typing import NamedTuple

_WS = re.compile(r"\s+")
# items[3].name 같은 인덱스 붙은 필드명을 축 이름으로 접기 위한 패턴.
_ITEM_FIELD_RE = re.compile(r"^items\[\d+\]\.(name|supply)$")

# 목록 칩·필터가 쓰는 대조 축 5종. 이 순서가 곧 화면 표시 순서다.
MISMATCH_FIELDS = ("recipient", "item_count", "name", "supply", "grand_total")


class Comparison(NamedTuple):
    """초안 1건과 최종본 1건의 비교 결과.

    edited는 타임스탬프 파생이라 API·화면에 노출하지 않는다(spec §3.2) — 품목만 고친
    교정을 통째로 놓치기 때문이다. 그럼에도 계산을 남기는 이유는 공유 골든 픽스처가
    ml 쪽 Comparison과 필드 단위로 동치를 요구하기 때문이다.
    """

    recipient: bool
    item_count: bool
    name_hits: int
    supply_hits: int
    pairs: int
    grand_total: bool
    edited: bool
    mismatches: tuple[tuple[str, object, object], ...]


def norm(s: str | None) -> str:
    """공백 연속을 하나로 줄이고 양끝을 잘라 이름을 비교 가능하게 만든다."""
    return _WS.sub(" ", s or "").strip()


def compare(draft: dict, final: dict) -> Comparison:
    """초안과 최종본을 필드별로 대조한다.

    품목은 item_order 순서로 짝지어 ``min(len)`` 개만 센다 — 항목 수가 다르면 그 사실은
    ``item_count``로 따로 남기고, 쌍 비교는 겹치는 앞부분만 본다.

    발행일(issue_date)은 대조 축이 아니다. hermes 스킬 1단계가 발행일을 판독하지 않고
    항상 오늘로 채우며 실제 거래일은 사람이 /edit/{id}에서 넣는 것이 설계된 절차라,
    불일치로 세면 사실상 전건이 mismatch가 되어 상태 축이 붕괴한다(spec §3.2).

    Args:
        draft: hermes 초안 바디(POST /api/invoices 요청 바디 그대로).
        final: 최종본(headers + items[]). created_at/updated_at을 포함한다.

    Returns:
        필드별 일치 여부·쌍 적중 수·불일치 목록을 담은 Comparison.
    """
    mism: list[tuple[str, object, object]] = []
    recipient_ok = norm(draft.get("recipient")) == norm(final.get("recipient"))
    if not recipient_ok:
        mism.append(("recipient", draft.get("recipient"), final.get("recipient")))
    d_items = draft.get("items") or []
    f_items = final.get("items") or []
    count_ok = len(d_items) == len(f_items)
    if not count_ok:
        mism.append(("item_count", len(d_items), len(f_items)))
    pairs = min(len(d_items), len(f_items))
    name_hits = supply_hits = 0
    for i in range(pairs):
        if norm(d_items[i].get("name")) == norm(f_items[i].get("name")):
            name_hits += 1
        else:
            mism.append((f"items[{i}].name", d_items[i].get("name"), f_items[i].get("name")))
        if int(d_items[i].get("supply") or 0) == int(f_items[i].get("supply") or 0):
            supply_hits += 1
        else:
            mism.append((f"items[{i}].supply", d_items[i].get("supply"), f_items[i].get("supply")))
    gt_ok = int(draft.get("grand_total") or 0) == int(final.get("grand_total") or 0)
    if not gt_ok:
        mism.append(("grand_total", draft.get("grand_total"), final.get("grand_total")))
    return Comparison(
        recipient=recipient_ok,
        item_count=count_ok,
        name_hits=name_hits,
        supply_hits=supply_hits,
        pairs=pairs,
        grand_total=gt_ok,
        edited=str(final.get("updated_at")) != str(final.get("created_at")),
        mismatches=tuple(mism),
    )


def _rate(hit: int, total: int) -> float:
    """계산 비율 (분모 0이면 0.0)."""
    return hit / total if total else 0.0


def summarize(rows: list[tuple[int, Comparison]], missing: int = 0) -> dict:
    """비교 결과 목록을 건수·일치율로 집계한다(분모 0이면 0.0).

    name_rate·supply_rate만 쌍(pairs) 분모이고 나머지 rate는 건(count) 분모다.

    Args:
        rows: (invoice id, Comparison) 목록.
        missing: 최종본이 없는(사람이 지운) 초안 수.

    Returns:
        13키 집계 dict. edited 키는 호출부가 응답 직전에 떼어낸다(spec §3.2).
    """
    n = len(rows)
    cs = [c for _, c in rows]
    pairs = sum(c.pairs for c in cs)
    names = sum(c.name_hits for c in cs)
    supplies = sum(c.supply_hits for c in cs)
    untouched = sum(1 for c in cs if not c.mismatches)
    return {
        "count": n,
        "missing": missing,
        "edited": sum(1 for c in cs if c.edited),
        "recipient_rate": _rate(sum(c.recipient for c in cs), n),
        "item_count_rate": _rate(sum(c.item_count for c in cs), n),
        "pairs": pairs,
        "name_hits": names,
        "name_rate": _rate(names, pairs),
        "supply_hits": supplies,
        "supply_rate": _rate(supplies, pairs),
        "grand_total_rate": _rate(sum(c.grand_total for c in cs), n),
        "untouched": untouched,
        "untouched_rate": _rate(untouched, n),
    }


def _fold(field: str) -> str:
    """items[i].name 형태의 필드명을 축 이름으로 접는다."""
    m = _ITEM_FIELD_RE.match(field)
    return m.group(1) if m else field


def mismatch_fields(comparison: Comparison) -> list[str]:
    """불일치 필드명을 대조 축 5종으로 접어 중복 없이 선언 순서대로 낸다.

    ml 쪽 agent_learn.kind_of와 같은 접기다(items[i].name → name). 목록의 칩이
    "3행 품목명·5행 품목명"이 아니라 "품목명" 하나로 보여야 한 줄에 들어간다.
    """
    seen = {_fold(f) for f, _, _ in comparison.mismatches}
    return [f for f in MISMATCH_FIELDS if f in seen]


def status_of(comparison: Comparison | None) -> str:
    """초안 1건의 상태를 대조 결과로 판정한다(spec §3.2).

    판정 주체는 백엔드 단독이다 — 프론트가 다시 판정하면 필터 결과와 배지가 갈린다.

    Args:
        comparison: 최종본이 없으면 None.

    Returns:
        "deleted"(최종본 부재) / "match"(전 축 일치) / "mismatch".
    """
    if comparison is None:
        return "deleted"
    return "mismatch" if comparison.mismatches else "match"


def _ts(x: object) -> str:
    """타임스탬프를 ISO 8601 형식으로 정규화한다."""
    return str(x).replace(" ", "T")[:19]


def version_for(created_at: object, versions: list[dict]) -> int:
    """생성 시각에 활성이던 판독 지식 버전(발행 시각 ≤ created_at 중 최신).

    ml 쪽 agent_learn.version_for와 같은 규칙이되 표기가 다르다 — 거기는 "v2"/"none"
    문자열이고 여기는 정수이며 0이 "발행 이전"이다(spec §4.1의 version: int). 공유
    픽스처는 compare·summarize만 다루므로 이 표기 차이는 동치 강제 대상이 아니다.

    Args:
        created_at: 최종본의 생성 시각(datetime 또는 문자열).
        versions: versions.jsonl 전량(발행·거부 기록 모두).

    Returns:
        활성 버전 번호. 발행 이전이거나 발행 기록이 없으면 0.
    """
    ts = _ts(created_at)
    active = [v["version"] for v in versions if "rejected" not in v and v["published_at"] <= ts]
    return max(active, default=0)
