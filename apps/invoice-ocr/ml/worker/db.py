"""ml-worker의 ocr_jobs 큐 접근. backend와 동일 MySQL, DB_* env."""

import json
import os

from sqlalchemy import create_engine, text


def build_engine():
    """DB_* env로 backend와 동일한 MySQL 엔진을 만든다."""
    host = os.environ.get("DB_HOST", "127.0.0.1")
    port = os.environ.get("DB_PORT", "3306")
    name = os.environ["DB_NAME"]
    user = os.environ["DB_USER"]
    pw = os.environ.get("DB_PASS", "")
    url = f"mysql+pymysql://{user}:{pw}@{host}:{port}/{name}?charset=utf8mb4"
    return create_engine(url, pool_pre_ping=True, future=True)


class WorkerQueue:
    """ocr_jobs 큐 접근 — pending 점유 및 done/failed 전이."""

    def __init__(self, engine):
        """엔진을 주입받아 큐를 초기화한다."""
        self.engine = engine

    def claim_next_pending(self) -> dict | None:
        """가장 오래된 pending 1건을 running으로 전이하고 반환(단일 워커 직렬).

        정렬은 신규 업로드를 앞세운다 — 재처리 잡은 정의상 옛 id라 순번만으로 세우면
        확정 잡 전량 재처리가 큐 앞을 점거해(잡당 수십 초) 사무실에서 올린 사진이 30분~1시간
        밀린다. 재처리는 배치성이라 뒤로 밀려도 손해가 없다(spec §2).

        재처리 판별에 표식 컬럼을 만들지 않는다 — 신규 잡은 insert 시 result_json이 NULL이라
        "pending인데 초안이 이미 있는가"로 구분이 자연히 선다(spec §1 · ADR 0010).

        Returns:
            {"id", "image_path", "is_reprocess"} 또는 큐가 비었으면 None.
        """
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT id, image_path, (result_json IS NOT NULL) AS is_reprocess "
                    "FROM ocr_jobs WHERE status='pending' "
                    "ORDER BY (result_json IS NOT NULL), id LIMIT 1 FOR UPDATE"
                )
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                text("UPDATE ocr_jobs SET status='running' WHERE id=:id"),
                {"id": row.id},
            )
            return {
                "id": row.id,
                "image_path": row.image_path,
                "is_reprocess": bool(row.is_reprocess),
            }

    def mark_done(self, job_id: int, result_json: dict) -> None:
        """잡을 done으로 전이하고 결과 JSON을 기록한다."""
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE ocr_jobs SET status='done', result_json=:r WHERE id=:id"),
                {"r": json.dumps(result_json, ensure_ascii=False), "id": job_id},
            )

    def mark_failed(self, job_id: int, error_json: dict) -> None:
        """잡을 failed로 전이하고 에러 JSON을 기록한다."""
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE ocr_jobs SET status='failed', result_json=:r WHERE id=:id"),
                {"r": json.dumps(error_json, ensure_ascii=False), "id": job_id},
            )

    def rollback_to_done(self, job_id: int) -> None:
        """재처리 실패를 done으로 되돌린다 — result_json은 건드리지 않는다.

        신규 잡의 실패는 failed지만 재처리 실패는 다르다: 옛 초안과 옛 크롭이 여전히 서로
        정합하므로, 그 상태를 그대로 유지하는 것이 옳다(spec 에러 처리 전수).
        """
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE ocr_jobs SET status='done' WHERE id=:id"),
                {"id": job_id},
            )

    def requeue_for_reprocess(self, job_id: int) -> None:
        """커밋 성공 후 크롭 교체가 실패한 잡을 다시 재처리 큐에 넣는다.

        복구가 "다시 재처리"로 성립하는 근거는 재처리의 멱등성이다 — 이미 새 좌표로 옮겨간
        쌍을 같은 사진·같은 엔진으로 다시 돌리면 새 result_json의 행 구성이 같으므로 매칭이
        항등이 되어 좌표가 제자리에 남는다. result_json을 남겨야 재처리로 판별된다.
        """
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE ocr_jobs SET status='pending' WHERE id=:id"),
                {"id": job_id},
            )
