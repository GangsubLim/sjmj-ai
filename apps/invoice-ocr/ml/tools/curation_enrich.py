"""큐레이션 리포트의 분석 계층 — 원격 산출물 파싱·버킷 귀속·진실원 조인·집계.

tools/curation_report.py에서 **순수함수만** 떼어낸 모듈이다(동작 변경 0의 기계적 분리).
리포트 본체는 fetch 글루·CLI·렌더까지 담아 파일 상한(800줄)에 닿는데, 이 계층은 IO 0·
부수효과 0이라 합성 데이터 단위테스트로 전량 닫히므로 경계가 자연스럽다
(tools/curation_cohort.py를 뗀 것과 같은 관용구).

의존 방향은 단방향이다: curation_report(fetch·CLI·렌더) → curation_enrich(분석) →
curation_cohort(판정). 반대로 렌더 계층을 떼면 render_report가 summarize·job_flags·
oob_label_counts를 쓰므로 순환이 생긴다.

코어 규약 준수: stdlib 전용(paddle/numpy/pillow 불필요), 전부 순수함수(+ 파서가 푸는 조회
SQL 상수 — 컬럼 계약이 파서와 한 벌이라 여기 산다).
"""

import json
from collections import Counter

from tools.curation_cohort import (
    ITEM_EVALUABLE_COHORTS,
    REEVALUATED_COHORT,
    PairCohort,
    is_item_evaluable,
    pair_cohort,
)

# 조회 SQL은 그 결과를 푸는 파서 옆에 둔다 — 컬럼 목록과 parse_pairs_tsv의 키가 한 벌이다.
# 소비자는 curation_report.fetch_all과 bank_update.fetch_pairs 둘이며, 후자가 SQL 하나 때문에
# 리포트 모듈(fetch 글루·CLI·bank_id까지 끌어온다)에 의존하지 않게 한다(L6).
PAIR_COLS = (
    "id, crop_ref, job_id, row_index, draft_label, final_label, "
    "canonical_label, supply, status, reviewed_at"
)
PAIRS_SQL = f"SELECT {PAIR_COLS} FROM training_pairs ORDER BY job_id, row_index"
JOBS_SQL = (
    "SELECT id, image_path, JSON_UNQUOTE(result_json) FROM ocr_jobs "
    "WHERE id IN (SELECT DISTINCT job_id FROM training_pairs)"
)

# warp_suspect를 켤 금액 실패 최소 건수 — 1건은 단일 오독으로도 나므로 잡 단위 신호가 못 된다.
MIN_WARP_SUSPECT_BAD = 2


def _cell(value: str) -> str | None:
    return None if value == "NULL" else value


def parse_pairs_tsv(text: str) -> list[dict]:
    """mysql --batch TSV(training_pairs)를 타입 변환된 dict 리스트로 파싱한다."""
    lines = text.strip().split("\n")
    header = lines[0].split("\t")
    out = []
    for ln in lines[1:]:
        d = dict(zip(header, ln.split("\t"), strict=True))
        supply = _cell(d["supply"])
        out.append(
            {
                "id": int(d["id"]),
                "crop_ref": d["crop_ref"],
                "job_id": int(d["job_id"]),
                "row_index": int(d["row_index"]),
                "draft_label": _cell(d["draft_label"]),
                "final_label": _cell(d["final_label"]),
                "canonical_label": _cell(d["canonical_label"]),
                "supply": None if supply is None else int(supply),
                "status": d["status"],
                "reviewed_at": _cell(d["reviewed_at"]),
            }
        )
    return out


def parse_jobs_tsv(text: str) -> list[dict]:
    """mysql --batch --raw TSV(ocr_jobs + result_json)를 파싱한다."""
    out = []
    for ln in text.strip().split("\n")[1:]:
        job_id, image_path, raw = ln.split("\t", 2)
        # image_path는 업로드 파일명 suffix를 물려받아 탭이 섞일 수 있다(--raw는 비이스케이프).
        # 컬럼 경계가 밀리면 조용한 오파싱 대신 즉시 실패시킨다.
        if not raw.lstrip().startswith("{"):
            raise ValueError(f"jobs TSV 컬럼 경계 오류(job_id={job_id}) — image_path 제어문자 의심")
        out.append({"job_id": int(job_id), "image_path": image_path, "result": json.loads(raw)})
    return out


