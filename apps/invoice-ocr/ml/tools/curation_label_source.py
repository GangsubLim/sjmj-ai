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

from tools.curation_cohort import (
    DATA_INTEGRITY_FAILURE_BUCKETS,
    RETRIEVAL_MISS_BUCKETS,
    TEMPORAL_UNEVALUABLE_BUCKETS,
    is_item_evaluable,
)

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


# 교차표의 열 — `curation_enrich.label_bucket`의 어휘 5종을 그대로 쓴다(새 어휘를 발명하지
# 않는다). 평가 가능 행은 구조상 이 다섯 중 하나지만, 어휘가 늘어도 값을 삼키지 않도록
# 관측된 미지 버킷은 열 뒤에 붙인다.
ITEM_BUCKET_COLUMNS = ("ok", "top5_only", "in_bank_miss", "out_of_bank", "no_candidates")


def _cross_table_and_columns(
    ev: list[tuple[dict, dict]],
) -> tuple[dict[str, Counter], tuple[str, ...]]:
    """평가 가능 조인쌍을 출처 × 버킷 `Counter`로 접고, 열 순서(기지 + 미지 사전순)를 낸다."""
    table: dict[str, Counter] = {}
    for ls, row in ev:
        # 미지 어휘는 원문 그대로 한 행을 차지한다 — 조용히 버리면 사다리 합과 표 합이 어긋난다.
        key = label_source_key(ls["label_source"]) or ls["label_source"]
        table.setdefault(key, Counter())[row["label_bucket"]] += 1
    observed_buckets = {bucket for row in table.values() for bucket in row}
    columns = ITEM_BUCKET_COLUMNS + tuple(sorted(observed_buckets - set(ITEM_BUCKET_COLUMNS)))
    return table, columns


def _densify_table(
    table: dict[str, Counter], columns: tuple[str, ...]
) -> tuple[dict[str, dict[str, int]], tuple[str, ...]]:
    """행 순서(`rows`)를 열과 대칭으로 정하고, `table`을 rows × columns 조밀 dict로 물질화한다.

    `rows`는 `KNOWN_LABEL_SOURCES` 순서(0건 출처도 유지) + 관측된 미지 출처를 `sorted()`로
    뒤에 붙인다 — 분포 절의 `dict.fromkeys(KNOWN_LABEL_SOURCES, 0)`과 같은 행 축 규약이다.
    `Counter`는 없는 키에 예외 없이 0을 내 렌더가 버킷 이름을 오타내도 조용히 0이 인쇄되므로,
    조밀 dict로 물질화해 모든 행 × 모든 열이 채워지게 한다(0건 행도 포함).
    """
    rows = KNOWN_LABEL_SOURCES + tuple(sorted(set(table) - set(KNOWN_LABEL_SOURCES)))
    dense = {src: {col: table.get(src, Counter()).get(col, 0) for col in columns} for src in rows}
    return dense, rows


def _split_unevaluable(
    inc: list[tuple[dict, dict]],
) -> tuple[list[tuple[dict, dict]], int, int]:
    """`inc`에서 평가 가능(`ev`)을 가르고, 나머지를 두 축으로 더 가른다.

    `n_unevaluable`(시점 판정 불가)과 `n_row_missing`(데이터 정합 장애, 재처리로 result_json과
    training_pairs가 어긋난 상태)은 관심사가 다른 두 축이다 — curation_cohort의 기존 상수
    (`TEMPORAL_UNEVALUABLE_BUCKETS`/`DATA_INTEGRITY_FAILURE_BUCKETS`)를 따라 갈라 낸다.
    """
    ev = [(ls, row) for ls, row in inc if is_item_evaluable(row)]
    unevaluable = [(ls, row) for ls, row in inc if not is_item_evaluable(row)]
    n_unevaluable = sum(
        1 for _, row in unevaluable if row["label_bucket"] in TEMPORAL_UNEVALUABLE_BUCKETS
    )
    n_row_missing = sum(
        1 for _, row in unevaluable if row["label_bucket"] in DATA_INTEGRITY_FAILURE_BUCKETS
    )
    return ev, n_unevaluable, n_row_missing


