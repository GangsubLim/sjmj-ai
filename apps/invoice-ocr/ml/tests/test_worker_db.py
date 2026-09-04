"""WorkerQueue 단위 테스트 — MagicMock engine, 라이브 MySQL 불필요."""

from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from handwriting.relink import RELINK_FAILED, NewRow, OldPair, plan_relink
from worker.db import WorkerQueue

# ---------------------------------------------------------------------------
# 실행형 픽스처 — SQL 문자열이 아니라 행의 최종 상태를 본다.
#
# 배제 소유권(사람/기계) 규칙은 CASE·WHERE의 의미론이 걸린 자리라, 문자열 단언으로는
# 조건을 뒤집어도 GREEN이 유지된다. 인메모리 엔진에 실제로 문장을 실어 결과 행을 읽는다.
# ⚠️ 이 경로에 쓰는 SQL은 UPDATE 대입 순서에 의존해선 안 된다 — MySQL은 뒤 대입이 앞
#    대입의 결과를 보고 SQLite는 항상 옛 값을 본다. 조건은 전부 WHERE로 나가야 두 엔진에서
#    같은 뜻이 된다.
# ---------------------------------------------------------------------------

_SCHEMA = (
    "CREATE TABLE ocr_jobs (id INTEGER PRIMARY KEY, status TEXT, image_path TEXT, "
    "result_json TEXT, curation_reviewed INTEGER DEFAULT 1, "
    # migration_014. 워커 내부 재시도 세 경로는 이 값을 **올리지 않아야** 한다(spec §6-2) —
    # 실행형 픽스처라야 그 부작위를 실제로 잴 수 있다.
    "reprocess_seq INTEGER NOT NULL DEFAULT 0)",
    # crop_ref UNIQUE는 운영 스키마 그대로다(migration_008) — 2-pass 순서 제약의 근거라
    # 여기서도 걸어야 순서를 뒤집었을 때 테스트가 실제로 깨진다.
    # draft_supply는 migration_012가 만든 ② 앵커 컬럼 — fetch_pairs가 이 컬럼만 읽는다.
    "CREATE TABLE training_pairs (id INTEGER PRIMARY KEY, job_id INTEGER, "
    "crop_ref TEXT UNIQUE, row_index INTEGER, supply INTEGER, draft_supply INTEGER, "
    "draft_label TEXT, status TEXT, exclusion_reason TEXT, reviewed_at TEXT)",
)


def _live_engine(pairs):
    """ocr_jobs 1건 + 주어진 training_pairs로 채운 인메모리 엔진을 만든다.

    pairs 항목의 "dsup"(draft_supply)·"dlab"(draft_label)는 선택이다 — 생략하면
    NULL(앵커·초안 라벨 없음)이다.
    """
    engine = create_engine("sqlite://", future=True)
    with engine.begin() as conn:
        for ddl in _SCHEMA:
            conn.execute(text(ddl))
        conn.execute(
            text(
                "INSERT INTO ocr_jobs (id, status, image_path, result_json) "
                "VALUES (5, 'running', '/data/up/5.jpeg', '{}')"
            )
        )
        for p in pairs:
            conn.execute(
                text(
                    "INSERT INTO training_pairs (id, job_id, crop_ref, row_index, supply, "
                    "draft_supply, draft_label, status, exclusion_reason, reviewed_at) VALUES "
                    "(:id, 5, :ref, :ri, :sup, :dsup, :dlab, :st, :reason, :rev)"
                ),
                {"dsup": None, "dlab": None, **p},
            )
    return engine


def _pair(engine, pair_id):
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT status, exclusion_reason, crop_ref FROM training_pairs WHERE id=:id"),
            {"id": pair_id},
        ).fetchone()
    return {"status": row[0], "exclusion_reason": row[1], "crop_ref": row[2]}


def _draft_labels(engine):
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT id, draft_label FROM training_pairs ORDER BY id")
        ).fetchall()
    return {r[0]: r[1] for r in rows}


# ---------------------------------------------------------------------------
# commit_job — 초안 갱신 + 2-pass 승계를 한 트랜잭션으로
# ---------------------------------------------------------------------------


def _executed(conn):
    """conn.execute 호출을 (sql, params) 목록으로 편다."""
    return [(str(c[0][0]), c[0][1] if len(c[0]) > 1 else {}) for c in conn.execute.call_args_list]


def test_commit_job_updates_draft_and_marks_done():
    """신규 잡(빈 계획)은 ① 초안 갱신 + done 전이만 수행한다."""
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    plan = plan_relink(5, [], [NewRow(row_index=0, supply=3000)])

    WorkerQueue(engine).commit_job(5, {"rows": [], "supply_sum": 0, "warp_ok": True}, plan)

    calls = _executed(conn)
    assert len(calls) == 1, "빈 계획이면 training_pairs를 건드리지 않는다"
    sql, params = calls[0]
    assert "status='done'" in sql
    assert '"warp_ok"' in params["r"]
    assert params["id"] == 5


