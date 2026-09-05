"""hermes 위임 거래명세서 입력의 초안↔최종본 일치율 리포트.

hermes 스킬(apps/invoice-ocr/agent/hermes-skills/sjmj/invoice-entry)이 남긴
``$SJMJ_DATA_DIR/agent_uploads/{id}.draft.json``(POST /api/invoices 바디 그대로)을
운영 DB의 invoices·invoice_items(사람이 /edit/{id}에서 고친 최종본)와 id로 조인해
수신처·항목 수·품목명·금액·합계 일치율과 무수정률을 마크다운으로 낸다. 삭제된(최종본
없는) 초안 수도 표에 남긴다. 불일치 건은 failures.jsonl로 떨어뜨려
``agent_uploads/{id}.jpg``와 바로 대조한다.

코어 규약: 순수 계층(load/norm/compare/summarize/render/failures)은 stdlib 전용이고
DB 글루(fetch_finals)만 SQLAlchemy를 함수 안에서 끌어온다. macmini에서 직접 실행하며
접속값은 DB_* env로만 주입한다(worker.db.build_engine 재사용).

Usage:
    uv run python -m tools.agent_report --data-dir /Users/submini/sjmj-ai-data \
        --out report/agent_report
"""

import argparse
import json
import os
import re
from pathlib import Path
from typing import NamedTuple

_DRAFT_RE = re.compile(r"^(\d+)\.draft\.json$")
_WS = re.compile(r"\s+")


class Draft(NamedTuple):
    """초안 1건 — 파일명의 invoice id와 POST 바디."""

    id: int
    body: dict


class Comparison(NamedTuple):
    """초안 1건과 최종본 1건의 비교 결과."""

    recipient: bool
    item_count: bool
    name_hits: int
    supply_hits: int
    pairs: int
    grand_total: bool
    edited: bool
    mismatches: tuple[tuple[str, object, object], ...]


def load_drafts(data_dir: Path) -> list[Draft]:
    """``{id}.draft.json``만 골라 id 오름차순으로 읽는다(그 외 파일은 무시)."""
    out = []
    for p in data_dir.iterdir():
        m = _DRAFT_RE.match(p.name)
        if m:
            out.append(Draft(int(m.group(1)), json.loads(p.read_text(encoding="utf-8"))))
    return sorted(out, key=lambda d: d.id)


def norm(s: str | None) -> str:
    """공백 연속을 하나로 줄이고 양끝을 잘라 이름을 비교 가능하게 만든다."""
    return _WS.sub(" ", s or "").strip()


def compare(draft: dict, final: dict) -> Comparison:
    """초안과 최종본을 필드별로 대조한다.

    품목은 item_order 순서로 짝지어 ``min(len)`` 개만 센다 — 항목 수가 다르면 그 사실은
    ``item_count``로 따로 남기고, 쌍 비교는 겹치는 앞부분만 본다.
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
    edited = str(final.get("updated_at")) != str(final.get("created_at"))
    return Comparison(
        recipient=recipient_ok,
        item_count=count_ok,
        name_hits=name_hits,
        supply_hits=supply_hits,
        pairs=pairs,
        grand_total=gt_ok,
        edited=edited,
        mismatches=tuple(mism),
    )


def _rate(hit: int, total: int) -> float:
    return hit / total if total else 0.0


def summarize(rows: list[tuple[int, Comparison]], missing: int = 0) -> dict:
    """비교 결과 목록을 건수·일치율로 집계한다(분모 0이면 0.0). missing은 최종본 없는 초안 수."""
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


def render(s: dict) -> str:
    """집계를 마크다운 표로 만든다."""
    n = s["count"]

    def pct(rate: float, hit: int, total: int) -> str:
        return f"{rate * 100:.1f}% ({hit}/{total})"

    lines = [
        "# hermes 위임 입력 초안↔최종본 일치율",
        "",
        "| 지표 | 값 |",
        "| --- | --- |",
        f"| 건수 | {n} (사람 수정 {s['edited']}) |",
        f"| 최종본 없음(삭제) | {s['missing']} |",
        f"| 수신처 일치율 | {pct(s['recipient_rate'], round(s['recipient_rate'] * n), n)} |",
        f"| 항목 수 일치율 | {pct(s['item_count_rate'], round(s['item_count_rate'] * n), n)} |",
        f"| 품목명 일치율 | {pct(s['name_rate'], s['name_hits'], s['pairs'])} |",
        f"| 금액(supply) 일치율 | {pct(s['supply_rate'], s['supply_hits'], s['pairs'])} |",
        f"| 합계 일치율 | {pct(s['grand_total_rate'], round(s['grand_total_rate'] * n), n)} |",
        f"| 무수정률 | {pct(s['untouched_rate'], s['untouched'], n)} |",
        "",
    ]
    return "\n".join(lines)


def failures(rows: list[tuple[int, Comparison]]) -> list[dict]:
    """불일치 항목을 jsonl 한 줄 단위 dict로 편다."""
    return [
        {"id": jid, "field": f, "draft": d, "final": v}
        for jid, c in rows
        for f, d, v in c.mismatches
    ]


# --- DB 글루 (SQLAlchemy는 함수 안에서만 import — 코어 venv 안전) ---

FINALS_SQL = """
SELECT i.id, i.recipient, i.grand_total, i.created_at, i.updated_at,
       t.item_order, t.name, t.supply
