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


class _RegistrationError(Exception):
    """라벨 등록에서 터지는 테스트 전용 예외."""


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
            raise _RegistrationError(item_name)
        super().ensure_exists(item_name)


class _FailingItemRepo(ItemRepository):
    """모든 라벨 등록에서 던진다 — patch_pair는 호출당 라벨이 하나뿐이라 '두 번째'가 없다."""

    def ensure_exists(self, item_name: str) -> None:
        raise _RegistrationError(item_name)


def _seed(engine, *, reviewed: int = 0) -> int:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ocr_jobs (status, image_path, curation_reviewed) "
                "VALUES ('done', '/tx.jpg', :r)"
            ),
            {"r": reviewed},
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

    with pytest.raises(_RegistrationError):
        service.mark_reviewed(job_id)

    assert item_repo.calls == 2
    assert _state(db_conn, job_id) == (0, 0, [])


def test_patch_pair_rolls_back_label_update_when_registration_fails(db_conn):
    """대칭 방어선 — patch_pair의 update_pair와 등록도 한 트랜잭션이어야 한다.

    단위 테스트는 transaction=nullcontext 주입이라 이 경계에 무감각하고 contract
    테스트는 성공 경로만 밟는다. `with self._transaction():`을 빼면 update_pair가
    standalone tx로 먼저 커밋돼, 등록이 실패해도 바뀐 라벨만 DB에 남는다.
    """
    job_id = _seed(db_conn, reviewed=1)
    with db_conn.begin() as conn:
        pair_id = conn.execute(
            text("SELECT id FROM training_pairs WHERE job_id = :i ORDER BY row_index LIMIT 1"),
            {"i": job_id},
        ).scalar()

    service = CurationService(CurationRepository(), _FailingItemRepo())
    with pytest.raises(_RegistrationError):
        service.patch_pair(int(pair_id), {"canonical_label": "새라벨"})

    with db_conn.begin() as conn:
        label = conn.execute(
            text("SELECT canonical_label FROM training_pairs WHERE id = :i"), {"i": pair_id}
        ).scalar()
    assert label == "휠"  # 등록이 실패했으니 라벨 갱신도 남지 않는다
    assert _state(db_conn, job_id)[2] == []
