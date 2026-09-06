"""hermes 위임 입력의 사용자 교정을 회수해 판독 지식(active.md)으로 누적하는 야간 배치 DAG.

extract: ``agent_uploads/{id}.draft.json`` ↔ 운영 DB 최종본 diff → ``corrections.jsonl``
         append(원장 해시로 멱등) → 결정적 절(1~4) 재생성 + 현재 LLM 절(5~6) 유지 → ``proposed.md``.
         신규 0건이면 stdout 마지막 줄 ``{"wakeAgent": false}``(hermes cron wake-gate).
publish: ``proposed.md`` 검증(헤딩 6개·결정적 절 무변조·상한·근거 id·금지어) →
         ``knowledge/v{N}.md`` + ``active.md`` 교체 + ``versions.jsonl`` 기록.
report:  ``tools.agent_report``에 지식 버전 축을 더해 버전별 일치율 표.

코어 규약: 순수 계층은 stdlib 전용이고 DB 글루(``fetch_vocab``·``_engine``)만 함수 안에서
SQLAlchemy를 끌어온다. macmini에서 ``PYTHON_BIN``(운영 venv)으로 실행하며 접속값은 DB_*
env로만 주입한다(``worker.db.build_engine`` 재사용).

Usage:
    python -m tools.agent_learn extract --data-dir /Users/submini/sjmj-ai-data
    python -m tools.agent_learn publish --data-dir /Users/submini/sjmj-ai-data
    python -m tools.agent_learn report  --data-dir /Users/submini/sjmj-ai-data --out /tmp/agent_report
"""

import hashlib
import json
import re
from pathlib import Path
from typing import NamedTuple

from tools.agent_report import Comparison, Draft, compare, norm

KNOWLEDGE_DIRNAME = "agent_knowledge"


class Correction(NamedTuple):
    """교정 1건 — 초안 값과 사람이 확정한 최종 값."""

    invoice_id: int
    field: str
    kind: str
    draft: object
    final: object
    digit_class: str | None
    observed_at: str


_ITEM_FIELD_RE = re.compile(r"^items\[\d+\]\.(name|supply)$")


def kind_of(field: str) -> str:
    """mismatch 필드명을 kind로 접는다(items[i].name→name, items[i].supply→supply)."""
    m = _ITEM_FIELD_RE.match(field)
    return m.group(1) if m else field


def digit_class(draft: object, final: object) -> str:
    """금액 오독 유형 — 접미 포함이면 prefix_drop, 같은 자릿수에 한 자리만 다르면 single_digit."""
    d, f = str(abs(int(draft or 0))), str(abs(int(final or 0)))
    if d != f and (d.endswith(f) or f.endswith(d)):
        return "prefix_drop"
    if len(d) == len(f) and sum(a != b for a, b in zip(d, f, strict=True)) == 1:
        return "single_digit"
    return "other"


def _text(v: object) -> str:
    return norm(v if isinstance(v, str) else ("" if v is None else str(v)))


def final_hash(final: dict) -> str:
    """최종본 내용(수신처·합계·품목명·금액) 해시 — 시각·공백은 무시."""
    key = [
        _text(final.get("recipient")),
        int(final.get("grand_total") or 0),
        [(_text(i.get("name")), int(i.get("supply") or 0)) for i in final.get("items") or []],
    ]
    return hashlib.sha1(json.dumps(key, ensure_ascii=False).encode("utf-8")).hexdigest()


def records_from(invoice_id: int, comparison: Comparison, observed_at: str) -> list[Correction]:
    """Comparison.mismatches를 Correction 레코드로 편다."""
    out = []
    for field, d, f in comparison.mismatches:
        k = kind_of(field)
        out.append(
            Correction(
                invoice_id,
                field,
                k,
                d,
                f,
                digit_class(d, f) if k == "supply" else None,
                observed_at,
            )
        )
    return out


def diff_new(
    drafts: list[Draft], finals: dict[int, dict], ledger: dict[int, str], observed_at: str
) -> tuple[list[Correction], dict[int, str]]:
    """원장에 없거나 최종본 해시가 바뀐 초안만 compare해 신규 레코드와 갱신된 원장을 낸다(원본 불변)."""
    new: list[Correction] = []
    updated = dict(ledger)
    for d in drafts:
        final = finals.get(d.id)
        if final is None:
            continue
        h = final_hash(final)
        if updated.get(d.id) == h:
            continue
        new.extend(records_from(d.id, compare(d.body, final), observed_at))
        updated[d.id] = h
    return new, updated


def load_ledger(path: Path) -> dict[int, str]:
    """``{invoice_id: final_hash}`` 원장을 읽는다(없으면 빈 dict)."""
    if not path.exists():
        return {}
    return {int(k): v for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def save_ledger(path: Path, ledger: dict[int, str]) -> None:
    """원장을 id 오름차순으로 기록한다."""
    path.write_text(
        json.dumps({str(k): v for k, v in sorted(ledger.items())}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def load_corrections(path: Path) -> list[Correction]:
    """corrections.jsonl 전량을 읽는다(없으면 빈 목록)."""
    if not path.exists():
        return []
    return [
        Correction(**json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def append_corrections(path: Path, records: list[Correction]) -> None:
    """레코드를 jsonl 한 줄씩 append한다."""
    with path.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r._asdict(), ensure_ascii=False) + "\n")
