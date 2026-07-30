"""OCR 큐레이션 학습쌍(training_pairs) 정확도 분석 리포트 도구.

배포 서버(macmini)의 운영 DB·모델뱅크·크롭 이미지를 ssh로 동기화해 로컬 캐시에 두고,
품목 retrieval(top1/top5·뱅크 내외 분해)과 금액 OCR(0-드리프트·퇴화출력·오독)의 실패를
버킷으로 귀속한 마크다운 리포트를 만든다. LLM 에이전트가 리포트→실패 크롭 시각 검수→
개선(뱅크 추가·warp 재검토) 루프를 돌리기 위한 입구다. 사용법은 docs/runbooks 참조.

코어 규약 준수: stdlib 전용(paddle/torch 불필요), 분석 계층은 순수함수(테스트 대상),
ssh/DB 접근은 fetch 글루에 격리. 원격 접속값은 env로만 주입한다. 시점 정합 판정(코호트·
평가 가능성 술어·재평가 유효성 게이트)은 tools/curation_cohort.py에 분리돼 있다.

Usage:
    uv run python -m tools.curation_report fetch        # 서버에서 pairs/jobs/bank 동기화
    uv run python -m tools.curation_report report       # 캐시 분석 → report.md/failures.jsonl
    uv run python -m tools.curation_report pull-images  # 실패 잡 크롭(+원본) 로컬 동기화
"""

import argparse
import io
import json
import shlex
import tarfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from tools.curation_cohort import (
    ITEM_EVALUABLE_COHORTS,
    REEVALUATED_COHORT,
    PairCohort,
    is_amount_failure,
    is_item_evaluable,
    is_item_failure,
    pair_cohort,
    partition_misses,
)
from tools.remote import (
    ENV_BACKEND_ENV,
    ENV_SSH_HOST,
    ENV_WORKER_ENV,
    env_or,
    mysql_script,
    run_ssh,
    source_env,
)

ML_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE = ML_ROOT / "results" / "curation"

PAIR_COLS = (
    "id, crop_ref, job_id, row_index, draft_label, final_label, "
    "canonical_label, supply, status, reviewed_at"
)
PAIRS_SQL = f"SELECT {PAIR_COLS} FROM training_pairs ORDER BY job_id, row_index"
JOBS_SQL = (
    "SELECT id, image_path, JSON_UNQUOTE(result_json) FROM ocr_jobs "
    "WHERE id IN (SELECT DISTINCT job_id FROM training_pairs)"
)

# ---------------------------------------------------------------------------
# 순수 분석 계층 (단위테스트 대상 — IO 없음)
# ---------------------------------------------------------------------------


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
    `top1_sim`은 `preds`가 비어있지 않을 때만 수치임이 강제되고 비어있을 때는 부재를 허용하는
    (불변식: preds 비어있음 ⟺ top1_sim is None) 조건부 검증이라 `.get()`을 유지한다 — 비어있는
    쪽에서 None을 가정해도 안전하다. `has_peer`도 검증 대상이 아니고 부재·None 모두 "판정
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
    """잡 단위 이상 플래그를 계산한다. warp_suspect = 금액 무산출·0드리프트가 과반(≥2건)."""
    by_job: dict[int, list[dict]] = {}
    for r in enriched:
        if r["status"] == "included":
            by_job.setdefault(r["job_id"], []).append(r)
    flags = {}
    for jid, recs in by_job.items():
        amts = [r["amount_bucket"] for r in recs if r["amount_bucket"] is not None]
        bad = sum(b in ("zero_drift", "degenerate") for b in amts)
        flags[jid] = ["warp_suspect"] if bad >= 2 and bad * 2 >= len(amts) else []
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
    hit_sims = [r["top1_sim"] for r in ev if r["label_bucket"] == "ok" and r["top1_sim"]]
    miss_sims = [r["top1_sim"] for r in ev if r["label_bucket"] != "ok" and r["top1_sim"]]
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


def _pct(k: int, n: int) -> str:
    return f"{k}/{n} ({100 * k / n:.1f}%)" if n else "0/0 (—)"


