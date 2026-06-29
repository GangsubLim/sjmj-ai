"""contract 골든 — TestClient + 실DB. PHP CompanyControllerTest 동치."""

import pytest

from tests.fixtures import companies_data as cd
from tests.fixtures import test_data as td

pytestmark = pytest.mark.usefixtures("db_conn")


def _create(client, **ov):
    return client.post("/api/companies", json=cd.company(ov))


def test_index_returns_list(client):
    _create(client)
    r = client.get("/api/companies")
    assert r.status_code == 200
    b = r.json()
    assert b["success"] is True
    assert isinstance(b["data"], list)
    assert len(b["data"]) == 1
    assert set(b["pagination"]) == {"page", "limit", "total", "totalPages"}
    assert b["pagination"]["limit"] == 9999
    assert b["pagination"]["total"] == 1


def test_index_rejects_invalid_sort_by(client):
    _create(client)
    r = client.get("/api/companies", params={"sort_by": "DROP TABLE"})
    assert r.status_code == 200
    assert r.json()["success"] is True


def test_show_returns_company(client):
    cid = _create(client).json()["data"]["id"]
    b = client.get(f"/api/companies/{cid}").json()
    assert b["success"] is True
    assert b["data"]["id"] == cid
    assert b["data"]["company_name"] == "한양운수"


def test_show_404_structured(client):
    r = client.get("/api/companies/999999")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_store_creates_and_returns_201(client):
    r = _create(client)
    assert r.status_code == 201
    b = r.json()
    assert b["success"] is True
    assert b["data"]["company_name"] == "한양운수"


def test_store_fails_without_company_name(client):
    body = cd.company()
    del body["company_name"]
    r = client.post("/api/companies", json=body)
    assert r.status_code == 400
    b = r.json()
    assert b["success"] is False
    assert b["error"]["code"] == "VALIDATION_ERROR"
    assert "company_name" in b["error"]["details"]


def test_store_fails_with_invalid_business_number(client):
    r = _create(client, business_number="123456789")  # 9자리 → invalid
    assert r.status_code == 400
    b = r.json()
    assert b["error"]["code"] == "VALIDATION_ERROR"
    assert "business_number" in b["error"]["details"]


def test_update_returns_company(client):
    cid = _create(client).json()["data"]["id"]
    r = client.put(
        f"/api/companies/{cid}", json=cd.company({"company_name": "수정거래처"})
    )
    assert r.status_code == 200
    assert r.json()["data"]["company_name"] == "수정거래처"


def test_update_404(client):
    r = client.put("/api/companies/999999", json=cd.company())
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_destroy_then_404(client):
    cid = _create(client).json()["data"]["id"]
    d = client.delete(f"/api/companies/{cid}")
    assert d.status_code == 200 and d.json()["success"] is True
    assert client.delete(f"/api/companies/{cid}").status_code == 404


def test_invoices_returns_list(client):
    cid = _create(client, company_name="인보이스연결거래처").json()["data"]["id"]
    client.post(
        "/api/invoices", json=td.invoice_with_items({"recipient": "인보이스연결거래처"})
    )
    r = client.get(f"/api/companies/{cid}/invoices")
    assert r.status_code == 200
    b = r.json()
    assert b["success"] is True
    assert isinstance(b["data"], list)
    assert len(b["data"]) == 1
    assert b["pagination"]["limit"] == 9999


def test_invoices_404_when_company_missing(client):
    r = client.get("/api/companies/999999/invoices")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"