def test_commit_job_rolls_everything_back_when_one_statement_fails():
    """초안 갱신과 승계가 갈라지면 그 사이가 정식 중간 단계로 승격된다(ADR 0010).

    engine.begin 호출 수를 세는 것으로는 원자성이 서지 않는다 — 한 번 열고 그 밖에서
    쓰는 코드도 call_count == 1이다. 문장 하나를 실패시켜 **아무것도 남지 않는지**를
    실 엔진에서 본다. 잡 5는 이미 다른 쌍이 job-5/row-0을 점유하고 있어 ②단계 최종
    좌표 기입이 crop_ref UNIQUE에 걸린다(운영 스키마 migration_008 그대로).
    """
    engine = _live_engine(
        [
            {
                "id": 1,
                "ref": "job-5/row-9",
                "ri": 9,
                "sup": 3000,
                "st": "included",
                "reason": None,
                "rev": None,
            },
            {
                "id": 2,
                "ref": "job-5/row-0",  # 계획 밖의 쌍이 최종 좌표를 선점
                "ri": 0,
                "sup": 9999,
                "st": "included",
                "reason": None,
                "rev": None,
            },
        ]
    )
    plan = plan_relink(5, [OldPair(1, 9, 3000)], [NewRow(0, 3000)])

    with pytest.raises(IntegrityError):
        WorkerQueue(engine).commit_job(5, {"rows": [], "warp_ok": True}, plan)

    with engine.begin() as conn:
        job = conn.execute(text("SELECT status, result_json FROM ocr_jobs WHERE id=5")).fetchone()
    assert job == ("running", "{}"), "초안 갱신도 함께 되돌아간다"
    assert _pair(engine, 1)["crop_ref"] == "job-5/row-9", "1단계 임시 좌표도 남지 않는다"


def _table_of(sql):
    """문장이 건드리는 테이블을 판별한다 — 아는 두 테이블 중 정확히 하나여야 한다.

    else로 떨어뜨리면 제3의 테이블(ocr_corrections 등)이 트랜잭션에 끼어들어도
    training_pairs로 분류돼 락 순서 단언이 그대로 통과한다(#94).
    """
    hit = [t for t in ("ocr_jobs", "training_pairs") if t in sql]
    assert len(hit) == 1, f"알 수 없는 테이블을 건드린다: {sql}"
    return hit[0]


def test_commit_job_locks_parent_before_children():
    """락 순서는 잡(부모) → 쌍(자식) — 사람의 PATCH와 순환 대기에 걸리지 않는다."""
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    plan = plan_relink(5, [OldPair(1, 0, 3000)], [NewRow(0, 3000)])

    WorkerQueue(engine).commit_job(5, {"rows": []}, plan)

    tables = [_table_of(sql) for sql, _ in _executed(conn)]
    assert tables[0] == "ocr_jobs"
    assert "ocr_jobs" not in tables[1:]


def test_commit_job_empties_row_namespace_before_writing_final_refs():
    """1단계가 그 잡의 쌍 전량을 row- 밖으로 뺀 뒤에야 2단계가 최종 좌표를 쓴다(§5).

    전부 한 칸씩 밀리는 케이스 — 순차 UPDATE였다면 첫 문장부터 duplicate key로 죽는다.
    """
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    olds = [OldPair(1, 0, 3000), OldPair(2, 1, 5000)]
    plan = plan_relink(5, olds, [NewRow(0, 9000), NewRow(1, 3000), NewRow(2, 5000)])

    WorkerQueue(engine).commit_job(5, {"rows": []}, plan)

    refs = [params.get("ref") for _, params in _executed(conn)[1:]]
    assert refs == ["job-5/tmp-1", "job-5/tmp-2", "job-5/row-1", "job-5/row-2"]


def test_commit_job_moves_orphans_out_in_stage_one():
    """미결 전환이 1단계에 함께 들어가야 한다 — 뒤로 미루면 옛 row-N을 점유한 채 남는다.

    옛 row-0이 미결이고 옛 row-1이 row-0으로 승계되는 경우가 정확히 그 충돌이다.
    """
    engine = _live_engine(
        [
            {
                "id": 1,
                "ref": "job-5/row-0",
                "ri": 0,
                "sup": 3000,
                "st": "included",
                "reason": None,
                "rev": None,
            },
            {
                "id": 2,
                "ref": "job-5/row-1",
                "ri": 1,
                "sup": 5000,
                "st": "included",
                "reason": None,
                "rev": None,
            },
        ]
    )
    olds = [OldPair(1, 0, 3000), OldPair(2, 1, 5000)]
    plan = plan_relink(5, olds, [NewRow(0, 5000)])

    # crop_ref UNIQUE 위에서 도는 실제 문장이다 — 미결 전환을 뒤로 미루면 쌍 1이 row-0을
    # 점유한 채 남아 쌍 2의 최종 좌표 기입이 duplicate key로 죽는다.
    WorkerQueue(engine).commit_job(5, {"rows": []}, plan)

    assert _pair(engine, 1)["crop_ref"] == "job-5/orphan-1"
    assert _pair(engine, 2)["crop_ref"] == "job-5/row-0"