FROM invoices i
LEFT JOIN invoice_items t ON t.invoice_id = i.id
WHERE i.id IN :ids
ORDER BY i.id, t.item_order
"""


def group_rows(rows: list[dict]) -> dict[int, dict]:
    """조인 행을 invoice id별 최종본(items는 item_order 오름차순)으로 묶는다."""
    out: dict[int, dict] = {}
    for r in rows:
        inv = out.setdefault(
            r["id"],
            {
                "recipient": r["recipient"],
                "grand_total": r["grand_total"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "items": [],
            },
        )
        if r["item_order"] is not None:
            inv["items"].append(
                {"item_order": r["item_order"], "name": r["name"], "supply": r["supply"]}
            )
    for inv in out.values():
        inv["items"].sort(key=lambda it: it["item_order"])
    return out


def fetch_finals(engine, ids: list[int]) -> dict[int, dict]:
    """운영 DB에서 id 목록의 최종본을 읽는다(없는 id는 결과에서 빠진다)."""
    if not ids:
        return {}
    from sqlalchemy import bindparam, text

    stmt = text(FINALS_SQL).bindparams(bindparam("ids", expanding=True))
    with engine.connect() as conn:
        rows = [dict(r._mapping) for r in conn.execute(stmt, {"ids": ids})]
    return group_rows(rows)


def _engine():
    from worker.db import build_engine

    return build_engine()


def main(argv: list[str] | None = None) -> None:
    """초안 디렉토리와 운영 DB를 조인해 report.md·failures.jsonl을 쓴다."""
    ap = argparse.ArgumentParser(prog="agent_report", description=__doc__)
    ap.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("SJMJ_DATA_DIR", "")),
        help="SJMJ_DATA_DIR (agent_uploads/의 부모). 기본값 env",
    )
    ap.add_argument("--out", type=Path, default=Path("report/agent_report"))
    args = ap.parse_args(argv)

    drafts = load_drafts(args.data_dir / "agent_uploads")
    finals = fetch_finals(_engine(), [d.id for d in drafts])
    missing = [d.id for d in drafts if d.id not in finals]
    rows = [(d.id, compare(d.body, finals[d.id])) for d in drafts if d.id in finals]

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.md").write_text(
        render(summarize(rows, missing=len(missing))), encoding="utf-8"
    )
    with (args.out / "failures.jsonl").open("w", encoding="utf-8") as f:
        for row in failures(rows):
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"초안 {len(drafts)}건 · 조인 {len(rows)}건 → {args.out / 'report.md'}")
    if missing:
        print(f"최종본 없음(삭제됨): {missing}")


if __name__ == "__main__":
    main()