def label_bucket(answer: str, top5_labels: list[str], bank: set[str]) -> str:
    """품목 결과를 실패 원인 버킷으로 귀속한다.

    정답원(answer)은 canonical_label이다 — 뱅크가 저장하는 라벨이 canonical이므로 retrieval
    예측도 canonical 공간이고, bank_update score도 canonical로 채점한다(spec §3-C).

    answer는 빈 값이 없는 str이어야 한다 — 정답 부재 판정은 호출자(`_item_bucket`)의 몫이다
    (`None`을 받으면 `out_of_bank`로 조용히 오분류되는 죽은 경로가 있었다).

    ok(=top1 적중) / out_of_bank(뱅크에 정답 없음 — 구조적 실패) /
    top5_only(후보엔 있었음) / in_bank_miss(뱅크에 있는데 후보 밖) / no_candidates.
    """
    if not top5_labels:
        return "no_candidates"
    if answer == top5_labels[0]:
        return "ok"
    if answer not in bank:
        return "out_of_bank"
    if answer in top5_labels:
        return "top5_only"
    return "in_bank_miss"


def _item_bucket(
    *,
    cohort: PairCohort,
    answer: str,
    row_missing: bool,
    top5_labels: list[str],
    bank: set[str],
) -> str:
    """품목 버킷 — 판정 불가 코호트는 버킷을 계산하지 않고 unevaluable로 귀속한다.

    시점 판정 불가(스탬프 없음·뱅크 불일치)와 정답 부재(no_label)를 모두 unevaluable로 보내
    성능 수치에서 격리한다. row_missing이 데이터 정합 문제를 격리한 것과 같은 관용구다.

    인자를 키워드 전용으로 강제한다 — cohort·answer가 인접 str이라 위치로 뒤바꿔 넘기면
    예외 없이 조용히 오분류된다(sample_cohort·pair_cohort와 같은 이유, M2).

    검사 순서가 계약이다.
      - reevaluated가 맨 앞: 재평가는 preds를 직접 주므로 result_json 조인 실패와 무관하게
        판정이 성립한다.
      - row_missing이 코호트 판정보다 앞: curation_cohort.DATA_INTEGRITY_FAILURE_BUCKETS
        계약상 row_missing은 unevaluable로 삼켜지면 안 되고 운영 실패로 남아야 한다
        (failures.jsonl·pull-images가 소비한다). 지금 데이터는 잡 전량이 스탬프 이전이라
        코호트를 먼저 보면 조인 결손이 통째로 사라진다. 실측 결측은 0건이므로 순수 방어다
        (training_pairs는 confirm 시 canonical_label = final_label로 생성된다 —
        migration_008).
    """
    if cohort == REEVALUATED_COHORT:
        return label_bucket(answer, top5_labels, bank)
    if row_missing:
        return "row_missing"
    if cohort not in ITEM_EVALUABLE_COHORTS:
        return "unevaluable"
    return label_bucket(answer, top5_labels, bank)


def amount_bucket(draft: int | None, final: int) -> str:
    """금액 결과를 실패 원인 버킷으로 귀속한다.

    degenerate(초안 무산출 draft=None — '!!!' 등 퇴화 출력)· zero_drift(0으로 읽음 —
    warp/칸위치 의심)· sign_mismatch(부호만 상이)· misread(다른 숫자)· ok.
    """
    if draft is None:
        return "degenerate"
    if draft == final:
        return "ok"
    if draft == 0 and final != 0:
        return "zero_drift"
    if draft == -final:
        return "sign_mismatch"
    return "misread"


def _truth_source(
    *, rec: dict | None, row: dict, answer: str
) -> tuple[list[str], float | None, bool | None]:
    """진실원에서 (top5_labels, top1_sim, reeval_has_peer)를 뽑는다.

    재평가 레코드가 있으면 그것이, 없으면 result_json이 진실원이다. **재평가에서 가져오는
    것은 preds(top5)와 top1_sim 둘뿐이다** — top5만 갈아끼우면 핵심 지표 표 안에서 시점이
    다시 섞인다(적중/미스 유사도 분포는 top1_sim이 낸다). 이 둘은 쿼리 임베딩과 뱅크만으로
    정해지므로 정답 라벨과 무관하게 유효하다. 반대로 in_bank·top1·top5는 채점 당시
    canonical_label 기준이라(bank_update.score_one) 쓰지 않는다 — 호출자가 현재 라벨로
    다시 계산한다.

    인자를 키워드 전용으로 강제한다 — rec·row가 인접 dict라 위치로 뒤바꿔 넘기면 예외 없이
    H1과 동일한 오분류가 조용히 난다(M2).

    `preds`·`label`은 `reeval_gate._validate_reeval_records`가 이미 존재·타입(list[str]·str)을
    강제했으므로 `[...]`로 직접 접근한다(H1 — `.get()` fail-open을 이 자리에서 걷어낸다).
    `top1_sim`은 게이트가 불변식(preds 비어있음 ⟺ top1_sim is None)을 양방향으로 강제하되 키
    자체의 부재는 허용하므로 `.get()`을 유지한다 — preds가 비어있는 쪽에서 None을 가정해도
    안전하다. `has_peer`도 검증 대상이 아니고 부재·None 모두 "판정
    보류"라는 유효한 의미를 이미 가지므로 `.get()`을 유지한다.

    Args:
        rec: 그 쌍의 재평가 레코드. None이면 재평가 없음.
        row: result_json의 그 행(조인 실패 시 빈 dict).
        answer: 현재 정답 라벨(strip된 canonical_label).

    Returns:
        (top5_labels, top1_sim, reeval_has_peer). has_peer도 채점 당시 라벨 기준이므로
        라벨이 그대로일 때만 싣고, 다르면 None = 판정 보류다 — 도달 불가를 증명할 수 없으므로
        미스 목록에 남긴다(fail-open은 사람 눈에 더 보여주는 방향이라 안전하다).
    """
    if rec is None:
        top5 = row.get("item_top5") or []
        return [t["label"] for t in top5], (top5[0]["sim"] if top5 else None), None
    has_peer = rec.get("has_peer") if rec["label"] == answer else None
    return list(rec["preds"]), rec.get("top1_sim"), has_peer