def test_commit_job_marks_orphans_excluded_with_reason_and_clears_review():
    """미결 쌍은 excluded + relink_failed + reviewed_at NULL로 사람 큐에 오른다(§6·§7)."""
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    plan = plan_relink(5, [OldPair(1, 0, 3000)], [])

    WorkerQueue(engine).commit_job(5, {"rows": []}, plan)

    sql, params = _executed(conn)[1]
    assert "status='excluded'" in sql
    assert "reviewed_at=NULL" in sql
    assert params["reason"] == RELINK_FAILED


def test_commit_job_restores_machine_excluded_pairs_when_relink_succeeds():
    """지난 재처리가 미결로 배제한 쌍이 이번에 승계되면 배제를 자동 해제한다.

    반복 재처리가 이 기능의 전제라, 여기서 되돌리지 않으면 그림이 돌아온 쌍이
    excluded·relink_failed인 채 남아 뱅크에 영영 들어가지 않는다.
    """
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    plan = plan_relink(5, [OldPair(1, 0, 3000)], [NewRow(0, 3000)])

    WorkerQueue(engine).commit_job(5, {"rows": []}, plan)

    sql, params = _executed(conn)[-1]
    assert "status = CASE WHEN exclusion_reason=:reason THEN 'included' ELSE status END" in sql
    assert (
        "exclusion_reason = CASE WHEN exclusion_reason=:reason THEN NULL ELSE exclusion_reason END"
        in sql
    )
    assert params["reason"] == RELINK_FAILED


def test_commit_job_leaves_human_excluded_pairs_excluded_when_relink_succeeds():
    """사람이 배제한 쌍은 승계돼도 배제로 남는다 — 복원 조건이 기계 사유에만 걸려 있다.

    사람이 배제하면 backend의 curation_repository.update_pair가 같은 UPDATE에서
    exclusion_reason을 NULL로 지운다(ADR 0006). 사유가 남아 있다는 것 자체가 "아직 기계
    판정이며 사람이 손대지 않았다"의 표식이라, 조건을 사유에 걸면 사람 소유 배제는
    자동으로 제외된다. 무조건 복원으로 바뀌면 이 단언이 RED가 된다.
    """
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    plan = plan_relink(5, [OldPair(1, 0, 3000)], [NewRow(0, 3000)])

    WorkerQueue(engine).commit_job(5, {"rows": []}, plan)

    sql, params = _executed(conn)[-1]
    assert sql.count("CASE WHEN exclusion_reason=:reason") == 2, "복원 조건은 기계 사유에만 건다"
    assert params["reason"] == RELINK_FAILED
    assert "status='included'" not in sql, "무조건 복원이면 사람 배제까지 되돌아온다"
    assert "exclusion_reason=NULL" not in sql, "사람 소유 표식(NULL 사유)을 덮어쓰지 않는다"


def test_orphaning_a_human_excluded_pair_keeps_its_null_reason():
    """사람이 배제한 쌍(사유 NULL)은 미결로 밀려도 사유가 NULL로 남는다.

    사유를 relink_failed로 덮으면 사람 소유 표식이 파괴되고, 다음 재처리에서 승계가
    성공하는 순간 ②단계의 복원 CASE가 참이 되어 사람이 "학습에 쓰지 말라"고 판정한 쌍이
    included로 되돌아간다(검수완료 잡이면 reviewed_at이 있어 큐에도 뜨지 않는다).
    """
    engine = _live_engine(
        [
            {
                "id": 1,
                "ref": "job-5/row-0",
                "ri": 0,
                "sup": 3000,
                "st": "excluded",
                "reason": None,
                "rev": "2026-01-01",
            }
        ]
    )
    plan = plan_relink(5, [OldPair(1, 0, 3000)], [])

    WorkerQueue(engine).commit_job(5, {"rows": []}, plan)

    assert _pair(engine, 1) == {
        "status": "excluded",
        "exclusion_reason": None,
        "crop_ref": "job-5/orphan-1",
    }


