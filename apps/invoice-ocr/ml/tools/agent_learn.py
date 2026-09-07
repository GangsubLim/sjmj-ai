"""hermes 위임 입력의 사용자 교정을 회수해 판독 지식(active.md)으로 누적하는 야간 배치 DAG.

extract: ``agent_uploads/{id}.draft.json`` ↔ 운영 DB 최종본 diff → ``corrections.jsonl``
         append(원장 해시로 멱등) → 결정적 절(1~3) 재생성 + 현재 LLM 절(4~5) 유지 → ``proposed.md``.
         신규 0건이면 stdout 마지막 줄 ``{"wakeAgent": false}``(hermes cron wake-gate).
publish: ``proposed.md`` 검증(헤딩 5개·결정적 절 무변조·상한·근거 id·금지어) →
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

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from tools.agent_report import (
    Comparison,
    Draft,
    compare,
    failures,
    fetch_finals,
    load_drafts,
    norm,
    render,
    summarize,
)

KNOWLEDGE_DIRNAME = "agent_knowledge"
TITLE = "# sjmj 판독 지식"
HEADINGS = (
    "## 확정 어휘",
    "## 금액 오류 통계",
    "## 데이터 현황",
    "## 거래처 프로필",
    "## 일반화 규칙",
)
DET_HEADINGS = HEADINGS[:3]
LLM_HEADINGS = HEADINGS[3:]
MAX_CHARS = 12000
MAX_PROFILE_LINES = 30
MAX_RULE_LINES = 20
FORBIDDEN = ("curl", "POST", "DELETE", "http://")
_ID_RE = re.compile(r"#(\d+)")
DIGIT_CLASSES = (
    ("prefix_drop", "앞자리 누락"),
    ("single_digit", "한 자리 혼동"),
    ("other", "기타"),
)
GRADES = (("자주(10회 이상)", 10), ("보통(3~9회)", 3), ("가끔(2회)", 0))


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


def _grade(cnt: int) -> str:
    return next(label for label, floor in GRADES if cnt >= floor)


def _vocab_body(vocab: dict) -> str:
    """품목은 등급 3단(등급 안 가나다순)·거래처는 목록. 빈도 숫자를 싣지 않아 집합이 바뀔 때만 절이 변한다."""
    by_grade: dict[str, list[str]] = {label: [] for label, _ in GRADES}
    for it in sorted(vocab.get("items", []), key=lambda x: x["item_name"]):
        unit = f" ({it['default_unit']})" if it.get("default_unit") else ""
        by_grade[_grade(it["cnt"])].append(f"- {it['item_name']}{unit}")
    lines = ["품목 — 최근 12개월 등장 등급"]
    if vocab.get("items"):
        for label, _ in GRADES:
            lines += [label, *(by_grade[label] or ["(없음)"])]
    else:
        lines.append("(없음)")
    comps = [f"- {c}" for c in vocab.get("companies", [])]
    return "\n".join([*lines, "", "거래처", *(comps or ["(없음)"])])


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
    """1~3절(확정 어휘·금액 오류 통계·데이터 현황)을 같은 입력이면 같은 문자열로 만든다."""
    return {
        HEADINGS[0]: _vocab_body(vocab),
        HEADINGS[1]: _amount_stats(corrections),
        HEADINGS[2]: _status(corrections, ledger_size),
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
    """제목 + 5절을 고정 순서로 조립한다. 비어 있는 절은 ``(없음)``."""
    parts = [TITLE, ""]
    for h in HEADINGS:
        body = (det.get(h) if h in DET_HEADINGS else llm.get(h, "")) or ""
        parts += [h, "", body.strip() or "(없음)", ""]
    return "\n".join(parts)


# --- 검증·발행 ---


def validate_proposed(md: str, det_expected: dict[str, str], known_ids: set[int]) -> list[str]:
    """proposed.md 검증 — 위반 사유 목록(비어 있으면 통과).

    헤딩 5개 정확·순서, 결정적 절 무변조, LLM 절 줄 상한, 불릿마다 근거 id, 금지어, 전체 크기.
    """
    errors: list[str] = []
    if len(md) > MAX_CHARS:
        errors.append(f"전체 {len(md)}자 > {MAX_CHARS}자")
    try:
        sections = split_sections(md)
    except ValueError as exc:
        return [str(exc)]
    heads = [line for line in md.splitlines() if line.startswith("## ")]
    if heads != list(HEADINGS):
        errors.append(f"헤딩 불일치: {heads}")
    for h in DET_HEADINGS:
        if sections.get(h, "") != det_expected[h].strip():
            errors.append(f"결정적 절 변조: {h}")
    for h, cap in ((HEADINGS[3], MAX_PROFILE_LINES), (HEADINGS[4], MAX_RULE_LINES)):
        lines = [line for line in sections.get(h, "").splitlines() if line.strip()]
        if len(lines) > cap:
            errors.append(f"{h} {len(lines)}줄 > {cap}줄")
        for line in lines:
            if not line.lstrip().startswith("- "):
                continue
            ids = {int(x) for x in _ID_RE.findall(line)}
            if not ids:
                errors.append(f"{h} 근거 id 없음: {line[:40]}")
            elif not ids <= known_ids:
                errors.append(f"{h} 미지의 근거 id {sorted(ids - known_ids)}: {line[:40]}")
    llm_text = "\n".join(sections.get(h, "") for h in LLM_HEADINGS)
    errors.extend(f"금지어 {w!r}" for w in FORBIDDEN if w in llm_text)
    return errors


class PublishResult(NamedTuple):
    """발행 결과 — version이 None이면 거부(reason에 사유)."""

    version: int | None
    reason: str


def load_versions(path: Path) -> list[dict]:
    """versions.jsonl 전량(발행·거부 기록 모두)."""
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def current_version(versions: list[dict]) -> int:
    """거부 기록을 제외한 최신 발행 버전(없으면 0)."""
    return max((v["version"] for v in versions if "rejected" not in v), default=0)


def _append_jsonl(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _vocab_items(md: str) -> int:
    """``## 확정 어휘``의 품목 불릿 수(``거래처`` 소제목 앞까지)."""
    body = split_sections(md).get(HEADINGS[0], "")
    items_part = body.split("\n거래처", 1)[0]
    return sum(1 for line in items_part.splitlines() if line.startswith("- "))


