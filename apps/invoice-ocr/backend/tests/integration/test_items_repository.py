import pytest

from app.repositories.items_repository import ItemRepository
from tests.fixtures import items_data as td

pytestmark = pytest.mark.usefixtures("db_conn")


def _filters(**kw):
    base = {"q": "", "category": "", "sort_by": "item_name"}
    base.update(kw)
    return base


def test_insert_and_find_by_id():
    repo = ItemRepository()
    data = td.item()
    new_id = repo.insert(data)
    row = repo.find_by_id(new_id)
    assert row["id"] == new_id
    assert row["item_name"] == "엔진오일"
    assert row["default_unit"] == "EA"
    assert row["default_unit_price"] == 30000  # INT → int
    assert row["category"] == "오일"
    assert row["notes"] == "테스트 품목"


def test_find_by_id_none_when_missing():
    assert ItemRepository().find_by_id(99999) is None


def test_find_all_with_search():
    repo = ItemRepository()
    repo.insert(td.item({"item_name": "검색엔진오일"}))
    repo.insert(td.item({"item_name": "검색브레이크오일"}))
    repo.insert(td.item({"item_name": "전혀다른품목"}))
    rows = repo.find_all(_filters(q="검색엔진"))
    assert len(rows) == 1
    assert rows[0]["item_name"] == "검색엔진오일"


def test_find_all_search_returns_all_matching():
    repo = ItemRepository()
    repo.insert(td.item({"item_name": "오일필터A"}))
    repo.insert(td.item({"item_name": "오일필터B"}))
    repo.insert(td.item({"item_name": "타이어교체"}))
    rows = repo.find_all(_filters(q="오일필터"))
    names = {r["item_name"] for r in rows}
    assert names == {"오일필터A", "오일필터B"}


def test_find_all_with_category_filter():
    repo = ItemRepository()
    repo.insert(td.item({"item_name": "카테고리오일A", "category": "오일"}))
    repo.insert(td.item({"item_name": "카테고리오일B", "category": "오일"}))
    repo.insert(td.item({"item_name": "카테고리타이어", "category": "타이어"}))
    rows = repo.find_all(_filters(category="오일"))
    assert len(rows) == 2
    assert all(r["category"] == "오일" for r in rows)


def test_find_all_category_excludes_other_categories():
    repo = ItemRepository()
    repo.insert(td.item({"item_name": "부품A", "category": "부품"}))
    repo.insert(td.item({"item_name": "공임A", "category": "공임"}))
    rows = repo.find_all(_filters(category="공임"))
    names = [r["item_name"] for r in rows]
    assert "공임A" in names
    assert "부품A" not in names


def test_find_all_sort_by_name_ascending():
    repo = ItemRepository()
    repo.insert(td.item({"item_name": "차나가나품목"}))
    repo.insert(td.item({"item_name": "가나나다품목"}))
    repo.insert(td.item({"item_name": "나다라마품목"}))
    rows = repo.find_all(_filters(sort_by="item_name"))
    names = [r["item_name"] for r in rows]
    assert names == sorted(names)


def test_sort_whitelist_rejects_injection():
    repo = ItemRepository()
    repo.insert(td.item())
    rows = repo.find_all(_filters(sort_by="DROP TABLE"))  # → item_name 보정
    assert isinstance(rows, list) and len(rows) == 1


def test_update_item():
    repo = ItemRepository()
    new_id = repo.insert(td.item({"item_name": "업데이트전품목"}))
    ok = repo.update(
        new_id,
        td.item(
            {
                "item_name": "업데이트후품목",
                "default_unit_price": 99000,
                "category": "타이어",
            }
        ),
    )
    assert ok is True
    row = repo.find_by_id(new_id)
    assert row["item_name"] == "업데이트후품목"
    assert row["default_unit_price"] == 99000
    assert row["category"] == "타이어"


def test_delete_item():
    repo = ItemRepository()
    new_id = repo.insert(td.item({"item_name": "삭제될품목"}))
    assert repo.delete(new_id) is True
    assert repo.find_by_id(new_id) is None


def test_delete_returns_false_when_missing():
    assert ItemRepository().delete(99999) is False


def test_increment_usage_by_name():
    repo = ItemRepository()
    new_id = repo.insert(td.item({"item_name": "품목사용증가테스트"}))
    before = repo.find_by_id(new_id)
    assert before["usage_count"] == 0
    assert before["last_used"] is None
    repo.increment_usage_by_name("품목사용증가테스트")
    after = repo.find_by_id(new_id)
    assert after["usage_count"] == 1
    assert after["last_used"] is not None


def test_increment_usage_by_name_accumulates():
    repo = ItemRepository()
    new_id = repo.insert(td.item({"item_name": "품목누적사용테스트"}))
    repo.increment_usage_by_name("품목누적사용테스트")
    repo.increment_usage_by_name("품목누적사용테스트")
    assert repo.find_by_id(new_id)["usage_count"] == 2
