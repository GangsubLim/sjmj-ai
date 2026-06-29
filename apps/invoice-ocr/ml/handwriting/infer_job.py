"""검증된 process_one을 감싸 HTML 대신 구조화 result_json을 반환한다.

assemble_result_json은 순수함수(TDD 대상). infer_job은 warp/embed/ocr 글루로
라이브 e2e가 검증한다(슬라이스는 실모델 추론을 단위테스트하지 않음).

⚠️ 모듈 레벨에 무거운 의존(cv2/torch/handwriting.infer_photo)을 두지 않는다.
   infer_job() 본문에서 지연 import한다 — 그래야 paddle-free venv에서도
   `from handwriting.infer_job import assemble_result_json`가 성공한다.
"""


# 수기 거래명세서는 천 단위를 생략해 적는다(spec: 단가·금액 100% 천원 배수) → 액면값에 ×1000.
THOUSAND_MULT = 1000


def assemble_result_json(job_id: int, rows: list[dict], warp_ok: bool) -> dict:
    out_rows = []
    supply_sum = 0
    for r in rows:
        supply = r.get("supply")
        if supply is not None:
            supply = supply * THOUSAND_MULT
        out_rows.append(
            {
                "row_index": r["row_index"],
                "crop_ref": f"job-{job_id}/row-{r['row_index']}",
                "item_top5": r.get("item_top5") or [],
                "supply": supply,
                "amount_raw": r.get("amount_raw", ""),
            }
        )
        if supply is not None:
            supply_sum += supply
    return {"rows": out_rows, "supply_sum": supply_sum, "warp_ok": warp_ok}


def infer_job(image_path: str, models, crop_out_dir, job_id: int) -> dict:
    """사진 1장 → result_json. crop PNG를 crop_out_dir/row-{i}.png로 저장.

    models: (item_model, E, lab, qwen, device) 번들(worker가 1회 적재).
    infer_photo.extract_rows_for_job(process_one과 공유하는 단일 추론 경로)를 재사용해
    HTML 조립을 제거하고 rows 리스트를 만들어 assemble_result_json으로 직렬화한다.

    runtime은 Task 17(macmini, worker venv + 실모델)에서 검증한다 — 여기서는 실행하지 않는다.
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
        return assemble_result_json(job_id, [], warp_ok=False)
    w = ip.rotate(warp(bgr, quad), ip.deskew_angle(warp(bgr, quad)))

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
