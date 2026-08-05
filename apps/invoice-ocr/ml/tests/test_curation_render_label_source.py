"""tools.curation_render_label_source 렌더 단위테스트 (IO 비의존, 합성 데이터만).

`## 조작 출처` 분포 절과 그 하위 `### 출처 × 품목 버킷` 교차 절만 본다 — 나머지 절의 렌더
테스트는 tests/test_curation_render.py에, 집계(파싱·분포·교차)의 테스트는
tests/test_curation_label_source.py에 있다.

두 절은 리포트 전체 조립을 통과한 결과로 단언한다(render_report 호출) — 절의 배치·앞뒤 절과의
경계도 이 절들의 계약이기 때문이다.
"""

from tests.conftest import _correction, _enriched_row
from tools.curation_cohort import DATA_INTEGRITY_FAILURE_BUCKETS, RETRIEVAL_MISS_BUCKETS
from tools.curation_label_source import ITEM_BUCKET_COLUMNS, MIN_RANK_SAMPLE
from tools.curation_render import render_report


def _render(enriched, meta, corrections=(), label_sources=()):
    """render_report 호출을 한 자리에 모은다 — 그 소스를 안 보는 테스트는 빈 목록을 준다."""
    return render_report(enriched, meta, list(corrections), label_sources=list(label_sources))


def _ls(label_source, ref="job-1/row-0"):
    """label_sources.json 1행 합성 — parse_label_sources_tsv 출력 shape과 한 벌이다."""
    return {"job_id": 1, "crop_ref": ref, "label_source": label_source}


def _ls_many(values):
    return [_ls(v, ref=f"job-1/row-{i}") for i, v in enumerate(values)]


def _label_source_section(md):
    """분포 절만 잘라낸다 — 뒤따르는 `### ` 하위 절이 `\n## `에 안 걸려 함께 딸려온다."""
    return md.split("## 조작 출처")[1].split("\n## ")[0].split("\n### ")[0]


# --- 조작 출처 분포 절 (#73 spec §3-5) ---


def test_label_source_section_sits_between_the_row_balance_and_the_key_metrics():
    """행 수지와 같은 교정 이력 소스를 쓰고 분모 사다리가 이어지므로 붙어 있어야 읽힌다."""
    md = _render(
        [], {"fetched_at": "t"}, [_correction(job_id=1, n_lines=1)], _ls_many(["top1_kept"])
    )
    assert md.index("## 행 수지") < md.index("## 조작 출처") < md.index("## 핵심 지표")


def test_label_source_ladder_puts_the_dropped_and_added_rows_outside_the_denominator():
    """폐기·추가 행은 lines[]에 없어 조작 출처가 존재하지 않는다 — 분포의 분모 밖이다."""
    md = _render(
        [],
        {"fetched_at": "t"},
        [_correction(job_id=1, n_lines=3, rows_added=4, rows_dropped=2)],
        _ls_many(["top1_kept", None, None]),
    )
    section = _label_source_section(md)
    assert "초안 5행 = 매칭 3 + 사람 폐기 2" in section
    assert "확정 7행 = 매칭 3 + 사람 추가 4" in section
    assert "매칭 3 → 기록 있음 1 / 미기록 2" in section
    assert "분모 밖" in section


def test_label_source_ladder_refuses_to_assert_a_sum_when_a_job_balance_is_unknown():
    """#72 불변식 — 수지 미상 잡을 0으로 접지 않는다. 폐기·추가는 `?`, 합은 단정하지 않는다."""
    md = _render(
        [],
        {"fetched_at": "t"},
        [_correction(job_id=1, n_lines=2), _correction(job_id=2, n_lines=None)],
        _ls_many(["top1_kept", None]),
    )
    section = _label_source_section(md)
    assert "초안 ?행 = 매칭 2 + 사람 폐기 ?" in section
    assert "확정 ?행 = 매칭 2 + 사람 추가 ?" in section
    assert "행 수지 미상 1잡" in section
    assert "`## 행 수지` 절은 같은 상황에서" in section


