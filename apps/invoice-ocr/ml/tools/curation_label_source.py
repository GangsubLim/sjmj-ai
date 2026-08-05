"""큐레이션 리포트의 조작 출처(label_source) 계층 — 조회 SQL·파서·분포·교차 집계.

`correction_json.lines[].label_source`는 확정 화면이 초안 행마다 남기는 **클라이언트 주장**
그대로의 UI 조작 출처다(백엔드 `services/ocr_correction.py`가 추론으로 덮지 않는 이유가 곧
이 모듈의 존재 이유다 — 관측값과 추정값을 섞지 않는다). top-1 적중률이 과소평가하던 "모델이
사람을 얼마나 도왔나"를 이 축이 낸다.

`curation_enrich.py`에 붙이지 않고 모듈을 뗀 이유는 둘이다(spec §3-1).
  - 회귀 위험 0 — enrich에는 네 소스의 파싱·버킷·조인·집계가 모두 들어 있어, 손대면
    top-1·금액·행 수지 전 지표가 변경 반경에 들어온다.
  - 파일 크기 — enrich는 이미 565줄이고 이 슬라이스는 ~180줄이라 상한(800)에 붙는다.

의존은 단방향이다: curation_render(렌더) → curation_label_source(이 모듈) →
curation_cohort(평가 가능성 술어). enrich에는 의존하지 않는다.

코어 규약 준수: stdlib 전용, 전부 순수함수(+ 파서가 푸는 조회 SQL 상수 — 컬럼 계약이 파서와
한 벌이라 여기 산다, CORRECTIONS_SQL이 파서 옆에 사는 것과 같은 관용구).
"""

from collections import Counter

# label_sources TSV의 컬럼 이름·위치 SSoT. SELECT 별칭 순서와 파서의 헤더 대조·위치 인덱싱이
# 이 튜플 하나로 묶인다(parse_corrections_tsv와 동일 방어).
LABEL_SOURCE_COLS = ("job_id", "crop_ref", "label_source")

# 잡별 최신 교정 1건을 고르는 상관 서브쿼리 템플릿 — `curation_enrich.CORRECTIONS_SQL`과
# **같은 규약**을 써야 한다. 다른 행을 고르면 행 수지 절과 이 절이 서로 다른 확정본을 말하고,
# 재확정(n_corrections > 1) 잡에서 즉시 어긋난다(spec §3-2·§8). `{job_col}`만 사용처별로
# 다른(비교 대상 테이블의 job 컬럼) 닫힌 템플릿이라, format() 결과 문자열 전체(비교 컬럼 +
# 닫는 괄호 포함)를 테스트가 못박는다 — enrich는 이 슬라이스에서 수정하지 않는다는 제약이
# 있어서다.
LATEST_CORRECTION_SUBQUERY = (
    "(SELECT MAX(c2.id) FROM ocr_corrections c2 WHERE c2.job_id = {job_col})"
)

# job_id가 NULL인 고아 교정(ocr_jobs 삭제로 FK가 ON DELETE SET NULL 된 행)은 상관 서브쿼리가
# NULL을 돌려줘 구조적으로 빠진다 — corrections.json과 같은 모집단 규약이다(런북 참조).
#
# JOIN JSON_TABLE(...)는 INNER JOIN이라 correction_json이 NULL이거나 `$.lines`가 배열이
# 아닌 잡은 여기서 0행으로 통째로 빠진다. 이미 관측된 모집단이다 — 그 수는
# `curation_enrich.summarize_row_balance`가 `n_missing_json_jobs`로 세어 리포트에 인쇄한다.
# 이 모듈에서 다시 세지 않는다.
LABEL_SOURCES_SQL = (
    "SELECT c.job_id AS job_id, jt.crop_ref AS crop_ref, jt.label_source AS label_source "
    "FROM ocr_corrections c "
    "JOIN JSON_TABLE(c.correction_json, '$.lines[*]' COLUMNS ("
    "crop_ref VARCHAR(255) PATH '$.crop_ref', "
    "label_source VARCHAR(64) PATH '$.label_source')) jt "
    f"WHERE c.id = {LATEST_CORRECTION_SUBQUERY.format(job_col='c.job_id')} "
    "ORDER BY c.job_id, jt.crop_ref"
)


def _cell(value: str) -> str | None:
    """`mysql --batch`의 SQL NULL 표기를 None으로 접는다.

    `curation_enrich._cell`과 같은 관용구를 의도적으로 복제한다 — 이 모듈은 enrich에
    의존하지 않는 것이 설계(의존 단방향)이고, 사설 이름을 가로질러 import하면 그 경계가
    무너진다. 2줄짜리 관용구라 복제 비용이 결합 비용보다 싸다.
    """
    return None if value == "NULL" else value


