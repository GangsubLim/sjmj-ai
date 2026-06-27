import pytest

from tests.fixtures import items_data as td

pytestmark = pytest.mark.usefixtures("db_conn")


def _create(client, **ov):
    return client.post("/api/items", json=td.item(ov))


def test_store_creates_and_returns_201_structured(client):
    r = _create(client)
    assert r.status_code == 201
    b = r.json()
    assert b["success"] is True
    assert b["data"]["item_name"] == "엔진오일"
    assert b["data"]["default_unit"] == "EA"
    assert "id" in b["data"]


def test_store_validation_error_envelope(client):
    r = client.post("/api/items", json={"default_unit": "EA"})  # item_name 누락
    assert r.status_code == 400
    b = r.json()
    assert b["success"] is False
    assert b["error"]["code"] == "VALIDATION_ERROR"
    assert "item_name" in b["error"]["details"]


def test_store_item_name_too_long(client):
    r = _create(client, item_name="가" * 201)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_store_duplicate_name_409(client):
    _create(client)
    r = _create(client)   # 동일 item_name UNIQUE 위반
    assert r.status_code == 409
    b = r.json()
    assert b["success"] is False
    assert b["error"]["code"] == "DUPLICATE_NAME"
    assert b["error"]["message"] == "이미 등록된 품목입니다."


def test_index_structured_pagination(client):
    _create(client)
    r = client.get("/api/items")
    b = r.json()
    assert b["success"] is True
    assert isinstance(b["data"], list) and len(b["data"]) == 1
    assert b["pagination"] == {"page": 1, "limit": 9999, "total": 1, "totalPages": 1}


def test_index_search_filter(client):
    _create(client, item_name="검색대상오일")
    _create(client, item_name="무관품목")
    b = client.get("/api/items", params={"q": "검색대상"}).json()
    assert len(b["data"]) == 1
    assert b["data"][0]["item_name"] == "검색대상오일"


def test_index_category_filter(client):
    _create(client, item_name="필터오일", category="오일")
    _create(client, item_name="필터타이어", category="타이어")
    b = client.get("/api/items", params={"category": "타이어"}).json()
    assert len(b["data"]) == 1
    assert b["data"][0]["item_name"] == "필터타이어"


def test_index_invalid_sort_by_defaults(client):
    _create(client)
    r = client.get("/api/items", params={"sort_by": "DROP TABLE"})
    assert r.status_code == 200
    assert len(r.json()["data"]) == 1


def test_show_returns_item(client):
    iid = _create(client).json()["data"]["id"]
    b = client.get(f"/api/items/{iid}").json()
    assert b["data"]["id"] == iid
    assert b["data"]["item_name"] == "엔진오일"


def test_show_404_structured(client):
    r = client.get("/api/items/999999")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_update_replaces_and_returns(client):
    iid = _create(client).json()["data"]["id"]
    r = client.put(f"/api/items/{iid}", json=td.item({"item_name": "수정품목"}))
    assert r.status_code == 200
    assert r.json()["data"]["item_name"] == "수정품목"


def test_update_404(client):
    r = client.put("/api/items/999999", json=td.item())
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_destroy_then_404(client):
    iid = _create(client).json()["data"]["id"]
    d = client.delete(f"/api/items/{iid}")
    assert d.status_code == 200
    assert d.json()["success"] is True
    assert client.delete(f"/api/items/{iid}").status_code == 404
