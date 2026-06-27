"""items 골든 입력 팩토리 — SJMJ-Web tests/Fixtures/TestData::item() 포팅."""
from __future__ import annotations


def _merge(base: dict, overrides: dict | None) -> dict:
    return {**base, **(overrides or {})}


def item(overrides: dict | None = None) -> dict:
    return _merge({
        "item_name": "엔진오일",
        "default_unit": "EA",
        "default_unit_price": 30000,
        "category": "오일",
        "notes": "테스트 품목",
    }, overrides)