# 표본 구성표 — 분모를 핵심 지표보다 먼저 읽게 한다(spec §3-C). 표에 없는 코호트가 생기면
# 그 쌍들은 조용히 사라지므로 test_cohort_table_covers_every_cohort_a_pair_can_get이 이 표를
# COHORTS(+ no_label)와 구조적으로 대조한다.
# ○/✗ 마크는 여기 적지 않는다 — _cohort_mark가 ITEM_EVALUABLE_COHORTS에서 도출한다(M3).
COHORT_TABLE = (
    ("reevaluated", "현재 뱅크로 재retrieval"),
    ("current_bank", "현재 retrieval 상태로 추론(스탬프 확인)"),
    ("stale_bank", "구 retrieval 상태 + 재평가 없음"),
    ("unknown", "스탬프 이전 잡 + 재평가 없음"),
    ("no_label", "canonical_label 없음(정답 부재)"),
)


def _cohort_mark(name: str) -> str:
    """지표 산출 대상 여부를 마크로 낸다 — 상수에서 도출해 계산과 표시가 갈라지지 않게 한다.

    표에 손으로 적으면 ITEM_EVALUABLE_COHORTS가 바뀌어도 표만 옛말을 인쇄한다(계산 A/표시 B).
    """
    return "○" if name in ITEM_EVALUABLE_COHORTS else "✗"


_REEVAL_ABSENT = (
    "재평가 없음: 서버에 재평가 산출물이 없다 — macmini에서 "
    "`bank_update score --scope all`을 돌리면 지표가 복원된다."
)
# 사유를 읽지 못했을 때의 폴백 — **없다고 단정하지 않는다**(H1). 부재 단정은 사용자를 엉뚱한
# 조치(재평가 재실행)로 보내는데, 실제 원인이 stale·다이제스트 불일치면 그 조치는 헛수고다.
_REEVAL_UNKNOWN_REASON = (
    "재평가 없음: 채택하지 않은 사유가 미상이다(리포트가 모르는 사유 코드) — "
    "서버 산출물의 유무는 이 줄로 판단할 수 없다. `meta.json`의 reeval 항목을 확인한다."
)
# 새 ReevalReason(curation_cohort.REEVAL_REJECT_REASONS)을 추가하고 문구를 빠뜨리면
# reeval_notice가 "사유 미상"을 낸다 — test_every_reeval_reject_reason_has_display_text가
# 두 집합의 일치를 강제한다.
_REEVAL_REJECT_TEXT = {
    "no_meta": (
        "서버에 score.jsonl은 있으나 score_meta.json이 없다(#53 이전 산출물) — "
        "재평가를 다시 실행해야 지표가 복원된다."
    ),
    "no_fingerprint": "retrieval 지문을 확정하지 못했다(코드 SHA 부재 등) — 재평가를 채택하지 않았다.",
    "stale": (
        "재평가의 after 지문이 현재와 달라 통째로 폐기했다 — 뱅크·모델·**배포 코드** 중 "
        "하나가 바뀌었다(릴리스 배포도 지문을 바꾼다). 각 쌍을 스탬프 기준으로 재분기했다"
        "(스탬프가 현재와 같은 잡은 current_bank로 남는다). 재평가를 다시 돌리면 복원된다."
    ),
    "digest_mismatch": "score_meta.json의 다이제스트가 회수분과 어긋난다(중단된 재실행 의심).",
    "bad_meta": "score_meta.json의 n_pairs가 올바른 정수가 아니다(산출물 손상 의심) — 재평가를 다시 실행해야 한다.",
    "no_records": (
        "재평가 대상 레코드가 0건이다(정상 — --scope 필터·크롭 부재로 표본이 0건일 수 있다). "
        "표본이 있는 재평가를 다시 돌리면 지표가 채워진다."
    ),
    "record_count": "레코드 수가 표본수 × 2 × 축수와 다르다(중단된 재실행 의심).",
    "no_invoice_axis": "재평가 산출물에 전표(invoice) 축이 없다(#53 이전 채점기 의심) — 재평가를 다시 실행해야 한다.",
    "record_shape": "재평가 레코드의 (side, axis) 조합이 axes와 다르다(산출물 손상 의심).",
    "pair_count": "전표 축 after 레코드 수가 표본 수와 다르다 — 일부 쌍이 사유 없이 빠졌다(중단된 재실행 의심).",
}


