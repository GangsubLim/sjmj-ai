"""한 잡 처리 단위. 잡 단위 격리 — 한 잡 실패가 워커를 죽이지 않는다.

크롭은 tmp 디렉터리에 만들고 DB 커밋이 성공한 뒤에만 디렉터리째 교체한다(ADR 0010) —
실패한 재처리가 "새 그림 + 옛 라벨"을 남기지 않게 하는 순서다. 신규 잡도 같은 경로를
탄다: 지금도 신규 잡의 부분 실패는 반쪽 크롭을 남기고, 경로가 둘이면 재처리에만 있는
버그가 생긴다(spec §9).
"""

import shutil
import sys
from pathlib import Path

from handwriting.relink import NewRow, plan_relink


def _new_rows(result_json: dict) -> list[NewRow]:
    """새 초안에서 행 앵커 입력을 뽑는다.

    supply는 assemble_result_json이 THOUSAND_MULT를 적용한 뒤의 원 단위라
    training_pairs.supply(사람이 화면에 입력한 원 단위)와 자가 같다(spec §4 축 정합).
    rows가 없거나 리스트가 아니면 빈 목록으로 본다 — 전량 미결이 되어 사람에게 드러난다.
    """
    rows = result_json.get("rows")
    if not isinstance(rows, list):
        return []
    return [NewRow(row_index=r["row_index"], supply=r.get("supply")) for r in rows]


def _swap_crop_dir(tmp_dir: Path, final_dir: Path) -> None:
    """Tmp 디렉터리를 최종 위치로 교체한다(같은 파일시스템 rename 2회).

    커밋과 이 교체 사이(밀리초)에 크롭을 읽는 요청은 "새 좌표 + 옛 그림"을 본다.
    비영속이며 새로고침으로 사라지므로 문서화된 한계로 수용한다(spec 아키텍처).

    **실패해도 .old를 되돌리지 않는 것이 의도다.** 이 지점에서 DB는 이미 새 좌표라,
    옛 그림을 제자리에 복원하면 "새 좌표 + 그럴싸한 옛 그림"이 영속돼 사람이 이상을
    감지하지 못한 채 검수를 확정한다 — 크롭이 아예 없어 404가 뜨는 편이 정직하고,
    뱅크 쪽도 prune_missing_crops가 add/replace를 보류해 오염이 막힌다. 남은 .old·.tmp는
    "이 잡은 파일 교체가 끝나지 않았다"는 durable 마커이며, --reembed-job 가드
    (tools/bank_update.require_settled_crops)가 이것을 읽어 재임베딩을 거부한다(§11-1).
    성공 경로에서만 마지막 rmtree로 지운다.
    """
    old_dir = final_dir.with_name(final_dir.name + ".old")
    shutil.rmtree(old_dir, ignore_errors=True)
    if final_dir.exists():
        final_dir.rename(old_dir)
    tmp_dir.rename(final_dir)
    shutil.rmtree(old_dir, ignore_errors=True)


def process_one_job(queue, infer_fn, crops_root) -> bool:
    """대기 중인 잡 1건을 처리한다. 처리했으면 True, 큐가 비었으면 False."""
    job = queue.claim_next_pending()
    if job is None:
        return False
    job_id = job["id"]
    crops_root = Path(crops_root)
    final_dir = crops_root / f"job-{job_id}"
    tmp_dir = crops_root / f"job-{job_id}.tmp"
    old_dir = crops_root / f"job-{job_id}.old"
    try:
        if tmp_dir.exists():
            # 앞선 실패가 남긴 잔여 — **지우지 않고 .old로 옮긴다.** 커밋 성공 후 교체
            # 직전에 죽은 실행에서는 이 tmp가 "교체가 끝나지 않았다"의 유일한 마커다.
            # 여기서 지우면 이번 실행마저 실패했을 때 DB는 새 좌표인데 파일은 옛 그림이고
            # 마커는 없는 상태가 되어, --reembed-job 가드가 그대로 통과한다(§11-1).
            # 내용은 판별 근거가 아니다(마커는 존재 자체가 신호다) — 접미사만 옮긴다.
            shutil.rmtree(old_dir, ignore_errors=True)
            tmp_dir.rename(old_dir)
        result = infer_fn(job["image_path"], tmp_dir, job_id)
        plan = plan_relink(job_id, queue.fetch_pairs(job_id), _new_rows(result))
        queue.commit_job(job_id, result, plan)
    except Exception as exc:  # noqa: BLE001 — 잡 단위 격리(워커 생존)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        if job["is_reprocess"]:
            # 재처리 실패는 failed가 아니라 done이다 — 이번 실행은 커밋에 닿지 못했으므로
            # 초안·크롭이 실행 전 그대로다. 단, 앞선 실행이 커밋 후 교체 전에 죽었다면
            # 실행 전 상태 자체가 이미 어긋나 있다(DB는 새 좌표, 파일은 옛 그림) — 그
            # 경우는 위에서 .old로 보존한 마커가 남아 재임베딩 가드가 잡아낸다.
            queue.rollback_to_done(job_id)
        else:
            queue.mark_failed(job_id, {"error": str(exc)})
        return True
    try:
        _swap_crop_dir(tmp_dir, final_dir)
    except OSError as exc:
        # 커밋은 이미 성공했으므로 DB는 새 좌표, 파일은 옛 그림이다. 재처리는 멱등이라
        # (같은 사진·같은 엔진이면 매칭이 항등) 다시 큐에 넣는 것이 복구다.
        print(
            f"[reprocess] job={job_id} 크롭 교체 실패 — 재처리 큐로 되돌린다: {exc}",
            file=sys.stderr,
            flush=True,
        )
        queue.requeue_for_reprocess(job_id)
    return True
