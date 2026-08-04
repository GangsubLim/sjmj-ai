"""큐레이션 리포트의 분석 계층 — 원격 산출물 파싱·버킷 귀속·진실원 조인·집계.

tools/curation_report.py에서 **순수함수만** 떼어낸 모듈이다(동작 변경 0의 기계적 분리).
리포트 본체는 fetch 글루·CLI에 렌더까지 담아 파일 상한(800줄)에 닿았는데, 이 계층은 IO 0·
부수효과 0이라 합성 데이터 단위테스트로 전량 닫히므로 경계가 자연스럽다
(tools/curation_cohort.py를 뗀 것과 같은 관용구. 이후 렌더 계층도 tools/curation_render.py로
같은 이유로 갈렸다 — Issue #38).

의존 방향은 단방향이다: curation_report(fetch·CLI) → curation_render(렌더) →
curation_enrich(분석, 이 모듈) → curation_cohort(판정).

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
    "canonical_label, supply, status, exclusion_reason, reviewed_at"
)
PAIRS_SQL = f"SELECT {PAIR_COLS} FROM training_pairs ORDER BY job_id, row_index"
JOBS_SQL = (
    "SELECT id, image_path, JSON_UNQUOTE(result_json) FROM ocr_jobs "
    "WHERE id IN (SELECT DISTINCT job_id FROM training_pairs)"
)

# 확정 잡 모집단 + 잡 단위 행 수지 원자료(네 번째 데이터 소스).
# WHERE는 백엔드 `apps/invoice-ocr/backend/app/repositories/ocr_repository.py`의
# `_UNCONFIRMED_WHERE`(미확정 판정)의 **부정**이다 — 확정 증거 세 predicate를 그대로
# 미러링한다. ml/은 backend/를 import할 수 없어 상수를 공유할 수 없으므로 양쪽 주석이
# 서로를 가리킨다. 두 곳이 갈라지면 이 리포트의 확정 잡 수와 처리 관측(/curation/pending)의
# 미확정 수의 합이 전체와 안 맞게 된다.
# 모집단의 출발점을 ocr_corrections가 아니라 ocr_jobs로 두는 이유: 교정 행이 없는 확정 잡
# (link_invoice 백필 이력)이 통째로 빠져 "미상"이 도달 불가가 된다. LEFT JOIN이 그 자리다.
# job_id는 UNIQUE가 아니다(migration_007:40-51). 명세서 삭제로 ocr_jobs.invoice_id가
# ON DELETE SET NULL 되면 재확정이 가능하고, 쌍 0개 잡은 crop_ref UNIQUE 충돌도 없어
# 교정 행이 둘 이상 남을 수 있다. 정본은 MAX(id) 1건으로 SQL에서 확정하고, 중복 사실은
# n_corrections로 리포트에 노출한다(제약 추가는 DB 변경이라 이 슬라이스 범위 밖 — 후속 이슈).
# has_correction은 n_corrections > 0으로 파서가 파생한다(컬럼 수는 CORRECTION_COLS 길이 그대로).
# image_path를 맨 뒤에 두는 이유: raw=False에서는 mysql이 탭·개행을 이스케이프해 컬럼 경계가
# 보장되지만, 유일한 가변 자유 문자열이라 맨 뒤 + split(maxsplit=len(CORRECTION_COLS)-1)로
# 방어 깊이를 한 겹 둔다.
# (parse_jobs_tsv의 탭 방어는 그쪽이 raw=True라 이스케이프가 없기 때문이다 — 근거가 다르다.)
# 별칭(AS)은 필수다 — mysql --batch TSV 헤더가 표현식 원문이 되면 읽기가 SQL에 묶이고,
# SELECT 순서는 CORRECTION_COLS와 정확히 같아야 한다(파서가 헤더로 대조해 fail-fast한다, M3).
CORRECTIONS_SQL = (
    "SELECT j.id AS job_id, "
    "(SELECT COUNT(*) FROM ocr_corrections c3 WHERE c3.job_id = j.id) AS n_corrections, "
    "JSON_EXTRACT(c.correction_json,'$.rows_added') AS rows_added, "
    "JSON_EXTRACT(c.correction_json,'$.rows_dropped') AS rows_dropped, "
    "JSON_LENGTH(c.correction_json,'$.lines') AS n_lines, "
    "j.image_path AS image_path "
    "FROM ocr_jobs j LEFT JOIN ocr_corrections c "
    "ON c.id = (SELECT MAX(c2.id) FROM ocr_corrections c2 WHERE c2.job_id = j.id) "
    "WHERE j.invoice_id IS NOT NULL "
    "OR EXISTS (SELECT 1 FROM ocr_corrections c2 WHERE c2.job_id = j.id) "
    "OR EXISTS (SELECT 1 FROM training_pairs tp WHERE tp.job_id = j.id) "
    "ORDER BY j.id"
)

# corrections TSV의 컬럼 이름·위치 SSoT. SELECT 별칭 순서와 파서의 헤더 대조·위치 인덱싱이
# 이 튜플 하나로 묶인다 — parse_pairs_tsv가 헤더 키 매핑 + strict=True로 순서 위험을 막는
# 것과 같은 이유다(M3). SELECT 순서만 바뀌어도(예: rows_added/rows_dropped 자리 교환) 위치
# 인덱싱은 예외 없이 조용히 뒤바뀐 수지를 낸다 — parse_corrections_tsv가 헤더를 이 튜플과
# 대조해 fail-fast로 막는다.
CORRECTION_COLS = (
    "job_id",
    "n_corrections",
    "rows_added",
    "rows_dropped",
    "n_lines",
    "image_path",
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
                "exclusion_reason": _cell(d["exclusion_reason"]),
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


def parse_corrections_tsv(text: str) -> list[dict]:
    """mysql --batch TSV(ocr_jobs ⨝ ocr_corrections)를 잡 단위 행 수지 dict로 파싱한다.

    수지 세 값 중 하나라도 NULL이면 셋 모두 None = 미상이다(0으로 접지 않는다) — 파생값
    (draft_rows·confirmed_rows)도 함께 None이 되어 이후 합계·플래그에서 통째로 빠진다.
    미상 두 종은 has_correction으로 가른다: 교정 이력 자체가 없다(구 데이터, 정상) vs
    교정 행은 있는데 correction_json이 NULL이다(데이터 결손, 버그 의심).

    n_corrections > 1은 재확정 흔적이다 — 정본은 SQL이 MAX(id)로 고르고, 이 값은 그 사실을
    리포트까지 나르는 통로다(조용한 최신 채택 금지).

    헤더를 CORRECTION_COLS와 대조해 fail-fast한다 — parse_pairs_tsv가 헤더 키 매핑으로 컬럼
    순서 위험을 막는 것과 같은 방어다(M3). SELECT 순서만 바뀌어도 위치 인덱싱은 예외 없이
    조용히 뒤바뀐 수지를 내므로, 여기서는 헤더 자체를 SSoT와 통째로 비교한다.

    Raises:
        ValueError: 헤더가 CORRECTION_COLS와 다르거나 컬럼 수가 어긋날 때.
    """
    stripped = text.strip()
    if not stripped:
        return []
    lines = stripped.split("\n")
    header = tuple(lines[0].split("\t"))
    if header != CORRECTION_COLS:
        raise ValueError(f"corrections TSV 헤더 불일치: {header!r} != {CORRECTION_COLS!r}")
    out: list[dict] = []
    for ln in lines[1:]:
        parts = ln.split("\t", len(CORRECTION_COLS) - 1)
        if len(parts) != len(CORRECTION_COLS):
            raise ValueError(f"corrections TSV 컬럼 수 오류({len(parts)}개): {ln[:80]!r}")
        row = dict(zip(CORRECTION_COLS, parts, strict=True))
        job_id = int(row["job_id"])
        n_corrections = int(row["n_corrections"])
        raw = [_cell(row[k]) for k in ("rows_added", "rows_dropped", "n_lines")]
        if any(v is None for v in raw):
            balance = dict.fromkeys(
                ("rows_added", "rows_dropped", "n_lines", "draft_rows", "confirmed_rows")
            )
        else:
            added, dropped, n_lines = (int(v) for v in raw)
            balance = {
                "rows_added": added,
                "rows_dropped": dropped,
                "n_lines": n_lines,
                "draft_rows": n_lines + dropped,
                "confirmed_rows": n_lines + added,
            }
        out.append(
            {
                "job_id": job_id,
                "n_corrections": n_corrections,
                "has_correction": n_corrections > 0,
                **balance,
                "image_path": _cell(row["image_path"]),  # E7과 한 벌
            }
        )
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


# --- 배제 소유 축 술어 (ADR 0006) ---
# 판정 조건은 소유 축(사유 유무)이지 특정 사유값이 아니다 — 사유가 늘어도 그대로 성립한다.
# 소비자는 둘이며 같은 술어를 부른다: `summarize`(리포트 머리말 수치)와
# `curation_render._render_excluded`(본문 나열). 두 곳에 조건을 손으로 적으면 한쪽만 고쳤을 때
# 머리말의 수와 나열된 행이 예외 없이 어긋나, ADR 0006이 기대는 오탐률 관측치가 조용히 거짓이 된다.


def is_machine_excluded(row: dict) -> bool:
    """기계가 자동 배제한 쌍 — 배제됐고 사유가 기록돼 있다."""
    return row["status"] == "excluded" and row["exclusion_reason"] is not None


def is_human_excluded(row: dict) -> bool:
    """사람이 배제한 쌍 — 배제됐는데 사유가 없다(크롭 불량 신호)."""
    return row["status"] == "excluded" and row["exclusion_reason"] is None


def is_reverted_machine_exclusion(row: dict) -> bool:
    """기계 자동 배제를 사람이 되돌린 쌍 — 학습에 포함됐는데 사유가 남아 있다(오탐 관측치)."""
    return row["status"] == "included" and row["exclusion_reason"] is not None


def summarize_row_balance(corrections: list[dict]) -> dict:
    """잡 단위 행 수지의 전체 합계와 모집단 경계를 낸다 — 미상 잡은 합계에서 뺀다.

    `summarize()`와 한 함수로 섞지 않는다: 두 집계의 모집단이 다르다(쌍 vs 잡). 섞으면
    분모가 뒤엉키고 기존 지표의 정의가 조용히 바뀐다(spec §4-2·§11).

    합계에서 빠진 미상 잡은 사라지지 않고 n_unknown_jobs로 남아 리포트가 그 사실을 인쇄한다.

    미상 판정은 `n_lines is None` 단일 키만 본다 — `parse_corrections_tsv`의 all-or-nothing
    접기(다섯 값 중 하나라도 NULL이면 다섯 다 None)에 기대는 것이다. 파서가 부분 None을
    허용하도록 바뀌면 이 판정도 함께 고쳐야 한다(그렇지 않으면 draft_rows 등 살아있는 값이
    조용히 합계에서 사라진다).
    """
    known = [c for c in corrections if c["n_lines"] is not None]
    unknown = [c for c in corrections if c["n_lines"] is None]
    return {
        "n_confirmed_jobs": len(corrections),
        "n_unknown_jobs": len(unknown),
        "n_no_correction_jobs": sum(not c["has_correction"] for c in unknown),
        "n_multi_correction_jobs": sum(c["n_corrections"] > 1 for c in corrections),
        "n_lines": sum(c["n_lines"] for c in known),
        "draft_rows": sum(c["draft_rows"] for c in known),
        "rows_added": sum(c["rows_added"] for c in known),
        "rows_dropped": sum(c["rows_dropped"] for c in known),
        "confirmed_rows": sum(c["confirmed_rows"] for c in known),
    }


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
        # 사람 배제와 기계 자동 배제를 섞어 세면 배제율이 파이프라인 개선 신호로서의
        # 의미를 잃는다(ADR 0006). 사유가 비어 있는 배제가 사람 판정이다.
        "n_excluded_machine": sum(is_machine_excluded(r) for r in enriched),
        "n_excluded_human": sum(is_human_excluded(r) for r in enriched),
        # 기계가 배제했으나 사람이 되돌린 쌍 — 이 개수가 곧 빈 크롭 가드의 오탐률 관측치다
        # (spec §6 세 번째 칸, ADR 0006 Consequences). 런북이 이 수치를 안내한다.
        # 집계 조건(exclusion_reason is not None)은 소유 축이라 사유가 늘어도 유효하다.
        # 다만 이 수를 "특정 가드"(예: 빈 크롭 가드)의 오탐으로 읽는 것은 사유 값 집합이
        # blank_crop 단일인 동안만 참이다 — 사유가 여러 개로 늘면 여러 가드의 되돌림이
        # 이 수치 하나에 섞인다(가드별 분해는 reverted_reason_counts 참조).
        "n_reverted_machine": sum(is_reverted_machine_exclusion(r) for r in enriched),
        "reverted_reason_counts": Counter(
            r["exclusion_reason"] for r in enriched if is_reverted_machine_exclusion(r)
        ),
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