def enrich_pairs(
    pairs: list[dict],
    jobs: list[dict],
    bank: set[str],
    *,
    reeval: dict[str, dict] | None = None,
    current_retrieval_version: str | None = None,
) -> list[dict]:
    """training_pairs에 진실원(재평가 또는 result_json)과 코호트를 조인해 버킷을 매긴다.

    enriched 행의 키 계약(정답 라벨이 둘로 보일 수 있어 명시한다): `answer`가 판정에 쓰인
    정본(strip된 canonical_label)이다 — `label_bucket`·`in_bank`·`oob_label_counts`가
    전부 이 키를 읽는다. `**p`로 실리는 `canonical_label`은 DB 원본 값(미가공 — 공백·None
    가능)이며 표시·감사용으로만 남고 어떤 판정에도 쓰이지 않는다. 새 소비자는 `answer`만
    읽어야 한다.

    `in_bank`는 재평가 레코드에 있어도 쓰지 않고 **현재 canonical_label × 현재 뱅크**로 다시
    계산한다 — 채점 후에도 PATCH로 라벨이 바뀔 수 있고, 커버리지·뱅크 후보의 진실원은 현재
    상태여야 한다(spec §3-C). 금액 축은 항상 result_json에서 온다(재평가는 품목만 다룬다).

    Args:
        pairs: training_pairs 전량.
        jobs: ocr_jobs + result_json.
        bank: 현재 뱅크의 라벨 집합.
        reeval: 유효성 게이트를 통과한 {crop_ref: 재평가 레코드}. None이면 재평가 없음 —
            게이트가 기각한 재평가는 통째로 버려지고 각 쌍이 스탬프 기준 분기로 간다.
        current_retrieval_version: 현재 서버 retrieval 지문. 코호트 판정의 기준값.
    """
    rows_by_ref = {r.get("crop_ref"): r for j in jobs for r in (j["result"].get("rows") or [])}
    version_by_job = {j["job_id"]: j["result"].get("retrieval_version") for j in jobs}
    reeval = reeval or {}
    out = []
    for p in pairs:
        row = rows_by_ref.get(p["crop_ref"])
        # 조인 실패(재처리 등으로 result_json에 crop_ref 부재)는 모델 실패(no_candidates)와
        # 구분해 row_missing으로 귀속한다 — 데이터 정합 문제가 성능 수치를 오염시키지 않도록.
        row_missing = row is None
        row = row or {}
        # 정답원은 canonical_label이다 — final_label은 confirm 시 사용자 입력명(불변 스냅샷)이고
        # canonical_label은 학습용 정규화 라벨이다(migration_008). 뱅크가 저장하는 쪽이 후자다.
        answer = (p.get("canonical_label") or "").strip()
        rec = reeval.get(p["crop_ref"])
        cohort = pair_cohort(
            answer=answer,
            job_retrieval_version=version_by_job.get(p["job_id"]),
            current_retrieval_version=current_retrieval_version,
            has_reeval=rec is not None,
        )
        top5_labels, top1_sim, reeval_has_peer = _truth_source(rec=rec, row=row, answer=answer)
        draft_supply = row.get("supply")
        out.append(
            {
                **p,
                "cohort": cohort,
                "top5_labels": top5_labels,
                "top1_sim": top1_sim,
                "answer": answer,
                "in_bank": bool(answer) and answer in bank,
                "label_bucket": _item_bucket(
                    cohort=cohort,
                    answer=answer,
                    row_missing=row_missing,
                    top5_labels=top5_labels,
                    bank=bank,
                ),
                "reeval_has_peer": reeval_has_peer,
                "draft_supply": draft_supply,
                "amount_raw": row.get("amount_raw", ""),
                "amount_bucket": (
                    None
                    if row_missing or p["supply"] is None
                    else amount_bucket(draft_supply, p["supply"])
                ),
            }
        )
    return out


