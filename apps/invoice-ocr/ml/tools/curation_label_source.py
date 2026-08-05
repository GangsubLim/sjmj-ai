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