def _cross_miss_stats(ev: list[tuple[dict, dict]]) -> dict:
    """headline 분자·분모 — 넓은 분모(AC 3)와 `RETRIEVAL_MISS_BUCKETS`로 좁힌 짝을 함께 낸다.

    `out_of_bank`(정답이 뱅크에 없음)·`no_candidates`(후보 0건)는 후보 칩이 구조적으로 도움을
    줄 수 없어 분모에만 기여하고 분자에는 기여할 수 없다 — 좁은 짝은 넓은 짝의 부분집합이다.
    """
    misses = [(ls, row) for ls, row in ev if row["label_bucket"] != "ok"]
    retrieval_misses = [
        (ls, row) for ls, row in misses if row["label_bucket"] in RETRIEVAL_MISS_BUCKETS
    ]

    def _n_candidate_picked(pairs: list[tuple[dict, dict]]) -> int:
        return sum(label_source_key(ls["label_source"]) == CANDIDATE_PICKED for ls, _ in pairs)

    return {
        "n_miss": len(misses),
        "n_miss_candidate_picked": _n_candidate_picked(misses),
        "n_retrieval_miss": len(retrieval_misses),
        "n_retrieval_miss_candidate_picked": _n_candidate_picked(retrieval_misses),
    }


def cross_label_source_buckets(label_sources: list[dict], enriched: list[dict]) -> dict:
    """기록된 조작 출처를 crop_ref로 enriched에 조인해 출처 × 품목 버킷 교차를 낸다.

    **`included` → `evaluable` 2단계 순서가 계약이다.** `is_item_evaluable`은 `label_bucket`
    한 키만 보고 `status`를 보지 않으므로(curation_cohort), status 필터를 빼면 학습 제외된 쌍
    중 버킷이 ok/top5_only인 것이 교차표와 headline 분모·분자로 샌다. `summarize()`가
    `inc → ev`로 좁히는 것과 같은 순서다(spec §3-6).

    모집단을 평가 가능 행으로 한정하는 이유는 기존 top-1 지표와 같은 잣대를 써야 리포트 안에서
    교차 검산이 되기 때문이다. 빠진 행은 사라지지 않고 사유별로 사다리에 남는다(사다리를 두
    축으로 가르는 근거는 `_split_unevaluable` 참조).

    조인 키가 `crop_ref` 단독인 근거는 둘이다. 형식(`job-N/row-M`)이 전역 유일하고,
    한 교정본의 `lines[]`에 같은 `crop_ref`가 두 번 들어간 상태는 **커밋될 수 없다** —
    `build_correction`의 `lines.append`는 중복을 막지 않지만, `ocr_service.confirm`이
    교정 insert와 `insert_training_pairs`를 한 트랜잭션으로 묶고 `training_pairs.crop_ref`가
    UNIQUE(migration_008)라 중복은 교정 행까지 함께 롤백시킨다. 따라서 1:N 조인은 도달 불가다
    (도달 불가 경로용 방어는 넣지 않는다 — 넣으면 리포트가 데이터 이상에서 죽는다).

    `by_ref = {row["crop_ref"]: row for row in enriched}`의 덮어쓰기 무해성은 반대 방향에서
    같은 제약이 보장한다 — `enriched`는 `training_pairs`를 그대로 읽은 것이고(curation_enrich의
    `PAIRS_SQL`) `training_pairs.crop_ref UNIQUE NOT NULL`(migration_008)이 같은 crop_ref를
    가진 두 행을 애초에 허용하지 않으므로, 이 dict는 서로 다른 두 행을 침묵 속에 덮어쓸 수 없다.
    """
    by_ref = {row["crop_ref"]: row for row in enriched}
    recorded = [r for r in label_sources if r["label_source"] is not None]
    joined = [(r, by_ref[r["crop_ref"]]) for r in recorded if r["crop_ref"] in by_ref]
    inc = [(ls, row) for ls, row in joined if row["status"] == "included"]
    ev, n_unevaluable, n_row_missing = _split_unevaluable(inc)

    table, columns = _cross_table_and_columns(ev)
    dense_table, rows = _densify_table(table, columns)

    return {
        "n_recorded": len(recorded),
        "n_no_pair": len(recorded) - len(joined),
        "n_excluded": len(joined) - len(inc),
        "n_unevaluable": n_unevaluable,
        "n_row_missing": n_row_missing,
        "n_evaluable": len(ev),
        "table": dense_table,
        "columns": columns,
        "rows": rows,
        **_cross_miss_stats(ev),
    }
