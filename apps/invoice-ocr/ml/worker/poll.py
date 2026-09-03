"""한 잡 처리 단위. 잡 단위 격리 — 한 잡 실패가 워커를 죽이지 않는다.

크롭은 tmp 디렉터리에 만들고 DB 커밋이 성공한 뒤에만 디렉터리째 교체한다(ADR 0010) —
실패한 재처리가 "새 그림 + 옛 라벨"을 남기지 않게 하는 순서다. 신규 잡도 같은 경로를
탄다: 지금도 신규 잡의 부분 실패는 반쪽 크롭을 남기고, 경로가 둘이면 재처리에만 있는
버그가 생긴다(spec §9).
"""

import shutil
import sys
from pathlib import Path
from typing import NamedTuple

from handwriting.amount_read import DegenerateOutputError
from worker.plan import build_plan, new_rows

# 크롭 교체(OSError) 연속 실패 상한(이슈 #88). 초과 시 requeue 대신 초안 보존 실패로
# 끊는다 — .old가 영구히 안 지워지는 잡이 잡당 수십 초의 재추론을 무한 반복하며 워커를
# 점유하는 것을 막는다. 카운터는 프로세스 메모리라 재시작 시 리셋된다(이슈가 수용 명시).
SWAP_RETRY_LIMIT = 3


class PollOutcome(NamedTuple):
    """한 번의 폴링 결과 — bool 반환을 명시 결과 타입으로 승격한 것.

    worked: 잡을 하나 처리했으면 True, 큐가 비었으면 False(호출자의 sleep 판단 입력).
    qwen_called: `result_json`에 행이 남아 있는가 — Qwen을 실제로 불렀는가의 프록시다.
        main()의 크래시루프 카운터 입력이며 게이트 강등(rows=[])·quad_missing은 False다.
        신규 행이 0건이면 group.block_amounts가 애초에 Qwen(read_fn)을 호출하지 않으므로
        qwen_called=False는 '미호출'과 정확히 일치한다.
    """

    worked: bool
    qwen_called: bool


class DegenerateWorkerState(SystemExit):
    """이 프로세스의 MLX 상태가 붕괴했으니 종료하고 재기동하라는 신호(이슈 #99).

    **SystemExit 서브클래스인 것이 계약이다.** 잡 격리 `except Exception`이나 미래에 추가될
    광역 핸들러가 실수로 흡수할 수 없다 — main 경계 테스트로 회귀를 막는 대신 언어 의미론으로
    보장한다. 종료 자체가 launchd KeepAlive=true의 재기동 트리거다 — KeepAlive는 boolean
    true라 종료 코드와 무관하게 재기동한다(code=1은 로그 판별용).
    """


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


