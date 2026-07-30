"""OCR 큐레이션 학습쌍(training_pairs) 정확도 분석 리포트 도구.

배포 서버(macmini)의 운영 DB·모델뱅크·크롭 이미지를 ssh로 동기화해 로컬 캐시에 두고,
품목 retrieval(top1/top5·뱅크 내외 분해)과 금액 OCR(0-드리프트·퇴화출력·오독)의 실패를
버킷으로 귀속한 마크다운 리포트를 만든다. LLM 에이전트가 리포트→실패 크롭 시각 검수→
개선(뱅크 추가·warp 재검토) 루프를 돌리기 위한 입구다. 사용법은 docs/runbooks 참조.

코어 규약 준수: stdlib 전용(paddle/torch 불필요), 렌더 계층은 순수함수(테스트 대상),
ssh/DB 접근은 fetch 글루에 격리. 원격 접속값은 env로만 주입한다. 순수 계층은 두 모듈에
분리돼 있고 의존은 단방향이다 — 이 모듈(fetch·CLI·렌더) → tools/curation_enrich.py(파싱·
버킷·조인·집계) → tools/curation_cohort.py(코호트·평가 가능성 술어·재평가 게이트).

Usage:
    uv run python -m tools.curation_report fetch        # 서버에서 pairs/jobs/bank 동기화
    uv run python -m tools.curation_report report       # 캐시 분석 → report.md/failures.jsonl
    uv run python -m tools.curation_report pull-images  # 실패 잡 크롭(+원본) 로컬 동기화
"""

import argparse
import io
import json
import os
import shlex
import tarfile
from datetime import UTC, datetime
from pathlib import Path

# bank_id는 stdlib 전용이고 handwriting에 __init__.py가 없는 암묵 namespace 패키지라
# 모듈 레벨 import가 numpy/torch를 끌지 않는다(paddle-free 코어 규약 유지).
from handwriting.bank_id import file_digest
from tools.curation_cohort import (
    ITEM_EVALUABLE_COHORTS,
    is_amount_failure,
    is_item_evaluable,
    is_item_failure,
    parse_reeval_jsonl,
    partition_misses,
    reeval_after,
    reeval_gate,
)
from tools.curation_enrich import (
    JOBS_SQL,
    PAIRS_SQL,
    enrich_pairs,
    job_flags,
    oob_label_counts,
    parse_jobs_tsv,
    parse_pairs_tsv,
    summarize,
)
from tools.remote import (
    ENV_BACKEND_ENV,
    ENV_ML_ROOT,
    ENV_SSH_HOST,
    ENV_WORKER_ENV,
    RemoteError,
    env_or,
    mysql_script,
    remote_path,
    run_ssh,
    source_env,
)

ML_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE = ML_ROOT / "results" / "curation"

