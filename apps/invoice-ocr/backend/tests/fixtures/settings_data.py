"""settings 골든 입력 팩토리 — SJMJ-Web tests/Fixtures/TestData.php(issuer/appSettings) 포팅."""

from __future__ import annotations


def _merge(base: dict, overrides: dict | None) -> dict:
    return {**base, **(overrides or {})}


def issuer(overrides: dict | None = None) -> dict:
    return _merge(
        {
            "company_name": "성진모터스",
            "representative": "김성진",
            "business_number": "123-45-67890",
            "address": "서울특별시 강남구 테헤란로 123",
            "business_type": "서비스",
            "business_item": "자동차정비",
            "phone": "02-1234-5678",
            "fax": "02-1234-5679",
            "tel_fax": "02-1234-5678/02-1234-5679",
            "bank_account": "국민은행 123-456-7890",
            "show_sjdojang": 1,
        },
        overrides,
    )


def app_settings() -> dict:
    return {
        "default_vat_rate": "0.1",
        "default_document_title": "거 래 명 세 서",
        "default_unit": "EA",
        "pdf_filename_pattern": "거래명세서_{recipient}_{issue_date}",
    }
