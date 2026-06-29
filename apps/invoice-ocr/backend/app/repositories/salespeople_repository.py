"""SalespersonRepository — PHP repositories/SalespersonRepository.php 동형(text() raw SQL).

정렬은 고정(is_active DESC, sort_order ASC, id ASC). 소프트삭제는 행 삭제가 아니라
is_active=0 UPDATE(sales_records FK RESTRICT). 숫자 컬럼은 pymysql이 int로 반환.
"""

from sqlalchemy import text

from app.db import connection

_COLUMNS = "id, name, sort_order, is_active, created_at, updated_at"


def _int_or(value, default: int) -> int:
    return int(value) if value is not None else default


class SalespersonRepository:
    def find_all(self) -> list[dict]:
        sql = f"""
            SELECT {_COLUMNS}
            FROM salespeople
            ORDER BY is_active DESC, sort_order ASC, id ASC
        """
        with connection() as conn:
            return [dict(m) for m in conn.execute(text(sql)).mappings().all()]

    def find_by_id(self, id: int) -> dict | None:
        with connection() as conn:
            row = (
                conn.execute(
                    text(f"SELECT {_COLUMNS} FROM salespeople WHERE id = :id"),
                    {"id": id},
                )
                .mappings()
                .first()
            )
            return dict(row) if row else None

    def find_active_by_name(
        self, name: str, exclude_id: int | None = None
    ) -> dict | None:
        sql = "SELECT id, name FROM salespeople WHERE name = :name AND is_active = 1"
        params: dict = {"name": name}
        if exclude_id is not None:
            sql += " AND id <> :exclude_id"
            params["exclude_id"] = exclude_id
        with connection() as conn:
            row = conn.execute(text(sql), params).mappings().first()
            return dict(row) if row else None

    def insert(self, data: dict) -> int:
        with connection() as conn:
            result = conn.execute(
                text("""
                    INSERT INTO salespeople (name, sort_order, is_active)
                    VALUES (:name, :sort_order, :is_active)
                """),
                {
                    "name": data["name"],
                    "sort_order": _int_or(data.get("sort_order"), 0),
                    "is_active": _int_or(data.get("is_active"), 1),
                },
            )
            return int(result.lastrowid)

    def update(self, id: int, data: dict) -> bool:
        with connection() as conn:
            result = conn.execute(
                text("""
                    UPDATE salespeople
                    SET name = :name, sort_order = :sort_order, is_active = :is_active
                    WHERE id = :id
                """),
                {
                    "id": id,
                    "name": data["name"],
                    "sort_order": _int_or(data.get("sort_order"), 0),
                    "is_active": _int_or(data.get("is_active"), 1),
                },
            )
            return result.rowcount > 0

    def soft_delete(self, id: int) -> bool:
        with connection() as conn:
            result = conn.execute(
                text(
                    "UPDATE salespeople SET is_active = 0 WHERE id = :id AND is_active = 1"
                ),
                {"id": id},
            )
            return result.rowcount > 0