def job_flags(enriched: list[dict]) -> dict[int, list[str]]:
    """잡 단위 이상 플래그를 계산한다.

    warp_suspect = 금액 무산출·0드리프트가 그 잡 금액 기재 행의 **절반 이상**이고 절대 건수가
    MIN_WARP_SUSPECT_BAD 이상. "과반"이 아니라 정확히 절반도 포함한다(`bad * 2 >= len(amts)`) —
    임계·비교 연산자는 운영 검수 대상 목록을 정하는 값이므로 이 이슈에서 바꾸지 않는다.
    """
    by_job: dict[int, list[dict]] = {}
    for r in enriched:
        if r["status"] == "included":
            by_job.setdefault(r["job_id"], []).append(r)
    flags = {}
    for jid, recs in by_job.items():
        amts = [r["amount_bucket"] for r in recs if r["amount_bucket"] is not None]
        bad = sum(b in ("zero_drift", "degenerate") for b in amts)
        suspect = bad >= MIN_WARP_SUSPECT_BAD and bad * 2 >= len(amts)
        flags[jid] = ["warp_suspect"] if suspect else []
    return flags


def oob_label_counts(enriched: list[dict]) -> list[tuple[str, int]]:
    """정답 라벨이 현재 뱅크에 없는 included 쌍의 빈도 내림차순 — 뱅크 추가 후보.

    성능 버킷(label_bucket)을 보지 않는다. 성능 측정은 *추론 시점 뱅크* 기준이어야 하지만
    뱅크 추가 후보는 *현재 뱅크* 기준이어야 한다 — 이미 든 라벨을 또 추가할 수는 없다
    (spec §1.2). 판정 불가 표본도 후보 집계에는 포함된다: "정답 라벨이 현재 뱅크에 없다"는
    추론 시점과 무관한 사실이다. 버킷을 보면 판정 불가 표본이 unevaluable로 귀속되는 순간
    후보 목록이 통째로 비어 개선 워크플로가 끊긴다.
    """
    counts = Counter(
        r["answer"]
        for r in enriched
        if r["status"] == "included" and r["answer"] and not r["in_bank"]
    )
    return counts.most_common()


def summarize(enriched: list[dict]) -> dict:
    """included 쌍의 핵심 지표를 집계한다 — 품목 지표는 평가 가능 쌍만 분모로 쓴다.

    금액 지표는 품목 평가 가능성과 무관하다(두 축이 독립이고 금액 버킷은 뱅크와 무관하다).
    label_buckets는 included 전체 분포를 유지한다 — unevaluable이 몇 건인지 보여야 한다.
    """
    inc = [r for r in enriched if r["status"] == "included"]
    ev = [r for r in inc if is_item_evaluable(r)]
    in_bank = [r for r in ev if r["in_bank"]]
    amounts = [r for r in inc if r["amount_bucket"] is not None]
    # `is not None`으로 명시한다 — truthiness 필터는 유사도 0.0(bank_update.score_one의
    # ranked[0][1]이 낼 수 있는 유효 관측치)을 관측 부재와 함께 버려 분포를 낙관 쪽으로 민다.
    hit_sims = [
        r["top1_sim"] for r in ev if r["label_bucket"] == "ok" and r["top1_sim"] is not None
    ]
    miss_sims = [
        r["top1_sim"] for r in ev if r["label_bucket"] != "ok" and r["top1_sim"] is not None
    ]
    return {
        "n_included": len(inc),
        "n_item_evaluable": len(ev),
        "n_excluded": sum(r["status"] == "excluded" for r in enriched),
        "n_jobs": len({r["job_id"] for r in enriched}),
        "top1_hits": sum(r["label_bucket"] == "ok" for r in ev),
        "top5_hits": sum(r["label_bucket"] in ("ok", "top5_only") for r in ev),
        "in_bank_n": len(in_bank),
        "in_bank_top1": sum(r["label_bucket"] == "ok" for r in in_bank),
        "in_bank_top5": sum(r["label_bucket"] in ("ok", "top5_only") for r in in_bank),
        "amount_n": len(amounts),
        "amount_ok": sum(r["amount_bucket"] == "ok" for r in amounts),
        "label_buckets": Counter(r["label_bucket"] for r in inc),
        "amount_buckets": Counter(r["amount_bucket"] for r in amounts),
        "hit_sim_mean": sum(hit_sims) / len(hit_sims) if hit_sims else None,
        "hit_sim_min": min(hit_sims) if hit_sims else None,
        "miss_sim_mean": sum(miss_sims) / len(miss_sims) if miss_sims else None,
        "miss_sim_max": max(miss_sims) if miss_sims else None,
        "cohorts": Counter(r["cohort"] for r in inc),
    }
