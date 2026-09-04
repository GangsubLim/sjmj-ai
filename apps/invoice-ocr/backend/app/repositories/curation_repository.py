"""training_pairs / ocr_jobs(큐레이션 관점) 데이터 접근. text() raw SQL 직접 발행."""

import json

from sqlalchemy import text

from app.db import connection
from app.schemas.curation import STATUS_EXCLUDED, STATUS_INCLUDED

# 세대 토큰(낙관적 잠금, spec §12)의 유일한 SQL 표현식 — 발급(find_job_detail)과
# 대조(get_job_token)가 반드시 같은 문자열을 내야 한다. 두 곳이 갈라지면 대조가 영구
# 불일치해 큐레이션 화면의 모든 쓰기가 409가 된다(#84). 표현·정밀도를 바꿀 일이 생기면
# 반드시 이 상수만 고친다. 사용자 입력이 아닌 모듈 상수라 f-string 조립을 허용한다
# (바인드 파라미터 자리가 아니다).
JOB_TOKEN_SQL = "CAST(UNIX_TIMESTAMP(updated_at) AS CHAR)"


def _row_delta_expr(key: str) -> str:
    """correction_json의 정수 스칼라만 수치로 좁히는 SQL 식을 만든다.

    JSON_TYPE이 INTEGER일 때만 캐스팅하고 그 외(키 부재·NULL·문자열·불리언)는 NULL로
    닫는다 — 값 없음과 값 0을 섞지 않기 위함이며, ocr_observation이 rows_type != 'ARRAY'로
    세운 방어와 같은 규율이다. 사용자 입력이 아닌 모듈 상수 조립이라 f-string을 허용한다.
    """
    path = f"'$.{key}'"
    return (
        f"CASE WHEN JSON_TYPE(JSON_EXTRACT(c.correction_json, {path})) = 'INTEGER' "
        f"THEN CAST(JSON_EXTRACT(c.correction_json, {path}) AS SIGNED) END"
    )


ROWS_ADDED_SQL = _row_delta_expr("rows_added")
ROWS_DROPPED_SQL = _row_delta_expr("rows_dropped")

# 잡당 최신 correction 1건만 붙인다 — 명세서 삭제 후 재확정으로 job_id가 1:N이 될 수 있어,
# 조건 없는 LEFT JOIN이면 COUNT(tp.id)·SUM(...)이 배로 부풀어 pair_count가 조용히 틀어진다.
LATEST_CORRECTION_JOIN = (
    "LEFT JOIN ocr_corrections c "
    "ON c.id = (SELECT MAX(c2.id) FROM ocr_corrections c2 WHERE c2.job_id = j.id) "
)

# 행 증감이 관측된 잡만 — NULL은 비교가 NULL이라 자동으로 빠진다(값 없음은 대상 아님).
ROW_DELTA_WHERE = f"WHERE ({ROWS_ADDED_SQL} > 0 OR {ROWS_DROPPED_SQL} > 0) "

