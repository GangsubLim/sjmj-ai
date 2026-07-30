"""tools.curation_report 렌더·소비자 계층 단위테스트 (ssh/DB 비의존, 합성 데이터만).

분석 계층(파싱·버킷·조인·집계)의 테스트는 tests/test_curation_enrich.py에 있다.
"""

from tests.conftest import (  # 합성 헬퍼는 분석 계층 테스트와 공유한다
    _enrich,
    _enriched_row,
    _job,
    _pair,
    _row,
)
from tools.curation_cohort import (
    COHORTS,
    ITEM_EVALUABLE_COHORTS,
    REEVAL_REJECT_REASONS,
    REEVAL_STATES,
    is_item_failure,
)
from tools.curation_report import (
    _REEVAL_REJECT_TEXT,
    COHORT_TABLE,
    _failure_job_ids,
    pull_images,
    reeval_notice,
    render_report,
)


def test_pull_images_noop_on_empty_job_ids(tmp_path):
    out_dir = pull_images("unused-host", "unused-env", tmp_path, [], with_originals=False)
    assert out_dir == tmp_path / "images"
    assert out_dir.is_dir()


def test_render_report_handles_job_with_only_excluded_pairs():
    pairs = [_pair(status="excluded")]
    enriched = _enrich(pairs, [_job(rows=[_row(top5=[("엔진오일", 0.9)])])])
    md = render_report(enriched, {"fetched_at": "t"})
    assert "excluded" in md


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
    md = render_report(enriched, {"fetched_at": "2026-07-27T00:00:00", "bank_distinct": 4})
    assert "핵심 지표" in md
    assert "뱅크 추가 후보" in md
    assert "안가방" in md
    assert "잡별" in md


# --- 판정 불가 소비자 회귀 (spec §3-C의 표 — 소비자 6곳) ---


def test_failure_job_ids_does_not_stampede_on_unevaluable_items():
    """전 잡 폭주 회귀 — 판정 불가를 실패로 세면 pull-images가 전 잡 크롭을 당긴다(실측 18잡)."""
    rows = [
        _enriched_row(job_id=1, label_bucket="unevaluable", amount_bucket="ok"),
        _enriched_row(
            job_id=2,
            crop_ref="job-2/row-0",
            label_bucket="unevaluable",
            amount_bucket="zero_drift",
        ),
        _enriched_row(
            job_id=3, crop_ref="job-3/row-0", status="excluded", label_bucket="unevaluable"
        ),
        _enriched_row(job_id=4, crop_ref="job-4/row-0", label_bucket="in_bank_miss"),
    ]
    assert _failure_job_ids(rows) == [2, 3, 4]  # 1은 판정 불가일 뿐 실패가 아니다


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
    md = render_report(rows, {"fetched_at": "t"})
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
    md = render_report([row], {"fetched_at": "t"})
    misses = md.split("## in-bank 리트리벌 미스")[1].split("##")[0]
    assert "answer='엔진오일'" in misses


def test_row_missing_pairs_stay_in_failures_and_pull_images():
    rows = [
        _enriched_row(job_id=1, label_bucket="row_missing", amount_bucket=None),
        _enriched_row(
            job_id=2, crop_ref="job-2/row-0", label_bucket="unevaluable", amount_bucket="ok"
        ),
    ]
    assert _failure_job_ids(rows) == [1]  # 2는 판정 불가일 뿐 실패가 아니다
    assert [r["job_id"] for r in rows if is_item_failure(r)] == [1]


# --- era-aware 재판정 (spec §3-C — unevaluable의 생산 지점) ---


def test_row_missing_survives_an_unevaluable_cohort():
    """M1 계약 유지 — 데이터 정합 장애는 시점 판정 불가에 삼켜지지 않는다.

    plan Task 11의 _item_bucket 초안은 코호트를 row_missing보다 먼저 봐서, 스탬프 없는 잡
    (현재 데이터 전량)의 조인 결손을 unevaluable로 흡수한다. 그러면 row_missing이
    failures.jsonl·pull-images에서 통째로 사라진다(curation_cohort.DATA_INTEGRITY_
    FAILURE_BUCKETS 계약 위반). 그래서 row_missing을 코호트보다 먼저 판정한다.
    """
    pairs = [_pair(row_index=9, crop_ref="job-1/row-9")]
    enriched = _enrich(pairs, [_job(rows=[], retrieval_version=None)])
    assert enriched[0]["cohort"] == "unknown"
    assert enriched[0]["label_bucket"] == "row_missing"
    assert _failure_job_ids(enriched) == [1]


