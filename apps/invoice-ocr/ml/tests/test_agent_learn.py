"""tools.agent_learn 순수 계층 단위테스트 (DB 비의존, 합성 데이터만)."""

import json
from pathlib import Path

import pytest

from tools.agent_learn import (
    DET_HEADINGS,
    FORBIDDEN,
    HEADINGS,
    LLM_HEADINGS,
    MAX_RULE_LINES,
    Correction,
    PublishResult,
    append_corrections,
    assemble,
    current_version,
    diff_new,
    digit_class,
    final_hash,
    kind_of,
    load_corrections,
    load_ledger,
    load_versions,
    publish,
    records_from,
    render_deterministic,
    save_ledger,
    split_sections,
    validate_proposed,
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


# --- 결정적 절 렌더 ---


def _corr(iid, field, kind, d, f, dc=None, t="2026-09-06T03:00:00") -> Correction:
    return Correction(iid, field, kind, d, f, dc, t)


VOCAB = {
    "items": [
        {"item_name": "히타", "default_unit": "EA"},
        {"item_name": "센터보도", "default_unit": None},
    ],
    "companies": ["테스트"],
}


def test_render_deterministic_lexicon_counts_and_sorts():
    cs = [
        _corr(573, "items[2].name", "name", "킹핀교환", "히타"),
        _corr(574, "items[2].name", "name", "킹핀교환", "히타"),
        _corr(572, "items[0].name", "name", "번호등", "보조물통"),
        _corr(570, "recipient", "recipient", "테스트 ", "테스트상사"),
    ]
    det = render_deterministic(cs, VOCAB, 5)
    lex = det[HEADINGS[0]].splitlines()
    assert lex[0] == "| 오독 | 정답 | 횟수 | 근거 id |"
    assert lex[2] == "| 킹핀교환 | 히타 | 2 | #573 #574 |"
    assert lex[3] == "| 번호등 | 보조물통 | 1 | #572 |"
    assert lex[4] == "| 테스트 | 테스트상사 | 1 | #570 |"


def test_render_deterministic_vocab_amount_status():
    cs = [
        _corr(570, "items[0].supply", "supply", 98000, 18400, "other"),
        _corr(571, "items[1].supply", "supply", 560000, 60000, "prefix_drop"),
    ]
    det = render_deterministic(cs, VOCAB, 5)
    assert "- 히타 (EA)" in det[HEADINGS[1]]
    assert "- 센터보도\n" in det[HEADINGS[1]] + "\n"
    assert "- 테스트" in det[HEADINGS[1]]
    assert "- 앞자리 누락(prefix_drop): 1" in det[HEADINGS[2]]
    assert "- #571 items[1].supply: 560000 → 60000 (prefix_drop)" in det[HEADINGS[2]]
    assert det[HEADINGS[3]] == (
        "- 누적 교정: 2건\n- 초안(원장): 5건\n- 마지막 교정 관측: 2026-09-06T03:00:00"
    )


def test_render_deterministic_empty_inputs():
    det = render_deterministic([], {"items": [], "companies": []}, 0)
    assert det[HEADINGS[0]] == "(없음)"
    assert "(없음)" in det[HEADINGS[1]]
    assert det[HEADINGS[3]].endswith("- 마지막 교정 관측: -")
    assert list(det) == list(DET_HEADINGS)


def test_render_deterministic_is_deterministic_and_escapes_pipe():
    cs = [_corr(1, "items[0].name", "name", "a|b", "c")]
    assert render_deterministic(cs, VOCAB, 1) == render_deterministic(cs, VOCAB, 1)
    assert "| a\\|b | c | 1 | #1 |" in render_deterministic(cs, VOCAB, 1)[HEADINGS[0]]


# --- 절 분리·조립 ---


def test_assemble_then_split_roundtrip():
    det = render_deterministic([], VOCAB, 0)
    llm = {
        HEADINGS[4]: "- 테스트: 자동차 부품 (#573)",
        HEADINGS[5]: "- 오일은 스프링일 수 있음 (#574)",
    }
    md = assemble(det, llm)
    assert md.startswith("# sjmj 판독 지식\n")
    sections = split_sections(md)
    assert list(sections) == list(HEADINGS)
    for h in DET_HEADINGS:
        assert sections[h] == det[h]
    for h in LLM_HEADINGS:
        assert sections[h] == llm[h]


def test_assemble_fills_missing_llm_sections():
    md = assemble(render_deterministic([], VOCAB, 0), {})
    assert split_sections(md)[HEADINGS[5]] == "(없음)"


def test_split_sections_rejects_duplicate_heading():
    with pytest.raises(ValueError):
        split_sections("## 교정 사전\nx\n## 교정 사전\ny\n")


# --- 검증·발행 ---


def _det() -> dict:
    return render_deterministic([_corr(573, "items[2].name", "name", "킹핀교환", "히타")], VOCAB, 1)


def _good_md() -> str:
    return assemble(
        _det(),
        {
            HEADINGS[4]: "- 테스트: 자동차 부품 위주 (#573)",
            HEADINGS[5]: "- 킹핀교환으로 읽히면 히타 우선 검토 (#573)",
        },
    )


def test_validate_accepts_good_document():
    assert validate_proposed(_good_md(), _det(), {573}) == []


def test_validate_rejects_missing_or_reordered_heading():
    md = _good_md().replace("## 일반화 규칙", "## 규칙")
    assert any("헤딩" in e for e in validate_proposed(md, _det(), {573}))
    parts = _good_md().split("## 거래처 프로필")
    swapped = parts[0].replace("## 데이터 현황", "## 거래처 프로필", 1)
    assert validate_proposed(swapped + "## 데이터 현황" + parts[1], _det(), {573})


def test_validate_rejects_tampered_deterministic_section():
    md = _good_md().replace("| 킹핀교환 | 히타 | 1 | #573 |", "| 킹핀교환 | 히터 | 1 | #573 |")
    assert any("결정적 절 변조" in e for e in validate_proposed(md, _det(), {573}))


def test_validate_rejects_rule_without_or_with_unknown_id():
    md = assemble(_det(), {HEADINGS[5]: "- 근거 없는 규칙"})
    assert any("근거 id 없음" in e for e in validate_proposed(md, _det(), {573}))
    md = assemble(_det(), {HEADINGS[5]: "- 규칙 (#999)"})
    assert any("미지의 근거 id" in e for e in validate_proposed(md, _det(), {573}))


def test_validate_rejects_line_cap_and_forbidden_and_size():
    rules = "\n".join(f"- 규칙 {i} (#573)" for i in range(MAX_RULE_LINES + 1))
    errs = validate_proposed(assemble(_det(), {HEADINGS[5]: rules}), _det(), {573})
    assert any("줄" in e for e in errs)
    for w in FORBIDDEN:
        md = assemble(_det(), {HEADINGS[5]: f"- {w} 써라 (#573)"})
        assert any("금지어" in e for e in validate_proposed(md, _det(), {573})), w
    big = assemble(_det(), {HEADINGS[4]: "x" * 12000})
    assert any("자" in e for e in validate_proposed(big, _det(), {573}))


def test_validate_allows_placeholder_and_non_bullet_lines():
    md = assemble(_det(), {})
    assert validate_proposed(md, _det(), {573}) == []


def test_publish_writes_version_active_and_log(tmp_path: Path):
    kdir = tmp_path
    (kdir / "proposed.md").write_text(_good_md(), encoding="utf-8")
    r = publish(kdir, _det(), {573}, 1, "2026-09-07T03:00:00")
    assert r == PublishResult(1, "published")
    assert (kdir / "knowledge" / "v1.md").read_text(encoding="utf-8") == _good_md()
    assert (kdir / "active.md").read_text(encoding="utf-8") == _good_md()
    vs = load_versions(kdir / "versions.jsonl")
    assert vs == [
        {
            "version": 1,
            "published_at": "2026-09-07T03:00:00",
            "corrections_through": 1,
            "added_pairs": 1,
        }
    ]
    assert current_version(vs) == 1

    (kdir / "proposed.md").write_text(_good_md(), encoding="utf-8")
    r2 = publish(kdir, _det(), {573}, 1, "2026-09-08T03:00:00")
    assert r2.version == 2
    assert load_versions(kdir / "versions.jsonl")[-1]["added_pairs"] == 0


def test_publish_rejection_keeps_active(tmp_path: Path):
    kdir = tmp_path
    (kdir / "proposed.md").write_text(_good_md(), encoding="utf-8")
    publish(kdir, _det(), {573}, 1, "t1")
    broken = _good_md().replace("## 일반화 규칙", "## 규칙")
    (kdir / "proposed.md").write_text(broken, encoding="utf-8")
    r = publish(kdir, _det(), {573}, 1, "t2")
    assert r.version is None and "헤딩" in r.reason
    assert (kdir / "active.md").read_text(encoding="utf-8") == _good_md()
    vs = load_versions(kdir / "versions.jsonl")
    assert vs[-1]["version"] == 1 and "헤딩" in vs[-1]["rejected"]
    assert current_version(vs) == 1
    assert not (kdir / "knowledge" / "v2.md").exists()


def test_publish_without_proposed(tmp_path: Path):
    assert publish(tmp_path, _det(), set(), 0, "t").version is None
