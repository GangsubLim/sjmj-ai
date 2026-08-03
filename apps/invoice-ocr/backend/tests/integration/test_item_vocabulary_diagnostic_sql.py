"""런북의 발산 진단 SQL 0행 불변식(#40 spec §5·§7).

SQL을 복사하지 않고 docs/runbooks/ocr-curation-analysis.md의 해당 절에서 읽어 실행한다 —
복사본은 런북과 갈리고, 그러면 운영자가 보는 진단과 테스트가 지키는 진단이 달라진다.

진단 조건은 등록 조건(CurationService·migration_010)과 정확히 같아야 한다. 빈 라벨 조건이
빠지면 0행 불변식이 성립하지 않는다 — 확정 요청의 품목 name은 빈 문자열이 허용되고
ocr_correction이 그 값을 그대로 canonical_label로 삼아 included 쌍을 만드는데, 등록 쪽은
그런 라벨을 건너뛰므로 정상적으로 등록되지 않은 빈 라벨이 영구히 발산으로 보고된다.

이 테스트가 고정하는 것은 "등록 조건 == 진단 조건"이지 "운영에서 항상 0행"이 아니다 —
사람이 사전 항목을 지우면 진단은 정상적으로 그 라벨을 보고한다(런북 원인 표 1행).
"""

from pathlib import Path

import pytest
from sqlalchemy import text

from app.repositories.items_repository import ItemRepository

# tests/integration/x.py → tests → backend → invoice-ocr → apps → repo root
_REPO_ROOT = Path(__file__).resolve().parents[5]
_RUNBOOK = _REPO_ROOT / "docs" / "runbooks" / "ocr-curation-analysis.md"
_HEADING = "## 품목 어휘 발산 진단"
_ANCHOR = "<!-- diagnostic-sql -->"

assert _RUNBOOK.is_file(), f"런북 파일이 없다(경로 계산이 틀렸을 수 있다): {_RUNBOOK}"

pytestmark = pytest.mark.usefixtures("db_conn")


def _diagnostic_sql() -> str:
    """런북 진단 절의 앵커 바로 다음 sql 코드펜스를 꺼낸다.

    "절 안 첫 펜스"로 찾으면 절에 확인용 예시 SQL이 앞에 추가되는 순간 그 예시를 실행하고,
    펜스 탐색이 절 경계를 넘어 다음 절의 펜스까지 집을 수 있다. 앵커 + 절 범위로 못 박는다.
    """
    lines = _RUNBOOK.read_text(encoding="utf-8").splitlines()
    assert _HEADING in lines, f"런북에 '{_HEADING}' 절이 없다: {_RUNBOOK}"
    start = lines.index(_HEADING)
    following = [
        i for i, line in enumerate(lines[start + 1 :], start + 1) if line.startswith("## ")
    ]
    section = lines[start : following[0] if following else len(lines)]
    assert _ANCHOR in section, f"'{_HEADING}' 절에 앵커 '{_ANCHOR}'가 없다: {_RUNBOOK}"
    fence = section.index(_ANCHOR) + 1
    while fence < len(section) and not section[fence].strip():
        fence += 1  # prettier가 앵커와 펜스 사이에 빈 줄을 넣는다(실측 3.8.3).
    assert fence < len(section), f"앵커 '{_ANCHOR}' 다음에 아무것도 없다: {_RUNBOOK}"
    assert section[fence] == "```sql", f"앵커 다음 줄이 '```sql' 펜스가 아니다: {_RUNBOOK}"
    assert "```" in section[fence + 1 :], f"진단 sql 펜스가 절 안에서 닫히지 않았다: {_RUNBOOK}"
    sql = "\n".join(section[fence + 1 : section.index("```", fence + 1)]).strip().rstrip(";")
    assert sql, f"진단 절의 sql 코드펜스가 비어 있다: {_RUNBOOK}"
    return sql


def _diverged_rows(engine) -> list[tuple[str, int]]:
    with engine.begin() as conn:
        return [(r[0], r[1]) for r in conn.execute(text(_diagnostic_sql())).all()]


def _diverged(engine) -> list[str]:
    return [label for label, _ in _diverged_rows(engine)]


def _seed_job(engine, *, reviewed=1):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ocr_jobs (status, image_path, curation_reviewed) "
                "VALUES ('done', '/d.jpg', :r)"
            ),
            {"r": reviewed},
        )
        return conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()


def _seed_pair(engine, job_id, row_index, label, status="included"):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO training_pairs "
                "(crop_ref, job_id, row_index, final_label, canonical_label, status) "
                "VALUES (:r, :j, :i, :l, :l, :s)"
            ),
            {
                "r": f"job-{job_id}/row-{row_index}",
                "j": job_id,
                "i": row_index,
                "l": label,
                "s": status,
            },
        )


def test_diagnostic_reports_nothing_when_labels_are_registered(db_conn):
    job_id = _seed_job(db_conn)
    _seed_pair(db_conn, job_id, 0, "휠")
    _seed_pair(db_conn, job_id, 1, "배선수리")
    ItemRepository().ensure_exists("휠")
    ItemRepository().ensure_exists("배선수리")

    assert _diverged(db_conn) == []


