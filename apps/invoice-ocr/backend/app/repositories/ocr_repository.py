"""ocr_jobs / ocr_corrections DB 접근. result_json/correction_json은 JSON 컬럼."""

import json

from sqlalchemy import text

from app.db import connection


def _parse_job(row) -> dict | None:
    if row is None:
        return None
    d = dict(row._mapping)
    raw = d.get("result_json")
    d["result_json"] = json.loads(raw) if isinstance(raw, str) else raw
    return d


class OcrRepository:
    """ocr_jobs와 ocr_corrections 테이블 데이터 접근 레포지토리."""

    def insert_job(self, image_path: str) -> int:
        """이미지 경로로 대기 상태 OCR 작업을 생성하고 job id를 반환한다."""
        with connection() as conn:
            result = conn.execute(
                text("INSERT INTO ocr_jobs (status, image_path) VALUES ('pending', :p)"),
                {"p": image_path},
            )
            return int(result.lastrowid)

    def find_job(self, job_id: int) -> dict | None:
        """OCR 작업을 ID로 단건 조회한다(result_json 파싱 포함)."""
        with connection() as conn:
            row = conn.execute(
                text(
                    "SELECT id, status, image_path, result_json, invoice_id, "
                    "created_at, updated_at FROM ocr_jobs WHERE id = :id"
                ),
                {"id": job_id},
            ).fetchone()
        return _parse_job(row)

    def claim_job(self, job_id: int) -> dict | None:
        """Confirm 트랜잭션 내에서 행을 잠그고 읽는다(SELECT ... FOR UPDATE)."""
        with connection() as conn:
            row = conn.execute(
                text(
                    "SELECT id, status, image_path, result_json, invoice_id "
                    "FROM ocr_jobs WHERE id = :id FOR UPDATE"
                ),
                {"id": job_id},
            ).fetchone()
        return _parse_job(row)

    def link_invoice(self, job_id: int, invoice_id: int) -> int:
        """invoice_id가 비어있을 때만 연결. 영향행 수를 반환(0이면 이미 연결됨)."""
        with connection() as conn:
            result = conn.execute(
                text(
                    "UPDATE ocr_jobs SET invoice_id = :inv WHERE id = :job AND invoice_id IS NULL"
                ),
                {"inv": invoice_id, "job": job_id},
            )
            return result.rowcount

    def update_result(self, job_id: int, status: str, result_json: dict) -> None:
        """OCR 작업의 상태와 결과 JSON을 갱신한다."""
        with connection() as conn:
            conn.execute(
                text("UPDATE ocr_jobs SET status = :s, result_json = :r WHERE id = :id"),
                {
                    "s": status,
                    "r": json.dumps(result_json, ensure_ascii=False),
                    "id": job_id,
                },
            )

    def insert_correction(self, job_id: int, invoice_id: int, correction_json: dict) -> int:
        """OCR 보정 내역을 삽입하고 생성된 id를 반환한다."""
        with connection() as conn:
            result = conn.execute(
                text(
                    "INSERT INTO ocr_corrections (job_id, invoice_id, correction_json) "
                    "VALUES (:j, :i, :c)"
                ),
                {
                    "j": job_id,
                    "i": invoice_id,
                    "c": json.dumps(correction_json, ensure_ascii=False),
                },
            )
            return int(result.lastrowid)