def test_label_source_table_keeps_every_rank_row_including_zero_counts():
    md = _render(
        [],
        {"fetched_at": "t"},
        [_correction(job_id=1, n_lines=2)],
        _ls_many(["candidate_picked:3", "top1_kept"]),
    )
    section = _label_source_section(md)
    assert "| candidate_picked | 1 | 50.0% |" in section
    assert "| └ rank 0 | 0 | 0.0% |" in section
    assert "| └ rank 3 | 1 | 50.0% |" in section
    assert "| └ rank 4 | 0 | 0.0% |" in section
    # └ 행은 candidate_picked의 분해다 — 다른 출처 밑으로 옮겨가면 표가 다른 뜻이 된다.
    assert section.index("| candidate_picked |") < section.index("| └ rank 0 |")
    assert section.index("| └ rank 4 |") < section.index("| manual_picked |")


def test_label_source_table_extends_the_rank_rows_past_the_default_range():
    md = _render(
        [],
        {"fetched_at": "t"},
        [_correction(job_id=1, n_lines=1)],
        _ls_many(["candidate_picked:6"]),
    )
    assert "| └ rank 6 | 1 | 100.0% |" in _label_source_section(md)


def test_label_source_section_warns_when_an_observed_rank_exceeds_the_default_range():
    """§3-4의 "리포트가 곧 드리프트 탐지기"를 candidate_picked 축에서도 성립시킨다."""
    md = _render(
        [],
        {"fetched_at": "t"},
        [_correction(job_id=1, n_lines=1)],
        _ls_many(["candidate_picked:6"]),
    )
    section = _label_source_section(md)
    assert "관측 rank가 기본 범위를 넘었다" in section
    assert "TOP_K" in section


def test_label_source_section_stays_quiet_when_every_rank_is_inside_the_default_range():
    """음성 테스트 — 계약 범위 안에서 경고가 뜨면 "확대됐다" 신호가 무의미해진다."""
    md = _render(
        [],
        {"fetched_at": "t"},
        [_correction(job_id=1, n_lines=1)],
        _ls_many(["candidate_picked:4"]),
    )
    assert "관측 rank가 기본 범위를 넘었다" not in _label_source_section(md)


def test_label_source_section_warns_below_the_rank_sample_floor():
    md = _render(
        [],
        {"fetched_at": "t"},
        [_correction(job_id=1, n_lines=1)],
        _ls_many(["candidate_picked:1"]),
    )
    section = _label_source_section(md)
    assert f"후보 칩 선택 표본 1건(하한 {MIN_RANK_SAMPLE})" in section
    assert "판단 근거가 되지 못한다" in section


def test_label_source_section_stays_quiet_once_the_rank_sample_floor_is_met():
    """음성 테스트 — 하한 이상에서도 경고가 붙으면 "아직 아니다" 신호가 무의미해진다."""
    values = [f"candidate_picked:{i % 5}" for i in range(MIN_RANK_SAMPLE)]
    md = _render(
        [], {"fetched_at": "t"}, [_correction(job_id=1, n_lines=len(values))], _ls_many(values)
    )
    assert "하한" not in _label_source_section(md)


def test_label_source_section_names_unknown_sources_and_says_they_are_in_the_denominator():
    md = _render(
        [],
        {"fetched_at": "t"},
        [_correction(job_id=1, n_lines=3)],
        _ls_many(["bulk_applied", "bulk_applied", "top1_kept"]),
    )
    section = _label_source_section(md)
    assert "알 수 없는 조작 출처 1종 2건: bulk_applied(2)" in section
    assert "app/schemas/ocr.py" in section
    assert "분모에는 포함되어 있다" in section
    # 표에 행이 없으면 "분모에 있다"는 경고와 표가 서로를 부정한다 — 행까지 함께 못박는다.
    assert "| bulk_applied (미지) | 2 | 66.7% |" in section


def test_label_source_section_stays_quiet_about_unknown_vocabulary_when_every_source_is_known():
    """음성 테스트 — 어휘가 전부 기지면 미지 어휘 경고가 뜨지 않는다(rank·표본 하한 경고의 짝)."""
    md = _render(
        [],
        {"fetched_at": "t"},
        [_correction(job_id=1, n_lines=2)],
        _ls_many(["top1_kept", "manual_picked"]),
    )
    assert "알 수 없는 조작 출처" not in _label_source_section(md)


