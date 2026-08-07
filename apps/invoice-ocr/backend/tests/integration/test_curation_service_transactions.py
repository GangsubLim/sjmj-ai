"""서비스 트랜잭션 경계 2종을 고정한다 — `mark_reviewed`의 등록 트리거(#40 spec §3.2) /
`patch_pair`의 게이트 해제(#52 spec §4.2).

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


class _PairUpdateError(Exception):
    """쌍 갱신에서 터지는 테스트 전용 예외."""


class _FailingPairRepo(CurationRepository):
    """release_gate는 실제로 쓰고 update_pair에서 던진다.

    #52로 patch_pair의 라벨 등록 경로가 사라져(spec §3.3) item_repo는 더 이상 실패
    지렛대가 아니다. 남은 두 쓰기 사이를 갈라놓으려면 여기서 던져야 한다.
    gate_released는 롤백 여부가 아니라 "release_gate가 실제로 호출됐는지"를 세는
    카운터다 — 이게 없으면 patch_pair가 release_gate 호출 자체를 건너뛰어도(레버가
    당겨지지 않아도) 게이트 값은 그대로 1로 남아 아래 단언이 우연히 통과한다.
    """

    def __init__(self):
        super().__init__()
        self.gate_released = 0

    def release_gate(self, job_id: int) -> None:
        self.gate_released += 1
        super().release_gate(job_id)

    def update_pair(self, pair_id: int, fields: dict) -> None:
        # **먼저 실제로 쓴 뒤** 던진다. 쓰지 않고 던지면 되돌아갈 쌍 갱신이 애초에 없어
        # release_gate의 롤백만 증명된다 — update_pair가 바운드 커넥션에 합류하는지는
        # 미검증으로 남고, 자체 커넥션을 여는 구현으로 퇴행해도 GREEN이다.
        super().update_pair(pair_id, fields)
        raise _PairUpdateError(pair_id)


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


def _canonical_label(engine, pair_id) -> str:
    with engine.begin() as conn:
        return conn.execute(
            text("SELECT canonical_label FROM training_pairs WHERE id = :id"), {"id": pair_id}
        ).scalar()


def test_mark_reviewed_rolls_back_everything_when_registration_fails(db_conn):
    """N번째 라벨 등록이 실패하면 검수완료 표시·reviewed_at 스탬프·앞선 등록까지 되돌아간다."""
    job_id = _seed(db_conn)
    item_repo = _FlakyItemRepo()
    service = CurationService(CurationRepository(), item_repo)

    with pytest.raises(_RegistrationError):
        service.mark_reviewed(job_id, CurationRepository().get_job_token(job_id))

    assert item_repo.calls == 2
    assert _state(db_conn, job_id) == (0, 0, [])


def test_patch_pair_rolls_back_gate_release_when_pair_update_fails(db_conn):
    """대칭 방어선 — patch_pair의 release_gate와 update_pair도 한 트랜잭션이어야 한다.

    단위 테스트는 transaction=nullcontext 주입이라 이 경계에 무감각하고 contract
    테스트는 성공 경로만 밟는다. `with self._transaction():`을 빼면 release_gate가
    standalone tx로 먼저 커밋돼, 쌍 갱신이 실패해도 게이트만 풀린 채 남는다 — 화면은
    재검수를 요구하는데 정작 고치려던 값은 반영되지 않은 상태다.
    """
    job_id = _seed(db_conn, reviewed=1)
    with db_conn.begin() as conn:
        pair_id = conn.execute(
            text("SELECT id FROM training_pairs WHERE job_id = :i ORDER BY row_index LIMIT 1"),
            {"i": job_id},
        ).scalar()

    repo = _FailingPairRepo()
    # ItemRepository는 patch_pair 경로에서 미사용 — 라우터 배선(item_repo 주입)을 그대로
    # 미러링하려는 의도일 뿐, 이 테스트가 등록 로직에 관여한다는 뜻은 아니다.
    service = CurationService(repo, ItemRepository())
    with pytest.raises(_PairUpdateError):
        service.patch_pair(int(pair_id), {"canonical_label": "새라벨"}, repo.get_job_token(job_id))

    assert repo.gate_released == 1  # 레버가 실제로 당겨졌다(호출 자체가 생략되지 않았다)
    # 게이트 해제가 되돌아간다 — 부분 반영이 남지 않는다.
    assert _state(db_conn, job_id)[0] == 1
    # 쌍 갱신도 함께 되돌아간다 — 두 쓰기가 같은 트랜잭션이라는 증거.
    assert _canonical_label(db_conn, int(pair_id)) == "휠"
