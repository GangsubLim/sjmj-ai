"""큐레이션 리포트의 조작 출처 렌더 계층 — `## 조작 출처` 분포 절 + 교차 하위 절.

`curation_render`에서 이 두 절만 떼어낸 모듈이다(동작 변경 0의 기계적 분리). 두 절은 다른
절들과 소스가 다르고(label_sources ⨝ corrections/enriched) 사다리·표·경고가 한 벌이라 경계가
자연스럽다 — 렌더 본체가 파일 상한(800줄)에 붙는 것을 함께 피한다.

의존 방향은 단방향이다: curation_render(렌더 조립) → curation_render_label_source(이 모듈)
→ curation_label_source(집계) → curation_cohort(판정). 사다리의 분모(수지 known 여부)는
curation_enrich의 summarize_row_balance에서 파생한다(행 수지 절과 파생 규약을 공유한다).
서식 원자(pct/share/known_text)는 두 렌더 모듈이 공유하므로 curation_render_fmt에 따로
산다(순환 방지).

코어 규약 준수: stdlib 전용, 전부 순수함수.
"""

from tools.curation_enrich import summarize_row_balance
from tools.curation_label_source import (
    CANDIDATE_PICKED,
    DEFAULT_RANK_SLOTS,
    KNOWN_LABEL_SOURCES,
    MIN_RANK_SAMPLE,
    cross_label_source_buckets,
    summarize_label_sources,
)
from tools.curation_render_fmt import known_text, pct, share


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
        f"초안 {known_text(_val('draft_rows'))}행 = 매칭 {matched} + 사람 폐기 {known_text(_val('rows_dropped'))}",
        f"확정 {known_text(_val('confirmed_rows'))}행 = 매칭 {matched} + 사람 추가 {known_text(_val('rows_added'))}",
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
        out.append(f"| {name} | {counts[name]} | {share(counts[name], s['n_recorded'])} |")
        if name == CANDIDATE_PICKED:
            out += [
                f"| └ rank {rank} | {n} | {share(n, s['n_recorded'])} |"
                for rank, n in s["rank_counts"].items()
            ]
    for value, n in s["unknown_counts"].items():
        out.append(f"| {value} (미지) | {n} | {share(n, s['n_recorded'])} |")
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


def render_label_sources(label_sources: list[dict], corrections: list[dict]) -> list[str]:
    """조작 출처 분포 절 — 분모 사다리를 먼저 놓고 그 위에 분포를 얹는다.

    `lines[]`에 **없는** 행이 두 종류라는 것이 이 사다리의 핵심이다: 사람이 폐기한 초안 행과
    사람이 추가한 확정 행은 매칭되지 않아 조작 출처가 존재조차 하지 않는다. 그 두 수는
    `summarize_row_balance`에서 **파생한다** — 여기서 다시 세면 이 절과 행 수지 절이 어긋난다
    (`curation_render._render_header`가 확정 잡 수를 파생하는 것과 같은 규약).

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


def _label_source_cross_table(c: dict) -> list[str]:
    """교차표 — 행·열 축은 집계가 소유한다(`rows`/`columns`). 렌더는 조밀 dict를 인덱싱만 한다.

    행 순서를 여기서 정렬하면 분포 표(KNOWN_LABEL_SOURCES 순)와 이 표가 따로 움직여, 같은
    출처의 두 행을 잇는 교차 검산이 매번 스캔이 된다. 0건 행도 인쇄한다 — 빼면 "그 출처는
    평가 가능 행이 하나도 없었다"는 관측이 사라진다(rank 행을 전량 인쇄하는 것과 같은 규약).
    """
    out = [
        "| 출처 | " + " | ".join(c["columns"]) + " |",
        "| --- | " + " | ".join("---" for _ in c["columns"]) + " |",
    ]
    out += [
        f"| {src} | " + " | ".join(str(c["table"][src][col]) for col in c["columns"]) + " |"
        for src in c["rows"]
    ]
    return out


def render_label_source_cross(label_sources: list[dict], enriched: list[dict]) -> list[str]:
    """출처 × 품목 버킷 교차 절 — 모집단은 평가 가능 행 한정이다.

    기존 top-1 지표와 같은 잣대를 써야 리포트 안에서 교차 검산이 된다. 빠진 행은 사유별로
    사다리에 남는다(학습쌍 없음 / 학습 제외 / 시점 판정 불가 / 정합 장애) — 판정 불가를 한 항으로
    합치지 않는 이유는 두 축의 후속 조치가 다르기 때문이다(재평가 실행 vs 재처리 흔적 조사).

    `top1_kept`인데 `ok`가 아닌 셀이 이 표의 핵심 관측이다 — 사람이 틀린 top-1을 그대로 뒀다는
    뜻이며, label_source가 클라이언트 주장 그대로 보존되기 때문에 볼 수 있는 값이다.
    """
    c = cross_label_source_buckets(label_sources, enriched)
    return [
        "### 출처 × 품목 버킷 (평가 가능 행 한정)",
        "",
        "```text",
        f"기록 {c['n_recorded']} → 학습쌍 없음 -{c['n_no_pair']} → 학습 제외 -{c['n_excluded']}"
        f" → 시점 판정 불가 -{c['n_unevaluable']} → 정합 장애 -{c['n_row_missing']}"
        f" → 평가 가능 {c['n_evaluable']}",
        "  (학습쌍 없음 = 행검출 누락·쌍 미생성 / 학습 제외 = status excluded",
        "   / 시점 판정 불가 = 구 뱅크 코호트·재평가 미채택 / 정합 장애 = row_missing)",
        "```",
        "",
        *_label_source_cross_table(c),
        "",
        # 빈 줄이 없으면 인접한 두 줄이 GFM soft break로 한 문단에 병합돼 └가 문장 중간에
        # 박힌다(_render_cohort_table·_render_bank_candidates가 이미 못박은 함정) — 펜스로
        # 감싸 계층 관계(└)를 그대로 보존한다. 각 줄에 분모의 정체를 인라인으로 밝혀
        # 문단까지 읽지 않아도 AC 수치(넓은 분모)와 보조 지표(좁힌 분모)를 구분하게 한다.
        "```text",
        f"top-1 미적중인데 후보 칩에서 고름: {pct(c['n_miss_candidate_picked'], c['n_miss'])}"
        "  (AC 수치 · 분모=label_bucket != ok 전량)",
        f"└ 정답이 뱅크에 있던 미스 한정: "
        f"{pct(c['n_retrieval_miss_candidate_picked'], c['n_retrieval_miss'])}"
        "  (보조 지표 · 분모=리트리벌 미스 한정)",
        "```",
        "",
        "위 넓은 분모에는 out_of_bank(정답이 뱅크에 없음)·no_candidates(후보 칩 0건)가 들어 있다"
        " — 후보 칩이 구조적으로 도울 수 없는 미스라 그만큼 비율이 과소평가된다. 좁힌 짝"
        "(top5_only·in_bank_miss)이 후보 칩이 실제로 도울 수 있었던 몫이다.",
        "",
    ]