def test_label_source_section_flags_a_matched_row_count_that_diverges_from_the_row_balance():
    """§8 리스크1의 런타임 관측 — 두 절이 다른 교정 행을 읽으면 매칭 행 수부터 어긋난다."""
    md = _render(
        [], {"fetched_at": "t"}, [_correction(job_id=1, n_lines=5)], _ls_many(["top1_kept"])
    )
    # 두 수를 뒤바꿔 적어도 통과하지 않도록 문장 전체를 못박는다 — 진단이 거꾸로 읽히면 안 된다.
    assert (
        "매칭 행 수가 행 수지 절과 다르다: 조작 출처 1행 vs 교정 이력 n_lines 5행"
        in _label_source_section(md)
    )


def test_label_source_section_stays_quiet_when_the_two_sources_agree():
    md = _render(
        [], {"fetched_at": "t"}, [_correction(job_id=1, n_lines=1)], _ls_many(["top1_kept"])
    )
    assert "매칭 행 수가 행 수지 절과 다르다" not in _label_source_section(md)


def test_label_source_ladder_prints_the_mismatch_warning_right_after_the_ladder():
    """M1 — 성립하지 않는 등식을 먼저 단정형으로 읽고 한참 뒤에야 경고를 보면 자기 규약
    (모르는 것을 말하지 않는다)에 어긋난다. 경고는 사다리(코드블록) 직후·설명 문단보다 앞에 온다.
    """
    md = _render(
        [], {"fetched_at": "t"}, [_correction(job_id=1, n_lines=5)], _ls_many(["top1_kept"])
    )
    section = _label_source_section(md)
    ladder_end = section.index("```", section.index("```text") + len("```text"))
    warning_pos = section.index("⚠ 매칭 행 수가 행 수지 절과 다르다")
    explanation_pos = section.index("매칭 행은 **잡별 최신")
    assert ladder_end < warning_pos < explanation_pos


def test_label_source_table_prints_a_dash_not_zero_percent_when_nothing_was_recorded():
    """도입 전 데이터는 전량 미기록이라 분모가 0이다 — 그때 '측정 안 됨'을 '측정했는데 0%'로
    인쇄하면 현행 운영 상태를 정반대로 읽는다. n_lines=0으로 두어 행 수지 어긋남 경고가
    기준선에 섞이지 않게 한다.
    """
    section = _label_source_section(
        _render([], {"fetched_at": "t"}, [_correction(job_id=1, n_lines=0)], [])
    )
    assert "| top1_kept | 0 | — |" in section
    assert "| └ rank 0 | 0 | — |" in section


# --- 출처 × 품목 버킷 교차 절 (#73 spec §3-6) ---


def _cross_section(md):
    """교차 하위 절만 잘라낸다 — 다음 `## ` 제목(핵심 지표)까지가 이 절이다."""
    return md.split("### 출처 × 품목 버킷")[1].split("\n## ")[0]


def _cross_fixture():
    """평가 가능 1 · 학습 제외 1 · 시점 판정 불가 1 · 학습쌍 없음 1의 비대칭 표본."""
    enriched = [
        _enriched_row(crop_ref="job-1/row-0", label_bucket="top5_only", top1_sim=0.5),
        _enriched_row(crop_ref="job-1/row-1", status="excluded", label_bucket="ok"),
        _enriched_row(crop_ref="job-1/row-2", label_bucket="unevaluable"),
    ]
    label_sources = [
        _ls("candidate_picked:1", ref="job-1/row-0"),
        _ls("top1_kept", ref="job-1/row-1"),
        _ls("top1_kept", ref="job-1/row-2"),
        _ls("top1_kept", ref="job-1/row-9"),
    ]
    return enriched, label_sources


def _cross_render():
    enriched, label_sources = _cross_fixture()
    return _cross_section(
        _render(enriched, {"fetched_at": "t"}, [_correction(job_id=1, n_lines=4)], label_sources)
    )


