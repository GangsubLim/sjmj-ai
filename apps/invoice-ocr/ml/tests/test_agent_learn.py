"""tools.agent_learn 순수 계층 단위테스트 (DB 비의존, 합성 데이터만)."""

import json
from pathlib import Path

from tools.agent_learn import (
    Correction,
    append_corrections,
    diff_new,
    digit_class,
    final_hash,
    kind_of,
    load_corrections,
    load_ledger,
    records_from,
    save_ledger,
)
from tools.agent_report import Draft, compare


def _draft(**over) -> dict:
    base = {
        "recipient": "테스트",
        "grand_total": 165000,
        "items": [{"name": "킹핀교환", "supply": 150000}],
    }
    base.update(over)
    return base


def _final(**over) -> dict:
    base = {
        "recipient": "테스트",
        "grand_total": 165000,
        "created_at": "2026-09-05 10:00:00",
        "updated_at": "2026-09-05 10:00:00",
        "items": [{"name": "히타", "supply": 150000}],
    }
    base.update(over)
    return base


# --- 레코드 변환 ---


def test_kind_of_maps_item_fields():
    assert kind_of("items[2].name") == "name"
    assert kind_of("items[0].supply") == "supply"
    assert kind_of("recipient") == "recipient"
    assert kind_of("item_count") == "item_count"
    assert kind_of("grand_total") == "grand_total"


def test_digit_class_prefix_drop_single_digit_other():
    assert digit_class(20000, 120000) == "prefix_drop"
    assert digit_class(560000, 60000) == "prefix_drop"
    assert digit_class(180000, 160000) == "single_digit"
    assert digit_class(98000, 18400) == "other"
    assert digit_class(None, 0) == "other"


def test_records_from_sets_kind_and_digit_class():
    c = compare(
        _draft(items=[{"name": "킹핀교환", "supply": 20000}]),
        _final(items=[{"name": "히타", "supply": 120000}]),
    )
    recs = records_from(573, c, "2026-09-06T03:00:00")
    assert [r.kind for r in recs] == ["name", "supply"]
    assert recs[0] == Correction(
        573, "items[0].name", "name", "킹핀교환", "히타", None, "2026-09-06T03:00:00"
    )
    assert recs[1].digit_class == "prefix_drop"
    assert recs[1].observed_at == "2026-09-06T03:00:00"


def test_final_hash_ignores_timestamps_and_whitespace():
    a = _final(updated_at="2026-09-06 00:00:00")
    b = _final(items=[{"name": " 히타 ", "supply": 150000}])
    c = _final(items=[{"name": "히타", "supply": 150001}])
    assert final_hash(a) == final_hash(b)
    assert final_hash(a) != final_hash(c)


# --- 원장 diff ---


def test_diff_new_skips_unchanged_and_reextracts_on_hash_change():
    drafts = [Draft(573, _draft()), Draft(574, _draft())]
    finals = {573: _final(), 574: _final(items=[{"name": "킹핀교환", "supply": 150000}])}
    new, ledger = diff_new(drafts, finals, {}, "t1")
    assert [r.invoice_id for r in new] == [573]
    assert set(ledger) == {573, 574}

    again, ledger2 = diff_new(drafts, finals, ledger, "t2")
    assert again == []
    assert ledger2 == ledger

    finals[574] = _final(items=[{"name": "스프링", "supply": 150000}])
    third, ledger3 = diff_new(drafts, finals, ledger2, "t3")
    assert [(r.invoice_id, r.final) for r in third] == [(574, "스프링")]
    assert ledger3[574] != ledger2[574]


def test_diff_new_ignores_drafts_without_final():
    new, ledger = diff_new([Draft(9, _draft())], {}, {}, "t")
    assert new == [] and ledger == {}


# --- 파일 I/O ---


def test_ledger_roundtrip(tmp_path: Path):
    p = tmp_path / "ledger.json"
    assert load_ledger(p) == {}
    save_ledger(p, {574: "b", 573: "a"})
    assert load_ledger(p) == {573: "a", 574: "b"}
    assert list(json.loads(p.read_text(encoding="utf-8"))) == ["573", "574"]


def test_corrections_append_and_load(tmp_path: Path):
    p = tmp_path / "corrections.jsonl"
    assert load_corrections(p) == []
    r1 = Correction(573, "items[0].name", "name", "킹핀교환", "히타", None, "t")
    r2 = Correction(570, "items[0].supply", "supply", 98000, 18400, "other", "t")
    append_corrections(p, [r1])
    append_corrections(p, [r2])
    assert load_corrections(p) == [r1, r2]
