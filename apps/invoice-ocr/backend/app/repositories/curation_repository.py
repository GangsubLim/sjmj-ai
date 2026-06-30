"""training_pairs / ocr_jobs(큐레이션 관점) 데이터 접근. text() raw SQL 직접 발행."""

from sqlalchemy import text

from app.db import connection

_PAIR_INSERT = text(
    "INSERT INTO training_pairs "
    "(crop_ref, job_id, invoice_id, row_index, draft_label, final_label, canonical_label, supply, status) "
    "VALUES (:crop_ref, :job_id, :invoice_id, :row_index, :draft_label, :final_label, "
    ":canonical_label, :supply, :status)"
)


class CurationRepository:
    """training_pairs 테이블의 단일 소유 레포지토리(읽기/쓰기)."""

    def insert_training_pairs(self, pairs: list[dict]) -> int:
        """학습쌍 dict 리스트를 라인별 삽입하고 삽입 행 수를 반환한다."""
        if not pairs:
            return 0
        with connection() as conn:
            for pair in pairs:
                conn.execute(_PAIR_INSERT, pair)
        return len(pairs)
