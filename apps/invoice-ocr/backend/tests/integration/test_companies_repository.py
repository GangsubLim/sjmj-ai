"""실DB 골든 — PHP CompanyRepositoryTest 동치 이식."""

import pytest

from app.repositories.companies_repository import CompanyRepository
from app.repositories.invoice_repository import InvoiceRepository
from tests.fixtures import companies_data as cd
from tests.fixtures import test_data as td

pytestmark = pytest.mark.usefixtures("db_conn")


def _filters(**kw):
    base = {"q": "", "sort_by": "company_name"}
    base.update(kw)
    return base


def test_insert_and_find_by_id():
    repo = CompanyRepository()
    data = cd.company()
    new_id = repo.insert(data)
    row = repo.find_by_id(new_id)
    assert row is not None
    assert int(row["id"]) == new_id
    assert row["company_name"] == data["company_name"]
    assert row["recipient2"] == data["recipient2"]
    assert row["phone"] == data["phone"]
    assert row["fax"] == data["fax"]
    assert row["sms_number_type"] == data["sms_number_type"]
    assert row["address"] == data["address"]
    assert row["business_number"] == data["business_number"]
    assert row["notes"] == data["notes"]


def test_find_by_id_none_when_missing():
    assert CompanyRepository().find_by_id(99999) is None


def test_insert_on_duplicate_key_updates():
    repo = CompanyRepository()
    repo.insert(
        cd.company(
            {
                "company_name": "중복테스트거래처",
                "phone": "02-1111-1111",
                "notes": "초기메모",
            }
        )
    )
    repo.insert(
        cd.company(
            {
                "company_name": "중복테스트거래처",
                "phone": "02-9999-9999",
                "notes": "변경메모",
            }
        )
    )

    all_rows = repo.find_all(_filters(q="중복테스트거래처"))
    assert len(all_rows) == 1
    assert all_rows[0]["phone"] == "02-9999-9999"
    assert all_rows[0]["fax"] == "02-9876-5433"
    assert all_rows[0]["sms_number_type"] == "phone"
    assert all_rows[0]["notes"] == "변경메모"


def test_find_all_with_search():
    repo = CompanyRepository()
    repo.insert(cd.company({"company_name": "검색한양운수"}))
    repo.insert(cd.company({"company_name": "검색대성물류"}))
    repo.insert(cd.company({"company_name": "전혀다른회사"}))

    results = repo.find_all(_filters(q="검색한양"))
    assert len(results) == 1
    assert results[0]["company_name"] == "검색한양운수"


def test_find_all_search_by_business_number():
    repo = CompanyRepository()
    repo.insert(
        cd.company(
            {"company_name": "사업자번호검색A", "business_number": "111-22-33333"}
        )
    )
    repo.insert(
        cd.company(
            {"company_name": "사업자번호검색B", "business_number": "444-55-66666"}
        )
    )

    results = repo.find_all(_filters(q="111-22"))
    assert len(results) == 1
    assert results[0]["company_name"] == "사업자번호검색A"


def test_find_all_sort_by_name():
    repo = CompanyRepository()
    repo.insert(cd.company({"company_name": "차나가나"}))
    repo.insert(cd.company({"company_name": "가나나다"}))
    repo.insert(cd.company({"company_name": "나다라마"}))

    results = repo.find_all(_filters(sort_by="company_name"))
    names = [r["company_name"] for r in results]
    assert names == sorted(names)
    assert names[0] == "가나나다"


def test_sort_whitelist_rejects_injection():
    repo = CompanyRepository()
    repo.insert(cd.company())
    rows = repo.find_all(_filters(sort_by="DROP TABLE"))  # → company_name 보정
    assert isinstance(rows, list) and len(rows) == 1


def test_update_company():
    repo = CompanyRepository()
    new_id = repo.insert(cd.company({"company_name": "업데이트전거래처"}))

    new_data = cd.company(
        {
            "company_name": "업데이트후거래처",
            "phone": "031-999-8888",
            "fax": "031-999-7777",
            "sms_number_type": "fax",
        }
    )
    assert repo.update(new_id, new_data) is True

    found = repo.find_by_id(new_id)
    assert found["company_name"] == "업데이트후거래처"
    assert found["phone"] == "031-999-8888"
    assert found["fax"] == "031-999-7777"
    assert found["sms_number_type"] == "fax"


def test_delete_company():
    repo = CompanyRepository()
    new_id = repo.insert(cd.company({"company_name": "삭제될거래처"}))
    assert repo.delete(new_id) is True
    assert repo.find_by_id(new_id) is None


def test_delete_returns_false():
    assert CompanyRepository().delete(99999) is False


def test_increment_usage_by_name():
    repo = CompanyRepository()
    new_id = repo.insert(cd.company({"company_name": "사용증가테스트"}))

    before = repo.find_by_id(new_id)
    assert int(before["usage_count"]) == 0
    assert before["last_used"] is None

    repo.increment_usage_by_name("사용증가테스트")

    after = repo.find_by_id(new_id)
    assert int(after["usage_count"]) == 1
    assert after["last_used"] is not None


def test_increment_usage_by_name_accumulates():
    repo = CompanyRepository()
    new_id = repo.insert(cd.company({"company_name": "누적사용테스트"}))

    repo.increment_usage_by_name("누적사용테스트")
    repo.increment_usage_by_name("누적사용테스트")
    repo.increment_usage_by_name("누적사용테스트")

    found = repo.find_by_id(new_id)
    assert int(found["usage_count"]) == 3


def test_find_invoices_by_company_id():
    company_repo = CompanyRepository()
    invoice_repo = InvoiceRepository()

    company_id = company_repo.insert(cd.company({"company_name": "인보이스연결거래처"}))

    invoice_repo.insert(
        td.invoice({"recipient": "인보이스연결거래처", "issue_date": "2025-01-10"})
    )
    invoice_repo.insert(
        td.invoice({"recipient": "인보이스연결거래처", "issue_date": "2025-02-20"})
    )
    invoice_repo.insert(td.invoice({"recipient": "다른거래처"}))

    invoices = company_repo.find_invoices_by_company_id(company_id)
    assert len(invoices) == 2
    for inv in invoices:
        assert inv["recipient"] == "인보이스연결거래처"
    # ORDER BY issue_date DESC
    assert invoices[0]["issue_date"].isoformat() == "2025-02-20"


def test_find_invoices_by_company_id_returns_empty():
    repo = CompanyRepository()
    company_id = repo.insert(cd.company({"company_name": "인보이스없는거래처"}))
    invoices = repo.find_invoices_by_company_id(company_id)
    assert invoices == []
