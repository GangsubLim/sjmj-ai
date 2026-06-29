"""CompanyService — PHP services/CompanyService.php 동형.

가짜 pagination(page=1, limit=9999, total=count, totalPages=1)을 그대로 유지한다.
company 단건은 트랜잭션이 필요 없는 단일 statement지만, 슬라이스 패턴과의
일관성을 위해 transaction seam은 주입 가능하게 둔다(미사용).
"""

from app import db
from app.repositories.companies_repository import CompanyRepository

_FAKE_LIMIT = 9999


class CompanyService:
    """거래처 비즈니스 로직 — PHP CompanyService 동형."""

    def __init__(self, repo=None, *, transaction=None):
        """repo와 transaction seam을 주입받아 초기화한다(미지정 시 기본값)."""
        self.repo = repo or CompanyRepository()
        self._transaction = transaction or db.transaction

    def get_list(self, filters: dict) -> dict:
        """거래처 목록을 가짜 pagination 봉투에 담아 반환한다."""
        data = self.repo.find_all(filters)
        total = len(data)
        return {
            "data": data,
            "pagination": {
                "page": 1,
                "limit": _FAKE_LIMIT,
                "total": total,
                "totalPages": 1,
            },
        }

    def get_by_id(self, id: int) -> dict | None:
        """거래처를 ID로 단건 조회한다."""
        return self.repo.find_by_id(id)

    def create(self, data: dict) -> dict:
        """거래처를 신규 생성하고 생성된 레코드를 반환한다."""
        new_id = self.repo.insert(data)
        return self.repo.find_by_id(new_id) or data

    def update(self, id: int, data: dict) -> dict | None:
        """거래처를 수정하고 갱신된 레코드를 반환한다(없으면 None)."""
        if not self.repo.find_by_id(id):
            return None
        self.repo.update(id, data)
        return self.repo.find_by_id(id)

    def delete(self, id: int) -> bool:
        """거래처를 삭제한다."""
        return self.repo.delete(id)

    def get_invoices(self, id: int) -> list[dict] | None:
        """거래처에 속한 거래명세서 목록을 조회한다(거래처 없으면 None)."""
        if not self.repo.find_by_id(id):
            return None
        return self.repo.find_invoices_by_company_id(id)