def test_diagnostic_ignores_null_empty_and_whitespace_labels(db_conn):
    """등록 쪽이 건너뛰는 라벨은 진단도 보고하지 않아야 0행 불변식이 성립한다."""
    job_id = _seed_job(db_conn)
    _seed_pair(db_conn, job_id, 0, None)
    _seed_pair(db_conn, job_id, 1, "")
    _seed_pair(db_conn, job_id, 2, "   ")

    assert _diverged(db_conn) == []


def test_diagnostic_ignores_excluded_and_unreviewed(db_conn):
    reviewed = _seed_job(db_conn, reviewed=1)
    unreviewed = _seed_job(db_conn, reviewed=0)
    _seed_pair(db_conn, reviewed, 0, "배제품목", status="excluded")
    _seed_pair(db_conn, unreviewed, 0, "미검수품목")

    assert _diverged(db_conn) == []


def test_diagnostic_reports_missing_label(db_conn):
    """신호가 죽어 있지 않음을 확인한다 — 사전에 없는 정상 라벨은 반드시 보고된다."""
    job_id = _seed_job(db_conn)
    _seed_pair(db_conn, job_id, 0, "사전에없는품목")

    assert _diverged(db_conn) == ["사전에없는품목"]


def test_diagnostic_ignores_padded_label_registered_in_trimmed_form(db_conn):
    """공백이 붙은 라벨도 트림된 형태로 등록됐으면 발산이 아니다.

    진단 조인이 원문 canonical_label을 쓰면 이 케이스가 영구 오탐이 된다 —
    utf8mb4_unicode_ci는 PAD SPACE라 후행 공백만 가려지고 선행 공백은 가려지지 않는다
    (로컬 MySQL 9.6.0: '라이닝1조' = '  라이닝1조  ' COLLATE utf8mb4_unicode_ci → 0).
    확정 요청의 품목 name은 원본을 strip하지 않고 저장되므로(app/schemas/ocr.py) 실재하는 입력이다.
    """
    job_id = _seed_job(db_conn)
    _seed_pair(db_conn, job_id, 0, "  라이닝1조  ")
    ItemRepository().ensure_exists("라이닝1조")

    assert _diverged(db_conn) == []


def test_diagnostic_groups_whitespace_variants_and_orders_by_pairs(db_conn):
    """공백 변형은 한 라벨로 묶이고 쌍 수 내림차순으로 보고된다.

    런북이 굵게 경고한 `GROUP BY` 별칭 불변식을 고정한다 — 별칭을 `canonical_label`로 두면
    MySQL이 `GROUP BY`를 원문 컬럼으로 해석해 변형마다 그룹이 쪼개진다
    (회귀 시 `[('라이닝1조', 1), ('라이닝1조', 1), ('휠', 1)]`). `pairs` 컬럼과
    `ORDER BY pairs DESC`도 이 단언이 함께 고정한다.
    """
    job_id = _seed_job(db_conn)
    _seed_pair(db_conn, job_id, 0, "라이닝1조")
    _seed_pair(db_conn, job_id, 1, " 라이닝1조 ")
    _seed_pair(db_conn, job_id, 2, "휠")

    assert _diverged_rows(db_conn) == [("라이닝1조", 2), ("휠", 1)]


def test_diagnostic_reports_label_when_only_padded_variant_is_registered(db_conn):
    """사전에 패딩 변형만 있으면 트림형 라벨은 발산으로 보고돼야 한다.

    등록(`ItemRepository.ensure_exists`의 ON DUPLICATE KEY UPDATE)이 "이미 있음"을 판정하는
    기준은 `item_suggestions.item_name`의 유니크 인덱스(utf8mb4_0900_ai_ci, NO PAD)다.
    진단이 PAD SPACE인 utf8mb4_unicode_ci로 비교하면 '휠 '과 '휠'이 같아져, 등록 쪽에서는
    별개 항목인 발산이 0행으로 숨는다. 뒤 공백이 붙은 이름은 `POST/PUT /api/items`가
    item_name을 strip하지 않으므로 사람이 실제로 넣을 수 있다.
    """
    job_id = _seed_job(db_conn)
    _seed_pair(db_conn, job_id, 0, "휠")
    ItemRepository().insert({"item_name": "휠 "})

    assert _diverged(db_conn) == ["휠"]


def test_diagnostic_normalization_matches_python_strip(db_conn):
    """SQL 정규화가 서비스의 .strip()과 같은 문자 집합을 지운다.

    MySQL TRIM()은 ASCII 스페이스만 지우므로 탭·U+3000이 붙은 라벨에서 등록과 진단이 갈린다.
    정규화를 REGEXP_REPLACE(…, '^[[:space:]]+|[[:space:]]+$', '')로 맞춘 것을 고정한다.
    """
    job_id = _seed_job(db_conn)
    for i, label in enumerate(["\t중고", "　휠", "  배선수리  "]):
        _seed_pair(db_conn, job_id, i, label)
        ItemRepository().ensure_exists(label.strip())

    assert _diverged(db_conn) == []