def test_machine_excluded_pair_still_gets_relink_failed_when_orphaned():
    """기계 배제(blank_crop)는 미결 사유로 덮인다 — 그 사유가 가리키던 그림이 이미 없다."""
    engine = _live_engine(
        [
            {
                "id": 1,
                "ref": "job-5/row-0",
                "ri": 0,
                "sup": 3000,
                "st": "excluded",
                "reason": "blank_crop",
                "rev": "2026-01-01",
            }
        ]
    )
    plan = plan_relink(5, [OldPair(1, 0, 3000)], [])

    WorkerQueue(engine).commit_job(5, {"rows": []}, plan)

    assert _pair(engine, 1)["exclusion_reason"] == RELINK_FAILED


def test_human_exclusion_survives_a_full_orphan_then_relink_cycle():
    """미결 → 재승계 왕복을 거쳐도 사람의 배제 결정이 살아남는다(반복 재처리 전제)."""
    engine = _live_engine(
        [
            {
                "id": 1,
                "ref": "job-5/row-0",
                "ri": 0,
                "sup": 3000,
                "st": "excluded",
                "reason": None,
                "rev": "2026-01-01",
            }
        ]
    )
    queue = WorkerQueue(engine)
    queue.commit_job(5, {"rows": []}, plan_relink(5, [OldPair(1, 0, 3000)], []))  # 1회차: 미결
    queue.commit_job(  # 2회차: 승계 성공
        5, {"rows": []}, plan_relink(5, [OldPair(1, 0, 3000)], [NewRow(0, 3000)])
    )

    assert _pair(engine, 1)["status"] == "excluded", "사람의 배제가 자동 복원되면 안 된다"
    assert _pair(engine, 1)["exclusion_reason"] is None


def test_commit_job_keeps_reviewed_at_of_relinked_pairs():
    """승계 성공 쌍의 reviewed_at은 그대로 둔다 — 사람이 볼 것이 미결뿐이 되도록(§7)."""
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    plan = plan_relink(5, [OldPair(1, 0, 3000)], [NewRow(0, 3000)])

    WorkerQueue(engine).commit_job(5, {"rows": []}, plan)

    assert all("reviewed_at" not in sql for sql, _ in _executed(conn))


def test_commit_job_releases_gate_only_when_orphans_exist():
    """미결이 나온 잡만 게이트를 해제한다(ADR 0011). curation_reviewed_at은 지우지 않는다."""
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    plan = plan_relink(5, [OldPair(1, 0, 3000)], [])

    WorkerQueue(engine).commit_job(5, {"rows": []}, plan)

    job_sql = _executed(conn)[0][0]
    assert "curation_reviewed = 0" in job_sql
    assert "curation_reviewed_at" not in job_sql


def test_commit_job_keeps_gate_when_every_pair_is_relinked():
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    plan = plan_relink(5, [OldPair(1, 0, 3000)], [NewRow(0, 3000)])

    WorkerQueue(engine).commit_job(5, {"rows": []}, plan)

    assert "curation_reviewed" not in _executed(conn)[0][0]


def test_commit_job_does_not_touch_draft_label():
    """correction_json.lines[].draft_label과의 짝을 깨지 않는다(§8).

    SQL 문자열에 'draft_label'이 없는지 보는 것은 반증이 어렵다 — 컬럼명을 감싸거나
    다른 문장에서 같은 값을 바꿔도 통과한다. 실 엔진에 문장을 실어 **행의 값이 그대로인지**
    본다. 계획은 승계와 미결을 함께 내야 네 문장이 전부 실행된다.
    """
    engine = _live_engine(
        [
            {
                "id": 1,
                "ref": "job-5/row-0",
                "ri": 0,
                "sup": 3000,
                "dlab": "중고타이어",
                "st": "included",
                "reason": None,
                "rev": None,
            },
            {
                "id": 2,
                "ref": "job-5/row-1",
                "ri": 1,
                "sup": 5000,
                "dlab": "엔진오일",
                "st": "included",
                "reason": None,
                "rev": None,
            },
        ]
    )
    plan = plan_relink(5, [OldPair(1, 0, 3000), OldPair(2, 1, 5000)], [NewRow(0, 3000)])
    assert plan.relinked and plan.orphaned, "네 문장 전부를 태우려면 승계·미결이 둘 다 필요하다"

    WorkerQueue(engine).commit_job(5, {"rows": []}, plan)

    assert _draft_labels(engine) == {1: "중고타이어", 2: "엔진오일"}


