import pytest
from sqlalchemy import text

from app import db
from tests.fixtures import sales_records_data as td

pytestmark = pytest.mark.usefixtures("db_conn")


def _insert_salesperson(overrides: dict | None = None) -> int:
    sp = td.salesperson(overrides)
    with db.connection() as conn:
        result = conn.execute(
            text(
                "INSERT INTO salespeople (name, sort_order, is_active) VALUES (:name, :sort_order, :is_active)"
            ),
            sp,
        )
        return int(result.lastrowid)


def test_store_upsert_creates_201_structured(client):
    sp_id = _insert_salesperson()
    r = client.post(
        "/api/sales-records",
        json={
            "salesperson_id": sp_id,
            "work_date": "2026-05-15",
            "quantity": 1000000,
        },
    )
    assert r.status_code == 201
    b = r.json()
    assert b["success"] is True
    assert b["data"]["salesperson_id"] == sp_id
    assert b["data"]["work_date"] == "2026-05-15"
    assert b["data"]["quantity"] == 1000000
    assert b["data"]["snapshot_name"] == "영업사원1"  # 서버가 채움


def test_store_ignores_client_snapshot_name(client):
    sp_id = _insert_salesperson({"name": "홍길동"})
    r = client.post(
        "/api/sales-records",
        json={
            "salesperson_id": sp_id,
            "work_date": "2026-05-15",
            "quantity": 100,
            "snapshot_name": "조작된이름",
        },
    )
    assert r.json()["data"]["snapshot_name"] == "홍길동"  # 클라 입력 무시


def test_store_upsert_updates_existing(client):
    sp_id = _insert_salesperson()
    client.post(
        "/api/sales-records",
        json={"salesperson_id": sp_id, "work_date": "2026-05-15", "quantity": 100},
    )
    r = client.post(
        "/api/sales-records",
        json={"salesperson_id": sp_id, "work_date": "2026-05-15", "quantity": 200},
    )
    assert r.status_code == 201
    assert r.json()["data"]["quantity"] == 200

    g = client.get("/api/sales-records", params={"year": 2026, "month": 5})
    assert len(g.json()["data"]["records"]) == 1  # 중복 없이 갱신


def test_store_unknown_salesperson_404(client):
    r = client.post(
        "/api/sales-records",
        json={
            "salesperson_id": 99999,
            "work_date": "2026-05-15",
            "quantity": 100,
        },
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_store_validation_missing_fields(client):
    r = client.post("/api/sales-records", json={"salesperson_id": 1})
    assert r.status_code == 400
    b = r.json()
    assert b["success"] is False
    assert b["error"]["code"] == "VALIDATION_ERROR"
    assert "work_date" in b["error"]["details"]
    assert "quantity" in b["error"]["details"]


def test_store_rejects_bad_date_format(client):
    sp_id = _insert_salesperson()
    r = client.post(
        "/api/sales-records",
        json={
            "salesperson_id": sp_id,
            "work_date": "2026/05/15",
            "quantity": 100,
        },
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "work_date" in r.json()["error"]["details"]


def test_store_rejects_negative_quantity(client):
    sp_id = _insert_salesperson()
    r = client.post(
        "/api/sales-records",
        json={
            "salesperson_id": sp_id,
            "work_date": "2026-05-15",
            "quantity": -100,
        },
    )
    assert r.status_code == 400
    assert r.json()["error"]["details"]["quantity"] == "out of range"


def test_store_rejects_over_max_quantity(client):
    sp_id = _insert_salesperson()
    r = client.post(
        "/api/sales-records",
        json={
            "salesperson_id": sp_id,
            "work_date": "2026-05-15",
            "quantity": 1000000000,
        },
    )
    assert r.status_code == 400
    assert r.json()["error"]["details"]["quantity"] == "out of range"


def test_store_rejects_non_integer_quantity(client):
    sp_id = _insert_salesperson()
    r = client.post(
        "/api/sales-records",
        json={
            "salesperson_id": sp_id,
            "work_date": "2026-05-15",
            "quantity": 1.5,
        },
    )
    assert r.status_code == 400
    assert r.json()["error"]["details"]["quantity"] == "integer required"


def test_index_validates_year_month(client):
    r = client.get("/api/sales-records", params={"year": 2026, "month": 13})
    assert r.status_code == 400
    b = r.json()
    assert b["error"]["code"] == "VALIDATION_ERROR"
    assert b["error"]["details"] == {"year": 2026, "month": 13}


def test_index_returns_single_aggregate_no_pagination(client):
    sp_id = _insert_salesperson()
    client.post(
        "/api/sales-records",
        json={"salesperson_id": sp_id, "work_date": "2026-05-15", "quantity": 1000},
    )
    r = client.get("/api/sales-records", params={"year": 2026, "month": 5})
    assert r.status_code == 200
    b = r.json()
    assert b["success"] is True
    assert "pagination" not in b  # 단일 집계 객체(목록 아님)
    assert "salespeople" in b["data"]
    assert "records" in b["data"]
    assert len(b["data"]["records"]) == 1
    assert b["data"]["records"][0]["work_date"] == "2026-05-15"


def test_index_salespeople_includes_active_and_record_holders(client):
    _insert_salesperson({"name": "Active", "sort_order": 1, "is_active": 1})
    inactive_id = _insert_salesperson(
        {"name": "Inactive", "sort_order": 2, "is_active": 0}
    )
    client.post(
        "/api/sales-records",
        json={
            "salesperson_id": inactive_id,
            "work_date": "2026-05-10",
            "quantity": 500,
        },
    )

    r = client.get("/api/sales-records", params={"year": 2026, "month": 5})
    names = [s["name"] for s in r.json()["data"]["salespeople"]]
    assert "Active" in names
    assert "Inactive" in names


def test_destroy_removes_then_404(client):
    sp_id = _insert_salesperson()
    client.post(
        "/api/sales-records",
        json={"salesperson_id": sp_id, "work_date": "2026-05-15", "quantity": 100},
    )
    rec = client.get("/api/sales-records", params={"year": 2026, "month": 5}).json()[
        "data"
    ]["records"][0]

    d = client.delete(f"/api/sales-records/{rec['id']}")
    assert d.status_code == 200
    assert d.json()["success"] is True
    assert d.json()["data"] is None

    assert client.delete(f"/api/sales-records/{rec['id']}").status_code == 404


def test_destroy_404_when_missing(client):
    r = client.delete("/api/sales-records/999999")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"
