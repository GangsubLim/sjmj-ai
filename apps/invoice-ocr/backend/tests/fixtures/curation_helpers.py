"""큐레이션 테스트 공용 헬퍼 — 세대 토큰(spec §12) 조회·조작.

contract와 integration 두 디렉토리가 같은 헬퍼를 각자 정의하고 있었다(#94). 정의가
둘이면 토큰 계약이 바뀔 때 한쪽만 고쳐져 조용히 갈린다 — 단일 정의로 모은다.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import Engine, text


def job_token(client: TestClient, job_id: int) -> dict[str, str]:
    """잡 상세에서 세대 토큰을 읽어 요청 body 조각으로 만든다(spec §12 — 필수 필드)."""
    return {"job_token": client.get(f"/api/curation/jobs/{job_id}").json()["data"]["job_token"]}


def rewind_job_token(engine: Engine, job_id: int) -> None:
    """updated_at을 1초 과거로 밀어 다음 전이가 토큰을 반드시 올리게 만든다.

    표현식 산술이라 토큰 해상도(초·밀리초)에 무관하다 — 값을 파싱하지 않는다.
    """
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE ocr_jobs SET updated_at = updated_at - INTERVAL 1 SECOND WHERE id = :id"),
            {"id": job_id},
        )