def test_commit_job_does_not_touch_draft_supply():
    """확정 시점 앵커는 재처리가 갱신하지 않는다(spec 결정 3 — #99 재오염 경로 차단).

    draft_supply는 build_training_pairs가 확정 트랜잭션에서 한 번만 적재한다(#106).
    승계가 이 컬럼을 건드리게 되면 붕괴 런(#99)이 다음 재처리에서 앵커를 재오염시키는
    경로가 되살아난다 — draft_label과 같은 규칙을 여기서도 고정한다.

    계획은 승계와 미결을 **함께** 내야 한다 — commit_job의 네 문장 중 둘이 미결 갈래라,
    승계만 있는 계획으로는 그 두 문장이 실행되지 않아 단언이 통째로 비껴간다.
    """
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    plan = plan_relink(5, [OldPair(1, 0, 3000), OldPair(2, 1, 5000)], [NewRow(0, 3000)])
    assert plan.relinked and plan.orphaned, "네 문장 전부를 태우려면 승계·미결이 둘 다 필요하다"

    WorkerQueue(engine).commit_job(5, {"rows": []}, plan)

    assert all("draft_supply" not in sql for sql, _ in _executed(conn))


# ---------------------------------------------------------------------------
# mark_failed
# ---------------------------------------------------------------------------


def test_mark_failed_serializes_json():
    """mark_failed는 error_json을 JSON 직렬화해 :r 에 바인딩한다.
    SQL에 status='failed' 리터럴, :s 바인딩 없음.
    """
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    q = WorkerQueue(engine)
    q.mark_failed(7, {"code": "TIMEOUT", "msg": "OCR timed out"})

    args = conn.execute.call_args
    assert args is not None
    sql_obj = args[0][0]
    assert "status='failed'" in str(sql_obj)
    params = args[0][1]
    assert "s" not in params, "status는 SQL 리터럴로 하드코딩 — :s 바인딩 없음"
    assert '"TIMEOUT"' in params["r"]
    assert params["id"] == 7


def test_requeue_stale_running_returns_stuck_jobs_to_pending():
    """부팅 워치독(#85) — running으로 굳은 잡 전량을 pending으로 되돌리고 id를 보고한다.

    추론 도중 프로세스가 죽으면 rollback_to_done이 돌지 않아 잡이 영구 running으로 남고,
    claim은 pending만 집고 reprocess API는 409라 복구가 순수 수동 절차였다. result_json은
    건드리지 않는다 — 신규/재처리는 다음 점유에서 rows 판별자가 스스로 재분류한다.
    """
    engine = _live_engine([])  # 잡 5가 running 상태로 심어진다

    ids = WorkerQueue(engine).requeue_stale_running()

    assert ids == [5]
    with engine.begin() as conn:
        row = conn.execute(text("SELECT status, result_json FROM ocr_jobs WHERE id=5")).fetchone()
    assert row[0] == "pending"
    assert row[1] == "{}", "재큐잉이 판별자 입력(result_json)을 건드리면 안 된다"


def test_requeue_stale_running_is_a_noop_when_nothing_is_stuck():
    engine = _live_engine([])
    with engine.begin() as conn:
        conn.execute(text("UPDATE ocr_jobs SET status='done' WHERE id=5"))

    assert WorkerQueue(engine).requeue_stale_running() == []

    with engine.begin() as conn:
        assert conn.execute(text("SELECT status FROM ocr_jobs WHERE id=5")).fetchone()[0] == "done"


def test_mark_failed_keep_result_preserves_the_draft():
    """초안 보존 실패 전이 — status만 failed로 바꾸고 result_json은 그대로 둔다.

    새 행 0건 가드(#92)·크롭 교체 상한(#88)이 쓰는 프리미티브다. 여기서 초안을 에러
    JSON으로 덮으면 잡의 좌표·초안이 사라져 실패를 사람이 복구할 근거가 없어지고,
    failed 재큐잉(#93) 시 신규/재처리 재분류의 판별자(rows 키)도 함께 부서진다.
    """
    engine = _live_engine([])

    WorkerQueue(engine).mark_failed_keep_result(5)

    with engine.begin() as conn:
        row = conn.execute(text("SELECT status, result_json FROM ocr_jobs WHERE id=5")).fetchone()
    assert row[0] == "failed"
    assert row[1] == "{}", "초안이 실행 전 그대로 남아야 한다"


# ---------------------------------------------------------------------------
# claim_next_pending — row 존재
# ---------------------------------------------------------------------------


def test_claim_next_pending_transitions_and_returns():
    """pending 행이 있으면 SELECT(FOR UPDATE) → UPDATE(running) 순서로 2회 execute,
    {id, image_path, is_reprocess, generation} dict 반환.
    """
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value

    fake_row = MagicMock()
    fake_row.id = 42
    fake_row.image_path = "/data/images/invoice_042.jpg"
    fake_row.is_reprocess = 0
    fake_row.reprocess_seq = 3

    first_result = MagicMock()
    first_result.fetchone.return_value = fake_row
    second_result = MagicMock()
    conn.execute.side_effect = [first_result, second_result]

    q = WorkerQueue(engine)
    result = q.claim_next_pending()

    assert conn.execute.call_count == 2, "SELECT와 UPDATE 각 1회씩 execute해야 함"
    select_sql = str(conn.execute.call_args_list[0][0][0])
    assert "FOR UPDATE" in select_sql
    update_args = conn.execute.call_args_list[1]
    assert "status='running'" in str(update_args[0][0])
    assert update_args[0][1]["id"] == 42
    assert result == {
        "id": 42,
        "image_path": "/data/images/invoice_042.jpg",
        "is_reprocess": False,
        "generation": 3,
    }


