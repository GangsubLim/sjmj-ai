"""tools.curation_render 렌더 계층 단위테스트 (IO 비의존, 합성 데이터만).

분석 계층(파싱·버킷·조인·집계)의 테스트는 tests/test_curation_enrich.py에,
fetch 글루·CLI(_failure_job_ids·_load_enriched 등)의 테스트는 tests/test_curation_report.py에 있다.
"""

from tests.conftest import (  # 합성 헬퍼는 분석 계층 테스트와 공유한다
    _CUR_VERSION,
    _correction,
    _enrich,
    _enriched_row,
    _job,
    _pair,
    _row,
)
from tools.curation_cohort import (
    ITEM_EVALUABLE_COHORTS,
    PAIR_COHORTS,
    REEVAL_REJECT_REASONS,
    REEVAL_STATES,
)
from tools.curation_render import (
    _REEVAL_REJECT_TEXT,
    COHORT_TABLE,
    reeval_notice,
    render_report,
)


def _render(enriched, meta, corrections=()):
    """render_report 호출을 한 자리에 모은다 — 교정 이력을 안 보는 테스트는 빈 목록을 준다."""
    return render_report(enriched, meta, list(corrections))


def test_report_header_states_the_confirmed_job_population():
    """AC — 기존 `잡 N개`(쌍 보유)의 바깥 경계를 옆에 적는다. K>0이면 눈먼 잡이 있다는 신호다."""
    rows = [_enriched_row(label_bucket="ok", top1_sim=0.9)]  # job_id=1
    corrections = [_correction(job_id=1), _correction(job_id=57, n_lines=0, rows_added=9)]
    md = _render(rows, {"fetched_at": "t"}, corrections)
    # 뒤쪽 " · included"까지 붙여 단언한다 — 이음매의 구분자를 지우는 뮤테이션도 여기서 잡는다(L3).
    assert "확정 잡 2개(쌍 보유 1 / 쌍 0개 1) · included" in md


def test_report_header_pair_ownership_counts_the_whole_pair_not_just_included():
    """M1 — "쌍 보유"는 included만이 아니라 excluded까지 포함한 전체 쌍 기준이다.

    배제쌍만 있는 잡(`test_render_report_handles_job_with_only_excluded_pairs`가 실재 상태로
    인정)을 included 기준으로 좁히면 "쌍 0개"로 잘못 계상돼, 헤더가 존재하지 않는 눈먼 잡을
    신호로 올린다.
    """
    enriched = _enrich([_pair(status="excluded")], [_job(rows=[])])
    md = _render(enriched, {"fetched_at": "t"}, [_correction(job_id=1)])
    assert "확정 잡 1개(쌍 보유 1 / 쌍 0개 0)" in md


def test_render_report_handles_job_with_only_excluded_pairs():
    # `"excluded" in md`로는 아무것도 확인되지 않는다 — 머리말의 "excluded {n}쌍"과 표본
    # 구성표의 `| excluded |` 행은 배제 절이 통째로 사라져도, 입력이 비어도 늘 찍힌다.
    # 이 쌍은 사유가 없으므로 사람 배제 절 **안에** 그 crop_ref가 있는지까지 본다.
    pairs = [_pair(status="excluded")]
    enriched = _enrich(pairs, [_job(rows=[_row(top5=[("엔진오일", 0.9)])])])
    md = _render(enriched, {"fetched_at": "t"})
    section = "## excluded — 사람 배제"
    assert section in md
    assert "job-1/row-0" in md.split(section)[1]