def test_cross_section_spells_out_the_ladder_from_recorded_to_evaluable():
    assert (
        "기록 4 → 학습쌍 없음 -1 → 학습 제외 -1 → 시점 판정 불가 -1 → 정합 장애 -0 → 평가 가능 1"
        in _cross_render()
    )


def test_cross_ladder_keeps_the_two_unevaluable_axes_as_separate_terms():
    """시점 판정 불가와 데이터 정합 장애는 후속 조치가 다르다 — 한 항으로 합치면 구분이 사라진다."""
    enriched = [
        _enriched_row(crop_ref="job-1/row-0", label_bucket="unevaluable"),
        _enriched_row(crop_ref="job-1/row-1", label_bucket="row_missing"),
    ]
    section = _cross_section(
        _render(
            enriched,
            {"fetched_at": "t"},
            [_correction(job_id=1, n_lines=2)],
            _ls_many(["top1_kept", "top1_kept"]),
        )
    )
    assert (
        "기록 2 → 학습쌍 없음 -0 → 학습 제외 -0 → 시점 판정 불가 -1 → 정합 장애 -1 → 평가 가능 0"
        in section
    )


def test_cross_table_uses_the_existing_bucket_vocabulary_as_columns():
    section = _cross_render()
    # 표 행은 한 줄 통째로 단언한다 — 칸을 따로 보면 열 순서가 뒤바뀌어도 통과한다.
    assert "| 출처 | ok | top5_only | in_bank_miss | out_of_bank | no_candidates |" in section
    assert "| candidate_picked | 0 | 1 | 0 | 0 | 0 |" in section


def test_cross_table_keeps_every_known_source_row_even_at_zero():
    """0건 행을 빼면 "이 출처는 평가 가능 행이 하나도 없었다"는 관측이 표에서 사라진다."""
    assert "| top1_kept | 0 | 0 | 0 | 0 | 0 |" in _cross_render()


def test_cross_headline_reports_candidate_picks_among_evaluable_top1_misses():
    """AC 3 — 학습 제외·판정 불가 쌍이 분모·분자 어디에도 새지 않는다."""
    assert "top-1 미적중인데 후보 칩에서 고름: 1/1 (100.0%)" in _cross_render()


def test_cross_headline_pairs_the_wide_denominator_with_the_retrieval_narrowed_one():
    """넓은 분모만 적으면 후보 칩이 구조적으로 도울 수 없는 미스까지 섞여 비율이 과소평가된다."""
    enriched = [
        _enriched_row(crop_ref="job-1/row-0", label_bucket="top5_only", top1_sim=0.5),
        _enriched_row(crop_ref="job-1/row-1", label_bucket="out_of_bank"),
    ]
    label_sources = [
        _ls("candidate_picked:1", ref="job-1/row-0"),
        _ls("manual_typed", ref="job-1/row-1"),
    ]
    section = _cross_section(
        _render(enriched, {"fetched_at": "t"}, [_correction(job_id=1, n_lines=2)], label_sources)
    )
    assert "top-1 미적중인데 후보 칩에서 고름: 1/2 (50.0%)" in section
    assert "└ 정답이 뱅크에 있던 미스 한정: 1/1 (100.0%)" in section
    assert "out_of_bank(정답이 뱅크에 없음)" in section
    assert "과소평가" in section


def test_cross_section_follows_the_distribution_section():
    enriched, label_sources = _cross_fixture()
    md = _render(enriched, {"fetched_at": "t"}, [_correction(job_id=1, n_lines=4)], label_sources)
    assert md.index("## 조작 출처") < md.index("### 출처 × 품목 버킷") < md.index("## 핵심 지표")


def test_cross_headline_two_lines_sit_inside_a_text_fence():
    """M3 — 펜스 밖에 두면 인접한 두 줄이 GFM soft break로 한 문단에 병합돼 └가 문장 중간에
    박힌다(`curation_render._render_cohort_table`·`_render_bank_candidates`가 같은 함정을
    이미 주석으로 못박은 관용구를 여기서도 따른다)."""
    section = _cross_render()
    headline_pos = section.index("top-1 미적중인데 후보 칩에서 고름")
    followup_pos = section.index("└ 정답이 뱅크에 있던 미스 한정")
    fence_open = section.rindex("```text", 0, headline_pos)
    fence_close = section.index("```", followup_pos)
    assert fence_open < headline_pos < followup_pos < fence_close


