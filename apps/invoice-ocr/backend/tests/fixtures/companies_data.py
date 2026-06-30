"""companies 테스트 입력 팩토리."""

from __future__ import annotations


def _merge(base: dict, overrides: dict | None) -> dict:
    return {**base, **(overrides or {})}


def company(overrides: dict | None = None) -> dict:
    return _merge(
        {
            "company_name": "한양운수",
            "recipient2": "김과장",
            "phone": "02-9876-5432",
            "fax": "02-9876-5433",
            "sms_number_type": "phone",
            "address": "서울특별시 중구 세종대로 100",
            "business_number": "987-65-43210",
            "notes": "테스트 거래처",
        },
        overrides,
    )
