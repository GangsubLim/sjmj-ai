"""검증된 process_one을 감싸 HTML 대신 구조화 result_json을 반환한다.

assemble_result_json은 순수함수(TDD 대상). infer_job은 warp/embed/ocr 글루로
라이브 e2e가 검증한다(슬라이스는 실모델 추론을 단위테스트하지 않음).

⚠️ 모듈 레벨에 무거운 의존(cv2/torch/handwriting.infer_photo)을 두지 않는다.
   infer_job() 본문에서 지연 import한다 — 그래야 paddle-free venv에서도
   `from handwriting.infer_job import assemble_result_json`가 성공한다.
   handwriting.warp_gate는 예외다 — 모듈 레벨 의존이 dataclasses뿐이라 상단
   import해도 이 규약을 깨지 않는다(tests/test_warp_gate.py의 코어 격리 테스트로 검증됨).
"""

from handwriting.warp_gate import (
    MAX_BLUE_ASYMMETRY,
    MAX_PITCH_DEV,
    MIN_BLUE_RATIO,
    MIN_HLINES,
    compute_metrics,
    evaluate_warp,
)

# 수기 거래명세서는 천 단위를 생략해 적는다(spec: 단가·금액 100% 천원 배수) → 액면값에 ×1000.
THOUSAND_MULT = 1000

# ── 품목 retrieval 미확신 판정 임계 ──────────────────────────────────────
# top1 유사도가 이 값 미만이면 행에 item_uncertain=True를 붙인다. 검수 UI가 후보 칩을
# 기본 펼침으로 보여주는 신호일 뿐, 자동 기각·재추론에는 쓰지 않는다 — 적중군과 미스군의
# 유사도 분포가 겹치기 때문이다.
# 2026-07-28 #17 갱신 뱅크(271→306) leave-self-out 재채점(bank_update score, 35쌍)으로
# 확정: hit 10건(평균 0.853, 최소 0.7595) · miss 25건(평균 0.770). 0.75는 hit 오염 0%를
# 유지하는 최댓값이며, 0.76은 miss recall이 동일(36%)한데 hit 오염만 10%p 늘어 0.75에
# 강지배된다. 이 임계에서도 miss의 64%(16/25)는 여전히 확신으로 표시된다 — 신호는
# 완전하지 않다. 표본이 35쌍뿐이라 릴리스 후 out-of-sample 재검증이 필요하다.
# 산정 근거·재조정 절차: docs/work/2026-07/2026-07-28-ocr-candidate-selection/threshold.md
# (docs/work는 git 비추적 — fresh clone에는 없다. 없으면 Issue #22를 본다.)
ITEM_CONF_THRESHOLD = 0.75


def _is_item_uncertain(top5: list[dict]) -> bool:
    """품목 top1이 임계 미만이거나 후보가 아예 없으면 미확신으로 본다.

    top5[0]["sim"]은 유일 생산자 infer_job()의 topk() 조립(ip.topk가 항상 float로
    캐스팅)이 보장하는 계약이다 — 존재하지 않으면 KeyError로 fail-fast, 방어하지 않는다.
    """
    if not top5:
        return True
    # NaN 입력에도 미확신(True)으로 닫히도록 `<` 대신 `not (>=)`를 쓴다 — NaN 비교는
    # 항상 False이므로 `<`였다면 NaN이 "확신"으로 fail-open했다(warp_gate.py와 동일 관용구).
    return not (float(top5[0]["sim"]) >= ITEM_CONF_THRESHOLD)


def assemble_result_json(
    job_id: int, rows: list[dict], warp_ok: bool, retrieval_version: str | None = None
) -> dict:
    """추론 행들을 천원곱 적용한 구조화 result_json으로 조립한다.

    Args:
        job_id: 잡 id(crop_ref 접두).
        rows: 추론 행 목록.
        warp_ok: 워프·격자 정합 게이트 통과 여부.
        retrieval_version: 추론에 쓰인 retrieval artifact 지문. None이거나 공백만이면
            키를 넣지 않는다 — 자리표시자를 쓰면 서로 다른 retrieval 상태가 한 코호트로
            합쳐진다(Issue #49). 빈 문자열도 그 자체로 자리표시자가 되므로 동일하게 막는다.
    """
    out_rows = []
    supply_sum = 0
    for r in rows:
        supply = r.get("supply")
        if supply is not None:
            supply = supply * THOUSAND_MULT
        top5 = r.get("item_top5") or []
        out_rows.append(
            {
                "row_index": r["row_index"],
                "crop_ref": f"job-{job_id}/row-{r['row_index']}",
                "item_top5": top5,
                "supply": supply,
                "amount_raw": r.get("amount_raw", ""),
                "item_uncertain": _is_item_uncertain(top5),
            }
        )
        if supply is not None:
            supply_sum += supply
    out = {
        "rows": out_rows,
        "supply_sum": supply_sum,
        "warp_ok": warp_ok,
        "item_conf_threshold": ITEM_CONF_THRESHOLD,
    }
    if retrieval_version is not None and retrieval_version.strip():
        out["retrieval_version"] = retrieval_version
    return out


