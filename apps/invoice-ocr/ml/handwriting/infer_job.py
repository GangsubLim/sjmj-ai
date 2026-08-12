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
    ENH_MAX_BLUE_ASYMMETRY,
    ENH_MAX_PITCH_DEV,
    ENH_MIN_BLUE_RATIO,
    ENH_MIN_HLINES,
    MAX_BLUE_ASYMMETRY,
    MAX_PITCH_DEV,
    MIN_BLUE_RATIO,
    MIN_HLINES,
    compute_metrics,
    evaluate_warp,
    evaluate_warp_enh,
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


def _thresholds_text() -> str:
    """로그에 실을 임계 두 벌(표준·enh)을 한 줄로 만든다.

    캘리브레이션이 바뀐 뒤에도 과거 로그 라인을 그 시점 기준으로 해석하려면 판정에 쓰인
    임계가 로그 안에 함께 있어야 한다.
    """
    return (
        f"thresholds=(min_hlines={MIN_HLINES}, max_pitch_dev={MAX_PITCH_DEV}, "
        f"min_blue_ratio={MIN_BLUE_RATIO}, max_blue_asymmetry={MAX_BLUE_ASYMMETRY}) "
        f"enh_thresholds=(min_hlines={ENH_MIN_HLINES}, max_pitch_dev={ENH_MAX_PITCH_DEV}, "
        f"min_blue_ratio={ENH_MIN_BLUE_RATIO}, max_blue_asymmetry={ENH_MAX_BLUE_ASYMMETRY})"
    )


def _warp_gate_passes(w, job_id: int) -> bool:
    """워프 결과가 전표 격자와 정합하는지 판정한다(표준 → enh 2단 폴백).

    쿼드를 '찾았다'와 '맞게 찾았다'는 다르다 — 격자 정합을 검증해 오검출 워프를 강등한다.
    실패 시 쿼드 미검출과 동일 계약(rows=[])으로 빠져, 배경을 읽은 쓰레기 초안과
    학습쌍 크롭이 만들어지는 것을 원천 차단한다(Issue #18).

    지표 4종이 전부 같은 파랑 마스크에서 파생돼, 워프 정합성과 무관한 교란변수 하나(청색
    채도)가 3지표를 동시에 무너뜨렸다(Issue #60 — 정상 전표 5회 연속 강등). 표준 판정이
    실패했을 때만 대비향상 마스크로 **네 지표 전부**를 1회 재측정해 재판정한다. 임계를
    낮추는 것이 아니라 다른 마스크 축을 추가하는 것이다.

    분기는 닫혀 있다 — 표준 통과 시 enh는 계산조차 하지 않고, enh 측정은 최대 1회다.
    result_json 계약은 불변이라 폴백 통과 여부를 실을 곳이 없다 — launchd stdout에만 남긴다
    (deploy/launchd/ai.sjmj.ml-worker.plist.template의 StandardOutPath). 어느 축에서 갈렸는지
    로그만으로 판별할 수 있도록 구제·강등 양쪽에 std·enh 지표를 두 벌 다 싣는다.
    flush=True 필수: 워커는 while True 폴링 상시 프로세스라 파일 리다이렉트 시 블록
    버퍼링에 걸리면 로그가 한참 뒤에야 보인다.

    ⚠️ 이 분기 순서(표준 우선 → 실패 시에만 enh)를 바꾸면 `tools.warp_gate_calib.classify_flip`도
    함께 고쳐야 한다 — 그 함수는 cv2 무의존 규약 때문에 이 함수를 직접 재사용하지 못하고
    같은 분기 구조를 별도로 복제해 갖고 있다. 순서만 여기서 바뀌면 그쪽은 옛 구조로 계속
    `pass→fail: []`을 출력해 거짓 증거를 낸다(#60 리뷰 M2).
    """
    std = compute_metrics(w)
    if evaluate_warp(std):
        return True
    enh = compute_metrics(w, enhanced=True)
    if evaluate_warp_enh(enh):
        print(
            f"[warp-gate] job={job_id} rescued-by-enh std={std} enh={enh} {_thresholds_text()}",
            flush=True,
        )
        return True
    print(
        f"[warp-gate] job={job_id} demoted std={std} enh={enh} {_thresholds_text()}",
        flush=True,
    )
    return False