def test_claim_next_pending_orders_new_uploads_before_reprocessing():
    """재처리 잡은 정의상 옛 id라 순번만으로 세우면 신규 업로드를 밀어낸다(spec §2)."""
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    conn.execute.return_value.fetchone.return_value = None

    WorkerQueue(engine).claim_next_pending()

    select_sql = str(conn.execute.call_args_list[0][0][0])
    assert "ORDER BY (JSON_EXTRACT(result_json, '$.rows') IS NOT NULL), id" in select_sql


def test_claim_next_pending_does_not_classify_an_error_draft_as_reprocess():
    """판별자는 rows 키 존재다 — result_json 존재가 아니다(이슈 #91).

    mark_failed가 같은 컬럼에 {"error": ...}를 쓰므로, 실패 잡을 pending으로 되돌리면
    옛 판별자(result_json IS NOT NULL)는 그것을 재처리로 오분류한다. 재실패 시
    rollback_to_done이 불려 한 번도 성공한 적 없는 잡이 done + 에러 초안으로 남는다.
    워커가 쓴 성공 초안만이 rows 키를 가진다(assemble_result_json) — JSON_EXTRACT는
    MySQL·SQLite 모두에서 같은 뜻이라 실행형 픽스처의 문도 열어 둔다.
    """
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    conn.execute.return_value.fetchone.return_value = None

    WorkerQueue(engine).claim_next_pending()

    select_sql = str(conn.execute.call_args_list[0][0][0])
    assert "(JSON_EXTRACT(result_json, '$.rows') IS NOT NULL) AS is_reprocess" in select_sql
    assert "(result_json IS NOT NULL) AS is_reprocess" not in select_sql


def test_claim_next_pending_flags_reprocessing_jobs():
    """pending인데 result_json이 이미 있으면 재처리다 — 표식 컬럼을 만들지 않는다(§1)."""
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    fake_row = MagicMock()
    fake_row.id = 7
    fake_row.image_path = "/x.jpg"
    fake_row.is_reprocess = 1
    first = MagicMock()
    first.fetchone.return_value = fake_row
    conn.execute.side_effect = [first, MagicMock()]

    assert WorkerQueue(engine).claim_next_pending()["is_reprocess"] is True


def test_rollback_to_done_preserves_result_json():
    """재처리 실패는 failed가 아니라 done으로 되돌린다 — 옛 초안·옛 크롭이 그대로 정합이다."""
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value

    WorkerQueue(engine).rollback_to_done(11)

    sql = str(conn.execute.call_args[0][0])
    assert "status='done'" in sql
    assert "result_json" not in sql, "옛 초안을 덮으면 되돌릴 대상이 사라진다"
    assert conn.execute.call_args[0][1]["id"] == 11


def test_requeue_for_reprocess_sets_pending_without_touching_result_json():
    """커밋 후 교체 실패의 복구 경로 — result_json이 남아야 다시 재처리로 판별된다."""
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value

    WorkerQueue(engine).requeue_for_reprocess(11)

    sql = str(conn.execute.call_args[0][0])
    assert "status='pending'" in sql
    assert "result_json" not in sql


def test_requeue_pending_returns_new_and_reprocess_jobs_alike_without_touching_result_json():
    """degenerate로 중단된 잡(신규·재처리 공용, B1-b)의 복구 경로.

    result_json을 건드리지 않아야 신규 잡은 NULL로 남아 다음 점유에서 신규로,
    재처리 잡은 값이 남아 재처리로 스스로 재분류된다(claim_next_pending 판별자).
    """
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value

    WorkerQueue(engine).requeue_pending(11)

    sql = str(conn.execute.call_args[0][0])
    assert "status='pending'" in sql
    assert "result_json" not in sql, "result_json을 건드리면 신규/재처리 판별자가 깨진다"
    assert conn.execute.call_args[0][1]["id"] == 11


# ---------------------------------------------------------------------------
# claim_next_pending — 행 없음
# ---------------------------------------------------------------------------


def test_claim_next_pending_returns_none_when_empty():
    """pending 행이 없으면 None 반환, UPDATE를 실행하지 않는다."""
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    conn.execute.return_value.fetchone.return_value = None

    q = WorkerQueue(engine)
    result = q.claim_next_pending()

    assert result is None
    # SELECT만 1회, UPDATE 없음
    assert conn.execute.call_count == 1, "빈 큐면 SELECT만 실행, UPDATE 없어야 함"


