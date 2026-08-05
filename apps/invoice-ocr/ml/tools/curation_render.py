"""큐레이션 리포트의 렌더 계층 — 분석 결과를 마크다운 리포트로 조립한다.

tools/curation_report.py에서 **순수함수만** 떼어낸 모듈이다(동작 변경 0의 기계적 분리, Issue
#38). 리포트 본체는 fetch 글루·CLI에 렌더 계층(표본 구성표·핵심 지표·잡별 요약·미스 목록·뱅크
후보·금액 실패·excluded 절)까지 담아 파일 상한(800줄)을 넘겼는데, 이 계층은 IO 0·부수효과 0이라
합성 데이터 단위테스트로 전량 닫히므로 경계가 자연스럽다(tools/curation_enrich.py·
tools/curation_cohort.py를 뗀 것과 같은 관용구).

의존 방향은 단방향이다: curation_report(fetch·CLI) → curation_render(이 모듈, 렌더) →
curation_enrich(분석) → curation_cohort(판정). curation_report는 render_report와
NO_FINGERPRINT_NOTICE(지문 미확정 안내 — fetch 직후 안내에도 재사용) 둘만 끌어온다.

코어 규약 준수: stdlib 전용(paddle/numpy/pillow 불필요), 전부 순수함수.
"""

from tools.curation_cohort import (
    ITEM_EVALUABLE_COHORTS,
    is_amount_failure,
    is_item_evaluable,
    partition_misses,
)
from tools.curation_enrich import (
    is_human_excluded,
    is_machine_excluded,
    is_reverted_machine_exclusion,
    is_row_balance_known,
    job_flags,
    oob_label_counts,
    summarize,
    summarize_row_balance,
)
from tools.curation_label_source import (
    CANDIDATE_PICKED,
    DEFAULT_RANK_SLOTS,
    KNOWN_LABEL_SOURCES,
    MIN_RANK_SAMPLE,
    summarize_label_sources,
)


def _pct(k: int, n: int) -> str:
    """비율을 `k/n (p%)`로 인쇄한다 — 분모 0에서도 **분자는 삼키지 않는다**.

    대부분의 호출부는 `k ≤ n`이 구조적으로 보장돼 `n=0 ⇒ k=0`이지만, 행 수지 절의 둘째 줄은
    분자(training_pairs)와 분모(교정 이력)의 소스가 달라 그 불변식이 깨진다 — 거기서 분자를
    0으로 접으면 하필 이 절이 드러내려는 소스 드리프트 신호가 지워진다.

    잡별 요약 표는 이 함수가 아니라 `_ratio`를 쓴다 — 거기선 분모 0에서 분자를 지운다(`—/0`).
    """
    return f"{k}/{n} ({100 * k / n:.1f}%)" if n else f"{k}/0 (—)"


def _share(k: int, n: int) -> str:
    """비율만 인쇄한다 — 건수는 표의 옆 칸이 이미 낸다. 분모 0이면 '—'.

    `_pct`(`k/n (p%)`)를 쓰면 건수 칸과 분자가 중복돼 표가 같은 수를 두 번 말한다.
    """
    return f"{100 * k / n:.1f}%" if n else "—"


