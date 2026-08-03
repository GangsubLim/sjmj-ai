"""training_pairs / ocr_jobs(큐레이션 관점) 데이터 접근. text() raw SQL 직접 발행."""

import json

from sqlalchemy import text

from app.db import connection
from app.schemas.curation import STATUS_EXCLUDED, STATUS_INCLUDED

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
                        "supply, status, exclusion_reason, reviewed_at FROM training_pairs "
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

    def find_pair(self, pair_id: int) -> dict | None:
        """학습쌍을 id로 단건 조회한다."""
        with connection() as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT id, crop_ref, job_id, invoice_id, row_index, draft_label, "
                        "final_label, canonical_label, supply, status, exclusion_reason, "
                        "reviewed_at, created_at FROM training_pairs WHERE id = :id"
                    ),
                    {"id": pair_id},
                )
                .mappings()
                .first()
            )
            return dict(row) if row else None

    def update_pair(self, pair_id: int, fields: dict) -> None:
        """학습쌍의 status/canonical_label을 갱신한다(화이트리스트 컬럼만).

        사람이 배제(status='excluded')하면 **같은 UPDATE 문에서** exclusion_reason을 NULL로
        지운다. 클라이언트가 보낸 값이 아니라 서버 파생 쓰기이므로 화이트리스트의 역할
        ("사유는 기계만 채운다"의 물리적 강제)은 그대로다. 비어 있는 사유가 곧 "사람 소유"
        표식이라, 남겨두면 기계가 사람의 배제를 자기 판정으로 오인해 되돌린다(ADR 0006 §6).
        포함 방향에서는 지우지 않는다 — 지우면 '사람이 되돌림' 칸이 '정상 후보' 칸과 같아져
        오탐 관측치와 영구 보호가 동시에 사라진다.

        이 메서드는 사람의 PATCH 경로 전용이다(현재 유일 호출자는 CurationService.patch_pair).
        후속에 추가될 기계 배제 writer는 이 메서드를 재사용하지 말 것 — 재사용하면 기계가
        자기 자신이 방금 심으려는 exclusion_reason을 같은 문장에서 지우게 된다(자기 무효화).
        """
        allowed = ("status", "canonical_label")
        cols = [c for c in allowed if c in fields]
        # 방어: 라우터는 model_validator로 검증된 비어있지 않은 fields만 전달(API 경로로는 도달 불가).
        if not cols:
            return
        assignments = [f"{c} = :{c}" for c in cols]
        if fields.get("status") == STATUS_EXCLUDED:
            assignments.append("exclusion_reason = NULL")
        params = {c: fields[c] for c in cols}
        params["id"] = pair_id
        with connection() as conn:
            conn.execute(
                text(f"UPDATE training_pairs SET {', '.join(assignments)} WHERE id = :id"), params
            )

    def job_exists(self, job_id: int) -> bool:
        """ocr_jobs에 해당 id가 존재하는지 여부."""
        with connection() as conn:
            return (
                conn.execute(text("SELECT 1 FROM ocr_jobs WHERE id = :id"), {"id": job_id}).first()
                is not None
            )

    def get_image_path(self, job_id: int) -> str | None:
        """잡의 원본 업로드 이미지 경로를 반환한다."""
        with connection() as conn:
            return conn.execute(
                text("SELECT image_path FROM ocr_jobs WHERE id = :id"), {"id": job_id}
            ).scalar()

    def mark_reviewed(self, job_id: int) -> None:
        """잡을 검수완료로 표시하고 미처리 쌍에 reviewed_at을 찍는다."""
        with connection() as conn:
            conn.execute(
                text("UPDATE ocr_jobs SET curation_reviewed = 1 WHERE id = :id"), {"id": job_id}
            )
            conn.execute(
                text(
                    "UPDATE training_pairs SET reviewed_at = CURRENT_TIMESTAMP "
                    "WHERE job_id = :id AND reviewed_at IS NULL"
                ),
                {"id": job_id},
            )

    def list_included_labels(self, job_id: int) -> list[str]:
        """잡의 included 쌍 정식 라벨을 행순으로 반환한다.

        NULL은 SQL이 거르고 빈 문자열·공백 문자열은 그대로 넘긴다 — 정규화(strip 후
        빈 값 skip)는 service가 ml/tools/bank_update.partition_valid와 같은 규칙으로 한다.

        Args:
            job_id: 대상 OCR 잡 id.

        Returns:
            row_index 오름차순 정식 라벨 목록. 대상이 없으면 빈 리스트.
        """
        with connection() as conn:
            rows = conn.execute(
                text(
                    "SELECT canonical_label FROM training_pairs "
                    "WHERE job_id = :id AND status = :status AND canonical_label IS NOT NULL "
                    "ORDER BY row_index ASC, id ASC"
                ),
                {"id": job_id, "status": STATUS_INCLUDED},
            ).scalars()
            return list(rows)

    def is_job_reviewed(self, job_id: int) -> bool:
        """잡이 검수완료 상태인지 여부(없는 잡은 False)."""
        with connection() as conn:
            return bool(
                conn.execute(
                    text("SELECT curation_reviewed FROM ocr_jobs WHERE id = :id"), {"id": job_id}
                ).scalar()
            )
