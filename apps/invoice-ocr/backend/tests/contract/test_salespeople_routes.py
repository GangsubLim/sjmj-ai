import pytest

pytestmark = pytest.mark.usefixtures("db_conn")


def _create(client, **ov):
    return client.post("/api/salespeople", json={"name": "영업사원1", **ov})


def test_store_creates_and_returns_201_structured(client):
    r = _create(client, name="홍길동")
    assert r.status_code == 201
    b = r.json()
    assert b["success"] is True
    assert b["data"]["name"] == "홍길동"
    assert b["data"]["is_active"] == 1
    assert b["data"]["sort_order"] == 0


def test_store_trims_name(client):
    b = _create(client, name="  김영업  ").json()
    assert b["data"]["name"] == "김영업"


def test_store_requires_name(client):
    r = client.post("/api/salespeople", json={})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_store_rejects_control_char(client):
    r = client.post("/api/salespeople", json={"name": "홍길동"})
    assert r.status_code == 400
    b = r.json()
    assert b["error"]["code"] == "VALIDATION_ERROR"
    assert b["error"]["details"] == {"name": "제어문자 거부"}


def test_store_duplicate_active_name_409(client):
    _create(client, name="홍길동")
    r = _create(client, name="홍길동")
    assert r.status_code == 409
    b = r.json()
    assert b["error"]["code"] == "DUPLICATE_NAME"
    assert b["error"]["message"] == "이미 등록된 영업사원 이름입니다."


def test_store_allows_emoji(client):
    b = _create(client, name="🌟홍길동").json()
    assert b["data"]["name"] == "🌟홍길동"


def test_index_fake_pagination_and_ordering(client):
    _create(client, name="A", sort_order=1)
    _create(client, name="B", sort_order=0)
    r = client.get("/api/salespeople")
    b = r.json()
    assert b["success"] is True
    assert [s["name"] for s in b["data"]] == ["B", "A"]  # sort_order ASC
    assert b["pagination"] == {"page": 1, "limit": 2, "total": 2, "totalPages": 1}


def test_index_inactive_listed_last(client):
    _create(client, name="활성", sort_order=9)
    sid = _create(client, name="비활성", sort_order=0).json()["data"]["id"]
    client.delete(f"/api/salespeople/{sid}")
    names = [s["name"] for s in client.get("/api/salespeople").json()["data"]]
    assert names == ["활성", "비활성"]  # is_active DESC 우선


def test_update_replaces_and_returns(client):
    sid = _create(client, name="원본").json()["data"]["id"]
    r = client.put(f"/api/salespeople/{sid}", json={"name": "수정", "sort_order": 3})
    assert r.status_code == 200
    b = r.json()
    assert b["data"]["name"] == "수정"
    assert b["data"]["sort_order"] == 3


def test_update_same_name_ok_excludes_self(client):
    sid = _create(client, name="홍길동").json()["data"]["id"]
    r = client.put(f"/api/salespeople/{sid}", json={"name": "홍길동", "sort_order": 5})
    assert r.status_code == 200
    assert r.json()["data"]["sort_order"] == 5


def test_update_404_when_missing(client):
    r = client.put("/api/salespeople/999999", json={"name": "x"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_update_requires_name(client):
    sid = _create(client, name="홍길동").json()["data"]["id"]
    r = client.put(f"/api/salespeople/{sid}", json={})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_destroy_soft_delete_then_404(client):
    sid = _create(client, name="홍길동").json()["data"]["id"]
    d = client.delete(f"/api/salespeople/{sid}")
    assert d.status_code == 200
    b = d.json()
    assert b["success"] is True
    assert b["data"] is None
    assert b["message"] == "비활성화되었습니다."
    assert client.delete(f"/api/salespeople/{sid}").status_code == 404  # 이미 비활성


def test_destroy_404_when_missing(client):
    r = client.delete("/api/salespeople/999999")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"