def test_unevaluable_jobs_do_not_stampede_the_failure_list():
    """전 잡 폭주 실증 — 스탬프 이전 잡을 대량으로 넣어도 실패 목록이 비어 있어야 한다."""
    pairs = [_pair(id=i, job_id=i, crop_ref=f"job-{i}/row-0") for i in range(1, 6)]
    jobs = [
        _job(job_id=i, rows=[_row(job=i, top5=[("타이어", 0.4)])], retrieval_version=None)
        for i in range(1, 6)
    ]
    enriched = _enrich(pairs, jobs)
    assert {r["label_bucket"] for r in enriched} == {"unevaluable"}
    assert _failure_job_ids(enriched) == []


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
    md = render_report(rows, {"fetched_at": "t"})
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
    md = render_report(rows, {"fetched_at": "t"})
    assert md.index("## 표본 구성") < md.index("## 핵심 지표")
    assert "| unknown | 1 |" in md
    assert "| no_label | 1 |" in md
    assert "| current_bank | 1 |" in md
    # 분모는 총 3쌍이 아니라 평가 가능 1쌍(현재 라벨 텍스트 유지 — 기존 지표 표 규약).
    assert "| 품목 top-1 (평가 가능 쌍 기준) | 1/1 (100.0%) |" in md
    assert "뱅크 추가 후보는 코호트와 무관하게" in md
    assert "peer" in md  # score.md 포인터(중복 구현 회피)


def test_cohort_table_covers_every_cohort_a_pair_can_get():
    """표에 없는 코호트가 생기면 그 쌍들은 표에서 조용히 사라지고 합계가 안 맞는다."""
    assert {name for name, _ in COHORT_TABLE} == set(COHORTS) | {"no_label"}


def test_cohort_table_marks_are_derived_from_the_evaluable_constant():
    """M3 — ○/✗를 표에 손으로 적으면 ITEM_EVALUABLE_COHORTS가 바뀔 때 표만 옛말을 인쇄한다."""
    md = render_report([_enriched_row(label_bucket="ok", top1_sim=0.9)], {"fetched_at": "t"})
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
    md = render_report(rows, {"fetched_at": "t"})
    assert "| current_bank | 2 |" in md  # ○ 코호트 합계는 2인데
    assert "품목 지표 분모(평가 가능 쌍) 1쌍" in md  # 분모는 1이고
    assert "row_missing 1건" in md  # 그 차이의 출처를 밝힌다


def test_reeval_notice_line_is_its_own_paragraph():
    """L4 — 알림 2건이 빈 줄 없이 붙으면 마크다운에서 한 문단으로 병합돼 한 문장처럼 읽힌다."""
    md = render_report([_enriched_row(label_bucket="ok", top1_sim=0.9)], {"fetched_at": "t"})
    assert f"{reeval_notice({'fetched_at': 't'})}\n\n뱅크 추가 후보는 코호트와 무관하게" in md


def test_current_bank_coverage_line_has_a_nonzero_denominator_even_when_unevaluable():
    """뱅크 추가 후보 절 머리의 커버리지 줄은 코호트와 무관하게 라벨 있는 included 전체가 분모다."""
    rows = [
        _enriched_row(cohort="unknown", label_bucket="unevaluable", in_bank=True),
        _enriched_row(
            crop_ref="job-1/row-1", cohort="unknown", label_bucket="unevaluable", in_bank=False
        ),
    ]
    md = render_report(rows, {"fetched_at": "t"})
    assert "현재 뱅크 보유: 1/2" in md


def test_bank_coverage_line_is_its_own_paragraph_not_a_list_continuation():
    """H2 — 후보 불릿 바로 뒤에 붙으면 CommonMark lazy continuation으로 마지막 항목에 흡수돼
    그 라벨의 커버리지인 것처럼 렌더된다(계산은 맞는데 표시가 거짓이 된다).
    """
    rows = [
        _enriched_row(answer="새라벨", label_bucket="unevaluable", in_bank=False),
        _enriched_row(crop_ref="job-1/row-1", label_bucket="ok", in_bank=True, top1_sim=0.9),
    ]
    md = render_report(rows, {"fetched_at": "t"})
    assert "- 새라벨 ×1\n\n현재 뱅크 보유:" in md


def test_bank_coverage_line_keeps_its_blank_line_when_there_are_no_candidates():
    md = render_report([_enriched_row(label_bucket="ok", top1_sim=0.9)], {"fetched_at": "t"})
    assert "- 없음\n\n현재 뱅크 보유:" in md


def test_next_actions_hints_pull_images_with_explicit_jobs():
    """Task 8 리뷰 M2 이관 — 재평가 전에는 pull-images 기본 호출이 판정 불가 잡을 당기지
    않아 "가져올 이미지가 없습니다"만 나온다. 안내 문구는 실행 가능한 대안(--jobs)을
    가리켜야 한다(pull 대상 자체는 넓히지 않는다 — spec §5).
    """
    rows = [_enriched_row(cohort="unknown", label_bucket="unevaluable", in_bank=False)]
    md = render_report(rows, {"fetched_at": "t"})
    next_actions = md.split("## 다음 액션")[1]
    assert "--jobs" in next_actions