def publish(
    kdir: Path, det_expected: dict[str, str], known_ids: set[int], corrections_count: int, now: str
) -> PublishResult:
    """proposed.md를 검증해 v{N+1}.md·active.md로 발행한다. 거부 시 active 무변경 + 사유 기록."""
    proposed = kdir / "proposed.md"
    if not proposed.exists():
        return PublishResult(None, "proposed.md 없음")
    md = proposed.read_text(encoding="utf-8")
    versions_path = kdir / "versions.jsonl"
    cur = current_version(load_versions(versions_path))
    errors = validate_proposed(md, det_expected, known_ids)
    if errors:
        reason = "; ".join(errors)
        _append_jsonl(versions_path, {"version": cur, "published_at": now, "rejected": reason})
        return PublishResult(None, reason)
    active = kdir / "active.md"
    n = cur + 1
    (kdir / "knowledge").mkdir(exist_ok=True)
    (kdir / "knowledge" / f"v{n}.md").write_text(md, encoding="utf-8")
    tmp = kdir / "active.md.tmp"
    tmp.write_text(md, encoding="utf-8")
    os.replace(tmp, active)
    _append_jsonl(
        versions_path,
        {"version": n, "published_at": now, "corrections_through": corrections_count},
    )
    return PublishResult(n, "published")


# --- 버전 매핑 · 버전별 리포트 ---


def _ts(x: object) -> str:
    return str(x).replace(" ", "T")[:19]


def version_for(created_at: object, versions: list[dict]) -> str:
    """invoice 생성 시각에 활성이던 지식 버전(발행 시각 ≤ created_at 중 최신), 없으면 ``none``."""
    ts = _ts(created_at)
    active = [v["version"] for v in versions if "rejected" not in v and v["published_at"] <= ts]
    return f"v{max(active)}" if active else "none"


