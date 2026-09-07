"""tools.agent_learn 순수 계층 단위테스트 (DB 비의존, 합성 데이터만)."""

import json
from pathlib import Path

import pytest

from tests.conftest import import_scopes
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
    cmd_extract,
    cmd_publish,
    cmd_report,
    current_version,
    diff_new,
    digit_class,
    duplicate_photos,
    final_hash,
    kind_of,
    load_corrections,
    load_ledger,
    load_versions,
    main,
    publish,
    records_from,
    render_by_version,
    render_deterministic,
    render_duplicates,
    save_ledger,
    split_sections,
    validate_proposed,
    version_for,
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
        {"item_name": "히타", "cnt": 5, "default_unit": "EA"},
        {"item_name": "센터보도", "cnt": 2, "default_unit": None},
    ],
    "companies": ["테스트"],
}


def test_render_deterministic_vocab_amount_status():
    cs = [
        _corr(570, "items[0].supply", "supply", 98000, 18400, "other"),
        _corr(571, "items[1].supply", "supply", 560000, 60000, "prefix_drop"),
    ]
    det = render_deterministic(cs, VOCAB, 5)
    assert "- 히타 (EA)" in det[HEADINGS[0]]
    assert "- 센터보도\n" in det[HEADINGS[0]] + "\n"
    assert "- 테스트" in det[HEADINGS[0]]
    assert "- 앞자리 누락(prefix_drop): 1" in det[HEADINGS[1]]
    assert "- #571 items[1].supply: 560000 → 60000 (prefix_drop)" in det[HEADINGS[1]]
    assert det[HEADINGS[2]] == (
        "- 누적 교정: 2건\n- 초안(원장): 5건\n- 마지막 교정 관측: 2026-09-06T03:00:00"
    )


def test_render_deterministic_empty_inputs():
    det = render_deterministic([], {"items": [], "companies": []}, 0)
    assert "(없음)" in det[HEADINGS[0]]
    assert det[HEADINGS[2]].endswith("- 마지막 교정 관측: -")
    assert list(det) == list(DET_HEADINGS)


def test_vocab_body_grades_by_cnt_and_sorts_within_grade():
    vocab = {
        "items": [
            {"item_name": "타이어", "cnt": 10, "default_unit": "EA"},
            {"item_name": "공임", "cnt": 12, "default_unit": None},
            {"item_name": "히타", "cnt": 9, "default_unit": "EA"},
            {"item_name": "구리스", "cnt": 3, "default_unit": None},
            {"item_name": "센터보도", "cnt": 2, "default_unit": None},
        ],
        "companies": ["테스트"],
    }
    body = render_deterministic([], vocab, 0)[HEADINGS[0]]
    assert body == "\n".join(
        [
            "품목 — 최근 12개월 등장 등급",
            "자주(10회 이상)",
            "- 공임",
            "- 타이어 (EA)",
            "보통(3~9회)",
            "- 구리스",
            "- 히타 (EA)",
            "가끔(2회)",
            "- 센터보도",
            "",
            "거래처",
            "- 테스트",
        ]
    )


def test_vocab_body_empty_grade_and_empty_items():
    one = {"items": [{"item_name": "히타", "cnt": 2, "default_unit": None}], "companies": []}
    body = render_deterministic([], one, 0)[HEADINGS[0]]
    assert "자주(10회 이상)\n(없음)\n보통(3~9회)\n(없음)\n가끔(2회)\n- 히타" in body
    assert body.endswith("거래처\n(없음)")
    empty = render_deterministic([], {"items": [], "companies": []}, 0)[HEADINGS[0]]
    assert empty == "품목 — 최근 12개월 등장 등급\n(없음)\n\n거래처\n(없음)"


def test_vocab_body_same_membership_renders_identically():
    a = {"items": [{"item_name": "히타", "cnt": 4, "default_unit": None}], "companies": []}
    b = {"items": [{"item_name": "히타", "cnt": 8, "default_unit": None}], "companies": []}
    assert render_deterministic([], a, 0) == render_deterministic([], b, 0)


def test_render_deterministic_is_deterministic_and_has_no_lexicon():
    cs = [_corr(1, "items[0].name", "name", "킹핀교환", "히타")]
    det = render_deterministic(cs, VOCAB, 1)
    assert det == render_deterministic(cs, VOCAB, 1)
    assert list(det) == ["## 확정 어휘", "## 금액 오류 통계", "## 데이터 현황"]
    assert "킹핀교환" not in "\n".join(det.values())


# --- 절 분리·조립 ---


