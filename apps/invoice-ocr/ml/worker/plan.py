"""재처리 승계 계획 조립 — 운영과 드라이런의 단일 출처(spec §3.1).

경로가 갈리는 지점은 커밋 여부 하나뿐이다. 앵커 조회(fetch_pairs)와 계획 함수(plan_relink)는
물론 그 둘을 잇는 조립까지 이 모듈이 소유한다 — 사본이 둘이면 한쪽만 바뀌는 드리프트로
예측과 실측이 조용히 갈린다.

⚠️ 모듈 레벨에 stdlib 밖 의존을 handwriting.relink 말고는 두지 않는다
   (handwriting/relink.py 상단 규약과 동일) — 그래야 paddle-free 코어 venv에서도
   `from worker.plan import build_plan`이 성공한다.
"""

from handwriting.relink import NewRow, RelinkPlan, plan_relink


def new_rows(result_json: dict) -> list[NewRow]:
    """새 초안에서 행 앵커 입력을 뽑는다.

    supply는 assemble_result_json이 THOUSAND_MULT를 적용한 뒤의 원 단위라
    training_pairs.supply(사람이 화면에 입력한 원 단위)와 자가 같다(spec §4 축 정합).
    rows가 없거나 리스트가 아니면 빈 목록으로 본다 — 전량 미결이 되어 사람에게 드러난다.

    Args:
        result_json: 새 초안.

    Returns:
        행 앵커 입력 목록. rows가 없으면 빈 리스트.
    """
    rows = result_json.get("rows")
    if not isinstance(rows, list):
        return []
    return [NewRow(row_index=r["row_index"], supply=r.get("supply")) for r in rows]


def build_plan(queue, job_id: int, result_json: dict) -> RelinkPlan:
    """앵커 조회부터 계획 수립까지를 한 번에 한다 — 커밋은 하지 않는다.

    Args:
        queue: WorkerQueue(또는 fetch_pairs 계약의 대역).
        job_id: 대상 OCR 잡 id.
        result_json: 새 초안.

    Returns:
        승계·미결이 빠짐없이 담긴 RelinkPlan.
    """
    # rows 먼저 뽑아 malformed 입력이 DB 왕복 이전에 드러남.
    rows = new_rows(result_json)
    return plan_relink(job_id, queue.fetch_pairs(job_id), rows)
