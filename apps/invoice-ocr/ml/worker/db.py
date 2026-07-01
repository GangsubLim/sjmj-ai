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
        """가장 오래된 pending 1건을 running으로 전이하고 반환(단일 워커 직렬)."""
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT id, image_path FROM ocr_jobs WHERE status='pending' "
                    "ORDER BY id LIMIT 1 FOR UPDATE"
                )
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                text("UPDATE ocr_jobs SET status='running' WHERE id=:id"),
                {"id": row.id},
            )
            return {"id": row.id, "image_path": row.image_path}

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
