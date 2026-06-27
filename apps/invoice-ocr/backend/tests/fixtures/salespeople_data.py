"""골든 입력 팩토리 — SJMJ-Web tests/Fixtures/TestData::salesperson 포팅."""
from __future__ import annotations


def _merge(base: dict, overrides: dict | None) -> dict:
    return {**base, **(overrides or {})}


def salesperson(overrides: dict | None = None) -> dict:
    return _merge({
        "name": "영업사원1",
        "sort_order": 0,
        "is_active": 1,
    }, overrides)
