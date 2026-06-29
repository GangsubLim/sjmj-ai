from contextlib import nullcontext
from unittest.mock import MagicMock

from app.services.invoice_service import InvoiceService
from tests.fixtures import test_data as td


def _svc(repo, company=None, item=None):
    return InvoiceService(
        repo, company or MagicMock(), item or MagicMock(), transaction=nullcontext
    )


def test_get_list_pagination():
    repo = MagicMock()
    repo.find_all.return_value = [td.invoice(), td.invoice({"recipient": "대성물류"})]
    repo.count_all.return_value = 2
    r = _svc(repo).get_list(
        {"page": 1, "limit": 10, "sort_by": "issue_date", "sort_order": "desc"}
    )
    assert len(r["data"]) == 2
    assert r["pagination"] == {"page": 1, "limit": 10, "total": 2, "totalPages": 1}


def test_get_list_total_pages_ceil():
    repo = MagicMock()
    repo.find_all.return_value = []
    repo.count_all.return_value = 25
    r = _svc(repo).get_list(
        {"page": 2, "limit": 10, "sort_by": "issue_date", "sort_order": "desc"}
    )
    assert r["pagination"]["totalPages"] == 3  # ceil(25/10)
    assert r["pagination"]["page"] == 2


def test_get_by_id_attaches_items():
    repo = MagicMock()
    repo.find_by_id.return_value = {**td.invoice(), "id": 1}
    repo.find_items.return_value = [
        td.invoice_item(),
        td.invoice_item({"name": "브레이크오일"}),
    ]
    r = _svc(repo).get_by_id(1)
    assert r["id"] == 1 and r["items"][1]["name"] == "브레이크오일"


def test_get_by_id_none_skips_find_items():
    repo = MagicMock()
    repo.find_by_id.return_value = None
    assert _svc(repo).get_by_id(42) is None
    repo.find_items.assert_not_called()


def test_delete_delegates():
    repo = MagicMock()
    repo.delete.return_value = True
    assert _svc(repo).delete(1) is True
    repo.delete.assert_called_once_with(1)


def test_delete_false_when_missing():
    repo = MagicMock()
    repo.delete.return_value = False
    assert _svc(repo).delete(999) is False


def test_create_inserts_invoice_items_and_usage():
    data = td.invoice_with_items()
    repo = MagicMock()
    repo.insert.return_value = 1
    repo.find_by_id.return_value = {**data, "id": 1}
    repo.find_items.return_value = data["items"]
    company, item = MagicMock(), MagicMock()
    r = _svc(repo, company, item).create(data)
    assert r["id"] == 1
    assert repo.insert_item.call_count == 3
    company.increment_usage_by_name.assert_called_once_with("한양운수")
    assert item.increment_usage_by_name.call_count == 3


def test_create_without_recipient_skips_company_usage():
    data = td.invoice_with_items({"recipient": ""})
    repo = MagicMock()
    repo.insert.return_value = 1
    repo.find_by_id.return_value = {**data, "id": 1}
    repo.find_items.return_value = data["items"]
    company = MagicMock()
    _svc(repo, company).create(data)
    company.increment_usage_by_name.assert_not_called()


def test_update_returns_none_when_missing():
    repo = MagicMock()
    repo.find_by_id.return_value = None
    assert _svc(repo).update(999, td.invoice_with_items()) is None


def test_update_replaces_items():
    data = td.invoice_with_items()
    repo = MagicMock()
    repo.find_by_id.return_value = {**data, "id": 1}
    repo.find_items.return_value = data["items"]
    r = _svc(repo).update(1, data)
    assert r["id"] == 1
    repo.update.assert_called_once()
    repo.delete_items.assert_called_once_with(1)
    assert repo.insert_item.call_count == 3


def test_duplicate_none_when_original_missing():
    repo = MagicMock()
    repo.find_by_id.return_value = None
    assert _svc(repo).duplicate(999) is None


def test_duplicate_creates_from_original_with_today_and_stripped_ids():
    original = {
        **td.invoice_with_items(),
        "id": 1,
        "created_at": "2025-01-01",
        "updated_at": "2025-01-01",
    }
    created = {**td.invoice(), "id": 2}

    repo = MagicMock()
    repo.find_by_id.side_effect = lambda i: (
        original if i == 1 else (created if i == 2 else None)
    )
    repo.find_items.return_value = original["items"]
    repo.insert.return_value = 2

    captured = {}

    def _capture_insert(d):
        captured.update(d)
        return 2

    repo.insert.side_effect = _capture_insert
    r = _svc(repo).duplicate(1)
    assert r["id"] == 2
    assert "id" not in captured and "created_at" not in captured  # id/타임스탬프 제거
