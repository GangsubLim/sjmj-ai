import pytest

from app.repositories.salespeople_repository import SalespersonRepository
from tests.fixtures import salespeople_data as td

pytestmark = pytest.mark.usefixtures("db_conn")


def test_insert_and_find_by_id():
    repo = SalespersonRepository()
    new_id = repo.insert(td.salesperson())
    row = repo.find_by_id(new_id)
    assert row["name"] == "영업사원1"
    assert row["sort_order"] == 0        # INT → int
    assert row["is_active"] == 1         # TINYINT → 1, not True


def test_find_by_id_none_when_missing():
    assert SalespersonRepository().find_by_id(999999) is None


def test_find_all_orders_by_active_then_sort_then_id():
    repo = SalespersonRepository()
    repo.insert(td.salesperson({"name": "A", "sort_order": 1}))
    repo.insert(td.salesperson({"name": "B", "sort_order": 0}))
    rows = repo.find_all()
    assert len(rows) == 2
    assert rows[0]["name"] == "B"        # sort_order ASC


def test_find_all_inactive_sorted_last():
    repo = SalespersonRepository()
    active = repo.insert(td.salesperson({"name": "활성", "sort_order": 9}))
    inactive = repo.insert(td.salesperson({"name": "비활성", "sort_order": 0}))
    repo.soft_delete(inactive)
    rows = repo.find_all()
    assert [r["id"] for r in rows] == [active, inactive]  # is_active DESC 우선


def test_update_changes_fields():
    repo = SalespersonRepository()
    new_id = repo.insert(td.salesperson())
    assert repo.update(new_id, {"name": "영업사원1수정", "sort_order": 5, "is_active": 1}) is True
    row = repo.find_by_id(new_id)
    assert row["name"] == "영업사원1수정"
    assert row["sort_order"] == 5


def test_soft_delete_sets_inactive_not_removed():
    repo = SalespersonRepository()
    new_id = repo.insert(td.salesperson())
    assert repo.soft_delete(new_id) is True
    row = repo.find_by_id(new_id)
    assert row is not None               # 행은 남는다(FK RESTRICT)
    assert row["is_active"] == 0


def test_soft_delete_already_inactive_returns_false():
    repo = SalespersonRepository()
    new_id = repo.insert(td.salesperson())
    repo.soft_delete(new_id)
    assert repo.soft_delete(new_id) is False   # rowcount 0


def test_find_active_by_name_matches_only_active():
    repo = SalespersonRepository()
    id1 = repo.insert(td.salesperson({"name": "홍길동"}))
    id2 = repo.insert(td.salesperson({"name": "홍길동"}))
    repo.soft_delete(id1)
    match = repo.find_active_by_name("홍길동")
    assert match is not None
    assert match["id"] == id2


def test_find_active_by_name_excludes_id():
    repo = SalespersonRepository()
    new_id = repo.insert(td.salesperson({"name": "홍길동"}))
    assert repo.find_active_by_name("홍길동", new_id) is None