def reeval_notice(meta: dict) -> str:
    """재평가 채택 여부와 사유를 한 줄로 낸다.

    score.jsonl만 있고 meta가 없는 경우(`no_meta`)도 정상 경로로 설명해 사용자가 재평가를
    돌렸다고 착각하지 않게 한다(spec §3-C).

    **"산출물이 없다"는 단정은 정보 자체가 없을 때만 한다**(H1). `state` 키 하나의 기본값으로
    부재를 단정하면 사유(reason)가 손에 있는데도 원인을 오보하고, 이 알림이 막으려던 오인
    (사용자가 원인을 모른 채 엉뚱한 조치를 함)을 알림이 스스로 만든다.

    `no_meta`는 회수 상태(ReevalState)이자 게이트 사유(ReevalReason)로 같은 철자를 쓰므로
    분기가 따로 필요 없다 — state를 사유 폴백으로 그대로 조회한다(M2).
    """
    info = meta.get("reeval") or {}
    if info.get("adopted"):
        return (
            f"재평가: {info.get('generated_at', '?')} · retrieval 지문 "
            f"{info.get('after', '?')}(현재와 일치) · scope={info.get('scope', '?')} · "
            f"표본 {info.get('n_pairs', '?')}쌍"
        )
    state = info.get("state")
    if not info or state == "absent":
        return _REEVAL_ABSENT
    text = _REEVAL_REJECT_TEXT.get(info.get("reason") or state)
    return f"재평가 없음: {text}" if text else _REEVAL_UNKNOWN_REASON


def _render_cohort_table(s: dict, meta: dict) -> list[str]:
    """표본 구성표 + 재평가 알림을 렌더한다(핵심 지표 절보다 먼저 — 분모를 먼저 읽는다, §3-C)."""
    return [
        "## 표본 구성",
        "",
        "| 코호트 | 쌍 | 지표 산출 |",
        "| --- | --- | --- |",
        *[
            f"| {name} | {s['cohorts'].get(name, 0)} | {_cohort_mark(name)} {note} |"
            for name, note in COHORT_TABLE
        ],
        f"| excluded | {s['n_excluded']} | — 검수자 학습 제외(해석 비대상) |",
        "",
        # ○ 코호트 합계와 품목 지표 분모는 어긋날 수 있다 — is_item_evaluable이 row_missing도
        # 분모에서 뺀다. 이 절의 존재 이유가 "분모를 먼저 읽게 한다"이므로 그 차이를 밝힌다.
        f"품목 지표 분모(평가 가능 쌍) {s['n_item_evaluable']}쌍 — ○ 코호트 합계와 다를 수 있다"
        f"(row_missing {s['label_buckets'].get('row_missing', 0)}건은 분모에서 빠진다).",
        "",
        reeval_notice(meta),
        # 빈 줄이 없으면 별개 알림 2건이 마크다운에서 한 문단으로 병합돼 한 문장처럼 읽힌다.
        "",
        "뱅크 추가 후보는 코호트와 무관하게 현재 뱅크 기준으로 집계된다(성능 측정과 기준이 다르다).",
        "",
    ]


