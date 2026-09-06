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
TITLE = "# sjmj 판독 지식"
HEADINGS = (
    "## 교정 사전",
    "## 확정 어휘",
    "## 금액 오류 통계",
    "## 데이터 현황",
    "## 거래처 프로필",
    "## 일반화 규칙",
)
DET_HEADINGS = HEADINGS[:4]
LLM_HEADINGS = HEADINGS[4:]
DIGIT_CLASSES = (
    ("prefix_drop", "앞자리 누락"),
    ("single_digit", "한 자리 혼동"),
    ("other", "기타"),
)


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


# --- 결정적 절 렌더 · 절 분리/조립 ---


def _cell(v: object) -> str:
    return _text(v).replace("|", "\\|")


def _lexicon(corrections: list[Correction]) -> str:
    pairs: dict[tuple[str, str], list[int]] = {}
    for c in corrections:
        if c.kind in ("name", "recipient"):
            pairs.setdefault((_cell(c.draft), _cell(c.final)), []).append(c.invoice_id)
    if not pairs:
        return "(없음)"
    rows = sorted(pairs.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    lines = ["| 오독 | 정답 | 횟수 | 근거 id |", "| --- | --- | --- | --- |"]
    for (d, f), ids in rows:
        refs = " ".join(f"#{i}" for i in sorted(set(ids)))
        lines.append(f"| {d} | {f} | {len(ids)} | {refs} |")
    return "\n".join(lines)


def _vocab_body(vocab: dict) -> str:
    items = [
        f"- {it['item_name']}" + (f" ({it['default_unit']})" if it.get("default_unit") else "")
        for it in vocab.get("items", [])
    ]
    comps = [f"- {c}" for c in vocab.get("companies", [])]
    return "\n".join(["품목", *(items or ["(없음)"]), "", "거래처", *(comps or ["(없음)"])])


def _amount_stats(corrections: list[Correction]) -> str:
    sup = [c for c in corrections if c.kind == "supply"]
    lines = [
        f"- {label}({key}): {sum(1 for c in sup if c.digit_class == key)}"
        for key, label in DIGIT_CLASSES
    ]
    if sup:
        lines += ["", "최근 예시"] + [
            f"- #{c.invoice_id} {c.field}: {c.draft} → {c.final} ({c.digit_class})"
            for c in sup[-3:]
        ]
    return "\n".join(lines)


def _status(corrections: list[Correction], ledger_size: int) -> str:
    last = max((c.observed_at for c in corrections), default="-")
    return "\n".join(
        [
            f"- 누적 교정: {len(corrections)}건",
            f"- 초안(원장): {ledger_size}건",
            f"- 마지막 교정 관측: {last}",
        ]
    )


def render_deterministic(
    corrections: list[Correction], vocab: dict, ledger_size: int
) -> dict[str, str]:
    """1~4절(교정 사전·확정 어휘·금액 오류 통계·데이터 현황)을 같은 입력이면 같은 문자열로 만든다."""
    return {
        HEADINGS[0]: _lexicon(corrections),
        HEADINGS[1]: _vocab_body(vocab),
        HEADINGS[2]: _amount_stats(corrections),
        HEADINGS[3]: _status(corrections, ledger_size),
    }


def split_sections(md: str) -> dict[str, str]:
    """``## `` 헤딩 줄 기준으로 본문을 가른다(본문은 strip). 순서 검증은 validate 담당, 중복은 즉시 오류."""
    sections: dict[str, str] = {}
    cur: str | None = None
    buf: list[str] = []
    for line in md.splitlines():
        if line.startswith("## "):
            if cur is not None:
                sections[cur] = "\n".join(buf).strip()
            if line in sections:
                raise ValueError(f"중복 헤딩: {line}")
            cur, buf = line, []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        sections[cur] = "\n".join(buf).strip()
    return sections


def assemble(det: dict[str, str], llm: dict[str, str]) -> str:
    """제목 + 6절을 고정 순서로 조립한다. 비어 있는 절은 ``(없음)``."""
    parts = [TITLE, ""]
    for h in HEADINGS:
        body = (det.get(h) if h in DET_HEADINGS else llm.get(h, "")) or ""
        parts += [h, "", body.strip() or "(없음)", ""]
    return "\n".join(parts)
