"""등록 트리거가 기존 쓰기와 한 트랜잭션인지 고정한다(#40 spec §3.2).

이 파일이 없으면 CurationService의 `with self._transaction():` 두 줄을 통째로 빼도
plan이 추가하는 신규 테스트가 전부 GREEN이다 — 원자성 요구의 회귀 방어선이 0이 된다.
로컬 재현(2026-08-03, sjmj_test): (curation_reviewed, 스탬프된 쌍, 사전 항목)이
트랜잭션이 있으면 (0, 0, [])이고, 없으면 (1, 2, ['휠'])로 부분 반영이 남는다.
"""

import pytest
from sqlalchemy import text

from app.repositories.curation_repository import CurationRepository
from app.repositories.items_repository import ItemRepository
from app.services.curation_service import CurationService

pytestmark = pytest.mark.usefixtures("db_conn")


class _SecondCallError(Exception):
    """두 번째 라벨 등록에서 터지는 테스트 전용 예외."""


class _FlakyItemRepo(ItemRepository):
    """첫 라벨은 실제로 INSERT하고 두 번째 라벨에서 던진다.

    순수 mock으로 대체하면 "첫 라벨도 사전에 없다"는 단언이 공허해진다(mock은 애초에
    쓰지 않는다). 첫 INSERT가 실제로 일어나야 롤백이 증명된다.
    """

    def __init__(self):
        super().__init__()
        self.calls = 0

    def ensure_exists(self, item_name: str) -> None:
        self.calls += 1
        if self.calls == 2:
            raise _SecondCallError(item_name)
        super().ensure_exists(item_name)


def _seed(engine) -> int:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ocr_jobs (status, image_path, curation_reviewed) "
                "VALUES ('done', '/tx.jpg', 0)"
            )
        )
        job_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
        for i, label in enumerate(["휠", "중고"]):
            conn.execute(
                text(
                    "INSERT INTO training_pairs "
                    "(crop_ref, job_id, row_index, final_label, canonical_label, status) "
                    "VALUES (:r, :j, :i, :l, :l, 'included')"
                ),
                {"r": f"job-{job_id}/row-{i}", "j": job_id, "i": i, "l": label},
            )
    return job_id


def _state(engine, job_id) -> tuple[int, int, list[str]]:
    with engine.begin() as conn:
        reviewed = conn.execute(
            text("SELECT curation_reviewed FROM ocr_jobs WHERE id = :i"), {"i": job_id}
        ).scalar()
        stamped = conn.execute(
            text(
                "SELECT COUNT(*) FROM training_pairs WHERE job_id = :i AND reviewed_at IS NOT NULL"
            ),
            {"i": job_id},
        ).scalar()
        names = list(conn.execute(text("SELECT item_name FROM item_suggestions")).scalars())
    return int(reviewed), int(stamped), names


def test_mark_reviewed_rolls_back_everything_when_registration_fails(db_conn):
    """N번째 라벨 등록이 실패하면 검수완료 표시·reviewed_at 스탬프·앞선 등록까지 되돌아간다."""
    job_id = _seed(db_conn)
    item_repo = _FlakyItemRepo()
    service = CurationService(CurationRepository(), item_repo)

    with pytest.raises(_SecondCallError):
        service.mark_reviewed(job_id)

    assert item_repo.calls == 2
    assert _state(db_conn, job_id) == (0, 0, [])