# ---------------------------------------------------------------------------
# fetch_pairs
# ---------------------------------------------------------------------------


def test_fetch_pairs_returns_anchor_inputs_in_row_order():
    """승계 입력은 (pair_id, row_index, supply, draft_supply) 넷 — 라벨은 승계가 건드리지 않는다.

    두 앵커 모두 이 쌍의 컬럼에서 온다 — 옛 초안(ocr_jobs.result_json)을 조회하지 않는다.
    """
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    conn.execute.return_value.fetchall.return_value = [(1, 0, 3000, 3100), (2, 1, None, None)]

    pairs = WorkerQueue(engine).fetch_pairs(9)

    assert [(p.pair_id, p.row_index, p.supply, p.draft_supply) for p in pairs] == [
        (1, 0, 3000, 3100),
        (2, 1, None, None),
    ]
    assert conn.execute.call_count == 1, "앵커 출처는 training_pairs 한 테이블뿐이다"
    sql = str(conn.execute.call_args[0][0])
    assert "ORDER BY row_index" in sql
    assert "result_json" not in sql, "옛 초안을 다시 조인하면 봉인 경로가 되살아난다"
    assert conn.execute.call_args[0][1]["id"] == 9


def test_fetch_pairs_carries_the_draft_supply_column():
    """확정 시점 초안이 ② 회수 앵커로 그대로 실린다.

    training_pairs.supply는 사람이 확정한 값이라(ocr_correction) 새 쪽 모델값과 축이 다르다.
    이 컬럼이 실리지 않으면 draft 앵커가 전부 None이 되어 ②단계가 항상 no-op이다.

    실엔진에서 네 필드를 통째로 단언한다 — MagicMock 단언은 SELECT가 무엇을 뽑든 고정
    튜플을 돌려주므로 컬럼 순서·선택이 어긋나도 초록이다. sup과 dsup을 다른 값으로 심어
    한쪽이 다른 쪽으로 중복 선택되는 어긋남까지 잡는다.
    """
    engine = _live_engine(
        [
            {
                "id": 1,
                "ref": "job-5/row-0",
                "ri": 0,
                "sup": 5000,
                "dsup": 5100,
                "st": "included",
                "reason": None,
                "rev": None,
            }
        ]
    )

    pair = WorkerQueue(engine).fetch_pairs(5)[0]

    assert (pair.pair_id, pair.row_index, pair.supply, pair.draft_supply) == (1, 0, 5000, 5100)


def test_fetch_pairs_carries_the_draft_supply_of_an_orphaned_pair():
    """미결(orphan-) 쌍도 draft 앵커를 싣고 나온다 — 이 이슈의 핵심 고정(AC 4).

    옛 구현은 crop_ref가 row- 형식인 쌍에만 draft를 실었다. 미결이 되는 순간 ② 앵커가
    영구 봉인되고, 유일한 탈출구로 ①(사람 확정 금액 == 이번 인식)만 남는데 미결 쌍은
    정의상 사람이 금액을 교정한 행이라 ①은 구조적으로 실패한다.
    """
    engine = _live_engine(
        [
            {
                "id": 1,
                "ref": "job-5/row-0",
                "ri": 0,
                "sup": 5000,
                "dsup": 5100,
                "st": "included",
                "reason": None,
                "rev": None,
            },
            {
                "id": 2,
                "ref": "job-5/orphan-2",
                "ri": 0,
                "sup": 7000,
                "dsup": 7300,
                "st": "excluded",
                "reason": RELINK_FAILED,
                "rev": None,
            },
        ]
    )

    assert [(p.pair_id, p.draft_supply) for p in WorkerQueue(engine).fetch_pairs(5)] == [
        (1, 5100),
        (2, 7300),
    ]


def test_fetch_pairs_reads_its_own_column_even_when_the_old_draft_disagrees():
    """낡은 row_index로 인한 오결합 회귀 가드(AC 5).

    ocr_jobs.result_json에 같은 row_index로 전혀 다른 금액을 심어 둔다 — 옛 구현은 그 값을
    앵커로 실었고, 미결 쌍의 낡은 row_index에서는 그것이 **다른 행**의 금액이었다.
    컬럼 전환으로 구조적 보장이지만, 앵커 출처가 다시 좌표 조인으로 돌아가는 것을 막는다.
    """
    engine = _live_engine(
        [
            {
                "id": 1,
                "ref": "job-5/row-0",
                "ri": 0,
                "sup": 5000,
                "dsup": 5100,
                "st": "included",
                "reason": None,
                "rev": None,
            },
            {
                "id": 2,
                "ref": "job-5/orphan-2",
                "ri": 0,
                "sup": 7000,
                "dsup": 7300,
                "st": "excluded",
                "reason": RELINK_FAILED,
                "rev": None,
            },
        ]
    )
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE ocr_jobs SET result_json=:r WHERE id=5"),
            {"r": '{"rows": [{"row_index": 0, "supply": 999000}]}'},
        )

    assert [(p.pair_id, p.draft_supply) for p in WorkerQueue(engine).fetch_pairs(5)] == [
        (1, 5100),
        (2, 7300),
    ]


