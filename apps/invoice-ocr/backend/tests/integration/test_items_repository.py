import pytest
from sqlalchemy.exc import DatabaseError

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


# ── ensure_exists — 정식 라벨 단방향 등록(ADR 0008, #40) ────────────────────


def test_ensure_exists_registers_missing_name():
    """없는 이름은 default_unit='EA'로 등록된다(반환값 없음 — 테이블 조회로 단언)."""
    repo = ItemRepository()

    repo.ensure_exists("배선수리")

    rows = repo.find_all(_filters(q="배선수리"))
    assert len(rows) == 1
    assert rows[0]["item_name"] == "배선수리"
    assert rows[0]["default_unit"] == "EA"
    assert rows[0]["default_unit_price"] == 0
    assert rows[0]["usage_count"] == 0
    assert rows[0]["category"] is None
    assert rows[0]["last_used"] is None


def test_ensure_exists_returns_none():
    """rowcount가 신규(1)와 중복(1)을 구분하지 못하므로 반환값을 두지 않는다(spec §3.1)."""
    assert ItemRepository().ensure_exists("휠") is None


def test_ensure_exists_is_idempotent_and_preserves_existing_row():
    """재호출은 행을 늘리지 않고 기존 행의 **모든 컬럼**을 그대로 둔다.

    일부 컬럼만 골라 단언하면 ON DUPLICATE KEY UPDATE가 나머지(default_unit_price·notes)를
    건드리도록 바뀌어도 통과한다 — 행 전체 동등성으로 닫는다.
    """
    repo = ItemRepository()
    repo.insert(td.item({"item_name": "라이닝1조", "category": "부품", "default_unit": "SET"}))
    repo.increment_usage_by_name("라이닝1조")
    before = repo.find_all(_filters(q="라이닝1조"))[0]
    # 대조군 사전 조건 — 기본값과 구분되는 상태에서 출발해야 보존 단언이 공허하지 않다.
    assert before["usage_count"] == 1
    assert before["last_used"] is not None
    assert (before["category"], before["default_unit"]) == ("부품", "SET")

    repo.ensure_exists("라이닝1조")

    rows = repo.find_all(_filters(q="라이닝1조"))
    assert len(rows) == 1
    assert rows[0] == before  # id·단가·비고까지 포함한 행 전체가 그대로


def test_ensure_exists_propagates_length_overflow_instead_of_swallowing():
    """길이 초과는 예외로 전파돼야 한다 — INSERT IGNORE 배제 근거의 행동 고정(spec §3.1).

    `INSERT IGNORE`로 바꾸면 STRICT_TRANS_TABLES에서도 이 에러가 경고로 낮아져,
    200자로 잘린 이름이 조용히 사전에 등록된다(호출자는 성공으로 본다).
    """
    too_long = "가" * 201  # item_suggestions.item_name VARCHAR(200)

    with pytest.raises(DatabaseError):
        ItemRepository().ensure_exists(too_long)

    # 잘린 이름이 남지 않았음까지 본다 — IGNORE 뮤턴트가 남기는 흔적이 바로 이것이다.
    assert ItemRepository().find_all(_filters(q="가" * 200)) == []
