from contextlib import nullcontext
from unittest.mock import MagicMock

import pytest

from app.core.errors import AppError
from app.services.salespeople_service import SalespersonService


def _svc(repo):
    return SalespersonService(repo, transaction=nullcontext)


def test_get_list_delegates_to_repo():
    repo = MagicMock()
    repo.find_all.return_value = [{"id": 1, "name": "A"}]
    assert _svc(repo).get_list() == [{"id": 1, "name": "A"}]


def test_create_trims_name():
    repo = MagicMock()
    repo.find_active_by_name.return_value = None
    repo.insert.return_value = 1
    repo.find_by_id.return_value = {
        "id": 1,
        "name": "홍길동",
        "sort_order": 0,
        "is_active": 1,
    }

    result = _svc(repo).create({"name": "  홍길동  "})

    assert result["name"] == "홍길동"
    assert repo.insert.call_args[0][0]["name"] == "홍길동"  # trim된 값으로 insert
    assert repo.insert.call_args[0][0]["is_active"] == 1  # 항상 활성 고정


def test_create_defaults_sort_order_zero():
    repo = MagicMock()
    repo.find_active_by_name.return_value = None
    repo.insert.return_value = 1
    repo.find_by_id.return_value = {
        "id": 1,
        "name": "x",
        "sort_order": 0,
        "is_active": 1,
    }

    _svc(repo).create({"name": "x"})
    assert repo.insert.call_args[0][0]["sort_order"] == 0


def test_create_rejects_control_char():
    with pytest.raises(AppError) as ei:
        _svc(MagicMock()).create({"name": "홍길\x01동"})
    assert ei.value.status == 400
    assert ei.value.details == {"name": "제어문자 거부"}


def test_create_rejects_empty_after_trim():
    with pytest.raises(AppError) as ei:
        _svc(MagicMock()).create({"name": "   "})
    assert ei.value.status == 400
    assert ei.value.details == {"name": "이름은 필수입니다."}


def test_create_rejects_over_100_chars():
    with pytest.raises(AppError) as ei:
        _svc(MagicMock()).create({"name": "가" * 101})
    assert ei.value.status == 400
    assert ei.value.details == {"name": "100자 초과"}


def test_create_blocks_duplicate_active_name():
    repo = MagicMock()
    repo.find_active_by_name.return_value = {"id": 5, "name": "홍길동"}

    with pytest.raises(AppError) as ei:
        _svc(repo).create({"name": "홍길동"})
    assert ei.value.status == 409
    assert ei.value.code == "DUPLICATE_NAME"
    repo.find_active_by_name.assert_called_once_with("홍길동", None)


def test_create_allows_emoji_and_korean():
    repo = MagicMock()
    repo.find_active_by_name.return_value = None
    repo.insert.return_value = 1
    repo.find_by_id.return_value = {
        "id": 1,
        "name": "🌟홍길동",
        "sort_order": 0,
        "is_active": 1,
    }

    result = _svc(repo).create({"name": "🌟홍길동"})
    assert result["name"] == "🌟홍길동"


def test_update_returns_none_when_missing():
    repo = MagicMock()
    repo.find_by_id.return_value = None
    assert _svc(repo).update(999, {"name": "x"}) is None


def test_update_excludes_self_from_duplicate_check():
    existing = {"id": 3, "name": "홍길동", "sort_order": 0, "is_active": 1}
    repo = MagicMock()
    repo.find_by_id.return_value = existing
    repo.find_active_by_name.return_value = None
    repo.update.return_value = True

    _svc(repo).update(3, {"name": "홍길동"})
    repo.find_active_by_name.assert_called_once_with("홍길동", 3)


def test_update_keeps_existing_sort_order_and_active_when_omitted():
    existing = {"id": 3, "name": "홍길동", "sort_order": 7, "is_active": 0}
    repo = MagicMock()
    repo.find_by_id.return_value = existing
    repo.find_active_by_name.return_value = None

    _svc(repo).update(3, {"name": "홍길동수정"})
    payload = repo.update.call_args[0][1]
    assert payload["sort_order"] == 7
    assert payload["is_active"] == 0


def test_soft_delete_delegates():
    repo = MagicMock()
    repo.soft_delete.return_value = True
    assert _svc(repo).soft_delete(7) is True
    repo.soft_delete.assert_called_once_with(7)