def _version_key(ver: str) -> int:
    return -1 if ver == "none" else int(ver[1:])


def render_by_version(groups: dict[str, dict]) -> str:
    """버전→summarize() dict를 버전별 일치율 표로 만든다(none 먼저, 이후 버전 오름차순)."""
    lines = [
        "## 지식 버전별 일치율",
        "",
        "| 버전 | 건수 | 품목명 | 금액 | 무수정률 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for ver in sorted(groups, key=_version_key):
        s = groups[ver]
        lines.append(
            f"| {ver} | {s['count']} "
            f"| {s['name_rate'] * 100:.1f}% ({s['name_hits']}/{s['pairs']}) "
            f"| {s['supply_rate'] * 100:.1f}% ({s['supply_hits']}/{s['pairs']}) "
            f"| {s['untouched_rate'] * 100:.1f}% ({s['untouched']}/{s['count']}) |"
        )
    return "\n".join(lines) + "\n"


# --- 명령 ---


def _kdir(data_dir: Path) -> Path:
    kdir = data_dir / KNOWLEDGE_DIRNAME
    kdir.mkdir(parents=True, exist_ok=True)
    return kdir


def _drafts(data_dir: Path) -> list[Draft]:
    up = data_dir / "agent_uploads"
    return load_drafts(up) if up.is_dir() else []


def cmd_extract(data_dir: Path, finals_fn, vocab_fn, now: str) -> dict:
    """초안↔최종본 diff → corrections.jsonl·ledger.json·vocab_snapshot.json·proposed.md. 요약 dict 반환."""
    kdir = _kdir(data_dir)
    drafts = _drafts(data_dir)
    finals = finals_fn([d.id for d in drafts]) if drafts else {}
    new, ledger = diff_new(drafts, finals, load_ledger(kdir / "ledger.json"), now)
    append_corrections(kdir / "corrections.jsonl", new)
    save_ledger(kdir / "ledger.json", ledger)
    vocab = vocab_fn()
    (kdir / "vocab_snapshot.json").write_text(
        json.dumps(vocab, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    corrections = load_corrections(kdir / "corrections.jsonl")
    det = render_deterministic(corrections, vocab, len(ledger))
    active = kdir / "active.md"
    llm = split_sections(active.read_text(encoding="utf-8")) if active.exists() else {}
    (kdir / "proposed.md").write_text(assemble(det, llm), encoding="utf-8")
    by_kind: dict[str, int] = {}
    for c in new:
        by_kind[c.kind] = by_kind.get(c.kind, 0) + 1
    return {
        "new": len(new),
        "by_kind": by_kind,
        "proposed": str(kdir / "proposed.md"),
        "active_version": current_version(load_versions(kdir / "versions.jsonl")),
    }


def _det_from_disk(kdir: Path) -> tuple[dict[str, str], list[Correction]]:
    corrections = load_corrections(kdir / "corrections.jsonl")
    snap = kdir / "vocab_snapshot.json"
    vocab = (
        json.loads(snap.read_text(encoding="utf-8"))
        if snap.exists()
        else {"items": [], "companies": []}
    )
    ledger = load_ledger(kdir / "ledger.json")
    return render_deterministic(corrections, vocab, len(ledger)), corrections


def cmd_publish(data_dir: Path, now: str) -> PublishResult:
    """디스크의 corrections·스냅샷·원장으로 결정적 절을 재생성해 proposed.md를 검증·발행한다(DB 무접촉)."""
    kdir = _kdir(data_dir)
    det, corrections = _det_from_disk(kdir)
    return publish(kdir, det, {c.invoice_id for c in corrections}, len(corrections), now)


def cmd_report(data_dir: Path, out: Path, finals_fn) -> str:
    """agent_report 전체 표 + 지식 버전별 표를 out/report.md·failures.jsonl로 쓴다."""
    kdir = _kdir(data_dir)
    drafts = _drafts(data_dir)
    finals = finals_fn([d.id for d in drafts]) if drafts else {}
    versions = load_versions(kdir / "versions.jsonl")
    rows = [(d.id, compare(d.body, finals[d.id])) for d in drafts if d.id in finals]
    missing = sum(1 for d in drafts if d.id not in finals)
    groups: dict[str, list] = {}
    for jid, c in rows:
        groups.setdefault(version_for(finals[jid]["created_at"], versions), []).append((jid, c))
    md = render(summarize(rows, missing=missing)) + "\n"
    md += render_by_version({ver: summarize(rs) for ver, rs in groups.items()})
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(md, encoding="utf-8")
    with (out / "failures.jsonl").open("w", encoding="utf-8") as f:
        for row in failures(rows):
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return md


# --- DB 글루 (SQLAlchemy는 함수 안에서만 import — 코어 venv 안전) ---

ITEMS_SQL = """
SELECT TRIM(ii.name) AS item_name, COUNT(*) AS cnt, MAX(s.default_unit) AS default_unit
FROM invoice_items ii
JOIN invoices i ON i.id = ii.invoice_id
LEFT JOIN item_suggestions s ON s.item_name = TRIM(ii.name)
WHERE i.issue_date >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH) AND TRIM(ii.name) <> ''
GROUP BY TRIM(ii.name)
HAVING cnt >= 2
ORDER BY item_name
"""
COMPANIES_SQL = "SELECT company_name FROM company_suggestions ORDER BY company_name"


def fetch_vocab(engine) -> dict:
    """품목은 최근 12개월 invoice_items 빈도(2회 이상)+기본단위, 거래처는 자동완성 사전 전량."""
    from sqlalchemy import text

    with engine.connect() as conn:
        items = [
            {"item_name": r.item_name, "cnt": int(r.cnt), "default_unit": r.default_unit}
            for r in conn.execute(text(ITEMS_SQL))
        ]
        companies = [r.company_name for r in conn.execute(text(COMPANIES_SQL))]
    return {"items": items, "companies": companies}


def _engine():
    from worker.db import build_engine

    return build_engine()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _publish_line(r: PublishResult, kdir: Path) -> str:
    if r.version is None:
        return f"rejected: {r.reason}"
    md = (kdir / "active.md").read_text(encoding="utf-8")
    rules = [
        line
        for line in split_sections(md).get(HEADINGS[4], "").splitlines()
        if line.lstrip().startswith("- ")
    ]
    return f"published v{r.version} · 어휘 {_vocab_items(md)}종 · 규칙 {len(rules)}줄"


def main(argv: list[str] | None = None) -> None:
    """extract / publish / report 서브커맨드. extract·publish는 항상 종료코드 0."""
    ap = argparse.ArgumentParser(
        prog="agent_learn",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("SJMJ_DATA_DIR", "")),
        help="SJMJ_DATA_DIR (agent_uploads/·agent_knowledge/의 부모). 기본값 env",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser(
        "extract",
        parents=[common],
        help="초안↔최종본 diff → corrections·proposed.md (wake-gate stdout)",
    )
    sub.add_parser("publish", parents=[common], help="proposed.md 검증·발행")
    rp = sub.add_parser("report", parents=[common], help="버전별 일치율 리포트")
    rp.add_argument("--out", type=Path, default=Path("report/agent_report"))
    args = ap.parse_args(argv)

    if args.cmd == "extract":
        try:
            engine = _engine()
            summary = cmd_extract(
                args.data_dir,
                lambda ids: fetch_finals(engine, ids),
                lambda: fetch_vocab(engine),
                _now(),
            )
        except Exception as exc:  # cron 실패 스트릭 방지 — 사유는 stderr, 게이트는 닫음
            print(f"extract 실패: {exc}", file=sys.stderr)
            print(json.dumps({"wakeAgent": False}))
            return
        print(json.dumps(summary, ensure_ascii=False))
        if summary["new"] == 0:
            print(json.dumps({"wakeAgent": False}))
    elif args.cmd == "publish":
        print(_publish_line(cmd_publish(args.data_dir, _now()), _kdir(args.data_dir)))
    else:
        engine = _engine()
        cmd_report(args.data_dir, args.out, lambda ids: fetch_finals(engine, ids))
        print(f"→ {args.out / 'report.md'}")


if __name__ == "__main__":
    main()
