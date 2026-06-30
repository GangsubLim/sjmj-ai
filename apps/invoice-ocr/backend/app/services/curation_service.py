"""CurationService — 검수 큐/잡 상세/쌍 큐레이션/검수완료/이미지 경로 해석.

라우터(HTTP)와 repository(SQL) 사이의 정규화·비즈니스 로직 계층.
"""

from app.repositories.curation_repository import CurationRepository


class CurationService:
    """큐레이션 도메인 서비스."""

    def __init__(self, repo=None):
        """저장소를 주입받아 초기화한다(미지정 시 기본 구현)."""
        self.repo = repo or CurationRepository()

    def list_jobs(self, page: int, limit: int) -> tuple[list[dict], int]:
        """검수 큐(페이지)를 조회하고 표시용 타입으로 정규화한다."""
        offset = (page - 1) * limit
        rows, total = self.repo.list_jobs(limit, offset)
        jobs = [
            {
                "job_id": int(r["job_id"]),
                "invoice_id": r["invoice_id"],
                "curation_reviewed": bool(r["curation_reviewed"]),
                "pair_count": int(r["pair_count"]),
                "unreviewed_count": int(r["unreviewed_count"] or 0),
                "created_at": r["created_at"],
            }
            for r in rows
        ]
        return jobs, total
