import json

from app.core import envelope


def _body(resp):
    return json.loads(resp.body)


def test_list_response_shape():
    r = envelope.list_response([{"id": 1}], {"page": 1, "limit": 20, "total": 1, "totalPages": 1})
    assert r.status_code == 200
    b = _body(r)
    assert b["success"] is True
    assert b["data"] == [{"id": 1}]
    assert b["pagination"] == {"page": 1, "limit": 20, "total": 1, "totalPages": 1}


def test_single_shape():
    r = envelope.single({"id": 1, "recipient": "한양운수"})
    assert r.status_code == 200
    assert _body(r) == {"success": True, "data": {"id": 1, "recipient": "한양운수"}}


def test_created_is_201():
    r = envelope.created({"id": 5})
    assert r.status_code == 201
    assert _body(r) == {"success": True, "data": {"id": 5}}


def test_deleted_shape():
    r = envelope.deleted("거래명세서가 삭제되었습니다.")
    assert _body(r) == {
        "success": True,
        "data": None,
        "message": "거래명세서가 삭제되었습니다.",
    }
