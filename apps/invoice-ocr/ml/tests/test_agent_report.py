"""tools.agent_report 순수 계층 단위테스트 (DB 비의존, 합성 데이터만)."""

import json
from pathlib import Path

from tools.agent_report import (
    FINALS_SQL,
    Comparison,
    compare,
    failures,
    load_drafts,
    norm,
    render,
    summarize,
)


def _draft(**over) -> dict:
    base = {
        "issue_date": "2026-09-05",
        "recipient": "○○상사",
        "grand_total": 165000,
        "items": [
            {"name": "각파이프 50x50", "supply": 150000},
        ],
    }
    base.update(over)
    return base


def _final(**over) -> dict:
    base = {
        "recipient": "○○상사",
        "grand_total": 165000,
        "created_at": "2026-09-05 10:00:00",
        "updated_at": "2026-09-05 10:00:00",
        "items": [{"name": "각파이프 50x50", "supply": 150000}],
    }
    base.update(over)
    return base


# --- 초안 로드 ---


def test_load_drafts_reads_id_from_filename(tmp_path: Path):
    (tmp_path / "12.draft.json").write_text(json.dumps(_draft()), encoding="utf-8")
    (tmp_path / "7.draft.json").write_text(json.dumps(_draft()), encoding="utf-8")
    (tmp_path / "12.jpg").write_bytes(b"")
    (tmp_path / "junk.json").write_text("{}", encoding="utf-8")
    drafts = load_drafts(tmp_path)
    assert [d.id for d in drafts] == [7, 12]
    assert drafts[0].body["recipient"] == "○○상사"


def test_load_drafts_empty_dir(tmp_path: Path):
    assert load_drafts(tmp_path) == []


# --- 정규화 ---


def test_norm_collapses_whitespace_and_none():
    assert norm("  각파이프  50x50 ") == "각파이프 50x50"
    assert norm(None) == ""


# --- 비교 ---


def test_compare_identical_is_all_match():
    c = compare(_draft(), _final())
    assert c == Comparison(
        recipient=True,
        item_count=True,
        name_hits=1,
        supply_hits=1,
        pairs=1,
        grand_total=True,
        edited=False,
        mismatches=(),
    )


def test_compare_detects_field_mismatches_and_edit():
    final = _final(
        recipient="△△상사",
        grand_total=99000,
        updated_at="2026-09-05 11:00:00",
        items=[{"name": "각파이프 50x50", "supply": 90000}, {"name": "앵글", "supply": 1000}],
    )
    c = compare(_draft(), final)
    assert c.recipient is False
    assert c.item_count is False
    assert c.name_hits == 1 and c.supply_hits == 0 and c.pairs == 1
    assert c.grand_total is False
    assert c.edited is True
    assert ("recipient", "○○상사", "△△상사") in c.mismatches
    assert ("items[0].supply", 150000, 90000) in c.mismatches
    assert ("item_count", 1, 2) in c.mismatches


def test_compare_pairs_by_min_length():
    draft = _draft(items=[{"name": "a", "supply": 1}, {"name": "b", "supply": 2}])
    final = _final(items=[{"name": "a", "supply": 1}])
    c = compare(draft, final)
    assert c.pairs == 1 and c.name_hits == 1 and c.supply_hits == 1


# --- 집계·렌더 ---


def test_summarize_rates():
    rows = [
        (1, compare(_draft(), _final())),
        (2, compare(_draft(), _final(recipient="x", updated_at="2026-09-05 11:00:00"))),
    ]
    s = summarize(rows)
    assert s["count"] == 2
    assert s["edited"] == 1
    assert s["recipient_rate"] == 0.5
    assert s["item_count_rate"] == 1.0
    assert s["name_rate"] == 1.0
    assert s["supply_rate"] == 1.0
    assert s["grand_total_rate"] == 1.0
    assert s["untouched_rate"] == 0.5


def test_summarize_empty():
    s = summarize([])
    assert s["count"] == 0 and s["recipient_rate"] == 0.0
    assert s["missing"] == 0


def test_summarize_missing_is_passed_through():
    s = summarize([], missing=3)
    assert s["missing"] == 3
    assert "| 최종본 없음(삭제) | 3 |" in render(s)


def test_render_markdown_table():
    md = render(summarize([(1, compare(_draft(), _final()))]))
    assert "| 품목명 일치율 | 100.0% (1/1) |" in md
    assert "| 건수 | 1 (사람 수정 0) |" in md
    assert "| 최종본 없음(삭제) | 0 |" in md


def test_failures_jsonl_rows():
    rows = [(5, compare(_draft(), _final(recipient="x")))]
    out = failures(rows)
    assert out == [{"id": 5, "field": "recipient", "draft": "○○상사", "final": "x"}]


# --- DB 글루 ---


def test_finals_sql_selects_required_columns():
    for col in (
        "i.recipient",
        "i.grand_total",
        "i.created_at",
        "i.updated_at",
        "t.item_order",
        "t.name",
        "t.supply",
    ):
        assert col in FINALS_SQL
    assert ":ids" in FINALS_SQL


def test_group_rows_builds_final_per_invoice():
    from tools.agent_report import group_rows

    rows = [
        {
            "id": 5,
            "recipient": "x",
            "grand_total": 10,
            "created_at": "c",
            "updated_at": "u",
            "item_order": 2,
            "name": "b",
            "supply": 2,
        },
        {
            "id": 5,
            "recipient": "x",
            "grand_total": 10,
            "created_at": "c",
            "updated_at": "u",
            "item_order": 1,
            "name": "a",
            "supply": 1,
        },
        {
            "id": 6,
            "recipient": "y",
            "grand_total": 0,
            "created_at": "c",
            "updated_at": "c",
            "item_order": None,
            "name": None,
            "supply": None,
        },
    ]
    g = group_rows(rows)
    assert g[5]["recipient"] == "x"
    assert [it["name"] for it in g[5]["items"]] == ["a", "b"]
    assert g[6]["items"] == []


def test_main_writes_report_and_failures(tmp_path: Path, monkeypatch):
    from tools import agent_report

    agent_uploads = tmp_path / "agent_uploads"
    agent_uploads.mkdir()
    (agent_uploads / "5.draft.json").write_text(json.dumps(_draft()), encoding="utf-8")
    monkeypatch.setattr(
        agent_report, "fetch_finals", lambda engine, ids: {5: _final(recipient="x")}
    )
    monkeypatch.setattr(agent_report, "_engine", lambda: object())
    out = tmp_path / "out"
    agent_report.main(["--data-dir", str(tmp_path), "--out", str(out)])
    md = (out / "report.md").read_text(encoding="utf-8")
    assert "| 건수 | 1 (사람 수정 0) |" in md
    lines = (out / "failures.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0]) == {"id": 5, "field": "recipient", "draft": "○○상사", "final": "x"}


def test_main_skips_drafts_without_final(tmp_path: Path, monkeypatch, capsys):
    from tools import agent_report

    agent_uploads = tmp_path / "agent_uploads"
    agent_uploads.mkdir()
    (agent_uploads / "9.draft.json").write_text(json.dumps(_draft()), encoding="utf-8")
    monkeypatch.setattr(agent_report, "fetch_finals", lambda engine, ids: {})
    monkeypatch.setattr(agent_report, "_engine", lambda: object())
    agent_report.main(["--data-dir", str(tmp_path), "--out", str(tmp_path / "o")])
    assert "최종본 없음(삭제됨): [9]" in capsys.readouterr().out
    assert "| 최종본 없음(삭제) | 1 |" in (tmp_path / "o" / "report.md").read_text(encoding="utf-8")