def _known(value: object) -> str:
    """값을 인쇄하되 **모를 때만** '?'로 물러선다 — 이 모듈은 모르는 것을 말하지 않는다.

    `dict.get(key, "?")`로는 이 폴백이 발화하지 않는다: 생산자(`curation_report._reeval_info`)가
    키를 항상 만들되 None으로 시드하므로 `get`은 기본값이 아니라 저장된 None을 돌려주고, 손상된
    score_meta.json이 리터럴 "None"으로 인쇄돼 진짜 값처럼 읽힌다. truthiness가 아니라
    `is None`으로 판정한다 — n_pairs 0쌍은 유효한 관측치라 '?'로 뭉개면 안 된다.
    """
    return "?" if value is None else str(value)


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
# curation_report._cmd_fetch도 fetch 직후 지문 미확정을 알릴 때 이 상수를 재사용한다.
NO_FINGERPRINT_NOTICE = (
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
    돌렸다고 착각하지 않게 한다(spec §3-C). **"산출물이 없다"는 단정은 정보 자체가 없을 때만
    한다**(H1 — 근거는 `_REEVAL_UNKNOWN_REASON`). `no_meta`는 회수 상태(ReevalState)이자 게이트
    사유(ReevalReason)로 같은 철자를 쓰므로 분기가 따로 필요 없다 — state를 사유 폴백으로
    그대로 조회한다(M2).
    """
    info = meta.get("reeval") or {}
    if info.get("adopted"):
        return (
            f"재평가: {_known(info.get('generated_at'))} · retrieval 지문 "
            f"{_known(info.get('after'))}(현재와 일치) · scope={_known(info.get('scope'))} · "
            f"표본 {_known(info.get('n_pairs'))}쌍"
        )
    state = info.get("state")
    if not info or state == "absent":
        return _REEVAL_ABSENT
    text = _REEVAL_REJECT_TEXT.get(info.get("reason") or state)
    return f"재평가 없음: {text}" if text else _REEVAL_UNKNOWN_REASON


def status_notice(meta: dict) -> str:
    """리포트가 실을 상태 한 줄 — 현재 지문 부재를 재평가 사유보다 먼저 말한다(H1).

    `reeval_notice`를 그대로 부르면 지문 미확정 상태에서 "재평가 산출물이 없다"를 인쇄하고 수십
    분짜리 원격 재채점을 권한다 — 그 재채점도 같은 이유로 기각돼 사용자는 시간을 쓰고도 같은
    0/0을 본다. 두 축을 한 함수에 겹치지 않는 이유: `reeval_notice`의 치역은 ReevalState/
    ReevalReason 전량과 대조되고 있어 지문 유무라는 직교 조건이 곱해지면 안 된다.
    """
    if not meta.get("retrieval_version"):
        return NO_FINGERPRINT_NOTICE
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
        # 배제는 사람 판정만이 아니다(ADR 0006 이후 기계 자동 배제가 섞인다) — 소유 축 분해는
        # 머리말과 excluded 절이 낸다. 여기서 "검수자"라고 못박으면 그 둘과 모순된다.
        f"| excluded | {s['n_excluded']} | — 학습 제외(해석 비대상 · 기계/사람 분해는 excluded 절) |",
        "",
        # ○ 합계와 품목 분모는 어긋날 수 있다 — is_item_evaluable이 row_missing도 분모에서 뺀다.
        f"품목 지표 분모(평가 가능 쌍) {s['n_item_evaluable']}쌍 — ○ 코호트 합계와 다를 수 있다"
        f"(row_missing {s['label_buckets'].get('row_missing', 0)}건은 분모에서 빠진다).",
        "",
        status_notice(meta),
        # 빈 줄이 없으면 별개 알림 2건이 마크다운에서 한 문단으로 병합돼 한 문장처럼 읽힌다.
        "",
        "뱅크 추가 후보는 코호트와 무관하게 현재 뱅크 기준으로 집계된다(성능 측정과 기준이 다르다).",
        "",
    ]


def _render_row_balance(enriched: list[dict], corrections: list[dict]) -> list[str]:
    """행 수지 절 — 손실을 두 단계로 분해한다(행검출 축 / 코호트·배제 축).

    한 단계로 `n_item_evaluable / confirmed_rows`만 적으면 **뱅크 시점 문제와 학습 제외까지
    행검출 누락으로 읽힌다.** 첫 줄만이 이 슬라이스가 새로 여는 축이고, 둘째 줄은 기존
    코호트·배제 축이라 읽는 법도 후속 조치도 다르다(런북 0번·4번).

    분자 n_lines는 교정 이력에서, 학습 후보 쌍 수는 training_pairs에서 온다 — 소스가 다르므로
    둘 다 적어 어긋남(재처리·삭제 흔적)이 드러나게 한다.

    두 줄 모두 **행 수지가 known인 잡**만 본다. 이 스코핑이 막는 것은 미상 잡의 쌍이 분자로
    새는 누수뿐이며, **100% 상한을 보증하지는 않는다** — 두 소스가 어긋나면(재처리·삭제로
    쌍이 교정 이력보다 많으면) 100%를 넘는 값이 그대로 인쇄된다. 그것이 이 절이 드러내려는
    신호이므로 클램프하지 않는다.
    """
    # 둘째 줄의 분자·분모 모집단을 맞춘다 — summarize()의 n_item_evaluable은 enriched 전량
    # 기준이라, 수지 미상 잡이 쌍을 가지면 분자만 부풀어 오른다(조용한 오수치). summarize()는
    # 손대지 않고(모집단이 다르다) 이 절에서만 known 잡으로 좁힌다.
    rb = summarize_row_balance(corrections)
    known_jobs = {c["job_id"] for c in corrections if is_row_balance_known(c)}
    scoped = [r for r in enriched if r["job_id"] in known_jobs]
    n_pairs = len(scoped)  # 배제 쌍도 센다 — n_lines는 confirm 시점 축이고 배제는 그 이후다
    n_evaluable = sum(is_item_evaluable(r) for r in scoped if r["status"] == "included")
    out = [
        "",
        "## 행 수지",
        "",
        "```text",
        f"초안 {rb['draft_rows']}행 → 사람 추가 +{rb['rows_added']} / "
        f"사람 폐기 -{rb['rows_dropped']} → 확정 {rb['confirmed_rows']}행",
        "",
        f"행검출 가시 범위   {_pct(rb['n_lines'], rb['confirmed_rows'])}"
        f"   (학습 후보가 된 행 / 사람이 인정한 행 · 학습 후보 쌍 {n_pairs}개(수지 known 잡 한정))",
        f"└ 그중 판정 가능   {_pct(n_evaluable, rb['n_lines'])}"
        "   (배제·구 뱅크 코호트·정합 장애로 빠진 몫 — 행검출 실패가 아니다)",
        "```",
        "",
        f"행 수지 미상 {rb['n_unknown_jobs']}잡"
        f"(교정 이력 없음 {rb['n_no_correction_jobs']} / "
        f"교정 JSON 결손 {rb['n_missing_json_jobs']}) — 위 합계 밖",
        "",
    ]
    if rb["n_multi_correction_jobs"]:
        out.append(
            f"재확정(교정 이력 2건 이상) {rb['n_multi_correction_jobs']}잡 — 최신 1건만 읽었다"
        )
        out.append("")  # 절 꼬리를 조건과 무관하게 같은 모양으로 닫는다
    return out


def _label_source_ladder(s: dict, rb: dict) -> list[str]:
    """분모 사다리 — 폐기·추가 행 수는 `summarize_row_balance`에서 파생한다(다시 세지 않는다).

    수지 미상 잡이 하나라도 있으면 폐기·추가와 그 합을 `?`로 적는다(#72 불변식) — 미상 잡을
    0으로 접으면 이 절이 실제보다 완전한 사다리를 인쇄한다.

    성립하지 않는 등식(매칭 행 수 어긋남)을 사다리 직후에 경고한다(설명 문단보다 앞) — 이 모듈은
    "모르는 것을 말하지 않는다"는 자기 규약을 두고 있는데, 경고를 뒤로 미루면 사람이 어긋난
    등식을 먼저 사실처럼 읽고 한참 뒤에야 그것이 흔들린다는 것을 알게 된다.
    """
    matched = s["n_records"]
    unknown_jobs = rb["n_unknown_jobs"]

    def _val(key: str) -> int | None:
        """수지 known 필드를 조회한다 — 미상 잡이 하나라도 있으면 None(#72 불변식)."""
        return None if unknown_jobs else rb[key]

    out = [
        "```text",
        f"초안 {_known(_val('draft_rows'))}행 = 매칭 {matched} + 사람 폐기 {_known(_val('rows_dropped'))}",
        f"확정 {_known(_val('confirmed_rows'))}행 = 매칭 {matched} + 사람 추가 {_known(_val('rows_added'))}",
        f"매칭 {matched} → 기록 있음 {s['n_recorded']} / 미기록 {s['n_unrecorded']}",
        "```",
        "",
    ]
    if matched != rb["n_lines"]:
        # 두 소스는 같은 교정 행(잡별 MAX(id))의 같은 lines[]를 세므로 원래 같아야 한다.
        # 어긋나면 한쪽이 다른 확정본을 읽고 있다는 뜻이라 그 자리에서 말한다(spec §8 리스크1).
        # fetch의 ssh 왕복 2회 사이에 확정이 끼어든 세대 어긋남도 이 줄이 관측한다.
        out += [
            f"⚠ 매칭 행 수가 행 수지 절과 다르다: 조작 출처 {matched}행 vs 교정 이력 n_lines "
            f"{rb['n_lines']}행 — 두 절이 같은 교정 행(잡별 MAX(id))을 읽는지 확인한다.",
            "",
        ]
    out += [
        "매칭 행은 **잡별 최신 교정 1건**의 `lines[]`다(재확정 잡의 이전 확정본은 읽지 않는다)."
        " 폐기·추가 행은 `lines[]`에 없어 조작 출처가 존재하지 않는다 — 아래 분포의 분모 밖이다"
        "(같은 값을 `## 행 수지` 절이 잡별로 분해한다). 미기록의 원인은 나누지 않는다 —"
        " 도입 전·미전송·오타 키로 인한 유실이 섞여 있고 NULL만으로는 원인을 증명하지 못한다.",
        "",
    ]
    if unknown_jobs:
        out += [
            f"행 수지 미상 {unknown_jobs}잡 — 폐기·추가 행 수를 몰라 합을 단정하지 않는다(`?`)."
            " `## 행 수지` 절은 같은 상황에서 **수지 known 잡 합계**를 실수치로 인쇄한다 —"
            " 여기의 `?`는 그 값이 없다는 뜻이 아니라 미상 잡을 포함한 전체 합을 단정하지"
            " 않는다는 뜻이다.",
            "",
        ]
    return out


def _label_source_table(s: dict) -> list[str]:
    """출처 표 — 표시 순서를 손으로 적지 않는다(KNOWN_LABEL_SOURCES에서 도출, M3).

    rank 행은 0건이어도 전량 인쇄한다 — "뒤쪽 rank에서 아무도 안 골랐다"가 곧 top-5 확대
    무용의 근거인데, 0건 행을 빼면 그 관측이 사라진다(spec §3-5).
    """
    counts = s["source_counts"]
    out = [
        f"| 출처 | 건수 | 비율(기록 {s['n_recorded']} 기준) |",
        "| --- | --- | --- |",
    ]
    for name in KNOWN_LABEL_SOURCES:
        out.append(f"| {name} | {counts[name]} | {_share(counts[name], s['n_recorded'])} |")
        if name == CANDIDATE_PICKED:
            out += [
                f"| └ rank {rank} | {n} | {_share(n, s['n_recorded'])} |"
                for rank, n in s["rank_counts"].items()
            ]
    for value, n in s["unknown_counts"].items():
        out.append(f"| {value} (미지) | {n} | {_share(n, s['n_recorded'])} |")
    return out


def _label_source_warnings(s: dict) -> list[str]:
    """이 절이 스스로 말하는 "아직 아니다" 셋 — 표본 하한 · rank 범위 초과 · 미지 어휘."""
    out: list[str] = []
    if s["n_candidate_picked"] < MIN_RANK_SAMPLE:
        out += [
            "",
            f"⚠ 후보 칩 선택 표본 {s['n_candidate_picked']}건(하한 {MIN_RANK_SAMPLE}) — "
            "rank 분포는 아직 판단 근거가 되지 못한다",
        ]
    if s["n_rank_slots"] > DEFAULT_RANK_SLOTS:
        # 접두 파싱은 범위를 묻지 않는다(문법만 본다) — 계약 범위 초과는 여기서 말한다.
        # 이 줄이 없으면 백엔드 TOP_K 확대가 rank 행 개수 변화로만 나타나 아무도 못 본다.
        out += [
            "",
            f"⚠ 관측 rank가 기본 범위를 넘었다(최대 rank {s['n_rank_slots'] - 1} · 기본 "
            f"{DEFAULT_RANK_SLOTS}칸) — 백엔드 `app/schemas/ocr.py`의 TOP_K가 늘었을 수 있다. "
            "이 모듈의 DEFAULT_RANK_SLOTS와 함께 확인한다.",
        ]
    if s["unknown_counts"]:
        detail = " · ".join(f"{v}({n})" for v, n in s["unknown_counts"].items())
        out += [
            "",
            f"⚠ 알 수 없는 조작 출처 {len(s['unknown_counts'])}종 {s['n_unknown']}건: {detail}",
            "  → 백엔드 `app/schemas/ocr.py`의 허용 어휘가 늘었을 수 있다. "
            "이 절의 분모에는 포함되어 있다.",
        ]
    return out


def _render_label_sources(label_sources: list[dict], corrections: list[dict]) -> list[str]:
    """조작 출처 분포 절 — 분모 사다리를 먼저 놓고 그 위에 분포를 얹는다.

    `lines[]`에 **없는** 행이 두 종류라는 것이 이 사다리의 핵심이다: 사람이 폐기한 초안 행과
    사람이 추가한 확정 행은 매칭되지 않아 조작 출처가 존재조차 하지 않는다. 그 두 수는
    `summarize_row_balance`에서 **파생한다** — 여기서 다시 세면 이 절과 행 수지 절이 어긋난다
    (`_render_header`가 확정 잡 수를 파생하는 것과 같은 규약).

    사다리·표·경고를 사설 헬퍼 셋으로 나눈 것은 함수 길이 규약(50줄) 때문이며, 조립 순서가
    곧 절의 읽는 순서다.
    """
    s = summarize_label_sources(label_sources)
    rb = summarize_row_balance(corrections)
    return (
        ["", "## 조작 출처", ""]
        + _label_source_ladder(s, rb)
        + _label_source_table(s)
        + _label_source_warnings(s)
        + [""]
    )


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
        f"| 빈 크롭 가드 오탐(되돌림/기계 판정) | "
        f"{_pct(s['n_reverted_machine'], s['n_reverted_machine'] + s['n_excluded_machine'])} |",
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


def _ratio(k: int, n: int) -> str:
    """분모가 0이면 `—/0`으로 적는다 — `0/0`은 판정 불가 잡을 전패로 오독하게 한다.

    `_pct`(분모 0에서도 분자를 인쇄한다)와 다르게 분자를 지운다: 이 표의 두 비율은 분자가
    분모의 부분집합(`ev`⊇적중, `amts`⊇ok)이라 `n=0 ⇒ k=0`이 구조적으로 참이고, 그래서 남길
    분자 정보 자체가 없다.
    """
    return f"{k}/{n}" if n else "—/0"


def _render_job_table(
    enriched: list[dict], inc: list[dict], flags: dict[int, list[str]], corrections: list[dict]
) -> list[str]:
    """잡별 요약 표를 렌더한다 — 행 수지 3열을 얹고 쌍 0개 잡도 한 행을 차지한다.

    순회 축이 `enriched ∪ corrections`라 학습 후보 쌍이 하나도 없는 확정 잡(행검출 전멸)이
    표에서 사라지지 않는다 — 가장 조용히 사라지는 잡이 가장 봐야 할 잡이다(spec §5-3).
    행 수지가 미상인 잡은 `?`로 적는다(0으로 접지 않는다).

    `pairs(incl)`는 included 한정이다(top1·금액ok와 같은 모집단) — 머리말의 "쌍 보유"는
    included+excluded 전체 기준이라 배제쌍만 있는 잡은 머리말과 이 표에서 다른 수로 찍힌다.
    그 차이는 두 계약이 다르다는 신호이지 버그가 아니다(M2) — 열 이름으로 표면화한다.
    """
    balance_by_job = {c["job_id"]: c for c in corrections}
    # 열 이름 하나에서 헤더와 구분선을 함께 도출한다 — 손으로 두 줄을 맞추면 열 수 드리프트가
    # 검출 불가하다(GFM은 헤더/구분선 셀 수가 다르면 표를 문단으로 뭉갠다, M1).
    cols = ("job", "pairs(incl)", "초안", "+행", "-행", "top1", "금액ok", "플래그")
    lines = [
        "",
        "## 잡별 요약",
        "",
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join("---" for _ in cols) + " |",
    ]
    for jid in sorted({r["job_id"] for r in enriched} | set(balance_by_job)):
        recs = [r for r in inc if r["job_id"] == jid]
        ev = [r for r in recs if is_item_evaluable(r)]
        amts = [r for r in recs if r["amount_bucket"] is not None]
        c = balance_by_job.get(jid)
        if c is None or not is_row_balance_known(c):
            draft, added, dropped = "?", "?", "?"
        else:
            draft, added, dropped = c["draft_rows"], c["rows_added"], c["rows_dropped"]
        lines.append(
            f"| {jid} | {len(recs)} | {draft} | {added} | {dropped} | "
            f"{_ratio(sum(r['label_bucket'] == 'ok' for r in ev), len(ev))} | "
            f"{_ratio(sum(r['amount_bucket'] == 'ok' for r in amts), len(amts))} | "
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
        # 빈 줄이 없으면 CommonMark lazy continuation으로 마지막 불릿에 흡수돼 그 라벨 수치로 읽힌다.
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
    """학습 제외 쌍을 소유 축(기계/사람)으로 갈라 렌더한다 — 빈 절은 만들지 않는다.

    사유가 기록된 배제가 기계 자동 배제이고, 사유가 빈 배제가 사람 판정이다(ADR 0006).
    한 목록으로 합치면 크롭 불량 신호(사람)와 가드 동작(기계)이 섞여 검수 대상이 흐려진다.
    included인데 사유가 남아 있는 쌍은 기계 배제를 사람이 되돌린 것 — 가드의 오탐 관측치라
    별도 절로 낸다.

    술어는 `summarize`(머리말 수치)와 공유한다 — 조건을 여기 다시 적으면 한쪽만 고쳤을 때
    머리말의 수와 아래 나열된 행이 예외 없이 어긋난다(curation_enrich의 술어 절 주석 참조).
    """
    machine = [r for r in enriched if is_machine_excluded(r)]
    human = [r for r in enriched if is_human_excluded(r)]
    reverted = [r for r in enriched if is_reverted_machine_exclusion(r)]
    lines: list[str] = []
    if machine:
        lines += ["", "## excluded — 기계 자동 배제 (사유 기록됨)", ""]
        lines += [
            f"- {r['crop_ref']}: [{r['exclusion_reason']}] final={r['final_label']!r} "
            f"draft={r['draft_label']!r}"
            for r in machine
        ]
    if human:
        lines += ["", "## excluded — 사람 배제 (사유 미분류 — 크롭 불량 신호)", ""]
        lines += [
            f"- {r['crop_ref']}: final={r['final_label']!r} draft={r['draft_label']!r}"
            for r in human
        ]
    if reverted:
        lines += ["", "## included — 기계 자동 배제를 사람이 되돌림 (오탐 관측치)", ""]
        lines += [
            f"- {r['crop_ref']}: [{r['exclusion_reason']}] final={r['final_label']!r}"
            for r in reverted
        ]
    return lines


def _render_header(s: dict, meta: dict, corrections: list[dict], enriched: list[dict]) -> list[str]:
    """리포트 제목·동기화 요약·뱅크 지문을 렌더한다(헬퍼 대칭 완성 — render_report는 조립만 한다).

    corrections(교정 이력)는 쌍 기준 지표의 **바깥 경계**를 낸다 — 쌍이 0개인 확정 잡은
    enriched에 한 줄도 없지만 확정 잡 모집단에는 들어 있다(spec §5-1). 확정 잡 수는
    `summarize_row_balance`에서 파생한다 — 여기서 다시 세면 이 절과 "## 행 수지" 절의
    수가 어긋날 수 있다(같은 파일의 배제 절 주석과 같은 이유, M3).

    "쌍 보유"는 **전체 쌍**(included + excluded) 기준이다 — included만으로 좁히면 배제쌍만
    있는 잡이 "쌍 0개"로 잘못 계상돼 눈먼 잡 신호가 거짓이 된다(M1).
    """
    n_confirmed = summarize_row_balance(corrections)["n_confirmed_jobs"]
    pair_job_ids = {r["job_id"] for r in enriched}
    n_with_pairs = sum(c["job_id"] in pair_job_ids for c in corrections)
    return [
        "# OCR 큐레이션 학습쌍 분석 리포트",
        "",
        f"- 동기화: {meta.get('fetched_at', '?')} · 잡 {s['n_jobs']}개 · "
        f"확정 잡 {n_confirmed}개(쌍 보유 {n_with_pairs} / "
        f"쌍 0개 {n_confirmed - n_with_pairs}) · "
        f"included {s['n_included']}쌍 · excluded {s['n_excluded']}쌍"
        f"(기계 {s['n_excluded_machine']} / 사람 {s['n_excluded_human']})"
        f" · 기계배제 되돌림 {s['n_reverted_machine']}쌍"
        f"(오탐 관측치, 사유별 {dict(s['reverted_reason_counts'])})",
        f"- 뱅크: 임베딩 {meta.get('bank_size', '?')}개 / 라벨 {meta.get('bank_distinct', '?')}종",
        # 코호트 판정의 기준값 — 인쇄하지 않으면 stale_bank 표기를 검증할 근거가 리포트에 없다.
        f"- 현재 retrieval 지문: {meta.get('retrieval_version') or '미확정'}",
        "",
    ]


def render_report(
    enriched: list[dict], meta: dict, corrections: list[dict], *, label_sources: list[dict]
) -> str:
    """분석 결과를 에이전트가 소비하기 좋은 마크다운 리포트로 렌더한다.

    `corrections`와 `label_sources`는 둘 다 `list[dict]`라 위치 인자로 두면 뒤바뀌어도 타입체커도
    테스트도 못 잡고 전량 오수치 리포트가 조용히 나온다 — `label_sources`를 키워드 전용으로 둔다.
    """
    s = summarize(enriched)
    flags = job_flags(enriched, corrections)
    inc = [r for r in enriched if r["status"] == "included"]
    lines = _render_header(s, meta, corrections, enriched)
    lines += _render_cohort_table(s, meta)
    lines += _render_row_balance(enriched, corrections)
    lines += _render_label_sources(label_sources, corrections)
    lines += _render_key_metrics(s)

    bank_candidate_lines, oob = _render_bank_candidates(enriched, inc)
    lines += bank_candidate_lines

    misses, unreachable = partition_misses(inc)
    lines += _render_miss_list(misses, unreachable)

    lines += _render_amount_failures(inc)
    lines += _render_job_table(enriched, inc, flags, corrections)
    lines += _render_excluded(enriched)

    warp_jobs = [jid for jid, f in flags.items() if "warp_suspect" in f]
    row_gap_jobs = sorted(jid for jid, f in flags.items() if "row_gap" in f)
    lines += [
        "",
        "## 다음 액션",
        "",
        f"- 뱅크 추가 후보 {len(oob)}라벨 {sum(n for _, n in oob)}크롭 → 재평가 전에는 "
        "`pull-images` 기본 호출이 판정 불가 잡을 당기지 않는다(정상) — 해당 라벨이 나온 "
        "잡 id를 확인해 `pull-images --jobs <job_id...>`로 직접 지정해 크롭을 검수한다",
        f"- warp 재검토 대상 잡: {warp_jobs or '없음'} "
        "→ warped.png를 시각 검수해 warp 실패 여부 확인",
        # 크롭이 아니라 원본을 가리킨다 — row_gap 잡은 쌍·크롭이 0개일 수 있어 크롭 검수로는
        # 아무것도 볼 수 없다(행검출이 전멸한 잡이 이 플래그의 표적이다).
        f"- 행 수지 이상 잡: {row_gap_jobs or '없음'} → 원본 사진과 행검출 결과를 대조한다"
        " (`pull-images --jobs <job_id...> --originals`"
        " — 쌍 0개·크롭 0개 잡도 원본은 받아진다)",
        f"- 리트리벌 미스 {len(misses)}건 → 해당 라벨 뱅크 프로토타입 보강 검토",
        "- 참고: 실패 잡 수(`pull-images` 기본 대상)에는 기계 자동 배제가 포함된다.",
        "- 뱅크에 넣은 크롭을 다시 맞히는 낙관 편향의 분해(peer/hold-out)는 여기서 다시 만들지",
        "  않는다 — `bank_update score`의 `score.md`가 `peer_n`/`peer_top1`으로 낸다.",
    ]
    return "\n".join(lines) + "\n"