def test_render_report_splits_excluded_sections():
    # 기계배제 2 (row-0, row-1) / 사람배제 1 (row-2)로 비대칭을 두고, 각 crop_ref가
    # 어느 섹션 "아래"에 찍히는지(섹션 경계로 분할한 뒤 부분 문자열 검사)까지 확인한다.
    # 두 리스트 필터가 뒤바뀌어도 헤더 존재만 보면 통과했을 취약점을 막는다.
    pairs = [
        _pair(id=1, crop_ref="job-1/row-0", status="excluded", exclusion_reason="blank_crop"),
        _pair(
            id=2,
            crop_ref="job-1/row-1",
            row_index=1,
            status="excluded",
            exclusion_reason="blank_crop",
        ),
        _pair(id=3, crop_ref="job-1/row-2", row_index=2, status="excluded", exclusion_reason=None),
    ]
    md = _render(_enrich(pairs, [_job(rows=[])]), {"fetched_at": "t"})
    machine_section = md.split("## excluded — 기계 자동 배제")[1].split("## excluded — 사람 배제")[
        0
    ]
    human_section = md.split("## excluded — 사람 배제")[1]
    # 머리말의 기계/사람 분해는 이 파일이 아니면 아무도 보지 않는다(수치 자체는
    # test_curation_enrich가, 절 분할은 아래가 본다) — 머리말에서 둘이 뒤바뀌어도 GREEN이었다.
    assert "excluded 3쌍(기계 2 / 사람 1)" in md
    assert "job-1/row-0" in machine_section
    assert "job-1/row-1" in machine_section
    assert "job-1/row-2" not in machine_section
    assert "job-1/row-2" in human_section
    assert "job-1/row-0" not in human_section
    assert "job-1/row-1" not in human_section


def test_render_report_shows_reverted_section():
    pairs = [_pair(id=1, crop_ref="job-1/row-0", status="included", exclusion_reason="blank_crop")]
    md = _render(_enrich(pairs, [_job(rows=[])]), {"fetched_at": "t"})
    section = "## included — 기계 자동 배제를 사람이 되돌림"
    assert section in md
    assert "job-1/row-0" in md.split(section)[1]


def test_render_report_hides_reverted_section_when_none_reverted():
    # 되돌림 0건이면 섹션 자체가 없어야 한다 — 지운 것과 동치인 RED를 방지하는 음성 테스트.
    pairs = [_pair(id=1, crop_ref="job-1/row-0", status="included", exclusion_reason=None)]
    rows = [_row(top5=[("엔진오일", 0.9)])]
    md = _render(_enrich(pairs, [_job(rows=rows)]), {"fetched_at": "t"})
    assert "기계 자동 배제를 사람이 되돌림" not in md


def test_render_report_shows_machine_exclusion_false_positive_rate():
    # 분모(기계 판정 총계 = 기계배제 + 되돌림) 없이 되돌림 절대수만 찍으면
    # 리포트 간 비교로 임계를 조정하려는 사람이 오판할 수 있다(M3).
    #
    # 사람 배제를 섞어 분모 후보를 갈라놓는다 — 되돌림 1 · 기계배제 2 · 사람배제 2이므로
    # 올바른 분모는 3(1/3)이고, 흔한 오답인 "전체 배제"는 4(1/4), "전체 배제 + 되돌림"은
    # 5(1/5)로 서로 다른 값이 된다(사람 배제가 없으면 셋 중 둘이 1/3으로 겹쳐 구분되지 않는다).
    pairs = [
        _pair(id=1, crop_ref="job-1/row-0", status="included", exclusion_reason="blank_crop"),
        _pair(
            id=2,
            crop_ref="job-1/row-1",
            row_index=1,
            status="excluded",
            exclusion_reason="blank_crop",
        ),
        _pair(
            id=3,
            crop_ref="job-1/row-2",
            row_index=2,
            status="excluded",
            exclusion_reason="blank_crop",
        ),
        _pair(id=4, crop_ref="job-1/row-3", row_index=3, status="excluded", exclusion_reason=None),
        _pair(id=5, crop_ref="job-1/row-4", row_index=4, status="excluded", exclusion_reason=None),
    ]
    md = _render(_enrich(pairs, [_job(rows=[])]), {"fetched_at": "t"})
    # 라벨과 비율을 따로 보면 지표 행이 서로 뒤바뀌어도 통과한다 — 한 문자열로 못박는다.
    assert "| 빈 크롭 가드 오탐(되돌림/기계 판정) | 1/3 (33.3%) |" in md


