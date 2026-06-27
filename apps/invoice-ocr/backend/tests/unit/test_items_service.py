from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.errors import AppError
from app.services.items_service import ItemService
from tests.fixtures import items_data as td


def test_get_list_returns_paginated_data():
    repo = MagicMock()
    repo.find_all.return_value = [
        {**td.item(), "id": 1},
        {**td.item({"item_name": "브레이크오일"}), "id": 2},
    ]
    r = ItemService(repo).get_list({})
    assert len(r["data"]) == 2
    # ItemService는 항상 page=1, limit=9999, totalPages=1
    assert r["pagination"] == {"page": 1, "limit": 9999, "total": 2, "totalPages": 1}


def test_get_list_empty():
    repo = MagicMock()
    repo.find_all.return_value = []
    r = ItemService(repo).get_list({})
    assert r["data"] == []
    assert r["pagination"]["total"] == 0


def test_get_by_id_returns_item():
    repo = MagicMock()
    repo.find_by_id.return_value = {**td.item(), "id": 4}
    r = ItemService(repo).get_by_id(4)
    assert r["id"] == 4
    assert r["item_name"] == "엔진오일"
    repo.find_by_id.assert_called_once_with(4)


def test_get_by_id_returns_none():
    repo = MagicMock()
    repo.find_by_id.return_value = None
    assert ItemService(repo).get_by_id(999) is None


def test_create_returns_new_item():
    data = td.item()
    repo = MagicMock()
    repo.insert.return_value = 7
    repo.find_by_id.return_value = {**data, "id": 7}
    r = ItemService(repo).create(data)
    assert r["id"] == 7
    assert r["item_name"] == "엔진오일"
    repo.insert.assert_called_once_with(data)


def test_create_duplicate_name_raises_409():
    repo = MagicMock()
    repo.insert.side_effect = IntegrityError("INSERT", {}, Exception("duplicate"))
    with pytest.raises(AppError) as exc:
        ItemService(repo).create(td.item())
    assert exc.value.status == 409
    assert exc.value.code == "DUPLICATE_NAME"
    assert exc.value.message == "이미 등록된 품목입니다."


def test_update_returns_updated_item():
    data = td.item({"item_name": "타이어"})
    existing = {**td.item(), "id": 3}
    updated = {**data, "id": 3}
    repo = MagicMock()
    repo.find_by_id.side_effect = [existing, updated]
    r = ItemService(repo).update(3, data)
    assert r["item_name"] == "타이어"
    repo.update.assert_called_once_with(3, data)


def test_update_returns_none_when_not_found():
    repo = MagicMock()
    repo.find_by_id.return_value = None
    assert ItemService(repo).update(999, td.item()) is None
    repo.update.assert_not_called()


def test_delete_returns_true():
    repo = MagicMock()
    repo.delete.return_value = True
    assert ItemService(repo).delete(1) is True
    repo.delete.assert_called_once_with(1)


def test_delete_returns_false():
    repo = MagicMock()
    repo.delete.return_value = False
    assert ItemService(repo).delete(999) is False
