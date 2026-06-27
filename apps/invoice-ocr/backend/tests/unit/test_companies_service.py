"""service 골든 — PHP CompanyServiceTest 동치(mock repo)."""
from unittest.mock import MagicMock

from app.services.companies_service import CompanyService
from tests.fixtures import companies_data as cd
from tests.fixtures import test_data as td


def test_get_list_returns_paginated_data():
    repo = MagicMock()
    repo.find_all.return_value = [
        {**cd.company(), "id": 1},
        {**cd.company({"company_name": "대성물류"}), "id": 2},
    ]
    r = CompanyService(repo).get_list({})
    assert len(r["data"]) == 2
    assert r["pagination"] == {"page": 1, "limit": 9999, "total": 2, "totalPages": 1}


def test_get_list_empty_pagination():
    repo = MagicMock()
    repo.find_all.return_value = []
    r = CompanyService(repo).get_list({})
    assert len(r["data"]) == 0
    assert r["pagination"]["total"] == 0


def test_get_by_id_returns_company():
    repo = MagicMock()
    repo.find_by_id.return_value = {**cd.company(), "id": 3}
    r = CompanyService(repo).get_by_id(3)
    assert r["id"] == 3
    assert r["company_name"] == "한양운수"


def test_get_by_id_returns_none():
    repo = MagicMock()
    repo.find_by_id.return_value = None
    assert CompanyService(repo).get_by_id(999) is None


def test_create_returns_new_company():
    data = cd.company()
    created = {**data, "id": 5}
    repo = MagicMock()
    repo.insert.return_value = 5
    repo.find_by_id.return_value = created

    r = CompanyService(repo).create(data)
    assert r["id"] == 5
    assert r["company_name"] == "한양운수"
    repo.insert.assert_called_once_with(data)


def test_update_returns_updated_company():
    existing = {**cd.company(), "id": 2}
    updated = {**cd.company({"company_name": "대성물류"}), "id": 2}
    data = cd.company({"company_name": "대성물류"})

    repo = MagicMock()
    repo.find_by_id.side_effect = [existing, updated]

    r = CompanyService(repo).update(2, data)
    assert r is not None
    assert r["company_name"] == "대성물류"
    repo.update.assert_called_once_with(2, data)


def test_update_returns_none_when_not_found():
    repo = MagicMock()
    repo.find_by_id.return_value = None
    r = CompanyService(repo).update(999, cd.company())
    assert r is None
    repo.update.assert_not_called()


def test_delete_returns_true():
    repo = MagicMock()
    repo.delete.return_value = True
    assert CompanyService(repo).delete(1) is True
    repo.delete.assert_called_once_with(1)


def test_delete_returns_false():
    repo = MagicMock()
    repo.delete.return_value = False
    assert CompanyService(repo).delete(999) is False


def test_get_invoices_none_when_company_not_found():
    repo = MagicMock()
    repo.find_by_id.return_value = None
    r = CompanyService(repo).get_invoices(999)
    assert r is None
    repo.find_invoices_by_company_id.assert_not_called()


def test_get_invoices_returns_list():
    repo = MagicMock()
    repo.find_by_id.return_value = {**cd.company(), "id": 1}
    repo.find_invoices_by_company_id.return_value = [
        {**td.invoice(), "id": 10},
        {**td.invoice({"issue_date": "2025-02-01"}), "id": 11},
    ]
    r = CompanyService(repo).get_invoices(1)
    assert isinstance(r, list)
    assert len(r) == 2
    assert r[0]["id"] == 10
