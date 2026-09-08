"""hermes 순수 대조 계층 단위테스트 (DB·파일시스템 비의존).

공유 골든 픽스처(apps/invoice-ocr/fixtures/hermes_compare_cases.json)를 ml 쪽
tests/test_agent_report.py와 같은 값으로 먹여 두 구현의 동치를 강제한다(spec §7.2).
"""

import json
from pathlib import Path

import pytest

from app.services.hermes_diff import (
    compare,
    mismatch_fields,
    norm,
    status_of,
    summarize,
    version_for,
)

# backend/tests/unit/ → backend/tests/ → backend/ → apps/invoice-ocr/ 이므로 parents[3].
_CASES_PATH = Path(__file__).resolve().parents[3] / "fixtures" / "hermes_compare_cases.json"


def _fixture() -> dict:
    return json.loads(_CASES_PATH.read_text(encoding="utf-8"))


# --- 공유 골든 픽스처 동치 ---


def test_shared_fixture_cases_match_compare():
    for case in _fixture()["cases"]:
        if case["final"] is None:
            continue
        c = compare(case["draft"], case["final"])
        assert c.recipient == case["expected"]["recipient"], case["name"]
        assert c.item_count == case["expected"]["item_count"], case["name"]
        assert c.name_hits == case["expected"]["name_hits"], case["name"]
        assert c.supply_hits == case["expected"]["supply_hits"], case["name"]
        assert c.pairs == case["expected"]["pairs"], case["name"]
        assert c.grand_total == case["expected"]["grand_total"], case["name"]
        assert c.edited == case["expected"]["edited"], case["name"]
        assert [list(m) for m in c.mismatches] == case["expected"]["mismatches"], case["name"]


def test_shared_fixture_summary_matches_summarize():
    fixture = _fixture()
    rows = [
        (i, compare(case["draft"], case["final"]))
        for i, case in enumerate(fixture["cases"])
        if case["final"] is not None
    ]
    missing = sum(1 for case in fixture["cases"] if case["final"] is None)
    assert summarize(rows, missing=missing) == fixture["expected_summary"]


# --- 정규화 ---


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("  가 나 ", "가 나"), ("가   나", "가 나"), (None, ""), ("", "")],
)
def test_norm_collapses_whitespace(raw, expected):
    assert norm(raw) == expected


# --- 분모 0 ---


def test_summarize_zero_denominator_returns_zero_rates():
    s = summarize([], missing=0)
    assert s["count"] == 0
    assert s["pairs"] == 0
    # 에러도 null도 아닌 0.0 — agent_report._rate와 같은 규약(spec §3.3).
    assert s["name_rate"] == 0.0
    assert s["untouched_rate"] == 0.0


# --- 상태 판정 ---


def test_status_of_missing_final_is_deleted():
    assert status_of(None) == "deleted"


def test_status_of_clean_comparison_is_match():
    case = next(c for c in _fixture()["cases"] if c["name"] == "all_match")
    assert status_of(compare(case["draft"], case["final"])) == "match"


def test_status_of_items_only_edit_is_mismatch():
    """품목만 고친 건 — 부모 타임스탬프가 안 움직여도 mismatch여야 한다(spec §3.2)."""
    case = next(c for c in _fixture()["cases"] if c["name"] == "items_only_edited")
    c = compare(case["draft"], case["final"])
    assert c.edited is False
    assert status_of(c) == "mismatch"


# --- 불일치 축 접기 ---


def test_mismatch_fields_folds_item_index_and_dedupes():
    draft = {
        "recipient": "가상사",
        "grand_total": 100,
        "items": [{"name": "a", "supply": 1}, {"name": "b", "supply": 2}],
    }
    final = {
        "recipient": "나상사",
        "grand_total": 100,
        "items": [{"name": "A", "supply": 9}, {"name": "B", "supply": 8}],
        "created_at": "x",
        "updated_at": "x",
    }
    # items[0].name·items[1].name → name 하나로 접히고, 선언 순서대로 나온다.
    assert mismatch_fields(compare(draft, final)) == ["recipient", "name", "supply"]


def test_mismatch_fields_empty_when_all_match():
    case = next(c for c in _fixture()["cases"] if c["name"] == "all_match")
    assert mismatch_fields(compare(case["draft"], case["final"])) == []


# --- 지식 버전 매핑 ---


_VERSIONS = [
    {"version": 1, "published_at": "2026-09-01T00:00:00", "corrections_through": 5},
    {"version": 1, "published_at": "2026-09-02T00:00:00", "rejected": "금지어"},
    {"version": 2, "published_at": "2026-09-03T00:00:00", "corrections_through": 25},
]


def test_version_for_before_first_publish_is_zero():
    assert version_for("2026-08-31 23:59:59", _VERSIONS) == 0


def test_version_for_picks_latest_published_at_or_before():
    assert version_for("2026-09-02 12:00:00", _VERSIONS) == 1
    assert version_for("2026-09-03 00:00:00", _VERSIONS) == 2


def test_version_for_ignores_rejected_records():
    """거부 기록은 활성 버전이 아니다 — 승격시키면 버전 축이 통째로 어긋난다."""
    rejected_only = [{"version": 3, "published_at": "2026-09-01T00:00:00", "rejected": "상한 초과"}]
    assert version_for("2026-09-05 00:00:00", rejected_only) == 0


def test_version_for_empty_versions_is_zero():
    assert version_for("2026-09-05 00:00:00", []) == 0
