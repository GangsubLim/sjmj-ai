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


# 미확정 = 확정 증거가 없는 잡. 세 predicate를 함께 쓰는 이유:
# - invoice_id: 확정의 1차 표식이지만 FK가 ON DELETE SET NULL이라 명세서 삭제로 풀린다.
# - ocr_corrections: OcrService.confirm이 학습쌍 유무와 무관하게 항상 남기고 job_id가
#   명세서 삭제를 견딘다(FK는 invoice_id만 SET NULL) — 영속적 확정 증거.
# - training_pairs: ocr_corrections와 중복이지만, "두 탭이 겹치지 않는다"를 학습쌍의
#   출처(라이브 confirm이냐 migration_008 백필이냐)에 대한 가정 없이 구조적으로 참이게
#   만든다. 비용은 인덱스된 FK 서브쿼리 하나.
_UNCONFIRMED_WHERE = (
    "WHERE j.invoice_id IS NULL "
    "AND NOT EXISTS (SELECT 1 FROM ocr_corrections c WHERE c.job_id = j.id) "
    "AND NOT EXISTS (SELECT 1 FROM training_pairs tp WHERE tp.job_id = j.id) "
)


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

    def job_exists(self, job_id: int) -> bool:
        """ocr_jobs에 해당 id가 존재하는지 여부(result_json 파싱 없는 경량 확인).

        curation_repository.CurationRepository.job_exists와 동일 패턴(repo 격리상 의도적 중복).
        """
        with connection() as conn:
            return (
                conn.execute(text("SELECT 1 FROM ocr_jobs WHERE id = :id"), {"id": job_id}).first()
                is not None
            )

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

    def list_unconfirmed(self, limit: int, offset: int) -> tuple[list[dict], int]:
        """미확정 잡을 최신순 페이지 조회하고 (행 리스트, 전체 건수)를 반환한다.

        result_json 전체를 끌어오지 않는다 — 잡 하나에 수십 행 × top5가 있어 20건이면
        payload가 폭발한다. 관측에 필요한 스칼라만 JSON 함수로 뽑는다.

        warp_ok는 JSON_UNQUOTE로 "true"/"false" 문자열로 받아 판정을 파이썬에 맡긴다 —
        드라이버가 JSON true를 무엇으로 주는지에 의존하지 않는다.

        JSON_TYPE은 경로 인자를 받지 않는 단일 인자 함수라 JSON_EXTRACT를 감싼다.
        rows: null이면 rows_type='NULL' + row_count=1(NULL이 아니다 — MySQL 실측),
        키 부재·result_json IS NULL이면 rows_type=NULL + row_count=NULL이다.
        따라서 row_count는 그 자체로 신뢰할 수 없고 rows_type='ARRAY'일 때만 의미가 있다 —
        판정은 service/순수함수가 하고 repo는 원값을 그대로 올려 보낸다.

        error도 같은 계열의 함정을 CASE WHEN으로 닫는다: JSON_UNQUOTE(JSON_EXTRACT(...))는
        값이 JSON null이면 SQL NULL이 아니라 문자열 'null'을 준다(MySQL 9.6.0 실측 — SQL NULL은
        키 부재·result_json IS NULL일 때만). 이 방어가 없으면 error="null"이 service의
        `error is not None` 분기를 통과해 실패 사유로 "null"이 화면에 뜬다.
        JSON_VALUE는 한 줄로 같은 일을 하지만 기본 RETURNING CHAR(512) + NULL ON ERROR라
        512자 넘는 실패 사유를 조용히 NULL로 만든다(실측) — 그래서 쓰지 않는다.

        Args:
            limit: 페이지 크기(라우터에서 1..100으로 clamp된 값).
            offset: 건너뛸 행 수.

        Returns:
            (행 dict 리스트, 같은 조건의 전체 건수).
        """
        list_sql = text(
            "SELECT j.id AS job_id, j.status, j.created_at, "
            "JSON_TYPE(JSON_EXTRACT(j.result_json, '$.rows')) AS rows_type, "
            "JSON_LENGTH(j.result_json, '$.rows') AS row_count, "
            "JSON_UNQUOTE(JSON_EXTRACT(j.result_json, '$.warp_ok')) AS warp_ok, "
            "CASE WHEN JSON_TYPE(JSON_EXTRACT(j.result_json, '$.error')) = 'NULL' THEN NULL "
            "ELSE JSON_UNQUOTE(JSON_EXTRACT(j.result_json, '$.error')) END AS error "
            "FROM ocr_jobs j " + _UNCONFIRMED_WHERE + "ORDER BY j.created_at DESC, j.id DESC "
            "LIMIT :limit OFFSET :offset"
        )
        count_sql = text("SELECT COUNT(*) FROM ocr_jobs j " + _UNCONFIRMED_WHERE)
        with connection() as conn:
            rows = conn.execute(list_sql, {"limit": limit, "offset": offset}).mappings().all()
            total = conn.execute(count_sql).scalar() or 0
        return [dict(r) for r in rows], int(total)