_PAIR_INSERT = text(
    "INSERT INTO training_pairs "
    "(crop_ref, job_id, invoice_id, row_index, draft_label, draft_supply, final_label, "
    "canonical_label, supply, status) "
    "VALUES (:crop_ref, :job_id, :invoice_id, :row_index, :draft_label, :draft_supply, "
    ":final_label, :canonical_label, :supply, :status)"
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

    def list_jobs(
        self, limit: int, offset: int, *, row_delta: bool = False
    ) -> tuple[list[dict], int]:
        """training_pairs 보유 잡을 검수상태·미처리수·행 증감과 함께 페이지 조회한다.

        모집단은 training_pairs를 1건 이상 가진 확정 잡으로 한정된다 — 초안 행을 전량
        교체해 쌍이 0건이 된 잡은 행 증감 신호가 가장 강해도 이 목록에서 빠진다.

        rows_added/rows_dropped는 최신 ocr_corrections 1건에서 투영하며, 관측이 없으면
        None이다(0과 구분 — spec §4-1). row_delta=True면 두 수 중 하나라도 0보다 큰 잡만
        남기고 total도 같은 조건으로 센다. False면 쿼리·정렬·total이 도입 이전과 동일하다.

        MAX()로 감싸는 이유: 조인이 잡당 최대 1행이라 MAX는 항등이지만, GROUP BY에 JSON
        식을 나열하지 않고도 ONLY_FULL_GROUP_BY를 만족시킨다.

        Args:
            limit: 페이지 크기.
            offset: 건너뛸 행 수.
            row_delta: 행 증감이 있는 잡만 남길지 여부.

        Returns:
            (행 dict 리스트, 조건에 해당하는 전체 잡 수).
        """
        where = ROW_DELTA_WHERE if row_delta else ""
        list_sql = text(
            "SELECT j.id AS job_id, j.invoice_id, j.curation_reviewed, j.curation_reviewed_at, "
            "j.created_at, "
            "COUNT(tp.id) AS pair_count, "
            "SUM(CASE WHEN tp.reviewed_at IS NULL THEN 1 ELSE 0 END) AS unreviewed_count, "
            f"MAX({ROWS_ADDED_SQL}) AS rows_added, "
            f"MAX({ROWS_DROPPED_SQL}) AS rows_dropped "
            "FROM ocr_jobs j JOIN training_pairs tp ON tp.job_id = j.id "
            f"{LATEST_CORRECTION_JOIN}"
            f"{where}"
            "GROUP BY j.id, j.invoice_id, j.curation_reviewed, j.curation_reviewed_at, j.created_at "
            "ORDER BY j.curation_reviewed ASC, j.created_at DESC, j.id DESC "
            "LIMIT :limit OFFSET :offset"
        )
        count_sql = (
            text(
                "SELECT COUNT(DISTINCT j.id) FROM ocr_jobs j "
                "JOIN training_pairs tp ON tp.job_id = j.id "
                f"{LATEST_CORRECTION_JOIN}{ROW_DELTA_WHERE}"
            )
            if row_delta
            else text("SELECT COUNT(DISTINCT job_id) FROM training_pairs")
        )
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
                        "SELECT id, invoice_id, status, curation_reviewed, curation_reviewed_at, "
                        "result_json, created_at, "
                        f"{JOB_TOKEN_SQL} AS job_token "
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
        """학습쌍의 status/canonical_label을 갱신하고 reviewed_at을 NULL로 되돌린다.

        **reviewed_at을 되돌리는 이유(Issue #52 spec §3.1).** reviewed_at은 "이 쌍이 사람
        확인을 통과했다"는 표식이지 감사 로그가 아니다. ml/tools/blank_crop_report.py의
        기계 경로(--recheck-reviewed)가 이미 같은 관례를 쓰므로, 사람 경로가 보존을 택하면
        두 경로가 같은 컬럼의 의미를 다르게 쓰게 된다. 또 목록의 unreviewed_count가 그대로
        "재확인해야 할 행 수"가 되어야 하는데, 보존하면 재검수 필요 상태에서 그 값이 0이
        되어 "미처리 0인데 미검수"라는 모순된 표시가 생긴다. 잃는 것(첫 검수 시각)은
        ocr_jobs.curation_reviewed_at이 잡 단위에서 회복한다.

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
        # cols가 비어도 조기 반환하지 않는다. 조기 반환하면 아래 reviewed_at 되돌림까지
        # 함께 건너뛰어, 이미 게이트를 해제한 patch_pair와 어긋난 상태("미처리 0인데
        # 미검수")가 조용히 생긴다. 라우터가 model_validator로 걸러 API 경로로는 도달하지
        # 않지만, 이 메서드가 소유한 상태 전이를 삼키지 않는 쪽이 안전하다.
        assignments = [f"{c} = :{c}" for c in cols]
        if fields.get("status") == STATUS_EXCLUDED:
            assignments.append("exclusion_reason = NULL")
        # 수정된 쌍은 재확인 대상으로 되돌린다 — 기계 경로(blank_crop_report)와 같은 관례.
        assignments.append("reviewed_at = NULL")
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

    def find_job_for_update(self, job_id: int) -> dict | None:
        """상태 전이 대상 잡의 현재 상태를 행잠금으로 읽는다(재처리 요청·검수 완료 공용).

        **락 유지 전제.** FOR UPDATE 락은 호출자가 db.transaction()을 연 경우에만 유지된다.
        트랜잭션 밖 standalone 호출은 connection()이 자체 트랜잭션을 열고 블록이 끝나는
        즉시 커밋하므로 락이 그 자리에서 풀린다 — 확인과 전이 사이가 비어 아래 경합 배제가
        성립하지 않는다. 이 전제를 지키는 장치는 두 축이다 — 토큰 조회의 **호출 배치**(트랜잭션 경계 안)는
        service 단위 테스트가, 실 커넥션 합류와 부분 반영 롤백은
        tests/integration/test_curation_service_transactions.py 가 고정한다. FOR UPDATE
        락 수명 자체를 2 커넥션으로 재는 테스트는 없다(#84 범위 밖).

        FOR UPDATE로 잡히므로, 확인과 전이 사이에 워커의 claim_next_pending이 끼어들어
        같은 잡을 두 번 집는 경합이 성립하지 않는다. 부모(ocr_jobs)를 먼저 잡는 자리라
        락 순서 불변식(잡 → 쌍)의 시작점이기도 하다 — Task 7의 mark_reviewed가 이 호출을
        재사용해 상태 가드를 건다(허용 상태는 경로별로 다르다 — 검수 완료는 done만,
        재처리 요청은 done·failed — #93).

        Args:
            job_id: 대상 OCR 잡 id.

        Returns:
            {"id", "status"} 또는 잡이 없으면 None.
        """
        with connection() as conn:
            row = (
                conn.execute(
                    text("SELECT id, status FROM ocr_jobs WHERE id = :id FOR UPDATE"),
                    {"id": job_id},
                )
                .mappings()
                .first()
            )
            return {"id": int(row["id"]), "status": row["status"]} if row else None

    def requeue_for_reprocess(self, job_id: int) -> None:
        """잡을 다시 추론 큐에 넣는다 — result_json은 건드리지 않는다.

        초안이 남아 있어야 워커가 이 잡을 재처리로 판별하고(spec §1), 재추론이 실패해도
        옛 초안으로 되돌아갈 수 있다. 지우면 두 성질이 함께 사라진다.
        """
        with connection() as conn:
            conn.execute(
                text("UPDATE ocr_jobs SET status = 'pending' WHERE id = :id"), {"id": job_id}
            )

    def get_image_path(self, job_id: int) -> str | None:
        """잡의 원본 업로드 이미지 경로를 반환한다."""
        with connection() as conn:
            return conn.execute(
                text("SELECT image_path FROM ocr_jobs WHERE id = :id"), {"id": job_id}
            ).scalar()

    def get_job_token(self, job_id: int) -> str | None:
        """잡의 세대 토큰을 행잠금으로 읽는다 — 낙관적 잠금 대조·갱신용(spec §12).

        토큰은 migration_007이 이미 정의한 ocr_jobs.updated_at(ON UPDATE
        CURRENT_TIMESTAMP)이다. 재처리는 status를 done → pending으로 전이하므로 이 값이
        반드시 튄다 — 새 컬럼이 필요 없어 마이그레이션 0이 유지된다.

        DATE_FORMAT 대신 UNIX_TIMESTAMP를 쓰는 이유는 문자열 왕복의 안정성이다 — 포맷
        문자열의 %는 DBAPI paramstyle과 충돌할 수 있고, epoch 수치는 타임존·표기 흔들림이 없다.
        정밀도는 밀리초다 — migration_013이 updated_at을 TIMESTAMP(3)으로 올려 같은 초 안의
        두 번째 쓰기도 토큰이 갈린다(#95-1). 같은 밀리초에 겹치는 창은 남는다 — 해상도를 올려
        창을 좁히는 것이지 충돌을 없애는 것이 아니다. 표현식은 JOB_TOKEN_SQL 한 곳에 있다.

        FOR UPDATE로 부모(ocr_jobs)를 먼저 잡으므로 뒤따르는 release_gate·update_pair가
        락 순서 불변식(잡 → 쌍)을 그대로 지킨다.

        Args:
            job_id: 대상 OCR 잡 id.

        Returns:
            불투명 토큰 문자열. 잡이 없으면 None.
        """
        with connection() as conn:
            return conn.execute(
                text(f"SELECT {JOB_TOKEN_SQL} FROM ocr_jobs WHERE id = :id FOR UPDATE"),
                {"id": job_id},
            ).scalar()

    def release_gate(self, job_id: int) -> None:
        """잡의 검수 게이트를 해제한다(curation_reviewed_at은 건드리지 않는다).

        **호출 순서 제약.** 이 메서드는 patch_pair 트랜잭션에서 반드시 update_pair보다
        **먼저** 호출돼야 한다. mark_reviewed가 ocr_jobs(부모) → training_pairs(자식)
        순서로 잠그므로, patch_pair가 반대 순서로 잠그면 두 **사람 경로** 사이에 순환
        대기가 성립한다. 같은 순서를 쓰면 사람 경로끼리는 추가 직렬화(SELECT … FOR
        UPDATE) 없이 교착이 배제된다. 기계 apply 경로(ml/tools/blank_crop_report.py의
        build_apply_script)도 #76에서 부모→자식으로 정렬돼 세 경로의 락 순서가 같다.
        이 순서는 repository가 강제할 수 없고 service의 호출
        순서에만 달려 있다 — 강제 장치는 service 단위 테스트의 호출 순서 단언이다.

        curation_reviewed_at을 지우지 않는 것이 "재검수 필요"(해제됐지만 과거에 검수된 잡)와
        "미검수"(한 번도 검수 안 한 잡)를 가르는 유일한 근거다.

        Args:
            job_id: 게이트를 해제할 OCR 잡 id.
        """
        with connection() as conn:
            conn.execute(
                text("UPDATE ocr_jobs SET curation_reviewed = 0 WHERE id = :id"), {"id": job_id}
            )

    def mark_reviewed(self, job_id: int) -> None:
        """잡을 검수완료로 표시하고 미처리 쌍에 reviewed_at을 찍는다.

        curation_reviewed_at은 COALESCE로 **첫 검수 시각만** 채운다. 게이트가 해제됐다가
        재확정될 때 이 값이 갱신되면 "한 번 검수됐다가 해제된 잡"의 판별 근거가 사라진다.

        잠금 순서: 부모(ocr_jobs) → 자식(training_pairs). 근거는 release_gate docstring 참조.
        """
        with connection() as conn:
            conn.execute(
                text(
                    "UPDATE ocr_jobs SET curation_reviewed = 1, "
                    "curation_reviewed_at = COALESCE(curation_reviewed_at, CURRENT_TIMESTAMP) "
                    "WHERE id = :id"
                ),
                {"id": job_id},
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
