import pytest

from tests.fixtures import test_data as td

pytestmark = pytest.mark.usefixtures("db_conn")


def _create(client, **ov):
    return client.post("/api/invoices", json=td.invoice_with_items(ov))


def test_store_creates_and_returns_201_structured(client):
    r = _create(client)
    assert r.status_code == 201
    b = r.json()
    assert b["success"] is True
    assert b["data"]["recipient"] == "한양운수"
    assert b["data"]["issue_date"] == "2026-05-15"
    assert len(b["data"]["items"]) == 3


def test_store_validation_error_envelope(client):
    r = client.post("/api/invoices", json={"recipient": "x"})  # issue_date·items 누락
    assert r.status_code == 400
    b = r.json()
    assert b["success"] is False
    assert b["error"]["code"] == "VALIDATION_ERROR"
    assert "issue_date" in b["error"]["details"] and "items" in b["error"]["details"]


def test_store_rejects_bad_date(client):
    r = _create(client, issue_date="2026/05/15")
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_index_structured_pagination(client):
    _create(client)
    r = client.get("/api/invoices", params={"page": 1, "limit": 20})
    b = r.json()
    assert b["success"] is True
    assert isinstance(b["data"], list) and len(b["data"]) == 1
    assert set(b["pagination"]) == {"page", "limit", "total", "totalPages"}
    assert b["pagination"]["total"] == 1


def test_index_limit_clamped(client):
    r = client.get("/api/invoices", params={"page": 0, "limit": 0})  # page>=1, limit>=1 보정
    assert r.json()["pagination"]["page"] == 1
    assert r.json()["pagination"]["limit"] == 1


def test_show_returns_invoice_with_items(client):
    iid = _create(client).json()["data"]["id"]
    b = client.get(f"/api/invoices/{iid}").json()
    assert b["data"]["id"] == iid and len(b["data"]["items"]) == 3


def test_show_404_structured(client):
    r = client.get("/api/invoices/999999")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_update_replaces_and_returns(client):
    iid = _create(client).json()["data"]["id"]
    r = client.put(f"/api/invoices/{iid}", json=td.invoice_with_items({"recipient": "수정거래처"}))
    assert r.status_code == 200 and r.json()["data"]["recipient"] == "수정거래처"


def test_update_404(client):
    r = client.put("/api/invoices/999999", json=td.invoice_with_items())
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_destroy_then_404(client):
    iid = _create(client).json()["data"]["id"]
    d = client.delete(f"/api/invoices/{iid}")
    assert d.status_code == 200 and d.json()["success"] is True
    assert client.delete(f"/api/invoices/{iid}").status_code == 404


def test_duplicate_201(client):
    iid = _create(client).json()["data"]["id"]
    r = client.post(f"/api/invoices/{iid}/duplicate")
    assert r.status_code == 201 and r.json()["data"]["id"] != iid


def test_duplicate_404(client):
    r = client.post("/api/invoices/999999/duplicate")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_export_csv_stream(client):
    _create(client)
    r = client.get("/api/invoices/export", params={"format": "csv"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert r.content.startswith(b"\xef\xbb\xbf")   # UTF-8 BOM


def test_export_bad_format_400(client):
    r = client.get("/api/invoices/export", params={"format": "pdf"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"