def process_one_job(
    queue,
    infer_fn,
    crops_root,
    qwen_jobs_before: int,
    *,
    swap_failures: dict[int, int] | None = None,
) -> PollOutcome:
    """대기 중인 잡 1건을 처리한다.

    Args:
        queue: WorkerQueue(또는 같은 계약의 대역).
        infer_fn: (image_path, crop_dir, job_id) → result_json.
        crops_root: 크롭 루트 디렉터리.
        qwen_jobs_before: **이 프로세스가** 지금까지 Qwen을 부른 잡 수. degenerate가 나면 이
            값으로 그 잡을 은퇴시킬지(0 — mark_failed/rollback_to_done) 재시도 가능 상태로
            되돌릴지(≥1 — requeue_pending, 신규·재처리 동일) 가른다. 어느 쪽이든 워커는
            종료한다(sticky 붕괴라 살려둬도 회복되지 않는다). 카운터의 소유·갱신은 main()이
            한다.
        swap_failures: 잡별 크롭 교체 연속 실패 횟수(이슈 #88). qwen_jobs와 같은 소유
            구조다 — main()이 프로세스 수명 동안 하나를 들고 매 호출에 넘겨야 상한이
            선다. None이면 호출 내 1회 실패가 전부라 상한에 닿지 않는다(단발 테스트 편의).

    Returns:
        PollOutcome. 큐가 비었으면 PollOutcome(False, False).

    Raises:
        DegenerateWorkerState: 판독기가 degenerate일 때 항상. qwen_jobs_before == 0이면 그
            잡을 은퇴시킨 뒤, 아니면 재시도 가능 상태로 되돌린 뒤 던진다 — 프로세스 종료가
            곧 복구 수단이다.
    """
    job = queue.claim_next_pending()
    if job is None:
        return PollOutcome(worked=False, qwen_called=False)
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
        if job["is_reprocess"] and not new_rows(result):
            # 새 행 0건 재처리는 커밋 전에 실패로 끊는다(이슈 #92 고려안 1). plan_relink는
            # 이 조합을 전량 미결의 정상 계획으로 취급하므로, 커밋에 닿으면 확정 쌍이
            # 이어 붙일 새 행도 없이 orphan- 좌표로 옮겨져 복구 경로 없이 그림을 잃는다
            # (잡 39). 신규 잡의 rows=0(게이트 강등)은 이 갈래를 타지 않는다 — 잃을 옛
            # 좌표가 없어 커밋이 정상이다. 초안 보존 실패라 옛 초안·좌표·크롭이 그대로
            # 남고, failed 재큐잉(#93)이 열리면 rows 판별자가 재처리로 재분류한다.
            print(
                f"[reprocess] job={job_id} 새 행 0건 — 승계를 건너뛰고 실패 처리(초안 보존)",
                file=sys.stderr,
                flush=True,
            )
            shutil.rmtree(tmp_dir, ignore_errors=True)
            queue.mark_failed_keep_result(job_id)
            return PollOutcome(worked=True, qwen_called=False)
        plan = build_plan(queue, job_id, result)
        queue.commit_job(job_id, result, plan)
    except DegenerateOutputError as exc:
        # **잡 격리 except Exception보다 반드시 앞에 온다.** 스팸 결과가 commit_job에 닿는
        # 경로는 존재하지 않는다 — 감지가 read 시점 raise이므로 여기 도달했다는 것 자체가
        # 커밋 이전이라는 뜻이다.
        print(f"[degenerate] job={job_id} raw={exc}", file=sys.stderr, flush=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        is_first_qwen_job = qwen_jobs_before == 0
        if is_first_qwen_job:
            # 재기동해도 같은 붕괴가 반복될 뿐이므로 재시도시키지 않고 은퇴시킨다(B2-b —
            # 워커도 이 잡과 함께 종료한다. 살려둬 봐야 sticky 붕괴는 회복되지 않는다).
            if job["is_reprocess"]:
                queue.rollback_to_done(job_id)
            else:
                queue.mark_failed(job_id, {"error": str(exc)})
        else:
            # 재시도 갈래는 신규·재처리를 가르지 않는다(B1-b) — result_json 유무가 다음
            # 점유에서 스스로 신규/재처리를 가른다(requeue_pending 참조).
            queue.requeue_pending(job_id)
        raise DegenerateWorkerState(1) from exc
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
        return PollOutcome(worked=True, qwen_called=False)
    # 게이트 강등·quad_missing은 rows=[]로 돌아온다 — Qwen 미호출이므로 계수하지 않는다.
    # new_rows는 순수함수라 두 번 호출이 무해하고, "rows가 있다"의 정의는 여전히 worker.plan
    # 한 곳에 있다(드라이런의 크래시루프 카운터도 같은 술어를 쓴다).
    qwen_called = bool(new_rows(result))
    if swap_failures is None:
        swap_failures = {}
    try:
        _swap_crop_dir(tmp_dir, final_dir)
        # 상한은 연속 실패에만 건다 — 중간 성공이 카운터를 지워야 훗날의 무관한 실패가
        # 옛 실패와 합산돼 조기 은퇴하지 않는다.
        swap_failures.pop(job_id, None)
    except OSError as exc:
        failures = swap_failures.get(job_id, 0) + 1
        swap_failures[job_id] = failures
        if failures >= SWAP_RETRY_LIMIT:
            # 커밋된 새 좌표가 정본이고 .old 마커가 재임베딩 가드를 계속 닫아 둔다 —
            # failed 전이가 사람에게 보이는 유일한 신호다. 복구는 failed 재큐잉으로
            # 재처리(멱등) 재시도.
            print(
                f"[reprocess] job={job_id} 크롭 교체 실패 {failures}회 — 재시도 상한 도달, "
                f"실패 처리(초안 보존): {exc}",
                file=sys.stderr,
                flush=True,
            )
            queue.mark_failed_keep_result(job_id)
        else:
            # 커밋은 이미 성공했으므로 DB는 새 좌표, 파일은 옛 그림이다. 재처리는 멱등이라
            # (같은 사진·같은 엔진이면 매칭이 항등) 다시 큐에 넣는 것이 복구다.
            print(
                f"[reprocess] job={job_id} 크롭 교체 실패({failures}/{SWAP_RETRY_LIMIT}) — "
                f"재처리 큐로 되돌린다: {exc}",
                file=sys.stderr,
                flush=True,
            )
            queue.requeue_for_reprocess(job_id)
    return PollOutcome(worked=True, qwen_called=qwen_called)
