"""tools.curation_label_source 단위테스트 (합성 TSV/dict만 — DB·ssh·paddle 비의존).

렌더 계층(`## 조작 출처` 절)의 테스트는 tests/test_curation_render.py에,
fetch 글루·캐시 가드의 테스트는 tests/test_curation_report.py에 있다.
"""

import re

import pytest

from tools.curation_enrich import CORRECTIONS_SQL
from tools.curation_label_source import (
    LABEL_SOURCE_COLS,
    LABEL_SOURCES_SQL,
    LATEST_CORRECTION_SUBQUERY,
    parse_label_sources_tsv,
)

_HEADER = "job_id\tcrop_ref\tlabel_source\n"


def test_parses_rows_and_folds_null_into_none():
    text = (
        _HEADER + "1\tjob-1/row-0\ttop1_kept\n" + "1\tjob-1/row-1\tNULL\n" + "1\tNULL\ttop1_kept\n"
    )
    assert parse_label_sources_tsv(text) == [
        {"job_id": 1, "crop_ref": "job-1/row-0", "label_source": "top1_kept"},
        # JSON null과 키 부재는 둘 다 SQL NULL로 와서 "미기록" 하나로 접힌다.
        {"job_id": 1, "crop_ref": "job-1/row-1", "label_source": None},
        # crop_ref도 같은 _cell 접힘을 통과한다(label_source만 NULL 접힘 대상이 아니다).
        {"job_id": 1, "crop_ref": None, "label_source": "top1_kept"},
    ]


def test_empty_output_is_an_empty_list():
    assert parse_label_sources_tsv("") == []


def test_rejects_a_header_that_drifted_from_the_column_ssot():
    """SELECT 순서만 바뀌어도 위치 인덱싱은 예외 없이 조용히 뒤바뀐 값을 낸다(파서 방어)."""
    text = "job_id\tlabel_source\tcrop_ref\n1\ttop1_kept\tjob-1/row-0\n"
    with pytest.raises(ValueError) as err:
        parse_label_sources_tsv(text)
    assert "헤더" in str(err.value)


def test_rejects_a_row_with_the_wrong_column_count():
    with pytest.raises(ValueError) as err:
        parse_label_sources_tsv(_HEADER + "1\tjob-1/row-0\n")
    assert "컬럼 수" in str(err.value)


def test_both_queries_pick_the_same_correction_row_per_job():
    """§8 리스크1 — 행 수지 절과 조작 출처 절이 다른 확정본을 말하면 재확정 잡에서 즉시 어긋난다.

    두 SQL이 같은 상관 서브쿼리(잡별 MAX(id))를 쓰는지, 닫힌 템플릿 전체(비교 컬럼 +
    닫는 괄호까지)로 못박는다. 어느 한쪽이 선택 규칙을 바꾸면(예: ORDER BY ... LIMIT 1)
    이 테스트가 RED가 된다.
    """
    assert LATEST_CORRECTION_SUBQUERY.format(job_col="c.job_id") in LABEL_SOURCES_SQL
    assert LATEST_CORRECTION_SUBQUERY.format(job_col="j.id") in CORRECTIONS_SQL


def test_the_select_aliases_match_the_column_ssot_in_order():
    """별칭 순서 = TSV 헤더 = 파서의 위치 인덱싱. 셋을 한 튜플로 묶는다."""
    assert tuple(re.findall(r"AS (\w+)", LABEL_SOURCES_SQL)) == LABEL_SOURCE_COLS
