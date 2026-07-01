"""sales_records 테스트 입력 팩토리."""

from __future__ import annotations


def _merge(base: dict, overrides: dict | None) -> dict:
    return {**base, **(overrides or {})}


def salesperson(overrides: dict | None = None) -> dict:
    return _merge({"name": "영업사원1", "sort_order": 0, "is_active": 1}, overrides)


def sales_record(overrides: dict | None = None) -> dict:
    return _merge(
        {
            "salesperson_id": 1,
            "work_date": "2026-05-15",
            "quantity": 1000000,
            "snapshot_name": "영업사원1",
        },
        overrides,
    )