def _gated_warp(bgr, aligner, job_id: int):
    """Quad 후보를 우선순위대로 워프·deskew해 게이트를 통과하는 첫 결과를 고른다.

    "쿼드를 찾았다"와 "맞게 찾았다"를 가르는 판정(_warp_gate_passes)을 공급자 **선택**의
    채점 기준으로도 그대로 쓴다 — DL quad가 강등되면 색 quad로 1회 재시도한다. 게이트의
    임계·분기·2단 폴백 구조는 건드리지 않는다(신호 교체는 #64 후속).

    색 후보가 항상 뒤에 남으므로 warp_ok는 구성상 현행 이상이다(회귀 불가). 통과한 후보의
    워프 결과를 그대로 돌려줘 하류가 재워프하지 않는다. 전부 강등되면 마지막 후보의 워프를
    passed=False와 함께 돌려준다 — warped.png(큐레이션 시각화)를 현행처럼 남기기 위해서다.
    DL만 후보를 냈고(색 경로가 아예 미검출) 그 DL 워프가 강등되는 경우도 이 규칙을 그대로
    탄다 — 강등된 DL 워프가 마지막(유일한) 후보로 warped.png에 남고, 호출부의 quad_missing
    마커(w=None)는 찍히지 않는다. warp_ok=False는 동일해 계약 회귀는 아니지만, "quad_missing
    = 후보 전무"라는 마커의 의미가 이 경로에서는 좁아진다는 뜻이다.

    aligner가 None이면 후보가 색 경로 하나뿐이라 현행 동작과 100% 동일하다(추가 로그 0).

    Args:
        bgr: EXIF 정위치 BGR 원본.
        aligner: DL 코너검출기(models.aligner) 또는 None.
        job_id: 로그 태그.

    Returns:
        (warped, passed). 후보가 하나도 없으면 (None, False) — 호출부가 quad_missing 처리.
    """
    from handwriting import infer_photo as ip
    from handwriting.corner_dl import log_fallback, quad_candidates
    from handwriting.grid_v4 import warp

    w = None
    for src, quad in quad_candidates(bgr, aligner, job_id=job_id):
        w = ip.rotate(warp(bgr, quad), ip.deskew_angle(warp(bgr, quad)))
        if _warp_gate_passes(w, job_id):
            return w, True
        if src == "dl":
            log_fallback(job_id, "gate-demoted")
    return w, False


def infer_job(image_path: str, models, crop_out_dir, job_id: int) -> dict:
    """사진 1장 → result_json. crop PNG를 crop_out_dir/row-{i}.png로 저장.

    models: worker.main.ModelBundle(worker가 1회 적재). 위치 언패킹이 아니라 속성으로 읽는다.
    quad는 corner_dl.quad_candidates를 통해 _gated_warp가 게이트 인지형으로 선택한다 — DL
    워프가 게이트에서 강등되면 색 quad로 1회 재시도. models.aligner가 None이면 현행 색
    경로와 동일하다.
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

    item_model, E, lab = models.item_model, models.emb, models.labs
    qwen, device = models.qwen, models.device
    stamp = models.retrieval_version
    crop_out_dir = Path(crop_out_dir)
    crop_out_dir.mkdir(parents=True, exist_ok=True)
    bgr = ip.load_bgr_path(image_path)
    w, gate_ok = _gated_warp(bgr, models.aligner, job_id)
    if w is None:
        print(f"[warp-gate] job={job_id} quad_missing", flush=True)  # 격자 부정합과 구분 가능하게
        return assemble_result_json(job_id, [], warp_ok=False, retrieval_version=stamp)
    cv2.imwrite(str(crop_out_dir / "warped.png"), w)  # 큐레이션 단계 시각화용 전표 1장

    if not gate_ok:
        return assemble_result_json(job_id, [], warp_ok=False, retrieval_version=stamp)

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

    return assemble_result_json(job_id, rows, warp_ok=True, retrieval_version=stamp)