def test_render_report_smoke_contains_key_sections():
    pairs = [
        _pair(),
        _pair(
            id=2,
            crop_ref="job-1/row-1",
            row_index=1,
            final_label="안가방",
            canonical_label="안가방",
        ),
    ]
    rows = [_row(top5=[("엔진오일", 0.9)]), _row(idx=1, top5=[("드라이", 0.7)], supply=0, raw="0")]
    enriched = _enrich(pairs, [_job(rows=rows)])
    md = _render(enriched, {"fetched_at": "2026-07-27T00:00:00", "bank_distinct": 4})
    assert "핵심 지표" in md
    assert "뱅크 추가 후보" in md
    assert "안가방" in md
    assert "잡별" in md


def test_render_report_shows_evaluable_denominators_and_no_none_sim_crash():
    rows = [
        _enriched_row(label_bucket="unevaluable", top1_sim=None),
        _enriched_row(
            crop_ref="job-1/row-1",
            label_bucket="in_bank_miss",
            top1_sim=0.7,
            top5_labels=["공임"],
        ),
    ]
    md = _render(rows, {"fetched_at": "t"})
    assert "| 품목 top-1 (평가 가능 쌍 기준) | 0/1 (0.0%) |" in md  # 분모가 2가 아니라 1
    assert "| 1 | 2 | 0/1 |" in md  # 잡별 top-1은 k/n 표기
    assert "job-1/row-1" in md  # 미스 목록엔 평가 가능 쌍만
    assert "job-1/row-0" not in md.split("## in-bank 리트리벌 미스")[1].split("##")[0]


def test_misses_list_prints_the_judged_answer_not_just_final_label():
    """H1 회귀 — 버킷은 answer(canonical_label)로 매겨지므로 판정에 쓴 값을 함께 인쇄해야
    한다. final만 찍으면 top5에 없는 라벨이 '정답'으로 읽혀 자기모순이 된다.
    """
    row = _enriched_row(
        answer="엔진오일",
        final_label="안가방",
        label_bucket="top5_only",
        top5_labels=["드라이", "엔진오일"],
        top1_sim=0.7,
    )
    md = _render([row], {"fetched_at": "t"})
    misses = md.split("## in-bank 리트리벌 미스")[1].split("##")[0]
    assert "answer='엔진오일'" in misses


def test_pairs_without_a_reachable_peer_are_kept_out_of_the_retrieval_miss_list():
    """구조적 도달 불가를 '리트리벌 실패'로 사람에게 보내지 않는다(전표 축 제외의 귀결)."""
    rows = [
        _enriched_row(
            cohort="reevaluated",
            label_bucket="in_bank_miss",
            reeval_has_peer=False,
            top1_sim=0.4,
        ),
        _enriched_row(
            crop_ref="job-1/row-1",
            cohort="reevaluated",
            label_bucket="in_bank_miss",
            reeval_has_peer=True,
            top1_sim=0.5,
        ),
    ]
    md = _render(rows, {"fetched_at": "t"})
    misses = md.split("## in-bank 리트리벌 미스")[1].split("##")[0]
    assert "job-1/row-1" in misses and "job-1/row-0" not in misses
    assert "도달 불가" in md  # 제외 건수를 공개한다


# --- 리포트 구조 (표본 구성표를 핵심 지표 위에 — spec §3-C) ---


def test_reeval_notice_reports_an_adopted_reevaluation():
    meta = {
        "reeval": {
            "state": "present",
            "adopted": True,
            "reason": None,
            "generated_at": "2026-07-30T05:12:00+09:00",
            "after": "a1b2c3d4e5f6",
            "scope": "all",
            "n_pairs": 44,
        }
    }
    line = reeval_notice(meta)
    assert "a1b2c3d4e5f6" in line and "현재와 일치" in line and "44" in line


