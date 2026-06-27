"""골든 입력 팩토리 — SJMJ-Web tests/Fixtures/TestData.php 포팅."""
from __future__ import annotations


def _merge(base: dict, overrides: dict | None) -> dict:
    return {**base, **(overrides or {})}


def invoice(overrides: dict | None = None) -> dict:
    return _merge({
        "document_title": "거 래 명 세 서",
        "issue_date": "2026-05-15",
        "recipient": "한양운수",
        "recipient2": "",
        "vehicle_no": "12가3456",
        "memo": None,
        "show_stamp": 1,
        "issuer_id": None,
        "total_supply": 100000,
        "total_vat": 10000,
        "grand_total": 110000,
    }, overrides)


def invoice_item(overrides: dict | None = None) -> dict:
    return _merge({
        "name": "엔진오일",
        "quantity": 2,
        "unit": "EA",
        "unit_price": 50000,
        "supply": 100000,
        "vat": 10000,
        "total": 110000,
        "deduction": 0,
    }, overrides)


def invoice_with_items(overrides: dict | None = None) -> dict:
    base = invoice()
    base["items"] = [
        invoice_item(),
        invoice_item({"name": "브레이크오일"}),
        invoice_item({"name": "에어필터"}),
    ]
    return _merge(base, overrides)


def company(overrides: dict | None = None) -> dict:
    return _merge({
        "company_name": "한양운수", "recipient2": None, "phone": "02-1234-5678",
        "fax": None, "sms_number_type": "phone", "address": "서울시 강남구",
        "business_number": "1234567890", "notes": None,
    }, overrides)


def item(overrides: dict | None = None) -> dict:
    return _merge({
        "item_name": "엔진오일", "default_unit": "EA", "default_unit_price": 50000,
        "category": "oil", "notes": None,
    }, overrides)
