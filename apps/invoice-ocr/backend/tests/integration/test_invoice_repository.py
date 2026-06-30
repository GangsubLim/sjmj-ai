import pytest

from app.repositories.invoice_repository import InvoiceRepository
from tests.fixtures import test_data as td

pytestmark = pytest.mark.usefixtures("db_conn")


def _filters(**kw):
    base = {
        "page": 1,
        "limit": 20,
        "search": "",
        "date_from": "",
        "date_to": "",
        "sort_by": "issue_date",
        "sort_order": "desc",
    }
    base.update(kw)
    return base


def test_insert_and_find_by_id():
    repo = InvoiceRepository()
    new_id = repo.insert(td.invoice())
    row = repo.find_by_id(new_id)
    assert row["recipient"] == "한양운수"
    assert row["grand_total"] == 110000  # INT → int
    assert row["show_stamp"] == 1  # BOOLEAN → 1, not True


def test_find_by_id_none_when_missing():
    assert InvoiceRepository().find_by_id(999999) is None


def test_insert_items_and_cascade_on_delete():
    repo = InvoiceRepository()
    iid = repo.insert(td.invoice())
    repo.insert_item({**td.invoice_item(), "invoice_id": iid, "item_order": 1})
    repo.insert_item(
        {
            **td.invoice_item({"name": "브레이크오일"}),
            "invoice_id": iid,
            "item_order": 2,
        }
    )
    items = repo.find_items(iid)
    assert [i["name"] for i in items] == ["엔진오일", "브레이크오일"]  # item_order 정렬
    repo.delete(iid)
    assert repo.find_items(iid) == []  # FK ON DELETE CASCADE


def test_find_all_search_and_count():
    repo = InvoiceRepository()
    repo.insert(td.invoice({"recipient": "대성물류", "vehicle_no": "99바9999"}))
    repo.insert(td.invoice({"recipient": "한양운수"}))
    rows = repo.find_all(_filters(search="대성"))
    assert all("대성" in r["recipient"] for r in rows)
    assert repo.count_all(_filters(search="대성")) == 1
    assert repo.count_all(_filters()) == 2


def test_find_all_date_filter_and_sort_grand_total_asc():
    repo = InvoiceRepository()
    repo.insert(td.invoice({"issue_date": "2026-01-01", "grand_total": 300}))
    repo.insert(td.invoice({"issue_date": "2026-06-01", "grand_total": 100}))
    rows = repo.find_all(_filters(date_from="2026-05-01", sort_by="grand_total", sort_order="asc"))
    assert len(rows) == 1 and rows[0]["grand_total"] == 100


def test_sort_whitelist_rejects_injection():
    repo = InvoiceRepository()
    repo.insert(td.invoice())
    rows = repo.find_all(
        _filters(sort_by="DROP TABLE", sort_order="evil")
    )  # → issue_date desc 보정
    assert isinstance(rows, list) and len(rows) == 1


def test_update_changes_fields():
    repo = InvoiceRepository()
    iid = repo.insert(td.invoice())
    assert repo.update(iid, td.invoice({"recipient": "수정거래처", "grand_total": 999})) is True
    row = repo.find_by_id(iid)
    assert row["recipient"] == "수정거래처" and row["grand_total"] == 999


def test_find_all_for_export_date_filter():
    repo = InvoiceRepository()
    repo.insert(td.invoice({"issue_date": "2026-01-01"}))
    repo.insert(td.invoice({"issue_date": "2026-06-01"}))
    rows = repo.find_all_for_export({"date_from": "2026-05-01", "date_to": "", "company_id": None})
    assert len(rows) == 1 and rows[0]["issue_date"].isoformat() == "2026-06-01"