def test_reeval_notice_never_prints_a_literal_none_for_a_damaged_score_meta():
    """`.get(key, '?')` 폴백은 발화할 수 없다 — 생산자(`_reeval_info`)가 키를 항상 만들되
    None으로 시드하므로 `get`은 기본값이 아니라 저장된 None을 돌려준다.

    그래서 손상·구버전 score_meta.json이 리터럴 "None"을 진짜 값처럼 인쇄했다(모르는 것을
    말하지 않는다는 이 모듈의 원칙 위반). 동시에 n_pairs 0은 유효한 관측치이므로 '?'로
    뭉개면 안 된다 — truthiness 폴백으로 고치는 것을 막는 반대쪽 경계다.
    """
    meta = {
        "reeval": {
            "state": "present",
            "adopted": True,
            "reason": None,
            "generated_at": None,
            "after": None,
            "scope": None,
            "n_pairs": 0,
        }
    }
    line = reeval_notice(meta)
    assert "None" not in line
    assert line.count("?") == 3  # generated_at·after·scope 셋만 미상이다
    assert "표본 0쌍" in line


def test_reeval_notice_explains_absent_output():
    assert "재평가 없음" in reeval_notice({"reeval": {"state": "absent", "adopted": False}})
    assert "score --scope all" in reeval_notice({"reeval": {"state": "absent", "adopted": False}})


def test_reeval_notice_explains_a_score_jsonl_without_meta():
    # 사용자가 재평가를 돌렸다고 착각하지 않도록 정상 경로임을 한 줄로 알린다(§3-C).
    # state 철자는 게이트 사유와 공유한다(no_meta) — 같은 조건을 두 이름으로 부르지 않는다(M2).
    line = reeval_notice({"reeval": {"state": "no_meta", "adopted": False}})
    assert "score_meta.json" in line


def test_reeval_notice_explains_a_discarded_stale_reevaluation():
    line = reeval_notice({"reeval": {"state": "present", "adopted": False, "reason": "stale"}})
    assert "폐기" in line


def test_reeval_notice_uses_the_reason_when_the_state_key_is_missing():
    """H1 회귀 — state 기본값을 absent로 두면 사유가 손에 있는데도 '산출물이 없다'고 단정한다.

    그 단정은 이 절이 막으려던 오인(사용자가 원인을 모른 채 엉뚱한 조치를 함)을 그대로 만든다.
    """
    line = reeval_notice({"reeval": {"adopted": False, "reason": "stale"}})
    assert "폐기" in line
    assert "산출물이 없다" not in line


def test_reeval_notice_does_not_claim_absence_when_the_reason_is_unknown():
    """정보가 있는데 사유만 못 읽은 경우 — '없다'고 단정하지 않고 사유 미상으로 물러선다."""
    line = reeval_notice({"reeval": {"adopted": False, "reason": "낯선사유"}})
    assert "산출물이 없다" not in line
    assert "미상" in line


def test_reeval_notice_covers_every_reeval_state():
    """M2 — 생산자(fetch)가 낼 수 있는 state 치역 전량이 사유 미상 폴백으로 새지 않는다."""
    reason_of = {"present": "stale"}  # present인데 미채택이면 게이트 사유가 반드시 있다
    for state in REEVAL_STATES:
        line = reeval_notice(
            {"reeval": {"state": state, "adopted": False, "reason": reason_of.get(state)}}
        )
        assert line.startswith("재평가 없음"), state
        assert "미상" not in line, state


def test_reeval_notice_survives_a_meta_without_reeval_info():
    assert "재평가 없음" in reeval_notice({"fetched_at": "t"})


def test_every_reeval_reject_reason_has_display_text():
    # 새 사유 코드를 추가하고 문구를 빠뜨리면 reeval_notice가 "사유 미상"을 낸다.
    assert set(_REEVAL_REJECT_TEXT) == set(REEVAL_REJECT_REASONS)


