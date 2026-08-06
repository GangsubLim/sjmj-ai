"""ml-worker의 ocr_jobs 큐 접근. backend와 동일 MySQL, DB_* env."""

import json
import os

from sqlalchemy import create_engine, text

from handwriting.relink import RELINK_FAILED, OldPair, RelinkPlan


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

    def fetch_pairs(self, job_id: int) -> list[OldPair]:
        """그 잡의 확정 학습쌍에서 행 앵커 입력만 뽑는다(승계는 라벨을 건드리지 않는다).

        신규 잡은 여기서 빈 리스트가 나와 승계가 자연히 no-op이 된다(spec §1).
        """
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, row_index, supply FROM training_pairs "
                    "WHERE job_id=:id ORDER BY row_index, id"
                ),
                {"id": job_id},
            ).fetchall()
        return [OldPair(pair_id=r[0], row_index=r[1], supply=r[2]) for r in rows]

    def commit_job(self, job_id: int, result_json: dict, plan: RelinkPlan) -> None:
        """초안 갱신과 라벨 승계를 한 트랜잭션으로 커밋한다(ADR 0010).

        세로선이 여기 한 번만 그어진다 — 커밋 이전엔 아무것도 바뀌지 않고, 커밋 이후엔
        파일만 바뀐다(크롭 디렉터리 교체는 worker/poll.py가 커밋 성공 후에만 한다).

        **왜 2-pass인가.** crop_ref가 UNIQUE(migration_008)라 순차 UPDATE는 첫 문장부터
        duplicate key로 죽는다 — 전부 한 칸씩 밀리는 것이 이 작업의 주 케이스다. 그래서
        ① 그 잡의 쌍 **전량**을 row- 네임스페이스 밖(임시·orphan 좌표)으로 비우고
        ② 승계 대상에만 최종 좌표를 기입한다. 미결 전환이 ①에 함께 들어가는 것이 이
        설계의 핵심 순서 제약이다 — 뒤로 미루면 미결 쌍이 옛 row-N을 점유한 채 남아
        승계 쌍의 최종 좌표와 충돌한다.

        ②는 좌표만 쓰지 않는다 — 지난 재처리가 미결로 배제해 둔 쌍이 이번에 승계되면
        배제까지 함께 되돌린다(아래 주석의 판별자 근거 참조). 반복 재처리로 회수된 쌍이
        excluded인 채 뱅크 밖에 남는 것을 막는 자리다.

        락 순서는 부모(ocr_jobs) → 자식(training_pairs)로, 사람의 PATCH 경로
        (backend CurationService.patch_pair)와 같다 — 뒤집으면 순환 대기가 성립한다.

        Args:
            job_id: 대상 OCR 잡 id.
            result_json: 새 초안. 신규 잡·재처리 모두 이 경로로 기록된다.
            plan: 승계 계획. 신규 잡은 비어 있어 ①만 수행된다.
        """
        # 미결이 나온 잡만 게이트를 해제한다(ADR 0011). curation_reviewed_at은 지우지
        # 않는다 — migration_011의 3-state가 그 잡을 "재검수 필요"로 분류해야 한다.
        gate = ", curation_reviewed = 0" if plan.should_release_gate else ""
        with self.engine.begin() as conn:
            conn.execute(
                text(f"UPDATE ocr_jobs SET status='done', result_json=:r{gate} WHERE id=:id"),
                {"r": json.dumps(result_json, ensure_ascii=False), "id": job_id},
            )
            # ① 전량을 row- 밖으로 — 이 시점에 그 잡의 어떤 쌍도 job-\d+/row-\d+ 형식을
            #    갖지 않는다는 것이 ②의 충돌 불가능성의 근거다.
            for item in plan.relinked:
                conn.execute(
                    text("UPDATE training_pairs SET crop_ref=:ref WHERE id=:id"),
                    {"ref": item.tmp_ref, "id": item.pair_id},
                )
            for item in plan.orphaned:
                # reviewed_at을 되돌리는 것은 미결 쌍뿐이다 — 그 잡의 unreviewed_count가
                # 정확히 미결 수가 되어 사람이 볼 것만 큐에 뜬다(§7).
                conn.execute(
                    text(
                        "UPDATE training_pairs SET crop_ref=:ref, status='excluded', "
                        "exclusion_reason=:reason, reviewed_at=NULL WHERE id=:id"
                    ),
                    {"ref": item.orphan_ref, "reason": RELINK_FAILED, "id": item.pair_id},
                )
            # ② 최종 좌표 기입 + 기계 소유 배제의 자동 복원.
            #    지난 재처리가 미결로 배제한 쌍이 이번엔 승계됐다면 그림이 돌아온 것이므로
            #    배제를 되돌린다 — 반복 재처리가 이 기능의 전제인데 되돌리지 않으면 회수된
            #    쌍이 excluded인 채 뱅크 밖에 영영 남는다.
            #    **판별자로 사유를 쓰는 근거.** 사람이 배제하면 backend의
            #    curation_repository.update_pair가 같은 UPDATE에서 exclusion_reason을 NULL로
            #    지운다(ADR 0006 §6) — 사유가 relink_failed로 남아 있다는 것 자체가 "아직
            #    기계 판정이며 사람이 손대지 않았다"는 뜻이라, 사람의 배제는 자동으로 빠진다.
            #    reviewed_at은 건드리지 않는다 — NULL로 남아 사람이 검수 큐에서 확인한다.
            #    런북 0단계의 결정(재처리 이전부터 blank_crop으로 배제돼 있던 쌍도 승계 실패
            #    시 relink_failed로 덮인다)과 맞물려 원래 blank_crop이던 쌍이 여기서 included로
            #    돌아올 수 있다 — 크롭이 여전히 비었다면 빈 크롭 가드가 다음 실행에서 다시
            #    배제하므로 그 결정의 논리("옛 사유가 가리키던 그림은 이미 없다")와 정합한다.
            for item in plan.relinked:
                conn.execute(
                    text(
                        "UPDATE training_pairs SET crop_ref=:ref, row_index=:ri, "
                        "status = CASE WHEN exclusion_reason=:reason THEN 'included' "
                        "ELSE status END, "
                        "exclusion_reason = CASE WHEN exclusion_reason=:reason THEN NULL "
                        "ELSE exclusion_reason END WHERE id=:id"
                    ),
                    {
                        "ref": item.final_ref,
                        "ri": item.final_row_index,
                        "reason": RELINK_FAILED,
                        "id": item.pair_id,
                    },
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
