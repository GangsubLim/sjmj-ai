"""ItemService — PHP services/ItemService.php 동형.

가짜 pagination(page=1, limit=9999, totalPages=1)으로 전체 목록을 반환한다.
POST는 modern 순수 INSERT 201이지만 item_name UNIQUE 위반은 graceful 409로
처리한다(modern의 500 위험은 버그 → 이탈 허용, spec §5).
"""

from sqlalchemy.exc import IntegrityError

from app import db
from app.core.errors import AppError
from app.repositories.items_repository import ItemRepository


class ItemService:
    def __init__(self, repo=None, *, transaction=None):
        self.repo = repo or ItemRepository()
        self._transaction = transaction or db.transaction

    def get_list(self, filters: dict) -> dict:
        data = self.repo.find_all(filters)
        total = len(data)
        return {
            "data": data,
            "pagination": {
                "page": 1,
                "limit": 9999,
                "total": total,
                "totalPages": 1,
            },
        }

    def get_by_id(self, id: int) -> dict | None:
        return self.repo.find_by_id(id)

    def create(self, data: dict) -> dict:
        try:
            new_id = self.repo.insert(data)
        except IntegrityError:
            raise AppError(409, "DUPLICATE_NAME", "이미 등록된 품목입니다.")
        return self.repo.find_by_id(new_id) or data

    def update(self, id: int, data: dict) -> dict | None:
        if not self.repo.find_by_id(id):
            return None
        self.repo.update(id, data)
        return self.repo.find_by_id(id)

    def delete(self, id: int) -> bool:
        return self.repo.delete(id)