def test_render_report_puts_the_sample_composition_above_the_core_metrics():
    rows = [
        _enriched_row(cohort="unknown", label_bucket="unevaluable"),
        _enriched_row(
            crop_ref="job-1/row-1", cohort="current_bank", label_bucket="ok", top1_sim=0.9
        ),
        _enriched_row(
            crop_ref="job-1/row-2",
            cohort="no_label",
            label_bucket="unevaluable",
            canonical_label=None,
            in_bank=False,
        ),
    ]
    md = _render(rows, {"fetched_at": "t"})
    assert md.index("## 표본 구성") < md.index("## 핵심 지표")
    assert "| unknown | 1 |" in md
    assert "| no_label | 1 |" in md
    assert "| current_bank | 1 |" in md
    # 분모는 총 3쌍이 아니라 평가 가능 1쌍(현재 라벨 텍스트 유지 — 기존 지표 표 규약).
    assert "| 품목 top-1 (평가 가능 쌍 기준) | 1/1 (100.0%) |" in md
    assert "뱅크 추가 후보는 코호트와 무관하게" in md
    assert "peer" in md  # score.md 포인터(중복 구현 회피)


def test_cohort_table_covers_every_cohort_a_pair_can_get():
    """표에 없는 코호트가 생기면 그 쌍들은 표에서 조용히 사라지고 합계가 안 맞는다.

    기대집합을 `set(COHORTS) | {"no_label"}`로 손으로 재구성하지 않는다 — `summarize`가 세는
    값은 `pair_cohort`의 치역(= PairCohort)이므로 그 진실원 상수와 직접 대조해야, 쌍 단위 치역이
    늘어날 때(예: 새 정답 부재 사유) 표만 옛말을 인쇄하는 드리프트를 잡는다. PairCohort가
    Cohort를 그대로 품는 관계 자체는 test_curation_cohort가 따로 고정한다.
    """
    assert {name for name, _ in COHORT_TABLE} == set(PAIR_COHORTS)


def test_cohort_table_marks_are_derived_from_the_evaluable_constant():
    """M3 — ○/✗를 표에 손으로 적으면 ITEM_EVALUABLE_COHORTS가 바뀔 때 표만 옛말을 인쇄한다."""
    md = _render([_enriched_row(label_bucket="ok", top1_sim=0.9)], {"fetched_at": "t"})
    for name, note in COHORT_TABLE:
        assert "○" not in note and "✗" not in note  # 설명문에는 마크가 없다(상수에서 도출)
        row = next(ln for ln in md.splitlines() if ln.startswith(f"| {name} |"))
        expected = "○" if name in ITEM_EVALUABLE_COHORTS else "✗"
        assert expected in row, name
        assert ("✗" if expected == "○" else "○") not in row, name


def test_sample_table_states_the_item_metric_denominator_and_row_missing():
    """M4 — ○ 코호트 합계와 품목 지표 분모가 어긋나는 이유(row_missing)를 표가 말해야 한다."""
    rows = [
        _enriched_row(cohort="current_bank", label_bucket="ok", top1_sim=0.9),
        _enriched_row(
            crop_ref="job-1/row-1",
            cohort="current_bank",
            label_bucket="row_missing",
            amount_bucket=None,
        ),
    ]
    md = _render(rows, {"fetched_at": "t"})
    assert "| current_bank | 2 |" in md  # ○ 코호트 합계는 2인데
    assert "품목 지표 분모(평가 가능 쌍) 1쌍" in md  # 분모는 1이고
    assert "row_missing 1건" in md  # 그 차이의 출처를 밝힌다


def test_reeval_notice_line_is_its_own_paragraph():
    """L4 — 알림 2건이 빈 줄 없이 붙으면 마크다운에서 한 문단으로 병합돼 한 문장처럼 읽힌다.

    지문이 확정된 meta로 렌더한다 — 미확정이면 그 줄이 지문 알림으로 갈리므로(H1) 이 절이
    고정하려는 재평가 알림 문단이 나오지 않는다.
    """
    meta = {"fetched_at": "t", "retrieval_version": _CUR_VERSION}
    md = _render([_enriched_row(label_bucket="ok", top1_sim=0.9)], meta)
    assert f"{reeval_notice(meta)}\n\n뱅크 추가 후보는 코호트와 무관하게" in md