def _render_key_metrics(s: dict) -> list[str]:
    """핵심 지표 표 + 유사도 통계 줄을 렌더한다(render_report에서 순수 추출, M3)."""
    lines = [
        "## 핵심 지표",
        "",
        "| 지표 | 값 |",
        "| --- | --- |",
        f"| 품목 top-1 (평가 가능 쌍 기준) | {_pct(s['top1_hits'], s['n_item_evaluable'])} |",
        f"| 품목 top-5 (평가 가능 쌍 기준) | {_pct(s['top5_hits'], s['n_item_evaluable'])} |",
        "| 정답이 뱅크에 존재(현재 뱅크 기준 · 평가 가능 쌍 분모) | "
        f"{_pct(s['in_bank_n'], s['n_item_evaluable'])} |",
        f"| in-bank 한정 top-1 | {_pct(s['in_bank_top1'], s['in_bank_n'])} |",
        f"| in-bank 한정 top-5 | {_pct(s['in_bank_top5'], s['in_bank_n'])} |",
        f"| 금액 일치 | {_pct(s['amount_ok'], s['amount_n'])} |",
        "",
        f"라벨 버킷: {dict(s['label_buckets'])}",
        f"금액 버킷: {dict(s['amount_buckets'])}",
    ]
    if s["hit_sim_mean"] is not None and s["miss_sim_mean"] is not None:
        lines += [
            "",
            f"top1 유사도 — 적중 평균 {s['hit_sim_mean']:.3f}(min {s['hit_sim_min']:.3f}) vs "
            f"미스 평균 {s['miss_sim_mean']:.3f}(max {s['miss_sim_max']:.3f})",
        ]
    return lines


