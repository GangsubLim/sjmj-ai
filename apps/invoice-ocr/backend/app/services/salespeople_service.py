"""SalespersonService — PHP services/SalespersonService.php 동형.

이름 정규화(trim/제어문자/길이)와 활성 중복 검사(자기 id 제외)를 수행한다.
검증 실패는 app.core.errors로 위임(400 VALIDATION_ERROR / 409 DUPLICATE_NAME).
"""
import re

from app import db
from app.core.errors import AppError, bad_request
from app.repositories.salespeople_repository import SalespersonRepository

_CONTROL_CHAR = re.compile(r"[\x00-\x1F\x7F]")
_MAX_NAME_LENGTH = 100


class SalespersonService:
    def __init__(self, repo=None, *, transaction=None):
        self.repo = repo or SalespersonRepository()
        self._transaction = transaction or db.transaction

    def get_list(self) -> list[dict]:
        return self.repo.find_all()

    def get_by_id(self, id: int) -> dict | None:
        return self.repo.find_by_id(id)

    def create(self, data: dict) -> dict | None:
        name = self._normalize_name(data.get("name") or "")
        self._assert_no_duplicate_active(name, None)
        with self._transaction():
            new_id = self.repo.insert({
                "name": name,
                "sort_order": int(data["sort_order"]) if data.get("sort_order") is not None else 0,
                "is_active": 1,
            })
        return self.repo.find_by_id(new_id)

    def update(self, id: int, data: dict) -> dict | None:
        existing = self.repo.find_by_id(id)
        if not existing:
            return None
        name = self._normalize_name(data.get("name") or "")
        self._assert_no_duplicate_active(name, id)
        with self._transaction():
            self.repo.update(id, {
                "name": name,
                "sort_order": int(data["sort_order"]) if data.get("sort_order") is not None
                else int(existing["sort_order"]),
                "is_active": int(data["is_active"]) if data.get("is_active") is not None
                else int(existing["is_active"]),
            })
        return self.repo.find_by_id(id)

    def soft_delete(self, id: int) -> bool:
        return self.repo.soft_delete(id)

    def _normalize_name(self, raw: str) -> str:
        trimmed = str(raw).strip()
        if trimmed == "":
            bad_request("이름은 필수입니다.", {"name": "이름은 필수입니다."})
        if _CONTROL_CHAR.search(trimmed):
            bad_request("이름에 제어문자가 포함될 수 없습니다.", {"name": "제어문자 거부"})
        if len(trimmed) > _MAX_NAME_LENGTH:
            bad_request("이름은 100자 이하여야 합니다.", {"name": "100자 초과"})
        return trimmed

    def _assert_no_duplicate_active(self, name: str, exclude_id: int | None) -> None:
        if self.repo.find_active_by_name(name, exclude_id) is not None:
            raise AppError(409, "DUPLICATE_NAME", "이미 등록된 영업사원 이름입니다.")