def _warp_gate_passes(w, job_id: int) -> bool:
    """워프 결과가 전표 격자와 정합하는지 판정한다. False면 강등 로그를 남긴다.

    쿼드를 '찾았다'와 '맞게 찾았다'는 다르다 — 격자 정합을 검증해 오검출 워프를 강등한다.
    실패 시 쿼드 미검출과 동일 계약(rows=[])으로 빠져, 배경을 읽은 쓰레기 초안과
    학습쌍 크롭이 만들어지는 것을 원천 차단한다(Issue #18).
    """
    gate_metrics = compute_metrics(w)
    if evaluate_warp(gate_metrics):
        return True
    # 계약(result_json)은 불변이라 지표를 실을 곳이 없다 — launchd stdout에만 남긴다
    # (deploy/launchd/ai.sjmj.ml-worker.plist.template의 StandardOutPath). 판정 임계값도
    # 함께 실어야 캘리브레이션이 바뀐 뒤에도 과거 로그 라인을 그 시점 기준으로 해석할 수 있다.
    # flush=True 필수: 워커는 while True 폴링 상시 프로세스라 파일 리다이렉트 시
    # 블록 버퍼링에 걸리면 로그가 한참 뒤에야 보인다. 워커의 첫 로그 라인이다.
    print(
        f"[warp-gate] job={job_id} demoted metrics={gate_metrics} "
        f"thresholds=(min_hlines={MIN_HLINES}, max_pitch_dev={MAX_PITCH_DEV}, "
        f"min_blue_ratio={MIN_BLUE_RATIO}, max_blue_asymmetry={MAX_BLUE_ASYMMETRY})",
        flush=True,
    )
    return False


def infer_job(image_path: str, models, crop_out_dir, job_id: int) -> dict:
    """사진 1장 → result_json. crop PNG를 crop_out_dir/row-{i}.png로 저장.

    models: (item_model, E, lab, qwen, device) 번들(worker가 1회 적재). infer_photo.
    extract_rows_for_job(process_one과 공유하는 단일 추론 경로)를 재사용해 HTML 조립을 제거하고
    rows 리스트를 만들어 assemble_result_json으로 직렬화한다. runtime은 Task 17(macmini,
    worker venv + 실모델)에서 검증한다 — 여기서는 실행하지 않는다.
    """
    import itertools
    import tempfile
    from pathlib import Path

    import cv2
    import numpy as np

    from handwriting import infer_photo as ip
    from handwriting.grid_v4 import warp

    item_model, E, lab, qwen, device = models
    crop_out_dir = Path(crop_out_dir)
    crop_out_dir.mkdir(parents=True, exist_ok=True)
    bgr = ip.load_bgr_path(image_path)
    quad = ip.form_quad_robust(bgr)
    if quad is None:
        print(f"[warp-gate] job={job_id} quad_missing", flush=True)  # 격자 부정합과 구분 가능하게
        return assemble_result_json(job_id, [], warp_ok=False)
    w = ip.rotate(warp(bgr, quad), ip.deskew_angle(warp(bgr, quad)))
    cv2.imwrite(str(crop_out_dir / "warped.png"), w)  # 큐레이션 단계 시각화용 전표 1장

    if not _warp_gate_passes(w, job_id):
        return assemble_result_json(job_id, [], warp_ok=False)

    # process_one과 동일한 행검출·crop·retrieval·금액 OCR(단일 경로).
    # extract_rows_for_job는 (news, crops, queries, amounts, prop, ys, P, bands)를 반환하며
    # 뒤 4개는 데모 HTML 컨텍스트라 여기선 *_로 버린다.
    tmp_dir = Path(tempfile.mkdtemp())
    counter = itertools.count()
    news, crops, queries, amounts, *_ = ip.extract_rows_for_job(
        w, item_model, qwen, tmp_dir, counter, device
    )
    rows = []
    for i, _row in enumerate(news):
        cv2.imwrite(str(crop_out_dir / f"row-{i}.png"), crops[i])
        sims = E @ queries[i] if len(queries) else np.zeros(0)
        top5 = [{"label": L, "sim": s} for L, s in ip.topk(sims, lab, ip.TOPK)] if len(sims) else []
        amt, raw = amounts[i]
        rows.append({"row_index": i, "item_top5": top5, "supply": amt, "amount_raw": raw})

    return assemble_result_json(job_id, rows, warp_ok=True)