def test_cross_headline_lines_state_their_denominator_inline():
    """문단까지 읽지 않아도 첫 줄이 AC 수치(넓은 분모), 둘째 줄이 보조 지표(좁힌 분모)임을
    구분할 수 있어야 한다."""
    section = _cross_render()
    headline_line = next(
        line for line in section.split("\n") if line.startswith("top-1 미적중인데 후보 칩에서 고름")
    )
    followup_line = next(
        line for line in section.split("\n") if line.startswith("└ 정답이 뱅크에 있던 미스 한정")
    )
    assert "AC 수치" in headline_line
    assert "리트리벌 미스" in followup_line


def test_ladder_legend_names_every_data_integrity_failure_bucket():
    """M2 — 범례가 손으로 적는 값이 상수를 벗어나면 버킷 추가 시 조용히 옛말이 남는다."""
    section = _cross_render()
    legend = section.split("```text")[1].split("```")[0]
    for bucket in DATA_INTEGRITY_FAILURE_BUCKETS:
        assert bucket in legend


def test_cross_headline_explanation_names_every_bucket_outside_the_retrieval_miss_set():
    """M2 — headline 뒤 설명이 손으로 적는 값이 RETRIEVAL_MISS_BUCKETS의 여집합(out_of_bank·
    no_candidates)을 벗어나면 버킷 추가 시 조용히 옛말이 남는다."""
    section = _cross_render()
    explanation = section.split("위 넓은 분모에는")[1]
    non_retrieval_miss_buckets = set(ITEM_BUCKET_COLUMNS) - {"ok"} - set(RETRIEVAL_MISS_BUCKETS)
    for bucket in non_retrieval_miss_buckets:
        assert bucket in explanation


def test_cross_table_rows_follow_the_distribution_order_with_unknowns_last():
    """두 표의 행 순서가 다르면 같은 출처의 두 행을 잇는 교차 검산이 매번 스캔이 된다."""
    enriched = [_enriched_row(crop_ref=f"job-1/row-{i}", label_bucket="ok") for i in range(3)]
    label_sources = [
        _ls("bulk_applied", ref="job-1/row-0"),
        _ls("manual_typed", ref="job-1/row-1"),
        _ls("top1_kept", ref="job-1/row-2"),
    ]
    section = _cross_section(
        _render(enriched, {"fetched_at": "t"}, [_correction(job_id=1, n_lines=3)], label_sources)
    )
    assert section.index("| top1_kept |") < section.index("| manual_typed |")
    assert section.index("| manual_typed |") < section.index("| bulk_applied (미지) |")


def test_cross_table_marks_an_unknown_row_so_it_reads_apart_from_the_known_vocabulary():
    """분포 절의 표는 미지 값에 `(미지)`를 붙이는데(§3-4), 교차표만 침묵하면 같은 리포트 안에서
    두 표가 미지 취급을 다르게 한 것처럼 읽힌다."""
    enriched = [_enriched_row(crop_ref="job-1/row-0", label_bucket="ok")]
    label_sources = [_ls("bulk_applied", ref="job-1/row-0")]
    section = _cross_section(
        _render(enriched, {"fetched_at": "t"}, [_correction(job_id=1, n_lines=1)], label_sources)
    )
    assert "| bulk_applied (미지) | 1 | 0 | 0 | 0 | 0 |" in section


def test_cross_table_does_not_mark_a_known_row_as_unknown():
    """음성 테스트 — 기지 출처 행에 `(미지)`가 붙으면 표시 자체가 신호를 잃는다."""
    enriched = [_enriched_row(crop_ref="job-1/row-0", label_bucket="ok")]
    label_sources = [_ls("top1_kept", ref="job-1/row-0")]
    section = _cross_section(
        _render(enriched, {"fetched_at": "t"}, [_correction(job_id=1, n_lines=1)], label_sources)
    )
    assert "| top1_kept (미지) |" not in section
    assert "| top1_kept | 1 | 0 | 0 | 0 | 0 |" in section
