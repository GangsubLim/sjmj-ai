"""training_pairs / ocr_jobs(큐레이션 관점) 데이터 접근. text() raw SQL 직접 발행."""

import json

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

    def list_jobs(self, limit: int, offset: int) -> tuple[list[dict], int]:
        """training_pairs 보유 잡을 검수상태·미처리수와 함께 페이지 조회한다."""
        list_sql = text(
            "SELECT j.id AS job_id, j.invoice_id, j.curation_reviewed, j.created_at, "
            "COUNT(tp.id) AS pair_count, "
            "SUM(CASE WHEN tp.reviewed_at IS NULL THEN 1 ELSE 0 END) AS unreviewed_count "
            "FROM ocr_jobs j JOIN training_pairs tp ON tp.job_id = j.id "
            "GROUP BY j.id, j.invoice_id, j.curation_reviewed, j.created_at "
            "ORDER BY j.curation_reviewed ASC, j.created_at DESC, j.id DESC "
            "LIMIT :limit OFFSET :offset"
        )
        count_sql = text("SELECT COUNT(DISTINCT job_id) FROM training_pairs")
        with connection() as conn:
            rows = conn.execute(list_sql, {"limit": limit, "offset": offset}).mappings().all()
            total = conn.execute(count_sql).scalar() or 0
        return [dict(r) for r in rows], int(total)

    def find_job_detail(self, job_id: int) -> dict | None:
        """잡 1건 + training_pairs(행순)를 함께 조회한다(result_json 파싱 포함)."""
        with connection() as conn:
            job_row = (
                conn.execute(
                    text(
                        "SELECT id, invoice_id, curation_reviewed, result_json, created_at "
                        "FROM ocr_jobs WHERE id = :id"
                    ),
                    {"id": job_id},
                )
                .mappings()
                .first()
            )
            if job_row is None:
                return None
            pair_rows = (
                conn.execute(
                    text(
                        "SELECT id, crop_ref, row_index, draft_label, final_label, canonical_label, "
                        "supply, status, reviewed_at FROM training_pairs "
                        "WHERE job_id = :id ORDER BY row_index ASC, id ASC"
                    ),
                    {"id": job_id},
                )
                .mappings()
                .all()
            )
        # result_json 파싱은 ocr_repository._parse_job와 동일 관용구 — repo 격리상 의도적 중복(공유 추출 안 함).
        job = dict(job_row)
        raw = job.get("result_json")
        job["result_json"] = json.loads(raw) if isinstance(raw, str) else raw
        return {"job": job, "pairs": [dict(p) for p in pair_rows]}
