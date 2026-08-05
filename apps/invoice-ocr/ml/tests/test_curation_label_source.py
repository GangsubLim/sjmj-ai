"""tools.curation_label_source 단위테스트 (합성 TSV/dict만 — DB·ssh·paddle 비의존).

렌더 계층(`## 조작 출처` 절)의 테스트는 tests/test_curation_render.py에,
fetch 글루·캐시 가드의 테스트는 tests/test_curation_report.py에 있다.
"""

import re

import pytest

from tools.curation_enrich import CORRECTIONS_SQL
from tools.curation_label_source import (
    CANDIDATE_PICKED,
    DEFAULT_RANK_SLOTS,
    KNOWN_LABEL_SOURCES,
    LABEL_SOURCE_COLS,
    LABEL_SOURCES_SQL,
    LATEST_CORRECTION_SUBQUERY,
    MIN_RANK_SAMPLE,
    label_source_key,
    parse_label_sources_tsv,
    parse_rank,
    summarize_label_sources,
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


def _ls(label_source, ref="job-1/row-0", job_id=1):
    """label_sources 캐시 1행 합성 — parse_label_sources_tsv 출력 shape과 한 벌이다."""
    return {"job_id": job_id, "crop_ref": ref, "label_source": label_source}


def _many(values):
    """crop_ref를 자동으로 유일하게 매기며 여러 행을 만든다(조인 테스트와 shape을 맞춘다)."""
    return [_ls(v, ref=f"job-1/row-{i}") for i, v in enumerate(values)]


def test_recorded_and_unrecorded_split_and_sum_to_the_matched_rows():
    """분모 사다리의 첫 갈래 — 기록/미기록이 갈리고 합이 매칭 행 수와 같다."""
    s = summarize_label_sources(_many(["top1_kept", None, None]))
    assert (s["n_records"], s["n_recorded"], s["n_unrecorded"]) == (3, 1, 2)
    assert s["n_recorded"] + s["n_unrecorded"] == s["n_records"]


def test_every_known_source_keeps_its_row_even_at_zero():
    """0건 출처가 표에서 사라지면 "아무도 이 경로를 안 썼다"는 관측이 함께 사라진다."""
    s = summarize_label_sources(_many(["top1_kept"]))
    assert set(s["source_counts"]) == set(KNOWN_LABEL_SOURCES)
    assert s["source_counts"]["manual_typed"] == 0


def test_summarize_of_no_records_reports_an_all_zero_baseline():
    """도입 전 데이터는 label_sources 캐시가 텅 비어 전량 미기록 상태로 시작한다."""
    s = summarize_label_sources([])
    assert (s["n_records"], s["n_recorded"], s["n_unrecorded"]) == (0, 0, 0)
    assert (s["n_known"], s["n_unknown"]) == (0, 0)
    assert s["source_counts"] == dict.fromkeys(KNOWN_LABEL_SOURCES, 0)
    assert s["unknown_counts"] == {}


def test_known_and_unknown_split_and_sum_to_recorded():
    """렌더가 뺄셈으로 파생하면 파생 산술이 문자열 조립 안으로 들어간다 — 여기서 갈라 낸다."""
    s = summarize_label_sources(_many(["top1_kept", "bulk_applied", "candidate_picked:2", None]))
    assert (s["n_known"], s["n_unknown"]) == (2, 1)
    assert s["n_known"] + s["n_unknown"] == s["n_recorded"]


@pytest.mark.parametrize(
    "value",
    [
        "",
        "candidate_picked:",
        "candidate_picked:abc",
        "candidate_picked:-1",
        "candidate_picked: 3",
        None,
        "candidate_picked:²",  # 위첨자 2 — isdigit()은 True를 내지만 isdecimal()은 False
    ],
)
def test_parse_rank_and_label_source_key_return_none_for_malformed_or_missing_values(value):
    assert parse_rank(value) is None
    assert label_source_key(value) is None


def test_rank_is_parsed_from_the_candidate_picked_prefix():
    assert parse_rank("candidate_picked:3") == 3
    assert parse_rank("candidate_picked:0") == 0  # 0-based — rank 0이 곧 top-1 후보다
    assert parse_rank("top1_kept") is None


def test_rank_rows_cover_the_default_five_slots_including_zero_counts():
    """ "뒤쪽 rank에서 아무도 안 골랐다"가 곧 top-5 확대 무용의 근거다 — 0건 행을 빼지 않는다."""
    s = summarize_label_sources(_many(["candidate_picked:3"]))
    assert list(s["rank_counts"]) == [0, 1, 2, 3, 4]
    assert s["rank_counts"][3] == 1
    assert s["rank_counts"][0] == 0
    assert s["n_rank_slots"] == DEFAULT_RANK_SLOTS


def test_rank_range_grows_when_an_observed_rank_exceeds_the_default():
    """백엔드 TOP_K가 늘면 관측 rank가 기본 범위를 넘는다 — 범위가 따라 늘어난다."""
    s = summarize_label_sources(_many(["candidate_picked:7"]))
    assert list(s["rank_counts"]) == list(range(8))
    assert s["rank_counts"][7] == 1


def test_rank_counts_sum_to_the_candidate_picked_total():
    """rank 행은 candidate_picked 행의 분해다 — 합이 어긋나면 한쪽이 값을 삼킨 것이다."""
    s = summarize_label_sources(_many(["candidate_picked:0", "candidate_picked:4", "top1_kept"]))
    assert sum(s["rank_counts"].values()) == s["source_counts"][CANDIDATE_PICKED] == 2
    assert s["n_candidate_picked"] == 2


def test_unknown_sources_stay_in_the_denominator_and_surface_as_a_warning_list():
    """미지 어휘를 조용히 버리면 백엔드 어휘 확장이 ml에서 오분류로만 나타난다(spec §3-4)."""
    s = summarize_label_sources(_many(["bulk_applied", "bulk_applied", "top1_kept"]))
    assert s["n_recorded"] == 3  # 분모에 남는다
    assert s["unknown_counts"] == {"bulk_applied": 2}
    assert sum(s["source_counts"].values()) == 1  # 기지 어휘 집계에는 들어가지 않는다


def test_a_bare_candidate_picked_without_a_rank_counts_as_unknown():
    """rank 없는 `candidate_picked`는 백엔드 화이트리스트가 허용하지 않는 값이다.

    기지로 세면 rank 행 합과 candidate_picked 건수가 어긋나 표가 스스로 모순된다.
    """
    s = summarize_label_sources(_many(["candidate_picked"]))
    assert s["source_counts"][CANDIDATE_PICKED] == 0
    assert s["unknown_counts"] == {"candidate_picked": 1}


def test_min_rank_sample_is_a_named_constant():
    """하한을 렌더에 인라인하면 근거 주석이 사라지고 두 곳이 갈라진다."""
    assert MIN_RANK_SAMPLE == 10
