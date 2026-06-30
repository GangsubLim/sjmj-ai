"""ItemService.

가짜 pagination(page=1, limit=9999, totalPages=1)으로 전체 목록을 반환한다.
POST는 modern 순수 INSERT 201이지만 item_name UNIQUE 위반은 graceful 409로
처리한다(modern의 500 위험은 버그 → 이탈 허용, spec §5).
"""

from sqlalchemy.exc import IntegrityError

from app import db
from app.core.errors import AppError
from app.repositories.items_repository import ItemRepository


class ItemService:
    """품목 도메인 비즈니스 로직."""

    def __init__(self, repo=None, *, transaction=None):
        """리포지토리와 트랜잭션 경계를 주입받아 초기화한다."""
        self.repo = repo or ItemRepository()
        self._transaction = transaction or db.transaction

    def get_list(self, filters: dict) -> dict:
        """필터에 맞는 품목 전체를 가짜 pagination 봉투에 담아 반환한다."""
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
        """품목을 ID로 단건 조회한다."""
        return self.repo.find_by_id(id)

    def create(self, data: dict) -> dict:
        """품목을 생성하고, item_name UNIQUE 위반은 409로 변환한다."""
        try:
            new_id = self.repo.insert(data)
        except IntegrityError as err:
            raise AppError(409, "DUPLICATE_NAME", "이미 등록된 품목입니다.") from err
        return self.repo.find_by_id(new_id) or data

    def update(self, id: int, data: dict) -> dict | None:
        """품목을 갱신하고 갱신된 행을 반환한다(없으면 None)."""
        if not self.repo.find_by_id(id):
            return None
        self.repo.update(id, data)
        return self.repo.find_by_id(id)

    def delete(self, id: int) -> bool:
        """품목을 삭제한다."""
        return self.repo.delete(id)