def test_current_bank_coverage_line_has_a_nonzero_denominator_even_when_unevaluable():
    """뱅크 추가 후보 절 머리의 커버리지 줄은 코호트와 무관하게 라벨 있는 included 전체가 분모다."""
    rows = [
        _enriched_row(cohort="unknown", label_bucket="unevaluable", in_bank=True),
        _enriched_row(
            crop_ref="job-1/row-1", cohort="unknown", label_bucket="unevaluable", in_bank=False
        ),
    ]
    md = _render(rows, {"fetched_at": "t"})
    assert "현재 뱅크 보유: 1/2" in md


def test_bank_coverage_line_is_its_own_paragraph_not_a_list_continuation():
    """H2 — 후보 불릿 바로 뒤에 붙으면 CommonMark lazy continuation으로 마지막 항목에 흡수돼
    그 라벨의 커버리지인 것처럼 렌더된다(계산은 맞는데 표시가 거짓이 된다).
    """
    rows = [
        _enriched_row(answer="새라벨", label_bucket="unevaluable", in_bank=False),
        _enriched_row(crop_ref="job-1/row-1", label_bucket="ok", in_bank=True, top1_sim=0.9),
    ]
    md = _render(rows, {"fetched_at": "t"})
    assert "- 새라벨 ×1\n\n현재 뱅크 보유:" in md


def test_bank_coverage_line_keeps_its_blank_line_when_there_are_no_candidates():
    md = _render([_enriched_row(label_bucket="ok", top1_sim=0.9)], {"fetched_at": "t"})
    assert "- 없음\n\n현재 뱅크 보유:" in md


def test_report_header_prints_the_current_retrieval_fingerprint():
    """H1 — 판정의 기준값(현재 지문)이 인쇄되지 않으면 코호트 표를 검증할 근거가 없다."""
    md = _render(
        [_enriched_row(label_bucket="ok", top1_sim=0.9)],
        {"fetched_at": "t", "retrieval_version": "a1b2c3d4e5f6"},
    )
    assert "현재 retrieval 지문: a1b2c3d4e5f6" in md


def test_report_without_a_current_fingerprint_sends_the_user_to_fetch_not_a_rescore():
    """H1 — 지문 미확정이면 전 잡이 stale_bank로 떨어지는데 원인은 재평가 부재가 아니다.

    이때 재평가 부재 문구를 인쇄하면 몇십 분짜리 원격 재채점을 권하는데, 그 재채점도 게이트가
    no_fingerprint로 기각해 지표는 여전히 0/0이다 — 실제 필요한 조치는 fetch 재실행이다.
    """
    md = _render(
        [_enriched_row(cohort="stale_bank", label_bucket="unevaluable")],
        {"fetched_at": "t", "reeval": {"state": "absent", "adopted": False}},
    )
    assert "현재 retrieval 지문: 미확정" in md
    assert "fetch" in md
    assert "score --scope all" not in md


def test_next_actions_hints_pull_images_with_explicit_jobs():
    """Task 8 리뷰 M2 이관 — 재평가 전에는 pull-images 기본 호출이 판정 불가 잡을 당기지
    않아 "가져올 이미지가 없습니다"만 나온다. 안내 문구는 실행 가능한 대안(--jobs)을
    가리켜야 한다(pull 대상 자체는 넓히지 않는다 — spec §5).
    """
    rows = [_enriched_row(cohort="unknown", label_bucket="unevaluable", in_bank=False)]
    md = _render(rows, {"fetched_at": "t"})
    next_actions = md.split("## 다음 액션")[1]
    assert "--jobs" in next_actions