def parse_label_sources_tsv(text: str) -> list[dict]:
    """mysql --batch TSV(ocr_corrections ⨝ JSON_TABLE lines[])를 dict 리스트로 파싱한다.

    label_source의 JSON `null`과 키 부재는 모두 SQL NULL로 와서 `None`("미기록") 하나로
    접힌다 — 둘을 구분해도 행동이 갈리지 않는다(둘 다 "이 행은 조작 출처가 기록되지 않았다").

    헤더를 LABEL_SOURCE_COLS와 통째로 대조해 fail-fast한다.

    Raises:
        ValueError: 헤더가 LABEL_SOURCE_COLS와 다르거나 컬럼 수가 어긋날 때.
    """
    stripped = text.strip()
    if not stripped:
        return []
    lines = stripped.split("\n")
    header = tuple(lines[0].split("\t"))
    if header != LABEL_SOURCE_COLS:
        raise ValueError(f"label_sources TSV 헤더 불일치: {header!r} != {LABEL_SOURCE_COLS!r}")
    out: list[dict] = []
    for ln in lines[1:]:
        parts = ln.split("\t")
        if len(parts) != len(LABEL_SOURCE_COLS):
            raise ValueError(f"label_sources TSV 컬럼 수 오류({len(parts)}개): {ln[:80]!r}")
        row = dict(zip(LABEL_SOURCE_COLS, parts, strict=True))
        out.append(
            {
                "job_id": int(row["job_id"]),
                "crop_ref": _cell(row["crop_ref"]),
                "label_source": _cell(row["label_source"]),
            }
        )
    return out


# ml이 드는 허용 어휘는 **표시 순서 + 기지 여부 판정용**이다. SSoT는 백엔드
# `app/schemas/ocr.py`의 LABEL_SOURCES(TOP_K 파생)이며, api-spec.json과의 동기 테스트는 붙이지
# 않는다(spec §3-4) — 읽기 전용 분석 도구를 백엔드 어휘 추가만으로 RED로 만드는 것은 값에 비해
# 비용이 크다. 대신 **리포트 자체가 드리프트 탐지기**다: 미지 값이 관측되면 그 자리에서
# 경고하고 백엔드 스키마를 가리킨다(드리프트가 실제로 문제가 되는 시점에 신호가 뜬다).
KNOWN_LABEL_SOURCES = (
    "top1_kept",
    "candidate_picked",
    "manual_picked",
    "manual_typed",
    "new_item_created",
)
CANDIDATE_PICKED = "candidate_picked"
_CANDIDATE_PREFIX = f"{CANDIDATE_PICKED}:"

# rank 행 기본 칸 수 = 백엔드 TOP_K(현재 5)의 **미동기 사본**이다. 값 자체는 파싱에 쓰이지
# 않는다(접두 파싱이 TOP_K 변경에 자동 추종한다) — 이 상수가 정하는 것은 "0건이어도 인쇄할
# rank 칸 수"의 하한뿐이라, 어긋나도 confirm이 죽지 않고 관측이 좁아질 뿐이다
# (`tests/test_topk_sync.py`가 이 상수도 세 번째 사본으로 묶어 CI에서 드리프트를 잡는다 —
# 다만 잡히더라도 다른 두 사본과 달리 confirm을 무너뜨리는 대신 관측만 좁아지는 고장 양상이다).
# 좁아지는 경우: TOP_K가 늘었는데 뒤쪽 rank 선택이 아직 0건이면 그 칸이 인쇄되지 않아
# "뒤쪽 rank에서 아무도 안 골랐다"는 관측이 사라진다. rank가 실제로 관측되면 범위가 따라
# 늘고 렌더가 ⚠ 경고를 띄운다(H1) — 그 경고가 이 사본의 갱신 신호다.
DEFAULT_RANK_SLOTS = 5

# rank 분포를 판단 근거로 쓸 최소 표본 수. 2026-08-04 운영 실측 표본은 2건이라, 하한 없이
# 비율만 인쇄하면 "rank 1이 50%"가 결론처럼 읽힌다(관측 2건의 50%다). 10은 rank 5칸에
# 칸당 평균 2건이 깔리는 최소선 — 근거 있는 정밀도가 아니라 "아직 아니다"를 말하는 문턱이다.
MIN_RANK_SAMPLE = 10