def test_assemble_then_split_roundtrip():
    det = render_deterministic([], VOCAB, 0)
    llm = {
        HEADINGS[3]: "- 테스트: 자동차 부품 (#573)",
        HEADINGS[4]: "- 오일은 스프링일 수 있음 (#574)",
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
    assert split_sections(md)[HEADINGS[4]] == "(없음)"


def test_split_sections_rejects_duplicate_heading():
    with pytest.raises(ValueError):
        split_sections("## 확정 어휘\nx\n## 확정 어휘\ny\n")


# --- 검증·발행 ---


def _det() -> dict:
    return render_deterministic([_corr(573, "items[2].name", "name", "킹핀교환", "히타")], VOCAB, 1)


def _good_md() -> str:
    return assemble(
        _det(),
        {
            HEADINGS[3]: "- 테스트: 자동차 부품 위주 (#573)",
            HEADINGS[4]: "- 합계가 안 맞으면 각 행 자릿수 재판독 (#573)",
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
    md = _good_md().replace("- 히타 (EA)", "- 히터 (EA)")
    assert any("결정적 절 변조" in e for e in validate_proposed(md, _det(), {573}))


def test_validate_rejects_rule_without_or_with_unknown_id():
    md = assemble(_det(), {HEADINGS[4]: "- 근거 없는 규칙"})
    assert any("근거 id 없음" in e for e in validate_proposed(md, _det(), {573}))
    md = assemble(_det(), {HEADINGS[4]: "- 규칙 (#999)"})
    assert any("미지의 근거 id" in e for e in validate_proposed(md, _det(), {573}))


def test_validate_rejects_line_cap_and_forbidden_and_size():
    rules = "\n".join(f"- 규칙 {i} (#573)" for i in range(MAX_RULE_LINES + 1))
    errs = validate_proposed(assemble(_det(), {HEADINGS[4]: rules}), _det(), {573})
    assert any("줄" in e for e in errs)
    for w in FORBIDDEN:
        md = assemble(_det(), {HEADINGS[4]: f"- {w} 써라 (#573)"})
        assert any("금지어" in e for e in validate_proposed(md, _det(), {573})), w
    big = assemble(_det(), {HEADINGS[3]: "x" * 12000})
    assert any("자" in e for e in validate_proposed(big, _det(), {573}))


def test_validate_allows_placeholder_and_non_bullet_lines():
    md = assemble(_det(), {})
    assert validate_proposed(md, _det(), {573}) == []


def test_validate_rejects_arrow_in_llm_sections_only():
    sup = [_corr(571, "items[1].supply", "supply", 560000, 60000, "prefix_drop")]
    det = render_deterministic(sup, VOCAB, 1)
    assert "560000 → 60000" in det[HEADINGS[1]]
    assert validate_proposed(assemble(det, {}), det, {571}) == []
    for arrow in ("→", "->"):
        md = assemble(det, {HEADINGS[4]: f"- 킹핀교환{arrow}히타 우선 검토 (#571)"})
        errs = validate_proposed(md, det, {571})
        assert any(e == f"금지어 {arrow!r}" for e in errs), (arrow, errs)


def test_publish_writes_version_active_and_log(tmp_path: Path):
    kdir = tmp_path
    (kdir / "proposed.md").write_text(_good_md(), encoding="utf-8")
    r = publish(kdir, _det(), {573}, 1, "2026-09-07T03:00:00")
    assert r == PublishResult(1, "published")
    assert (kdir / "knowledge" / "v1.md").read_text(encoding="utf-8") == _good_md()
    assert (kdir / "active.md").read_text(encoding="utf-8") == _good_md()
    vs = load_versions(kdir / "versions.jsonl")
    assert vs == [{"version": 1, "published_at": "2026-09-07T03:00:00", "corrections_through": 1}]
    assert current_version(vs) == 1

    (kdir / "proposed.md").write_text(_good_md(), encoding="utf-8")
    r2 = publish(kdir, _det(), {573}, 1, "2026-09-08T03:00:00")
    assert r2.version == 2
    assert load_versions(kdir / "versions.jsonl")[-1] == {
        "version": 2,
        "published_at": "2026-09-08T03:00:00",
        "corrections_through": 1,
    }


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


# --- 버전 매핑·리포트 ---

VERSIONS = [
    {"version": 1, "published_at": "2026-09-07T03:00:00"},
    {"version": 1, "published_at": "2026-09-08T03:00:00", "rejected": "x"},
    {"version": 2, "published_at": "2026-09-09T03:00:00"},
]


def test_version_for_picks_latest_published_before_created_at():
    assert version_for("2026-09-06 10:00:00", VERSIONS) == "none"
    assert version_for("2026-09-07 03:00:00", VERSIONS) == "v1"
    assert version_for("2026-09-08 12:00:00", VERSIONS) == "v1"
    assert version_for("2026-09-10 00:00:00", VERSIONS) == "v2"


def test_render_by_version_table_orders_none_first():
    from tools.agent_report import summarize

    rows_v1 = [(1, compare(_draft(), _final()))]
    rows_none = [(2, compare(_draft(), _final(items=[{"name": "킹핀교환", "supply": 150000}])))]
    md = render_by_version({"v1": summarize(rows_v1), "none": summarize(rows_none)})
    lines = md.splitlines()
    assert lines[0] == "## 지식 버전별 일치율"
    assert lines[2] == "| 버전 | 건수 | 품목명 | 금액 | 무수정률 |"
    assert lines[4].startswith("| none | 1 | 100.0% (1/1) | 100.0% (1/1) | 100.0% (1/1) |")
    assert lines[5].startswith("| v1 | 1 | 0.0% (0/1) | 100.0% (1/1) | 0.0% (0/1) |")


# --- CLI ---


def _seed(data_dir: Path) -> None:
    up = data_dir / "agent_uploads"
    up.mkdir(parents=True)
    (up / "573.draft.json").write_text(json.dumps(_draft()), encoding="utf-8")
    (up / "574.draft.json").write_text(json.dumps(_draft()), encoding="utf-8")


FINALS = {573: _final(), 574: _final(items=[{"name": "킹핀교환", "supply": 150000}])}


def _finals(ids):
    return {i: FINALS[i] for i in ids}


def test_cmd_extract_writes_artifacts_and_summary(tmp_path: Path):
    _seed(tmp_path)
    s = cmd_extract(tmp_path, _finals, lambda: VOCAB, "2026-09-07T03:00:00")
    kdir = tmp_path / "agent_knowledge"
    assert s == {
        "new": 1,
        "by_kind": {"name": 1},
        "proposed": str(kdir / "proposed.md"),
        "active_version": 0,
    }
    assert load_corrections(kdir / "corrections.jsonl")[0].final == "히타"
    assert set(load_ledger(kdir / "ledger.json")) == {573, 574}
    assert json.loads((kdir / "vocab_snapshot.json").read_text(encoding="utf-8")) == VOCAB
    proposed = split_sections((kdir / "proposed.md").read_text(encoding="utf-8"))
    assert "- 히타 (EA)" in proposed[HEADINGS[0]]
    assert proposed[HEADINGS[4]] == "(없음)"
    assert not (kdir / "active.md").exists()


def test_cmd_extract_preserves_llm_sections_from_active(tmp_path: Path):
    _seed(tmp_path)
    kdir = tmp_path / "agent_knowledge"
    kdir.mkdir()
    md = assemble(render_deterministic([], VOCAB, 0), {HEADINGS[4]: "- 기존 규칙 (#573)"})
    (kdir / "active.md").write_text(md, encoding="utf-8")
    cmd_extract(tmp_path, _finals, lambda: VOCAB, "t")
    proposed = (kdir / "proposed.md").read_text(encoding="utf-8")
    assert split_sections(proposed)[HEADINGS[4]] == "- 기존 규칙 (#573)"


def test_cmd_extract_without_uploads_dir(tmp_path: Path):
    s = cmd_extract(tmp_path, lambda ids: {}, lambda: VOCAB, "t")
    assert s["new"] == 0 and s["active_version"] == 0


def _stale_active(kdir: Path, rules: str) -> str:
    """corrections는 현재 것, 어휘는 비어 있는 구버전 active.md를 쓰고 그 원문을 돌려준다."""
    corrections = load_corrections(kdir / "corrections.jsonl")
    md = assemble(
        render_deterministic(corrections, {"items": [], "companies": []}, 2),
        {HEADINGS[4]: rules},
    )
    (kdir / "active.md").write_text(md, encoding="utf-8")
    return md


def test_cmd_extract_auto_publishes_when_det_changed_without_new(tmp_path: Path):
    _seed(tmp_path)
    kdir = tmp_path / "agent_knowledge"
    first = cmd_extract(tmp_path, _finals, lambda: VOCAB, "t1")
    assert first["new"] == 1 and "auto_publish" not in first
    _stale_active(kdir, "- 기존 규칙 (#573)")

    s = cmd_extract(tmp_path, _finals, lambda: VOCAB, "2026-09-08T03:00:00")
    assert s["new"] == 0
    assert s["auto_publish"] == {"version": 1, "reason": "published"}
    assert s["active_version"] == 1
    active = split_sections((kdir / "active.md").read_text(encoding="utf-8"))
    assert "- 히타 (EA)" in active[HEADINGS[0]]
    assert active[HEADINGS[4]] == "- 기존 규칙 (#573)"
    assert load_versions(kdir / "versions.jsonl") == [
        {"version": 1, "published_at": "2026-09-08T03:00:00", "corrections_through": 1}
    ]
    assert (kdir / "knowledge" / "v1.md").read_text(encoding="utf-8") == (
        kdir / "active.md"
    ).read_text(encoding="utf-8")


def test_cmd_extract_skips_publish_when_only_llm_whitespace_differs(tmp_path: Path):
    _seed(tmp_path)
    kdir = tmp_path / "agent_knowledge"
    cmd_extract(tmp_path, _finals, lambda: VOCAB, "t1")
    assert cmd_publish(tmp_path, "t1") == PublishResult(1, "published")
    active = kdir / "active.md"
    md = active.read_text(encoding="utf-8")
    active.write_text(
        md.replace("## 일반화 규칙\n\n(없음)", "## 일반화 규칙\n\n\n(없음)   \n"), encoding="utf-8"
    )

    s = cmd_extract(tmp_path, _finals, lambda: VOCAB, "t2")
    assert s["new"] == 0 and "auto_publish" not in s and s["active_version"] == 1
    assert len(load_versions(kdir / "versions.jsonl")) == 1
    assert not (kdir / "knowledge" / "v2.md").exists()


def test_cmd_extract_auto_publish_rejected_keeps_active(tmp_path: Path):
    _seed(tmp_path)
    kdir = tmp_path / "agent_knowledge"
    cmd_extract(tmp_path, _finals, lambda: VOCAB, "t1")
    stale = _stale_active(kdir, "- 킹핀교환→히타 (#573)")

    s = cmd_extract(tmp_path, _finals, lambda: VOCAB, "t2")
    assert s["auto_publish"]["version"] is None
    assert "금지어" in s["auto_publish"]["reason"]
    assert s["active_version"] == 0
    assert (kdir / "active.md").read_text(encoding="utf-8") == stale
    vs = load_versions(kdir / "versions.jsonl")
    assert vs[-1]["version"] == 0 and "금지어" in vs[-1]["rejected"]
    assert not (kdir / "knowledge").exists()


def test_cmd_publish_and_report_roundtrip(tmp_path: Path):
    _seed(tmp_path)
    cmd_extract(tmp_path, _finals, lambda: VOCAB, "2026-09-07T03:00:00")
    assert cmd_publish(tmp_path, "2026-09-07T03:01:00") == PublishResult(1, "published")
    md = cmd_report(tmp_path, tmp_path / "rep", _finals)
    assert "## 지식 버전별 일치율" in md
    assert "| none | 2 |" in md
    assert "동일 사진" not in md
    assert (tmp_path / "rep" / "failures.jsonl").exists()


def test_duplicate_photos_groups_same_bytes_across_extensions(tmp_path: Path):
    up = tmp_path / "agent_uploads"
    up.mkdir()
    (up / "574.jpeg").write_bytes(b"same")
    (up / "573.jpg").write_bytes(b"same")
    (up / "575.png").write_bytes(b"same")
    (up / "576.jpg").write_bytes(b"other")
    (up / "580.jpg").write_bytes(b"pair")
    (up / "578.jpg").write_bytes(b"pair")
    (up / "577.draft.json").write_bytes(b"same")
    assert duplicate_photos(up) == [[573, 574, 575], [578, 580]]
    assert duplicate_photos(tmp_path / "missing") == []


def test_render_duplicates_section_or_empty():
    assert render_duplicates([[573, 574, 575]]) == "## 동일 사진\n\n- 동일 사진: #573 #574 #575\n"
    assert render_duplicates([]) == ""


def test_cmd_report_marks_duplicate_photos(tmp_path: Path):
    _seed(tmp_path)
    up = tmp_path / "agent_uploads"
    (up / "573.jpg").write_bytes(b"same")
    (up / "574.jpg").write_bytes(b"same")
    md = cmd_report(tmp_path, tmp_path / "rep", _finals)
    assert md.endswith("## 동일 사진\n\n- 동일 사진: #573 #574\n")
    assert (tmp_path / "rep" / "report.md").read_text(encoding="utf-8") == md


def test_main_extract_wake_gate_and_publish_report(tmp_path: Path, capsys, monkeypatch):
    _seed(tmp_path)
    import tools.agent_learn as al

    monkeypatch.setattr(al, "_engine", lambda: object())
    monkeypatch.setattr(al, "fetch_finals", lambda engine, ids: _finals(ids))
    monkeypatch.setattr(al, "fetch_vocab", lambda engine: VOCAB)

    main(["extract", "--data-dir", str(tmp_path)])
    out = capsys.readouterr().out.strip().splitlines()
    assert json.loads(out[0])["new"] == 1
    assert len(out) == 1

    main(["extract", "--data-dir", str(tmp_path)])
    out = capsys.readouterr().out.strip().splitlines()
    assert json.loads(out[-1]) == {"wakeAgent": False}

    kdir = tmp_path / "agent_knowledge"
    md = (kdir / "proposed.md").read_text(encoding="utf-8")
    edited = md.replace(
        "## 일반화 규칙\n\n(없음)", "## 일반화 규칙\n\n- 합계 불일치 시 자릿수 재판독 (#573)"
    )
    (kdir / "proposed.md").write_text(edited, encoding="utf-8")
    main(["publish", "--data-dir", str(tmp_path)])
    assert capsys.readouterr().out.strip() == "published v1 · 어휘 2종 · 규칙 1줄"
    assert (kdir / "active.md").exists()

    (kdir / "proposed.md").write_text(md.replace("## 일반화 규칙", "## 규칙"), encoding="utf-8")
    main(["publish", "--data-dir", str(tmp_path)])
    assert capsys.readouterr().out.startswith("rejected: 헤딩 불일치")

    main(["report", "--data-dir", str(tmp_path), "--out", str(tmp_path / "rep")])
    rep = (tmp_path / "rep" / "report.md").read_text(encoding="utf-8")
    assert "# hermes 위임 입력 초안↔최종본 일치율" in rep
    assert "| none | 2 |" in rep


def test_main_extract_db_failure_is_silent_wake_gate(tmp_path: Path, capsys, monkeypatch):
    import tools.agent_learn as al

    def boom():
        raise RuntimeError("no db")

    monkeypatch.setattr(al, "_engine", boom)
    main(["extract", "--data-dir", str(tmp_path)])
    captured = capsys.readouterr()
    assert json.loads(captured.out.strip().splitlines()[-1]) == {"wakeAgent": False}
    assert "no db" in captured.err


def test_main_extract_auto_publish_rejection_goes_to_stderr(tmp_path: Path, capsys, monkeypatch):
    _seed(tmp_path)
    import tools.agent_learn as al

    monkeypatch.setattr(al, "_engine", lambda: object())
    monkeypatch.setattr(al, "fetch_finals", lambda engine, ids: _finals(ids))
    monkeypatch.setattr(al, "fetch_vocab", lambda engine: VOCAB)
    main(["extract", "--data-dir", str(tmp_path)])
    capsys.readouterr()
    _stale_active(tmp_path / "agent_knowledge", "- 킹핀교환->히타 (#573)")

    main(["extract", "--data-dir", str(tmp_path)])
    captured = capsys.readouterr()
    assert json.loads(captured.out.strip().splitlines()[-1]) == {"wakeAgent": False}
    assert "무인 발행 거부" in captured.err and "금지어" in captured.err


def test_fetch_vocab_maps_cnt_and_companies():
    pytest.importorskip("sqlalchemy")
    from types import SimpleNamespace

    from tools.agent_learn import COMPANIES_SQL, ITEMS_SQL, fetch_vocab

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, stmt):
            sql = str(stmt)
            if sql == ITEMS_SQL:
                return [SimpleNamespace(item_name="히타", cnt=5, default_unit=None)]
            assert sql == COMPANIES_SQL
            return [SimpleNamespace(company_name="테스트")]

    engine = SimpleNamespace(connect=lambda: _Conn())
    assert fetch_vocab(engine) == {
        "items": [{"item_name": "히타", "cnt": 5, "default_unit": None}],
        "companies": ["테스트"],
    }


def test_agent_learn_keeps_heavy_imports_lazy():
    src = Path(__file__).resolve().parents[1] / "tools" / "agent_learn.py"
    module_level, in_functions = import_scopes(src)
    forbidden = {"sqlalchemy", "worker.db", "worker.main", "cv2", "numpy", "torch"}
    assert not (module_level & forbidden), module_level & forbidden
    assert {"worker.db", "sqlalchemy"} & in_functions