def _render_job_table(
    enriched: list[dict], inc: list[dict], flags: dict[int, list[str]]
) -> list[str]:
    """잡별 요약 표를 렌더한다(render_report에서 순수 추출, M3)."""
    lines = [
        "",
        "## 잡별 요약",
        "",
        "| job | pairs | top1 | 금액ok | 플래그 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for jid in sorted({r["job_id"] for r in enriched}):
        recs = [r for r in inc if r["job_id"] == jid]
        ev = [r for r in recs if is_item_evaluable(r)]
        amts = [r for r in recs if r["amount_bucket"] is not None]
        # top-1을 k/n으로 적는다 — 0/n으로 적히면 판정 불가 잡이 전패로 오독된다.
        lines.append(
            f"| {jid} | {len(recs)} | "
            f"{sum(r['label_bucket'] == 'ok' for r in ev)}/{len(ev)} | "
            f"{sum(r['amount_bucket'] == 'ok' for r in amts)}/{len(amts)} | "
            f"{', '.join(flags.get(jid, [])) or '—'} |"
        )
    return lines


def _render_miss_list(misses: list[dict], unreachable: list[dict]) -> list[str]:
    """in-bank 리트리벌 미스 목록을 렌더한다(render_report에서 순수 추출).

    구조적 도달 불가(전표 축 제외로 정답 크롭이 후보에서 전부 빠진 쌍)는 목록에서 빼되 건수는
    공개한다 — 숨기면 미스 수가 조용히 줄어 개선 여지가 없는 쌍을 사람이 계속 뒤진다.
    """
    lines = ["", "## in-bank 리트리벌 미스", ""]
    for r in misses:
        lines.append(
            f"- {r['crop_ref']}: answer={r['answer']!r} (final={r['final_label']!r}) "
            f"draft={r['draft_label']!r} sim={r['top1_sim']:.3f} [{r['label_bucket']}] "
            f"top5={r['top5_labels']}"
        )
    if not misses:
        lines.append("- 없음")
    if unreachable:
        lines += [
            "",
            f"※ 전표 축 제외로 정답에 **도달 불가**한 {len(unreachable)}건은 위 목록에서 뺐다"
            "(재평가 has_peer=False — 정답 라벨이 그 잡의 크롭으로만 뱅크에 있다).",
        ]
    return lines


def _render_bank_candidates(
    enriched: list[dict], inc: list[dict]
) -> tuple[list[str], list[tuple[str, int]]]:
    """뱅크 추가 후보 절 + 현재-뱅크 커버리지 줄을 렌더한다.

    커버리지는 핵심 지표 표 분모(평가 가능 쌍)엔 안 보이는 코호트 무관 수치라 여기서 직접
    낸다(spec §1.2). oob는 render_report의 "다음 액션" 절이 재사용한다.
    """
    lines = [
        "",
        "## 뱅크 추가 후보 (현재 뱅크에 없는 정답 라벨)",
        "",
        "현재 뱅크 기준이며 코호트·성능 버킷과 무관하게 집계된다 — 이미 든 라벨을 또 추가할 수",
        "없으므로 성능 측정과 기준이 다른 것이 정상이다.",
        "",
    ]
    oob = oob_label_counts(enriched)
    if oob:
        lines += [f"- {label} ×{n}" for label, n in oob]
    else:
        lines.append("- 없음")
    labeled = [r for r in inc if r["answer"]]
    lines += [
        # 빈 줄이 없으면 CommonMark lazy continuation으로 이 줄이 마지막 후보 불릿의 문단에
        # 병합돼, 전체 커버리지가 그 라벨 하나의 수치인 것처럼 읽힌다(계산 A/표시 B).
        "",
        f"현재 뱅크 보유: {_pct(sum(r['in_bank'] for r in labeled), len(labeled))} "
        "(라벨 있는 included 전체 기준 — 코호트와 무관)",
    ]
    return lines, oob


def _render_amount_failures(inc: list[dict]) -> list[str]:
    """금액 실패 목록을 렌더한다(헬퍼 대칭 완성 — render_report는 조립만 한다)."""
    amt_fail = [r for r in inc if is_amount_failure(r)]
    lines = ["", "## 금액 실패", ""]
    lines += [
        f"- {r['crop_ref']}: draft={r['draft_supply']} final={r['supply']} "
        f"raw={r['amount_raw']!r} [{r['amount_bucket']}] (품목={r['final_label']!r})"
        for r in amt_fail
    ]
    if not amt_fail:
        lines.append("- 없음")
    return lines


def _render_excluded(enriched: list[dict]) -> list[str]:
    """검수자가 학습 제외한 쌍 목록을 렌더한다 — 없으면 절 자체를 만들지 않는다."""
    excluded = [r for r in enriched if r["status"] == "excluded"]
    if not excluded:
        return []
    return [
        "",
        "## excluded (검수자가 학습 제외 — 크롭 불량 신호)",
        "",
        *[
            f"- {r['crop_ref']}: final={r['final_label']!r} draft={r['draft_label']!r}"
            for r in excluded
        ],
    ]


def render_report(enriched: list[dict], meta: dict) -> str:
    """분석 결과를 에이전트가 소비하기 좋은 마크다운 리포트로 렌더한다."""
    s = summarize(enriched)
    flags = job_flags(enriched)
    inc = [r for r in enriched if r["status"] == "included"]
    lines = [
        "# OCR 큐레이션 학습쌍 분석 리포트",
        "",
        f"- 동기화: {meta.get('fetched_at', '?')} · 잡 {s['n_jobs']}개 · "
        f"included {s['n_included']}쌍 · excluded {s['n_excluded']}쌍",
        f"- 뱅크: 임베딩 {meta.get('bank_size', '?')}개 / 라벨 {meta.get('bank_distinct', '?')}종",
        "",
    ]
    lines += _render_cohort_table(s, meta)
    lines += _render_key_metrics(s)

    bank_candidate_lines, oob = _render_bank_candidates(enriched, inc)
    lines += bank_candidate_lines

    misses, unreachable = partition_misses(inc)
    lines += _render_miss_list(misses, unreachable)

    lines += _render_amount_failures(inc)
    lines += _render_job_table(enriched, inc, flags)
    lines += _render_excluded(enriched)

    warp_jobs = [jid for jid, f in flags.items() if "warp_suspect" in f]
    lines += [
        "",
        "## 다음 액션",
        "",
        f"- 뱅크 추가 후보 {len(oob)}라벨 {sum(n for _, n in oob)}크롭 → 재평가 전에는 "
        "`pull-images` 기본 호출이 판정 불가 잡을 당기지 않는다(정상) — 해당 라벨이 나온 "
        "잡 id를 확인해 `pull-images --jobs <job_id...>`로 직접 지정해 크롭을 검수한다",
        f"- warp 재검토 대상 잡: {warp_jobs or '없음'} "
        "→ warped.png를 시각 검수해 warp 실패 여부 확인",
        f"- 리트리벌 미스 {len(misses)}건 → 해당 라벨 뱅크 프로토타입 보강 검토",
        "- 뱅크에 넣은 크롭을 다시 맞히는 낙관 편향의 분해(peer/hold-out)는 여기서 다시 만들지",
        "  않는다 — `bank_update score`의 `score.md`가 `peer_n`/`peer_top1`으로 낸다.",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# ssh fetch 글루 (원격 접속 — 단위테스트 비대상)
# ---------------------------------------------------------------------------


_BANK_PY = (
    "import numpy as np, json, os, collections; "
    "z = np.load(os.environ['SJMJ_ML_MODELS_DIR'] + '/bank.npz', allow_pickle=True); "
    "labs = [str(x) for x in z['lab']]; "
    "print(json.dumps({'size': len(labs), 'counts': collections.Counter(labs)}, "
    "ensure_ascii=False))"
)


def fetch_all(host: str, backend_env: str, worker_env: str, cache: Path) -> dict:
    """서버에서 training_pairs·result_json·뱅크 라벨을 동기화해 캐시 JSON으로 저장한다."""
    cache.mkdir(parents=True, exist_ok=True)
    pairs = parse_pairs_tsv(run_ssh(host, mysql_script(backend_env, PAIRS_SQL, raw=False)).decode())
    jobs = parse_jobs_tsv(run_ssh(host, mysql_script(backend_env, JOBS_SQL, raw=True)).decode())
    bank_script = f'{source_env(worker_env)}"$PYTHON_BIN" -c "{_BANK_PY}"'
    bank = json.loads(run_ssh(host, bank_script).decode())
    meta = {
        "fetched_at": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
        "host": host,
        "bank_size": bank["size"],
        "bank_distinct": len(bank["counts"]),
    }
    (cache / "pairs.json").write_text(json.dumps(pairs, ensure_ascii=False, indent=1))
    (cache / "jobs.json").write_text(json.dumps(jobs, ensure_ascii=False, indent=1))
    (cache / "bank.json").write_text(json.dumps(bank, ensure_ascii=False, indent=1))
    (cache / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    return meta


def pull_images(
    host: str, backend_env: str, cache: Path, job_ids: list[int], with_originals: bool
) -> Path:
    """지정 잡들의 크롭 디렉터리(+옵션 원본 사진)를 캐시로 동기화한다."""
    out_dir = cache / "images"
    out_dir.mkdir(parents=True, exist_ok=True)
    # 빈 목록이면 원격 tar가 인자 없이 실행돼 exit 1로 죽는다 — 정상 상태이므로 no-op.
    if not job_ids:
        return out_dir
    names = " ".join(f"job-{j}" for j in job_ids)
    tar_script = f'{source_env(backend_env)}tar -C "$SJMJ_DATA_DIR/ocr_crops" -cf - {names}'
    with tarfile.open(fileobj=io.BytesIO(run_ssh(host, tar_script))) as tf:
        tf.extractall(out_dir, filter="data")
    if with_originals:
        jobs = json.loads((cache / "jobs.json").read_text())
        for j in jobs:
            if j["job_id"] in job_ids:
                # image_path는 신뢰 DB 값이지만 원격 셸에 들어가므로 방어적으로 quote한다.
                data = run_ssh(host, f"cat {shlex.quote(j['image_path'])}")
                dst = out_dir / f"job-{j['job_id']}"
                dst.mkdir(parents=True, exist_ok=True)
                (dst / "original.jpg").write_bytes(data)
    return out_dir


def _write_images_index(cache: Path, enriched: list[dict], job_ids: list[int]) -> Path:
    """가져온 크롭을 검수할 때 참조할 ref→파일→라벨 인덱스를 만든다.

    M6: 판정 술어(`is_item_evaluable`/`is_item_failure`)로 거르지 않고 그 잡의 행 전량을
    나열한다 — 이 함수는 spec §3-C 소비자 표(5곳)에 없다. plan Task 8 Step 5 Self-Review
    항목 ②가 "`_write_images_index`는 표시용이라 술어를 쓰지 않는다(의도)"라고 명시했다.
    `pull-images`로 당겨온 잡은 검수자가 크롭을 육안으로 보며 판정하므로, 판정 불가 행도
    같이 보여야 "이 행이 왜 판정 불가인지"를 그 자리에서 확인할 수 있다.
    """
    lines = ["# 큐레이션 크롭 검수 인덱스", ""]
    for r in enriched:
        if r["job_id"] not in job_ids:
            continue
        lines.append(
            f"- images/{r['crop_ref']}.png · answer={r['answer']!r} (final={r['final_label']!r}) "
            f"draft={r['draft_label']!r} [{r['label_bucket']}/{r['amount_bucket']}] "
            f"supply={r['supply']} raw={r['amount_raw']!r}"
        )
    path = cache / "images_index.md"
    path.write_text("\n".join(lines) + "\n")
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_enriched(cache: Path) -> tuple[list[dict], dict]:
    pairs = json.loads((cache / "pairs.json").read_text())
    jobs = json.loads((cache / "jobs.json").read_text())
    bank = json.loads((cache / "bank.json").read_text())
    meta = json.loads((cache / "meta.json").read_text())
    return enrich_pairs(pairs, jobs, set(bank["counts"])), meta


def _failure_job_ids(enriched: list[dict]) -> list[int]:
    """pull-images 기본 대상 — 검수 대상 실패가 있는 잡 + excluded가 있는 잡.

    판정 불가만 있는 잡은 당기지 않는다(전 잡 폭주 방지). 재평가 전에는 금액 실패·excluded
    기반 검수 루프만 돌고, 품목 크롭 검수는 재평가 이후에 의미가 생긴다(spec §5).
    """
    return sorted(
        {r["job_id"] for r in enriched if r["status"] == "excluded" or is_item_failure(r)}
    )


def main(argv: list[str] | None = None) -> None:
    """서브커맨드(fetch/report/pull-images)를 파싱해 실행한다."""
    ap = argparse.ArgumentParser(prog="curation_report", description=__doc__)
    ap.add_argument("--host", default=env_or(ENV_SSH_HOST), help="ssh 호스트(별칭)")
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE, help="로컬 캐시 디렉터리")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch", help="서버에서 pairs/jobs/bank 동기화")
    sub.add_parser("report", help="캐시 분석 → report.md + failures.jsonl")
    p_img = sub.add_parser("pull-images", help="실패 잡 크롭 동기화(기본: 실패 잡 전체)")
    p_img.add_argument("--jobs", type=int, nargs="*", help="특정 잡만")
    p_img.add_argument("--originals", action="store_true", help="원본 사진도 포함")
    args = ap.parse_args(argv)

    backend_env = env_or(ENV_BACKEND_ENV)
    worker_env = env_or(ENV_WORKER_ENV)

    if args.cmd == "fetch":
        meta = fetch_all(args.host, backend_env, worker_env, args.cache)
        print(f"동기화 완료 → {args.cache} ({meta['fetched_at']})")
        return

    enriched, meta = _load_enriched(args.cache)

    if args.cmd == "report":
        report = render_report(enriched, meta)
        report_path = args.cache / "report.md"
        report_path.write_text(report)
        # 에이전트가 소비하는 실패 목록 — unevaluable이 섞이면 이슈가 지적한 왜곡이
        # 산출물에 그대로 남는다(spec §3-C).
        failures = [r for r in enriched if is_item_failure(r)]
        fail_path = args.cache / "failures.jsonl"
        fail_path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in failures) + "\n"
        )
        print(report)
        print(f"저장: {report_path}\n저장: {fail_path}")
        return

    if args.cmd == "pull-images":
        job_ids = args.jobs or _failure_job_ids(enriched)
        if not job_ids:
            print("실패 잡이 없어 가져올 이미지가 없습니다.")
            return
        out_dir = pull_images(args.host, backend_env, args.cache, job_ids, args.originals)
        index = _write_images_index(args.cache, enriched, job_ids)
        print(f"이미지 동기화 → {out_dir} (잡 {len(job_ids)}개)\n인덱스: {index}")


if __name__ == "__main__":
    main()
