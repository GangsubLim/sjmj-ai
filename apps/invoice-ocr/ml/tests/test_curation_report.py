"""tools.curation_report 렌더·소비자 계층 단위테스트 (ssh/DB 비의존, 합성 데이터만).

분석 계층(파싱·버킷·조인·집계)의 테스트는 tests/test_curation_enrich.py에 있다.
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import (  # 합성 헬퍼는 분석 계층 테스트와 공유한다
    _CUR_VERSION,
    BANK,
    _enrich,
    _enriched_row,
    _four_vintages,
    _job,
    _pair,
    _reeval_meta,
    _row,
)
from tools.curation_cohort import (
    ITEM_EVALUABLE_COHORTS,
    PAIR_COHORTS,
    REEVAL_REJECT_REASONS,
    REEVAL_STATES,
    is_item_failure,
)
from tools.curation_report import (
    _BANK_PY,
    _CACHE_RECOVERY,
    _REEVAL_REJECT_TEXT,
    COHORT_TABLE,
    _clear_reeval,
    _cmd_fetch,
    _failure_job_ids,
    _fetch_reeval,
    _load_enriched,
    _replace_atomically,
    bank_script,
    fetch_all,
    fetch_error_message,
    pull_images,
    reeval_cat_script,
    reeval_notice,
    reeval_probe_script,
    render_report,
)
from tools.remote import RemoteError


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
    """표에 없는 코호트가 생기면 그 쌍들은 표에서 조용히 사라지고 합계가 안 맞는다.

    기대집합을 `set(COHORTS) | {"no_label"}`로 손으로 재구성하지 않는다 — `summarize`가 세는
    값은 `pair_cohort`의 치역(= PairCohort)이므로 그 진실원 상수와 직접 대조해야, 쌍 단위 치역이
    늘어날 때(예: 새 정답 부재 사유) 표만 옛말을 인쇄하는 드리프트를 잡는다. PairCohort가
    Cohort를 그대로 품는 관계 자체는 test_curation_cohort가 따로 고정한다.
    """
    assert {name for name, _ in COHORT_TABLE} == set(PAIR_COHORTS)


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
    """L4 — 알림 2건이 빈 줄 없이 붙으면 마크다운에서 한 문단으로 병합돼 한 문장처럼 읽힌다.

    지문이 확정된 meta로 렌더한다 — 미확정이면 그 줄이 지문 알림으로 갈리므로(H1) 이 절이
    고정하려는 재평가 알림 문단이 나오지 않는다.
    """
    meta = {"fetched_at": "t", "retrieval_version": _CUR_VERSION}
    md = render_report([_enriched_row(label_bucket="ok", top1_sim=0.9)], meta)
    assert f"{reeval_notice(meta)}\n\n뱅크 추가 후보는 코호트와 무관하게" in md


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


def test_report_header_prints_the_current_retrieval_fingerprint():
    """H1 — 판정의 기준값(현재 지문)이 인쇄되지 않으면 코호트 표를 검증할 근거가 없다."""
    md = render_report(
        [_enriched_row(label_bucket="ok", top1_sim=0.9)],
        {"fetched_at": "t", "retrieval_version": "a1b2c3d4e5f6"},
    )
    assert "현재 retrieval 지문: a1b2c3d4e5f6" in md


def test_report_without_a_current_fingerprint_sends_the_user_to_fetch_not_a_rescore():
    """H1 — 지문 미확정이면 전 잡이 stale_bank로 떨어지는데 원인은 재평가 부재가 아니다.

    이때 재평가 부재 문구를 인쇄하면 몇십 분짜리 원격 재채점을 권하는데, 그 재채점도 게이트가
    no_fingerprint로 기각해 지표는 여전히 0/0이다 — 실제 필요한 조치는 fetch 재실행이다.
    """
    md = render_report(
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
    md = render_report(rows, {"fetched_at": "t"})
    next_actions = md.split("## 다음 액션")[1]
    assert "--jobs" in next_actions


# --- fetch/report 배선 (ssh는 비대상 — 스크립트 조립과 로컬 캐시 계약만) ---


def test_bank_script_cds_into_ml_root_and_shares_the_fingerprint_entry_point():
    script = bank_script("$HOME/.sjmj-ai/ml-worker.env", "/srv/ml")
    assert 'cd "/srv/ml"' in script  # python -c는 cwd를 sys.path에 넣는다
    assert "from handwriting import bank_id" in script
    # 지문 입력(파일명·배열 선택)까지 워커와 공유하는 단일 진입점을 부른다(M4).
    assert "bank_retrieval_version" in script
    # 페이로드 키 형태로 못박는다 — `"retrieval_version" in script`는 앞줄이 통과시킨
    # "bank_retrieval_version"의 부분문자열이라 단독으로 실패할 수 없는 항진이었다.
    # fetch_all이 `bank.get("retrieval_version")`로 읽으므로 이 키가 곧 계약이다.
    assert "'retrieval_version': version" in script
    assert "'retrieval_version_error': error" in script  # 실패 사유도 stdout으로 온다
    assert script.startswith("set -eu")  # env 부재 시 즉시 실패(source_env 관례)


def test_bank_script_isolates_a_fingerprint_failure_but_keeps_the_import_hard_failing():
    """M3 — 지문 계산 실패가 pairs/jobs 동기화까지 막으면 분석 도구만 전체 정지한다.

    운영 워커는 정확히 그 이유로 같은 실패를 진단 필드 하나로 격리한다
    (worker.main.retrieval_version_or_none). 다만 `handwriting` import 실패는 배포 누락
    신호이므로 hard-fail을 유지한다 — try 블록 밖에 둔다.
    """
    script = bank_script("$HOME/.sjmj-ai/ml-worker.env", "/srv/ml")
    assert "try:" in script and "except Exception" in script
    assert script.index("from handwriting import bank_id") < script.index("try:")
    assert "version = None" in script  # 실패는 지문 null로 내보낸다


def test_bank_script_body_is_valid_python_and_survives_double_quoting():
    """원격 스크립트는 테스트에서 실행되지 않는다 — 문법 오류·금지 문자가 조용히 배포된다.

    `_BANK_PY`는 셸 이중따옴표 안에 그대로 보간되므로 `"`·`$`·백틱·백슬래시를 쓸 수 없다(모듈
    상단 주석의 계약). 그중 하나라도 새면 fetch가 원격에서 문장이 깨진 채 실패하고, 문법 오류는
    지문 질의 전체를 죽인다 — 둘 다 로컬에서 compile 한 번으로 잡힌다.
    """
    compile(_BANK_PY, "<remote bank script>", "exec")
    assert [c for c in ('"', "$", "`", "\\") if c in _BANK_PY] == []


def test_remote_scripts_expand_a_tilde_ml_root_instead_of_quoting_it_literally():
    """Task 6 리뷰 M2 이관 — `SJMJ_REMOTE_ML_ROOT=~/…` 주입이 원격에서 즉시 실패하지 않게 한다."""
    for script in (
        bank_script("~/e.env", "~/sjmj-ai/apps/invoice-ocr/ml"),
        reeval_probe_script("~/sjmj-ai/apps/invoice-ocr/ml"),
        reeval_cat_script("~/sjmj-ai/apps/invoice-ocr/ml", "score.jsonl"),
    ):
        assert '"~/' not in script
        assert "$HOME/sjmj-ai/apps/invoice-ocr/ml" in script


@pytest.mark.parametrize(
    "evil", ['/srv/ml"; rm -rf /', "/srv/$(id)/ml", "/srv/`id`/ml", "/srv\\ml"]
)
def test_remote_scripts_refuse_a_path_that_breaks_out_of_the_double_quotes(evil):
    """C9 — 경로는 `cd "..."`·`source "..."`에 그대로 보간된다.

    `remote_path`가 "이중따옴표 안에서 안전"을 약속하면서 `"`·백틱·`$(`·백슬래시를 통과시키면
    문장이 깨지거나 원격에서 다른 명령이 돈다. 출처가 운영자 자신의 env(SJMJ_REMOTE_*)라
    공격면은 아니지만, 오타 하나가 조용히 다른 명령이 되는 것보다 즉시 실패가 낫다.
    `$HOME` 확장은 살아 있어야 하므로 `$`를 통째로 막지 않는다(아래 테스트가 그 경계를 잡는다).
    """
    for build in (
        lambda p: bank_script("~/e.env", p),
        reeval_probe_script,
        lambda p: reeval_cat_script(p, "score.jsonl"),
    ):
        with pytest.raises(ValueError, match="셸 특수문자"):
            build(evil)


def test_remote_scripts_still_allow_a_plain_dollar_expansion():
    # `$HOME/...`이 원격 셸에서 확장되는 것이 이 경로 관례의 전제다 — 함께 막으면 기본값이 깨진다.
    assert '"$HOME/sjmj-ai/ml/results/bank_update/score.jsonl"' in reeval_cat_script(
        "$HOME/sjmj-ai/ml", "score.jsonl"
    )


def test_reeval_probe_script_does_not_fail_when_the_directory_is_absent():
    # 부재는 정상 상태다(재평가 미실행) — 비0 종료로 fetch 전체를 죽이지 않는다.
    script = reeval_probe_script("/srv/ml")
    assert "exit 0" in script and "|| true" in script
    assert 'cd "/srv/ml/results/bank_update"' in script


def test_reeval_cat_script_double_quotes_the_remote_path():
    # 공백·셸 메타문자가 든 경로가 단어분리로 갈라지지 않게 한다.
    assert (
        reeval_cat_script("/srv/my ml", "score.jsonl")
        == 'cat "/srv/my ml/results/bank_update/score.jsonl"'
    )


_GROUP = ("reeval.jsonl", "reeval_meta.json", "meta.json")


def test_replace_atomically_writes_every_file_and_leaves_no_tmp(tmp_path):
    _replace_atomically(tmp_path, [(name, f"{name}-body".encode()) for name in _GROUP])
    assert [(tmp_path / name).read_bytes() for name in _GROUP] == [
        f"{name}-body".encode() for name in _GROUP
    ]
    assert list(tmp_path.glob("*.tmp")) == []


def test_replace_atomically_leaves_the_old_group_intact_when_one_write_fails(tmp_path):
    """반쪽만 새것인 캐시를 만들지 않는다 — 전부 tmp로 받은 뒤에 교체한다."""
    _replace_atomically(tmp_path, [(name, b"old") for name in _GROUP])
    with pytest.raises(TypeError):
        _replace_atomically(
            tmp_path,
            [
                ("reeval.jsonl", b"new"),
                ("reeval_meta.json", b"new"),
                ("meta.json", "bytes가 아니다"),
            ],
        )
    assert [(tmp_path / name).read_bytes() for name in _GROUP] == [b"old"] * len(_GROUP)
    assert list(tmp_path.glob("*.tmp")) == []


_PAIRS_TSV = (
    "id\tcrop_ref\tjob_id\trow_index\tdraft_label\tfinal_label\t"
    "canonical_label\tsupply\tstatus\treviewed_at\n"
    "1\tjob-1/row-0\t1\t0\t엔진오일\t엔진오일\t엔진오일\t100000\tincluded\tNULL\n"
)
_JOBS_TSV = "id\timage_path\tresult\n1\t/data/up/1.jpeg\t" + json.dumps(
    {"rows": [], "warp_ok": True, "retrieval_version": _CUR_VERSION}, ensure_ascii=False
)
_REEVAL_BODIES = (
    ("score.jsonl", b'{"side": "after"}\n'),
    ("score_meta.json", b'{"n_pairs": 1}\n'),
)


def _fake_ssh(
    *,
    remote_names=(),
    bodies=_REEVAL_BODIES,
    retrieval_version=_CUR_VERSION,
    version_error=None,
    bank_error=None,
):
    """run_ssh 대역 — 스크립트 내용으로 질의를 구분한다(ssh 없이 fetch 배선만 닫는다).

    `bank_error`를 주면 지문 질의만 RemoteError로 실패시킨다 — pairs/jobs는 성공한 뒤 지문에서
    죽는 실제 순서를 그대로 재현해야 fetch_all의 예외 변환 배선을 지난다.
    """
    bank = {
        "size": 1,
        "counts": {"엔진오일": 1},
        "retrieval_version": retrieval_version,
        "retrieval_version_error": version_error,
    }

    def run(host, script):
        if "training_pairs ORDER BY" in script:
            return _PAIRS_TSV.encode()
        if "ocr_jobs" in script:
            return _JOBS_TSV.encode()
        if "PYTHON_BIN" in script:
            if bank_error:
                raise RemoteError(bank_error)
            return json.dumps(bank, ensure_ascii=False).encode()
        if "ls score.jsonl" in script:
            return " ".join(remote_names).encode()
        for name, body in bodies:
            if script.endswith(f'/{name}"'):
                return body
        raise AssertionError(f"예상 못 한 원격 스크립트: {script}")

    return run


def _fetch_all(cache):
    """fetch_all 호출 — 키워드 전용 시그니처를 테스트에서도 한 자리에만 적는다."""
    return fetch_all(
        host="h",
        backend_env="backend.env",
        worker_env="worker.env",
        ml_root="/srv/ml",
        cache=cache,
    )


def _assert_names_file_and_recovery(err, filename):
    """손상 메시지가 **파일명과 복구 절차를** 함께 말하는지 본다.

    파일명만 보던 단언은 테스트 이름이 약속한 "복구 절차"를 검증하지 않았다 — 지침을 지워도
    GREEN이었다. 문구는 상수(`_CACHE_RECOVERY`)로 대조해 기대값을 손으로 복제하지 않되, 상수가
    빈 문자열이 되면 `"" in text`가 항진이 되므로 비어있지 않음과 실제 조치(`fetch`)를 함께 본다.
    """
    text = str(err.value)
    assert filename in text, text
    assert _CACHE_RECOVERY and _CACHE_RECOVERY in text, text
    assert "fetch" in text, text  # 복구 절차가 실행할 명령을 가리킨다


def test_fetch_reeval_returns_both_bodies_when_the_server_has_the_pair(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tools.curation_report.run_ssh",
        _fake_ssh(remote_names=("score.jsonl", "score_meta.json")),
    )
    assert _fetch_reeval("h", "/srv/ml", tmp_path) == (
        "present",
        [("reeval.jsonl", b'{"side": "after"}\n'), ("reeval_meta.json", b'{"n_pairs": 1}\n')],
    )


def test_fetch_reeval_reports_a_score_jsonl_without_meta(tmp_path, monkeypatch):
    # #53 이전 산출물 — 정상 경로이므로 죽지 않고 상태 어휘로 알린다.
    monkeypatch.setattr("tools.curation_report.run_ssh", _fake_ssh(remote_names=("score.jsonl",)))
    assert _fetch_reeval("h", "/srv/ml", tmp_path) == ("no_meta", [])


def test_fetch_reeval_treats_a_lone_meta_as_absent(tmp_path, monkeypatch):
    """비자명 분기 — meta만 있고 jsonl이 없으면 no_meta가 아니라 absent다(해석할 레코드가 없다)."""
    monkeypatch.setattr(
        "tools.curation_report.run_ssh", _fake_ssh(remote_names=("score_meta.json",))
    )
    assert _fetch_reeval("h", "/srv/ml", tmp_path) == ("absent", [])


def test_fetch_reeval_clears_a_stale_local_pair_when_the_server_has_nothing(tmp_path, monkeypatch):
    """서버에서 산출물이 사라지면 로컬도 지운다 — 남으면 재평가가 유효한 것처럼 읽힌다."""
    (tmp_path / "reeval.jsonl").write_text("x", encoding="utf-8")
    (tmp_path / "reeval_meta.json").write_text("y", encoding="utf-8")
    monkeypatch.setattr("tools.curation_report.run_ssh", _fake_ssh())
    assert _fetch_reeval("h", "/srv/ml", tmp_path) == ("absent", [])
    assert not (tmp_path / "reeval.jsonl").exists()
    assert not (tmp_path / "reeval_meta.json").exists()


def test_fetch_reeval_state_range_matches_the_literal_bijectively(tmp_path, monkeypatch):
    """C6 — production이 ReevalState/REEVAL_STATES를 쓰지 않아 철자 드리프트를 잡는 장치가 없었다
    (있던 테스트조차 상수를 손으로 적은 집합과만 대조해 생산자는 검증 밖이었다).

    생산자(`_fetch_reeval`)의 **실제 반환 집합**을 전수 입력으로 치역과 맞춘다 — 오타("presnt")도
    dead 상태도 잡힌다. 오타는 `_reeval_info`가 present 아닌 값으로 읽어 재평가를 fail-closed로
    버리고 리포트는 사유를 "미상"으로 오보한다. Cohort/COHORTS·ReevalReason도 같은 짝을 둔다.
    """
    server_states = [(), ("score.jsonl",), ("score.jsonl", "score_meta.json")]
    states = set()
    for names in server_states:
        monkeypatch.setattr("tools.curation_report.run_ssh", _fake_ssh(remote_names=names))
        states.add(_fetch_reeval("h", "/srv/ml", tmp_path)[0])
    assert states == set(REEVAL_STATES)


def test_fetch_all_replaces_the_meta_together_with_the_reeval_pair(tmp_path, monkeypatch):
    """M2 — 두 파일을 **해석하는** meta.json이 원자 교체 밖에 있으면 한 벌이 반쪽만 원자적이다.

    중간 실패는 fail-closed라 수치는 오염되지 않지만, 사유가 stale로 오보되어 사용자가 몇십 분
    짜리 재채점으로 간다(H1과 같은 오조치).
    """
    monkeypatch.setattr(
        "tools.curation_report.run_ssh",
        _fake_ssh(remote_names=("score.jsonl", "score_meta.json")),
    )
    replaced: list[str] = []
    real_replace = os.replace

    def spy(src, dst):
        replaced.append(Path(dst).name)
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    _fetch_all(tmp_path)
    assert replaced == ["reeval.jsonl", "reeval_meta.json", "meta.json"]


def test_fetch_all_rejects_positional_arguments(tmp_path, monkeypatch):
    """C3 — host·두 env 경로·ml_root는 동종 str 4개다. ml_root가 뒤에 끼어든 뒤로는 위치 인자
    하나만 밀려도 예외 없이 다른 파일을 source하거나 다른 디렉터리로 cd한다(조용한 오연결).
    """
    monkeypatch.setattr("tools.curation_report.run_ssh", _fake_ssh())
    with pytest.raises(TypeError):
        fetch_all("h", "backend.env", "worker.env", "/srv/ml", tmp_path)


def test_fetch_all_syncs_the_cache_even_when_the_remote_fingerprint_is_null(tmp_path, monkeypatch):
    """M3 — 원격 지문 계산 실패(null)가 pairs/jobs 동기화까지 막지 않는다."""
    monkeypatch.setattr("tools.curation_report.run_ssh", _fake_ssh(retrieval_version=None))
    meta = _fetch_all(tmp_path)
    assert meta["retrieval_version"] is None
    assert meta["reeval_state"] == "absent"
    assert json.loads((tmp_path / "pairs.json").read_text(encoding="utf-8"))[0]["job_id"] == 1
    assert list(tmp_path.glob("*.tmp")) == []


def test_fetch_all_keeps_the_remote_reason_for_a_null_fingerprint(tmp_path, monkeypatch):
    """C2 — 원격은 사유를 알지만 exit 0이라 run_ssh가 stderr를 버렸다(사유 유실). 사유를 stdout
    페이로드로 받아 캐시 meta에 남겨야 운영자가 원인(git SHA 부재/npz 결손/모델 접근 실패)을
    구분할 수 있다 — 없으면 "지문 미확정"만 보고 무엇을 고칠지 알 수 없다.
    """
    monkeypatch.setattr(
        "tools.curation_report.run_ssh",
        _fake_ssh(retrieval_version=None, version_error="FileNotFoundError: keys.npy"),
    )
    meta = _fetch_all(tmp_path)
    assert meta["retrieval_version_error"] == "FileNotFoundError: keys.npy"
    cached = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    assert cached["retrieval_version_error"] == "FileNotFoundError: keys.npy"


def test_fetch_all_leaves_the_reason_none_when_the_fingerprint_succeeded(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.curation_report.run_ssh", _fake_ssh())
    assert _fetch_all(tmp_path)["retrieval_version_error"] is None


def test_cmd_fetch_prints_the_remote_reason_next_to_the_fingerprint_notice(
    tmp_path, monkeypatch, capsys
):
    """C2 — meta에 담기만 하고 인쇄하지 않으면 운영자는 캐시 파일을 열어봐야 원인을 안다."""
    monkeypatch.setattr(
        "tools.curation_report.run_ssh",
        _fake_ssh(retrieval_version=None, version_error="RuntimeError: git SHA 없음"),
    )
    _cmd_fetch("h", "backend.env", "worker.env", "/srv/ml", tmp_path)
    out = capsys.readouterr().out
    assert "RuntimeError: git SHA 없음" in out
    assert "fetch" in out  # 지문 미확정 안내(조치)도 함께 나온다


def test_cmd_fetch_says_nothing_about_a_reason_when_the_fingerprint_is_known(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr("tools.curation_report.run_ssh", _fake_ssh())
    _cmd_fetch("h", "backend.env", "worker.env", "/srv/ml", tmp_path)
    out = capsys.readouterr().out
    assert "실패 사유" not in out
    assert _CUR_VERSION in out


def test_clear_reeval_removes_both_files_together(tmp_path):
    """서버에 재평가가 없으면 로컬의 두 파일을 지운다 — 남으면 유효한 것처럼 읽힌다."""
    (tmp_path / "reeval.jsonl").write_text("x", encoding="utf-8")
    (tmp_path / "reeval_meta.json").write_text("y", encoding="utf-8")
    _clear_reeval(tmp_path)
    assert not (tmp_path / "reeval.jsonl").exists()
    assert not (tmp_path / "reeval_meta.json").exists()
    _clear_reeval(tmp_path)  # 멱등 — 이미 없어도 실패하지 않는다


def _write_cache(
    tmp_path,
    *,
    pairs,
    jobs,
    bank_labels=None,
    retrieval_version=_CUR_VERSION,
    reeval=None,
    reeval_meta=None,
    reeval_state="absent",
):
    # 쓰기 인코딩을 대상 코드(_load_enriched·_read_reeval_files의 utf-8 읽기)와 맞춘다 — 기본
    # 인코딩에 맡기면 로케일이 utf-8이 아닌 환경에서 한글 라벨이 UnicodeEncodeError로 터진다.
    bank_labels = sorted(BANK) if bank_labels is None else bank_labels
    (tmp_path / "pairs.json").write_text(json.dumps(pairs, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "jobs.json").write_text(json.dumps(jobs, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "bank.json").write_text(
        json.dumps(
            {"size": len(bank_labels), "counts": {lb: 1 for lb in bank_labels}}, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    if reeval is not None:
        (tmp_path / "reeval.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in reeval) + "\n", encoding="utf-8"
        )
    if reeval_meta is not None:
        (tmp_path / "reeval_meta.json").write_text(
            json.dumps(reeval_meta, ensure_ascii=False), encoding="utf-8"
        )
    (tmp_path / "meta.json").write_text(
        json.dumps(
            {
                "fetched_at": "t",
                "host": "h",
                "bank_size": len(bank_labels),
                "bank_distinct": len(bank_labels),
                "retrieval_version": retrieval_version,
                "reeval_state": reeval_state,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rewrite_reeval_meta(tmp_path, **over):
    """캐시에 **실제로 쓰인 바이트** 기준으로 다이제스트를 다시 적는다.

    줄바꿈·직렬화 차이를 그대로 반영해야 게이트가 digest_mismatch로 기각하지 않는다. 같은
    6줄이 세 테스트에 복붙돼 있었고 그 사본들만 쓰기 인코딩이 빠져 있었다 — 읽기가 utf-8이므로
    쓰기도 여기서 한 번 못박는다(_write_cache와 같은 이유).
    """
    meta = _reeval_meta(score_jsonl_sha256=_digest(tmp_path / "reeval.jsonl"), **over)
    (tmp_path / "reeval_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )


def test_load_enriched_wires_the_current_fingerprint_so_pairs_stay_evaluable(tmp_path):
    """Task 11 리뷰 M5 이관 — 지문을 넘기지 않으면 CLI 리포트가 전량 unevaluable이 된다."""
    _write_cache(
        tmp_path,
        pairs=[_pair()],
        jobs=[_job(rows=[_row(top5=[("엔진오일", 0.9)])], retrieval_version=_CUR_VERSION)],
    )
    enriched, meta = _load_enriched(tmp_path)
    assert enriched[0]["cohort"] == "current_bank"
    assert enriched[0]["label_bucket"] == "ok"
    assert meta["reeval"]["state"] == "absent"


def test_load_enriched_adopts_a_consistent_reevaluation(tmp_path):
    records = _four_vintages()
    _write_cache(
        tmp_path,
        pairs=[_pair()],
        jobs=[_job(rows=[_row(top5=[("타이어", 0.3)])], retrieval_version="old")],
        reeval=records,
        reeval_meta=_reeval_meta(score_jsonl_sha256="placeholder"),
        reeval_state="present",
    )
    _rewrite_reeval_meta(tmp_path)
    enriched, meta = _load_enriched(tmp_path)
    assert meta["reeval"]["adopted"] is True
    assert meta["reeval"]["after"] == _CUR_VERSION
    assert enriched[0]["cohort"] == "reevaluated"
    assert enriched[0]["top5_labels"] == ["안가방", "공임"]


def test_load_enriched_flattens_the_nested_fingerprint_for_the_notice(tmp_path):
    """Task 12 리뷰 이관 — score_meta는 지문을 중첩으로 쓰고 reeval_notice는 평탄 키를 읽는다.

    재맵이 없으면 채택 문구가 지문 자리에 '?'를 인쇄한다(계산 A/표시 B).
    """
    _write_cache(
        tmp_path,
        pairs=[_pair()],
        jobs=[_job(rows=[_row(top5=[("타이어", 0.3)])], retrieval_version="old")],
        reeval=_four_vintages(),
        reeval_meta=_reeval_meta(),
        reeval_state="present",
    )
    _rewrite_reeval_meta(tmp_path)
    _, meta = _load_enriched(tmp_path)
    line = reeval_notice(meta)
    assert _CUR_VERSION in line and "현재와 일치" in line
    assert "?" not in line


def test_load_enriched_reports_a_score_jsonl_without_meta_as_a_normal_path(tmp_path):
    _write_cache(
        tmp_path,
        pairs=[_pair()],
        jobs=[_job(rows=[_row(top5=[("엔진오일", 0.9)])], retrieval_version=None)],
        reeval_state="no_meta",
    )
    enriched, meta = _load_enriched(tmp_path)
    assert meta["reeval"] == {
        "state": "no_meta",
        "adopted": False,
        "reason": None,
        "generated_at": None,
        "after": None,
        "scope": None,
        "n_pairs": None,
    }
    assert enriched[0]["cohort"] == "unknown"
    assert "score_meta.json" in reeval_notice(meta)


def test_load_enriched_discards_a_stale_reevaluation_but_keeps_current_bank_pairs(tmp_path):
    """§3-C stale 방어 — 재평가는 통째로 버리고 각 쌍을 스탬프 기준으로 재분기한다."""
    _write_cache(
        tmp_path,
        pairs=[_pair()],
        jobs=[_job(rows=[_row(top5=[("엔진오일", 0.9)])], retrieval_version=_CUR_VERSION)],
        reeval=_four_vintages(),
        reeval_meta=_reeval_meta(),
        reeval_state="present",
    )
    _rewrite_reeval_meta(tmp_path, retrieval_version={"before": "x", "after": "older"})
    enriched, meta = _load_enriched(tmp_path)
    assert meta["reeval"]["adopted"] is False and meta["reeval"]["reason"] == "stale"
    # 스탬프가 현재와 같은 잡은 current_bank로 남는다 — 낡은 재평가가 없어도 그 잡은 현재
    # retrieval 상태로 추론된 것이다.
    assert enriched[0]["cohort"] == "current_bank"
    assert enriched[0]["label_bucket"] == "ok"


@pytest.mark.parametrize("body", ["[1, 2]", '"corrupt"', "null"])
def test_load_enriched_rejects_a_reeval_meta_that_is_not_an_object(tmp_path, body):
    """H2 — dict가 아닌 meta를 게이트 안쪽까지 흘리면 AttributeError로 도구가 통째로 죽는다.

    parse_reeval_jsonl이 같은 클래스를 경계에서 막는 이유와 같다(원인이 파싱 경계에서
    멀어진다). `null`은 게이트가 no_meta로 정상 처리하는데도 _reeval_info가 먼저 죽었다.
    """
    _write_cache(
        tmp_path,
        pairs=[_pair()],
        jobs=[_job()],
        reeval=_four_vintages(),
        reeval_meta={},
        reeval_state="present",
    )
    (tmp_path / "reeval_meta.json").write_text(body, encoding="utf-8")
    with pytest.raises(ValueError) as err:
        _load_enriched(tmp_path)
    _assert_names_file_and_recovery(err, "reeval_meta.json")


def test_load_enriched_names_the_file_and_the_recovery_when_the_reeval_meta_is_corrupt(tmp_path):
    """H2 — 손상 파일의 JSONDecodeError가 raw로 새면 어느 파일을 어떻게 고치는지 알 수 없다."""
    _write_cache(
        tmp_path,
        pairs=[_pair()],
        jobs=[_job()],
        reeval=_four_vintages(),
        reeval_meta={},
        reeval_state="present",
    )
    (tmp_path / "reeval_meta.json").write_text("{not json}", encoding="utf-8")
    with pytest.raises(ValueError) as err:
        _load_enriched(tmp_path)
    _assert_names_file_and_recovery(err, "reeval_meta.json")


def test_load_enriched_names_the_file_and_the_recovery_when_the_reeval_jsonl_is_corrupt(tmp_path):
    """H2 — jsonl 쪽도 같은 지침을 붙인다(타입은 유지 — 즉시 실패 계약을 바꾸지 않는다).

    타입 유지가 계약의 절반이므로 예외 타입도 함께 본다: JSONDecodeError는 ValueError의
    하위형이라 호출자는 ValueError 하나로 잡지만, meta 쪽처럼 평범한 ValueError로 갈아치우면
    "즉시 실패 계약을 바꾸지 않는다"는 이 테스트의 전제가 조용히 깨진다.
    """
    _write_cache(
        tmp_path,
        pairs=[_pair()],
        jobs=[_job()],
        reeval=[],
        reeval_meta=_reeval_meta(),
        reeval_state="present",
    )
    (tmp_path / "reeval.jsonl").write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError) as err:
        _load_enriched(tmp_path)
    _assert_names_file_and_recovery(err, "reeval.jsonl")


# --- 지문 기능 이전 릴리스 안내 (C1 — 문구를 상상하지 않고 CPython에서 받아온다) ---


def _real_import_error_stderr(tmp_path, *, package_exists):
    """`from handwriting import bank_id`가 서버에서 **실제로** 내는 stderr를 받아온다.

    손으로 만든 문자열은 "우리가 상상한 문구"만 검증한다 — 그게 이 헬퍼가 막는 함정이다. 배포
    서버에는 `handwriting/`이 이미 있고 `bank_id.py`만 없어 CPython이 ModuleNotFoundError를 삼키고
    `ImportError: cannot import name 'bank_id' from 'handwriting'`을 낸다(= "No module named"가
    없다). 원격 스크립트도 `python -c`라 cwd가 sys.path에 들어가므로 cwd만 갈아 재현한다.
    """
    root = tmp_path / f"tree-{package_exists}"
    root.mkdir()
    if package_exists:
        (root / "handwriting").mkdir()
    proc = subprocess.run(
        [sys.executable, "-c", "from handwriting import bank_id"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": ""},
    )
    assert proc.returncode != 0, proc.stdout
    return proc.stderr


@pytest.mark.parametrize("package_exists", [True, False])
def test_fetch_error_message_recognizes_what_python_actually_raises(tmp_path, package_exists):
    """배포 누락의 두 형태를 실제 인터프리터 출력으로 닫는다.

    package_exists=True가 **주 시나리오**다(배포 서버에는 handwriting/이 이미 있다) — 예전
    테스트는 손으로 만든 "No module named 'handwriting.bank_id'"만 넣어서 이 경로가 안내문을
    내지 못하는 것을 놓쳤고, 운영자는 행동 지침 대신 raw traceback을 봤다.
    """
    stderr = _real_import_error_stderr(tmp_path, package_exists=package_exists)
    msg = fetch_error_message(stderr)
    assert msg is not None, stderr
    assert "Issue #49" in msg
    assert stderr.strip().splitlines()[-1] in msg  # 원본 마지막 줄(원인)을 삼키지 않는다


def test_fetch_error_message_ignores_an_unrelated_missing_module():
    """M1 — `No module named` 단독 매칭은 서버 venv의 numpy/torch 부재까지 오진한다.

    그 경우 배포는 이미 됐고 실제 원인은 venv라, "태그 배포 후 다시 실행하라"는 지침은
    사용자를 엉뚱한 조치로 보낸다.
    """
    stderr = "ssh 실패(macmini, exit 1): ModuleNotFoundError: No module named 'numpy'"
    assert fetch_error_message(stderr) is None


def test_fetch_error_message_returns_none_for_unrelated_failures():
    assert fetch_error_message("ssh 실패(macmini, exit 255): Connection refused") is None


def test_fetch_all_turns_a_pre_release_import_failure_into_a_guide(tmp_path, monkeypatch):
    """C21 — 순수 헬퍼만 테스트하던 배선(fetch_all의 except RemoteError → RuntimeError)을 실행한다.

    이 배선이 없으면(또는 판정이 발화하지 않으면) 운영자는 안내문 대신 원격 traceback을 본다.
    """
    stderr = _real_import_error_stderr(tmp_path, package_exists=True)
    monkeypatch.setattr(
        "tools.curation_report.run_ssh", _fake_ssh(bank_error=f"ssh 실패(h, exit 1): {stderr}")
    )
    with pytest.raises(RuntimeError, match="Issue #49"):
        _fetch_all(tmp_path)


def test_fetch_all_reraises_an_unrelated_remote_failure_as_is(tmp_path, monkeypatch):
    """무관한 실패를 RuntimeError로 갈아치우면 원인(ssh 계층)과 예외 타입이 함께 지워진다."""
    monkeypatch.setattr(
        "tools.curation_report.run_ssh",
        _fake_ssh(bank_error="ssh 실패(h, exit 255): Connection refused"),
    )
    with pytest.raises(RemoteError, match="Connection refused"):
        _fetch_all(tmp_path)