def parse_rank(value: str | None) -> int | None:
    """`candidate_picked:N`의 rank(0-based)를 뽑는다 — 접두 파싱이라 TOP_K 변경에 자동 추종한다.

    저장값이 `candidate_picked:0`부터이므로 rank 0이 곧 top-1 후보다. 표시도 0-based로
    유지한다 — 1-based로 바꾸면 리포트와 `failures.jsonl`·원본 `correction_json`이 한 칸씩
    어긋난다(spec §3-5).

    문법만 본다 — 계약 범위(백엔드 TOP_K) 초과 여부는 판정하지 않는다. 범위 초과는 렌더의
    경고 줄이 말한다(값은 분모·rank 행에 그대로 남는다, spec §3-5의 자동 확장 규정).

    Returns:
        rank 정수. 접두가 없거나 접미가 십진 정수가 아니면 None(= 미지 값).
    """
    if value is None or not value.startswith(_CANDIDATE_PREFIX):
        return None
    suffix = value[len(_CANDIDATE_PREFIX) :]
    # isdigit()은 유니코드 No 범주(위첨자 `²` 등)에도 True를 내 int()가 ValueError로 터지고,
    # isdecimal()은 아라비아-인도 숫자(`٣`)도 걸러 십진 정수만 통과시킨다.
    return int(suffix) if suffix.isdecimal() else None


def label_source_key(value: str) -> str | None:
    """기록된 값의 집계 키를 낸다 — 미지 어휘는 None이다(분모에는 남고 경고로 뜬다).

    rank 없는 맨 `candidate_picked`는 미지로 본다: 백엔드 화이트리스트가 허용하지 않는 값이고,
    기지로 세면 rank 행의 합과 candidate_picked 건수가 어긋나 표가 스스로 모순된다.
    """
    if parse_rank(value) is not None:
        return CANDIDATE_PICKED
    if value in KNOWN_LABEL_SOURCES and value != CANDIDATE_PICKED:
        return value
    return None


def summarize_label_sources(label_sources: list[dict]) -> dict:
    """조작 출처 분포를 집계한다 — 미기록은 분모에서 갈리고, 미지 어휘는 분모에 남는다.

    분모 사다리의 규약(spec §3-5):
      - n_records = 매칭 행(잡별 최신 교정의 lines[]) 전량. 사람 폐기·추가 행은 lines[]에
        아예 없어 이 수의 밖이다(그 값은 행 수지 집계에서 파생한다 — 여기서 다시 세지 않는다).
      - n_recorded / n_unrecorded = 기록 있음 / 미기록. 미기록의 **원인은 나누지 않는다**
        (도입 전·미전송·오타 키 유실이 섞여 있고, NULL만으로는 원인을 증명하지 못한다).
      - n_known / n_unknown = 기록 있음의 하위 갈래(기지 어휘 합계 / 미지 어휘 합계). 렌더가
        뺄셈으로 파생하면 파생 산술이 문자열 조립 안으로 들어간다 — 여기서 미리 갈라 낸다
        (`curation_enrich.summarize_row_balance`의 `n_no_correction_jobs`/`n_missing_json_jobs`와
        같은 관용구). 합은 n_recorded와 같다.
    """
    recorded = [r["label_source"] for r in label_sources if r["label_source"] is not None]
    source_counts = dict.fromkeys(KNOWN_LABEL_SOURCES, 0)
    ranks: Counter = Counter()
    unknown: Counter = Counter()
    for value in recorded:
        key = label_source_key(value)
        if key is None:
            unknown[value] += 1
            continue
        source_counts[key] += 1
        rank = parse_rank(value)
        if rank is not None:
            ranks[rank] += 1
    n_rank_slots = max(DEFAULT_RANK_SLOTS, max(ranks, default=-1) + 1)
    n_known = sum(source_counts.values())
    n_unknown = sum(unknown.values())
    return {
        "n_records": len(label_sources),
        "n_recorded": len(recorded),
        "n_unrecorded": len(label_sources) - len(recorded),
        "n_known": n_known,
        "n_unknown": n_unknown,
        "source_counts": source_counts,
        "rank_counts": {rank: ranks.get(rank, 0) for rank in range(n_rank_slots)},
        "n_rank_slots": n_rank_slots,
        "n_candidate_picked": source_counts[CANDIDATE_PICKED],
        # 건수 내림차순 → 값 사전순. 경고 줄이 매 실행마다 같은 순서로 나오게 한다.
        "unknown_counts": dict(sorted(unknown.items(), key=lambda kv: (-kv[1], kv[0]))),
    }