# ---------------------------------------------------------------------------
# 리포트 렌더 계층 (단위테스트 대상 — IO 없음)
# ---------------------------------------------------------------------------


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
# 현재 지문을 모르면 재평가 사유를 말할 자격이 없다(H1) — 그 상태에서는 어떤 재평가도
# 게이트(no_fingerprint)를 통과하지 못하고 전 잡이 stale_bank/unknown으로 떨어진다.
_NO_FINGERPRINT_NOTICE = (
    "현재 retrieval 지문을 확정하지 못했다 — 재평가를 돌려도 게이트가 no_fingerprint로 "
    "기각해 지표는 그대로 0/0이다. `fetch`를 먼저 다시 실행한다"
    "(그래도 미확정이면 서버 `git rev-parse HEAD`·모델 파일을 확인한다)."
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


def status_notice(meta: dict) -> str:
    """리포트가 실을 상태 한 줄 — 현재 지문 부재를 재평가 사유보다 먼저 말한다(H1).

    `reeval_notice`를 그대로 부르면 지문 미확정 상태에서 "재평가 산출물이 없다"를 인쇄하고
    수십 분짜리 원격 재채점을 권한다. 그 재채점도 같은 이유로 기각되므로 사용자는 시간을 쓰고도
    같은 0/0을 본다 — 이 모듈의 원칙("부재 단정은 사용자를 엉뚱한 조치로 보낸다")을 지문 축에서
    다시 밟는 것이다. 두 축을 한 함수에 겹치지 않는 이유: `reeval_notice`의 치역은
    ReevalState/ReevalReason 전량과 대조되고 있어 지문 유무라는 직교 조건이 곱해지면 안 된다.
    """
    if not meta.get("retrieval_version"):
        return _NO_FINGERPRINT_NOTICE
    return reeval_notice(meta)


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
        status_notice(meta),
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
        # 코호트 판정의 기준값 — 인쇄하지 않으면 stale_bank 표기를 검증할 근거가 리포트에 없다.
        f"- 현재 retrieval 지문: {meta.get('retrieval_version') or '미확정'}",
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


# 원격 인라인 스크립트에 지문 로직도 그 **입력**(모델 파일명·뱅크 파일명·배열 선택)도 복붙하지
# 않는다 — bank_id.bank_retrieval_version 하나를 워커와 공유한다(M4). 두 곳이 다른 입력을 고르면
# 지문이 전량 어긋나 모든 잡이 조용히 stale이 된다(spec §3-A). 지문은 **원격에서** 계산해야
# 유효하다: 코드 SHA가 입력이라 로컬에서 계산하면 전 잡이 조용히 stale로 오분류된다.
#
# 지문 계산만 try로 감싼다(M3) — keys 없는 뱅크는 실재 가능한 상태이고(운영 워커도 같은 실패를
# 진단 필드 하나로 격리한다) 그 실패로 pairs/jobs 동기화까지 막을 이유가 없다. `handwriting`
# import는 try 밖이라 hard-fail을 유지한다 — 그건 배포 누락 신호다.
# 셸 이중따옴표 안에 그대로 들어가므로 `"`·`$`·백틱·백슬래시를 쓰지 않는다.
_BANK_PY = """
import collections, json, os, sys
import numpy as np
from handwriting import bank_id
d = os.environ['SJMJ_ML_MODELS_DIR']
z = np.load(os.path.join(d, bank_id.BANK_FILENAME), allow_pickle=True)
labs = [str(x) for x in z['lab']]
try:
    version = bank_id.bank_retrieval_version(d, z, labs)
except Exception as e:
    print('retrieval_version 계산 실패(%s: %s)' % (type(e).__name__, e), file=sys.stderr)
    version = None
print(json.dumps({'size': len(labs), 'counts': collections.Counter(labs),
                  'retrieval_version': version}, ensure_ascii=False))
"""

# 이 값들이 없으면 서버가 지문 기능(#49) 이전 릴리스라는 뜻이다 — 다른 모듈 부재는 다른 원인이다.
_FINGERPRINT_MODULES = ("handwriting.bank_id", "handwriting")
# 상단 메시지에 실을 원격 stderr 꼬리 줄 수 — traceback 전문은 길고 원인은 끝에 있다.
_STDERR_EXCERPT_LINES = 3

# 캐시 손상은 원인이 무엇이든 복구 절차가 하나다 — 서버에서 다시 받는다.
_CACHE_RECOVERY = "로컬 캐시 손상이다. `fetch`를 다시 실행한다."

# 재평가 산출물이 사는 원격 하위 경로(bank_update.DEFAULT_OUT과 같은 자리).
REEVAL_SUBDIR = "results/bank_update"
# (원격 파일명, 캐시 파일명). 순서가 곧 쓰기 순서다 — jsonl 먼저, 그 다음 meta.
REEVAL_FILES = (("score.jsonl", "reeval.jsonl"), ("score_meta.json", "reeval_meta.json"))
# 원자 교체 한 벌의 마지막 파일 — 앞의 두 파일을 **해석하는** 쪽이라 가장 나중에 갈아끼운다(M2).
CACHE_META = "meta.json"


def bank_script(worker_env: str, ml_root: str) -> str:
    """원격 뱅크 라벨 집계 + 현재 retrieval 지문을 한 번에 얻는 셸 스크립트를 만든다.

    ml_root로 cd하는 이유: `python -c`는 cwd를 sys.path에 넣으므로 그래야 handwriting
    패키지를 import할 수 있다. 서버 레포에 handwriting.bank_id가 없으면(릴리스 배포 전)
    ModuleNotFoundError로 크게 실패한다 — 지문 없이 조용히 진행하면 전 표본이 stale/unknown
    으로 떨어져 품목 지표가 0/0이 되므로, 원인을 메시지로 풀어 주는 편이 낫다.
    """
    return f'{source_env(worker_env)}cd "{remote_path(ml_root)}"; "$PYTHON_BIN" -c "{_BANK_PY}"'


def reeval_probe_script(ml_root: str) -> str:
    """재평가 산출물 존재를 확인한다 — 부재는 정상 상태이므로 비0으로 죽지 않는다."""
    return (
        f'cd "{remote_path(ml_root)}/{REEVAL_SUBDIR}" 2>/dev/null || exit 0; '
        "ls score.jsonl score_meta.json 2>/dev/null || true"
    )


def reeval_cat_script(ml_root: str, name: str) -> str:
    """재평가 산출물 1개를 그대로 읽어온다(name은 REEVAL_FILES의 상수다)."""
    return f'cat "{remote_path(ml_root)}/{REEVAL_SUBDIR}/{name}"'


def _replace_atomically(cache: Path, files: list[tuple[str, bytes]]) -> None:
    """항상 함께 움직여야 하는 파일들을 **전부** tmp로 받은 뒤 순서대로 교체한다.

    한 벌은 셋이다(M2): 재평가 두 파일과 **그 둘을 해석하는** meta.json(retrieval_version·
    reeval_state). 앞의 둘만 원자적이면 meta.json이 평범한 쓰기로 먼저 굳어, 짝이 어긋난 상태의
    사유가 stale로 오보된다 — 수치는 fail-closed라 안전하지만 사용자는 잘못된 조치로 간다.

    교체 사이에 죽는 창은 남으므로 순서를 고정한다(호출자가 준 순서 = REEVAL_FILES + meta.json):
    meta.json 교체 전에 죽으면 이전 meta의 지문·다이제스트가 새 산출물과 어긋나 게이트가
    "재평가 없음"으로 닫는다(fail-closed).
    """
    staged = [(cache / name, cache / f"{name}.tmp", body) for name, body in files]
    try:
        for _path, tmp, body in staged:
            tmp.write_bytes(body)
        for path, tmp, _body in staged:
            os.replace(tmp, path)
    except Exception:
        for _path, tmp, _body in staged:
            tmp.unlink(missing_ok=True)
        raise


def _clear_reeval(cache: Path) -> None:
    """캐시의 재평가 두 파일을 함께 지운다 — 두 파일은 항상 같이 움직인다.

    남겨두면 서버에서 산출물이 사라지거나 옮겨진 뒤에도 로컬 reeval_meta.json이 살아남아
    재평가가 유효한 것처럼 읽힌다(warp_gate_report.fetch_all이 이전 fetch 산출을 먼저
    rmtree하는 것과 같은 이유).
    """
    for _remote, local in REEVAL_FILES:
        (cache / local).unlink(missing_ok=True)


def _read_reeval_files(jsonl_path: Path, meta_path: Path) -> tuple[list[dict], dict]:
    """캐시의 재평가 두 파일을 읽는다 — 손상은 파일명·복구 지침과 함께 경계에서 막는다(H2).

    `parse_reeval_jsonl`이 dict 아닌 줄을 막는 것과 같은 이유로 meta도 dict 여부를 본다:
    게이트 안쪽까지 흘러가면 dict가 아닌 값에 AttributeError가 나 원인이 파싱 경계에서
    멀어진다(`null`은 게이트가 no_meta로 정상 처리하는데 `_reeval_info`가 먼저 죽었다).
    읽기 인코딩도 여기서 못박는다(L5) — 쓰기는 UTF-8 bytes다.

    Raises:
        json.JSONDecodeError: score.jsonl이 파싱되지 않을 때(즉시 실패 계약 유지 — 이 타입은
            ValueError의 하위형이라 호출자는 ValueError 하나로 잡는다).
        ValueError: score_meta.json이 파싱되지 않거나 JSON 객체가 아닐 때.
    """
    try:
        records = parse_reeval_jsonl(jsonl_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(
            f"{jsonl_path.name} 손상({e.msg}) — {_CACHE_RECOVERY}", e.doc, e.pos
        ) from e
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{meta_path.name} 손상({e.msg}) — {_CACHE_RECOVERY}") from e
    if not isinstance(meta, dict):
        raise ValueError(
            f"{meta_path.name}이 JSON 객체가 아니다({type(meta).__name__}) — {_CACHE_RECOVERY}"
        )
    return records, meta


def _reeval_info(cache: Path, meta: dict) -> tuple[dict | None, dict]:
    """캐시의 재평가를 유효성 게이트에 통과시키고 리포트용 상태 정보를 만든다.

    `after`는 `reeval_after`로 평탄화한다 — score_meta는 지문을 중첩(`{before, after}`)으로
    쓰는데 `reeval_notice`는 평탄 키를 읽으므로, 재맵을 빠뜨리면 채택 문구가 지문을 못 찾는다.

    Returns:
        (게이트를 통과한 {crop_ref: 레코드} 또는 None, meta["reeval"]에 실을 상태 정보).
    """
    info = {
        "state": meta.get("reeval_state", "absent"),
        "adopted": False,
        "reason": None,
        "generated_at": None,
        "after": None,
        "scope": None,
        "n_pairs": None,
    }
    jsonl_path, meta_path = cache / "reeval.jsonl", cache / "reeval_meta.json"
    if info["state"] != "present" or not (jsonl_path.exists() and meta_path.exists()):
        return None, info
    records, reeval_meta = _read_reeval_files(jsonl_path, meta_path)
    gate = reeval_gate(
        records=records,
        meta=reeval_meta,
        current_retrieval_version=meta.get("retrieval_version"),
        jsonl_sha256=file_digest(jsonl_path),
    )
    return gate.pairs, {
        **info,
        "adopted": gate.pairs is not None,
        "reason": gate.reason,
        "generated_at": reeval_meta.get("generated_at"),
        "after": reeval_after(reeval_meta),
        "scope": reeval_meta.get("scope"),
        "n_pairs": reeval_meta.get("n_pairs"),
    }


def fetch_error_message(stderr: str) -> str | None:
    """서버가 지문 기능(Issue #49) 이전 릴리스일 때 쓸 행동 지침을 만든다. 아니면 None.

    배포 전에는 서버 레포에 handwriting.bank_id가 없다 — hard-fail은 의도이지만 raw
    traceback은 행동 지침이 아니다. 문자열 판정만 하는 순수 헬퍼라 ssh 없이 단위테스트로 닫는다.

    판정은 **모듈명까지** 본다(M1). `No module named` 단독 매칭은 서버 venv의 numpy/torch
    부재까지 "#49 이전 릴리스"로 오진하는데, 그 경우 배포는 이미 됐고 원인은 venv라 지침이
    엉뚱하다. 원본 stderr 발췌도 싣는다 — 삼키면 어떤 모듈이 없는지 확인할 창구가 사라진다.

    raise가 아니라 메시지를 반환한다 — 조건부로만 던지는 헬퍼는 호출부에서 제어흐름이 보이지
    않아, 반환 후 다음 줄이 실행되는지를 헬퍼 본문을 열어야 알 수 있다.
    """
    if not any(f"No module named '{name}'" in stderr for name in _FINGERPRINT_MODULES):
        return None
    excerpt = " / ".join(stderr.strip().splitlines()[-_STDERR_EXCERPT_LINES:])
    return (
        "서버 코드가 retrieval 지문 기능(Issue #49) 이전 릴리스다 — "
        "`v*` 태그 배포 후 다시 실행한다. "
        "(배포 전에는 기존 캐시로 `report`를 돌려 금액·excluded 검수 루프를 계속할 수 있다.) "
        f"원격 stderr: {excerpt}"
    )


def _fetch_reeval(host: str, ml_root: str, cache: Path) -> tuple[str, list[tuple[str, bytes]]]:
    """서버의 재평가 산출물을 회수해 (회수 상태 ReevalState, 캐시에 쓸 (파일명, 내용))을 낸다.

    쓰기는 호출자가 meta.json과 **한 벌로** 교체한다(M2) — 그래야 세 파일이 함께 움직인다.
    부재 경로에서는 이전 회수분을 즉시 지운다(그 자리에 쓸 새 내용이 없으므로 한 벌에 넣을 수
    없고, 남겨두면 재평가가 유효한 것처럼 읽힌다).
    """
    names = set(run_ssh(host, reeval_probe_script(ml_root)).decode().split())
    if {remote for remote, _local in REEVAL_FILES} <= names:
        bodies = [
            (local, run_ssh(host, reeval_cat_script(ml_root, remote)))
            for remote, local in REEVAL_FILES
        ]
        return "present", bodies
    _clear_reeval(cache)
    # score.jsonl만 있는 상태는 정상 경로다(#53 이전 산출물) — 리포트가 한 줄 알린다.
    return ("no_meta" if "score.jsonl" in names else "absent"), []


def _write_json(path: Path, obj) -> None:
    """캐시 JSON 1개를 UTF-8로 쓴다 — 읽기(`_load_enriched`)와 인코딩을 맞춘다(L5)."""
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def fetch_all(host: str, backend_env: str, worker_env: str, ml_root: str, cache: Path) -> dict:
    """서버에서 training_pairs·result_json·뱅크 라벨·현재 지문·재평가 산출물을 동기화한다."""
    cache.mkdir(parents=True, exist_ok=True)
    pairs = parse_pairs_tsv(run_ssh(host, mysql_script(backend_env, PAIRS_SQL, raw=False)).decode())
    jobs = parse_jobs_tsv(run_ssh(host, mysql_script(backend_env, JOBS_SQL, raw=True)).decode())
    try:
        bank = json.loads(run_ssh(host, bank_script(worker_env, ml_root)).decode())
    except RemoteError as e:
        message = fetch_error_message(str(e))
        if message:
            raise RuntimeError(message) from e
        raise

    reeval_state, reeval_files = _fetch_reeval(host, ml_root, cache)
    meta = {
        "fetched_at": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
        "host": host,
        "bank_size": bank["size"],
        "bank_distinct": len(bank["counts"]),
        "retrieval_version": bank.get("retrieval_version"),
        "reeval_state": reeval_state,
    }
    _write_json(cache / "pairs.json", pairs)
    _write_json(cache / "jobs.json", jobs)
    _write_json(cache / "bank.json", bank)
    # 재평가 두 파일과 그 해석자(meta.json)는 한 벌로 갈아끼운다 — 순서는 해석자가 마지막.
    meta_body = json.dumps(meta, ensure_ascii=False, indent=1).encode()
    _replace_atomically(cache, [*reeval_files, (CACHE_META, meta_body)])
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
    """캐시를 읽어 재평가·현재 지문까지 배선한 enriched 행과 meta를 만든다.

    `reeval`·`current_retrieval_version`을 넘기지 않으면 코호트 판정이 기준값을 잃어 리포트가
    전량 `unevaluable`로 떨어진다 — 이 배선이 곧 era-aware 재판정의 소비 지점이다.
    """
    pairs = json.loads((cache / "pairs.json").read_text(encoding="utf-8"))
    jobs = json.loads((cache / "jobs.json").read_text(encoding="utf-8"))
    bank = json.loads((cache / "bank.json").read_text(encoding="utf-8"))
    meta = json.loads((cache / CACHE_META).read_text(encoding="utf-8"))
    reeval, info = _reeval_info(cache, meta)
    enriched = enrich_pairs(
        pairs,
        jobs,
        set(bank["counts"]),
        reeval=reeval,
        current_retrieval_version=meta.get("retrieval_version"),
    )
    return enriched, {**meta, "reeval": info}


def _failure_job_ids(enriched: list[dict]) -> list[int]:
    """pull-images 기본 대상 — 검수 대상 실패가 있는 잡 + excluded가 있는 잡.

    판정 불가만 있는 잡은 당기지 않는다(전 잡 폭주 방지). 재평가 전에는 금액 실패·excluded
    기반 검수 루프만 돌고, 품목 크롭 검수는 재평가 이후에 의미가 생긴다(spec §5).
    """
    return sorted(
        {r["job_id"] for r in enriched if r["status"] == "excluded" or is_item_failure(r)}
    )


def _cmd_fetch(host: str, backend_env: str, worker_env: str, ml_root: str, cache: Path) -> None:
    """fetch 서브커맨드 — 동기화하고 다음 조치를 판단할 요약을 출력한다.

    지문이 미확정이면 그 사실과 조치를 그 자리에서 말한다(M3·H1) — 원격 지문 계산 실패는 fetch를
    죽이지 않고 null로 통과시키므로, 여기서 안 알리면 리포트가 전량 stale_bank로 나온 뒤에야
    원인을 찾게 된다.
    """
    meta = fetch_all(host, backend_env, worker_env, ml_root, cache)
    version = meta["retrieval_version"]
    print(f"동기화 완료 → {cache} ({meta['fetched_at']})")
    print(f"현재 retrieval 지문: {version or '미확정'} · 재평가: {meta['reeval_state']}")
    if not version:
        print(_NO_FINGERPRINT_NOTICE)


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
    ml_root = env_or(ENV_ML_ROOT)

    if args.cmd == "fetch":
        _cmd_fetch(args.host, backend_env, worker_env, ml_root, args.cache)
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