# ---------------------------------------------------------------------------
# fetch_pairs → plan_relink — 컬럼 앵커가 실제 ②단계 회수로 이어지는지(AC 4 후반)
# ---------------------------------------------------------------------------


def test_an_orphaned_pair_is_relinked_by_its_stored_draft_anchor():
    """미결 쌍이 컬럼 앵커로 실제 회수(relinked)된다 — 이슈 #106의 본체.

    이슈 본문 잡 69의 실측을 그대로 옮긴 값이다: 모델은 그때도 지금도 1720000으로 읽고
    사람은 720000으로 교정했다. ①(확정 금액 == 새 인식)은 구조적으로 실패하므로 회수는
    오직 draft 앵커로만 성립한다.
    """
    engine = _live_engine(
        [
            {
                "id": 1,
                "ref": "job-5/orphan-1",
                "ri": 0,
                "sup": 720000,
                "dsup": 1720000,
                "st": "excluded",
                "reason": RELINK_FAILED,
                "rev": None,
            }
        ]
    )

    olds = WorkerQueue(engine).fetch_pairs(5)
    plan = plan_relink(5, olds, [NewRow(row_index=0, supply=1720000)])

    assert [r.pair_id for r in plan.relinked] == [1]
    assert plan.relinked[0].final_ref == "job-5/row-0"
    assert plan.orphaned == ()


def test_a_pair_without_a_stored_draft_stays_orphaned():
    """NULL 앵커는 회수에서 빠진다 — 사유(정합 가드 탈락·미판독·범위 밖)를 구분하지 않는다.

    _anchor_seq가 None을 서로 절대 같지 않은 유일값으로 치환하므로 셋이 동일하게 동작한다.
    """
    engine = _live_engine(
        [
            {
                "id": 1,
                "ref": "job-5/orphan-1",
                "ri": 0,
                "sup": 720000,
                "st": "excluded",
                "reason": RELINK_FAILED,
                "rev": None,
            }
        ]
    )

    olds = WorkerQueue(engine).fetch_pairs(5)
    plan = plan_relink(5, olds, [NewRow(row_index=0, supply=1720000)])

    assert plan.relinked == ()
    assert [o.pair_id for o in plan.orphaned] == [1]


# ---------------------------------------------------------------------------
# fetch_image_path — 드라이런의 유일한 잡 조회(읽기 전용)
# ---------------------------------------------------------------------------


def test_fetch_image_path_reads_the_photo_without_changing_the_job():
    """claim_next_pending과 달리 status를 전이시키지 않는다 — 드라이런 무변경의 근거(spec §3.2)."""
    engine = _live_engine([])

    assert WorkerQueue(engine).fetch_image_path(5) == "/data/up/5.jpeg"

    with engine.begin() as conn:
        row = conn.execute(text("SELECT status FROM ocr_jobs WHERE id=5")).fetchone()
    assert row[0] == "running", "조회가 상태를 건드리면 무변경 보장이 무너진다"


def test_fetch_image_path_is_none_for_a_missing_job():
    assert WorkerQueue(_live_engine([])).fetch_image_path(999) is None


# ---------------------------------------------------------------------------
# claim_next_pending — 세대(generation)
# ---------------------------------------------------------------------------


def test_claim_next_pending_selects_the_reprocess_generation():
    """세대는 잡을 점유한 그 시점의 값이어야 한다 — 나중에 다시 읽으면 재처리 요청이 끼어든다."""
    engine = MagicMock()
    conn = engine.begin.return_value.__enter__.return_value
    conn.execute.return_value.fetchone.return_value = None

    WorkerQueue(engine).claim_next_pending()

    select_sql = str(conn.execute.call_args_list[0][0][0])
    assert "reprocess_seq" in select_sql


def test_worker_internal_requeues_do_not_bump_the_generation():
    """워커 내부 재시도는 같은 논리 세대다(spec §6-2) — 같은 사진·같은 엔진의 멱등 재실행.

    올리면 재실행이 쓴 geometry.json이 자기 잡의 DB 세대보다 뒤처져 영구 409가 된다.
    """
    engine = _live_engine([])
    q = WorkerQueue(engine)

    q.requeue_for_reprocess(5)
    q.requeue_pending(5)
    q.requeue_stale_running()

    with engine.begin() as conn:
        seq = conn.execute(text("SELECT reprocess_seq FROM ocr_jobs WHERE id=5")).scalar()
    assert seq == 0
