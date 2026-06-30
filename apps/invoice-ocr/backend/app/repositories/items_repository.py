"""ItemRepository — text() raw SQL 직접 발행.

정렬 컬럼은 화이트리스트 매핑으로만 변환(문자열 보간 SQL injection 방어).
usage_count·last_used는 기본 DESC, 그 외는 ASC.
"""

from sqlalchemy import text

from app.db import connection

_ALLOWED_SORT_COLUMNS = {
    "item_name": "item_name",
    "usage_count": "usage_count",
    "last_used": "last_used",
    "category": "category",
}
_DESC_COLUMNS = {"usage_count", "last_used"}

_SELECT_COLUMNS = (
    "id, item_name, default_unit, default_unit_price, category, "
    "notes, usage_count, last_used, created_at"
)


def _rows(result) -> list[dict]:
    return [dict(m) for m in result.mappings().all()]


class ItemRepository:
    """품목 추천(item_suggestions) 테이블 데이터 접근 레포지터리."""

    def find_all(self, filters: dict) -> list[dict]:
        """필터(q·category·sort_by)에 맞는 품목 목록을 조회한다."""
        where = "1=1"
        params: dict = {}
        if filters.get("q"):
            where += " AND item_name LIKE :q"
            params["q"] = f"%{filters['q']}%"
        if filters.get("category"):
            where += " AND category = :category"
            params["category"] = filters["category"]
        col = _ALLOWED_SORT_COLUMNS.get(filters.get("sort_by"), _ALLOWED_SORT_COLUMNS["item_name"])
        order = "DESC" if col in _DESC_COLUMNS else "ASC"
        sql = f"""
            SELECT {_SELECT_COLUMNS}
            FROM item_suggestions
            WHERE {where}
            ORDER BY {col} {order}
        """
        with connection() as conn:
            return _rows(conn.execute(text(sql), params))

    def find_by_id(self, id: int) -> dict | None:
        """품목을 ID로 단건 조회한다."""
        with connection() as conn:
            row = (
                conn.execute(
                    text(f"SELECT {_SELECT_COLUMNS} FROM item_suggestions WHERE id = :id"),
                    {"id": id},
                )
                .mappings()
                .first()
            )
            return dict(row) if row else None

    def insert(self, data: dict) -> int:
        """품목을 삽입하고 생성된 id를 반환한다."""
        with connection() as conn:
            result = conn.execute(
                text("""
                INSERT INTO item_suggestions (
                    item_name, default_unit, default_unit_price, category, notes
                ) VALUES (
                    :item_name, :default_unit, :default_unit_price, :category, :notes
                )
            """),
                {
                    "item_name": data["item_name"],
                    "default_unit": data.get("default_unit", "EA"),
                    "default_unit_price": data.get("default_unit_price", 0),
                    "category": data.get("category"),
                    "notes": data.get("notes"),
                },
            )
            return int(result.lastrowid)

    def update(self, id: int, data: dict) -> bool:
        """품목을 수정하고 변경 여부를 반환한다."""
        with connection() as conn:
            result = conn.execute(
                text("""
                UPDATE item_suggestions SET
                    item_name = :item_name,
                    default_unit = :default_unit,
                    default_unit_price = :default_unit_price,
                    category = :category,
                    notes = :notes
                WHERE id = :id
            """),
                {
                    "id": id,
                    "item_name": data["item_name"],
                    "default_unit": data.get("default_unit", "EA"),
                    "default_unit_price": data.get("default_unit_price", 0),
                    "category": data.get("category"),
                    "notes": data.get("notes"),
                },
            )
            return result.rowcount > 0

    def delete(self, id: int) -> bool:
        """품목을 삭제하고 삭제 여부를 반환한다."""
        with connection() as conn:
            return (
                conn.execute(
                    text("DELETE FROM item_suggestions WHERE id = :id"), {"id": id}
                ).rowcount
                > 0
            )

    def increment_usage_by_name(self, item_name: str) -> None:
        """품목명으로 사용 횟수를 1 증가시키고 마지막 사용 시각을 갱신한다."""
        with connection() as conn:
            conn.execute(
                text("""
                UPDATE item_suggestions
                SET usage_count = usage_count + 1, last_used = NOW()
                WHERE item_name = :name
            """),
                {"name": item_name},
            )
