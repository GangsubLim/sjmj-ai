"""plan_relink 단위 테스트 — 합성 데이터(정수 리스트 두 개)만. DB·파일 무의존."""

from dataclasses import FrozenInstanceError

import pytest

from handwriting.relink import (
    RELINK_FAILED,
    NewRow,
    OldPair,
    RelinkPlan,
    plan_relink,
)
from tools.bank_update import is_crop_ref

JOB = 42


def _olds(*supplies):
    """supply 목록을 row_index 0..n-1 · pair_id 100+i 의 옛 쌍으로 만든다."""
    return [OldPair(pair_id=100 + i, row_index=i, supply=s) for i, s in enumerate(supplies)]


def _news(*supplies):
    """supply 목록을 row_index 0..n-1 의 새 행으로 만든다."""
    return [NewRow(row_index=i, supply=s) for i, s in enumerate(supplies)]


def _final_by_pair(plan):
    return {r.pair_id: r.final_ref for r in plan.relinked}


# --- ① 단조 정렬 (§4 ①) ---


def test_identical_rows_keep_every_coordinate_in_place():
    """같은 행 구성이면 매칭이 항등이라 좌표가 제자리에 남는다(재처리 멱등성의 근거)."""
    plan = plan_relink(JOB, _olds(3000, 5000, 7000), _news(3000, 5000, 7000))

    assert plan.orphaned == ()
    assert _final_by_pair(plan) == {
        100: "job-42/row-0",
        101: "job-42/row-1",
        102: "job-42/row-2",
    }


def test_row_inserted_at_top_shifts_every_label_down_by_one():
    """새 줄이 앞에 하나 더 검출되면 옛 쌍 전량이 한 칸씩 밀려 승계된다(이 작업의 주 케이스)."""
    plan = plan_relink(JOB, _olds(3000, 5000), _news(9000, 3000, 5000))

    assert plan.orphaned == ()
    assert _final_by_pair(plan) == {100: "job-42/row-1", 101: "job-42/row-2"}


def test_deleted_row_orphans_only_that_pair():
    """옛 줄 하나가 사라지면 그 쌍만 미결이고 나머지는 순서대로 승계된다."""
    plan = plan_relink(JOB, _olds(3000, 5000, 7000), _news(3000, 7000))

    assert [o.pair_id for o in plan.orphaned] == [101]
    assert _final_by_pair(plan) == {100: "job-42/row-0", 102: "job-42/row-1"}


def test_matching_preserves_order_and_never_crosses():
    """순서가 뒤집힌 새 배치라도 승계된 최종 좌표는 옛 순서에 대해 단조 증가한다.

    옛 (3000, 5000, 7000) vs 새 (3000, 7000, 5000)를 쓴다 — 옛 3000·7000 순서가 새
    배치에서 뒤바뀌어 있어, greedy first-match였다면 교차 매칭(순서 역전)이 나올 수
    있는 입력이다. 매칭이 2건 이상 나와야(len(plan.relinked) >= 2) 단조성 단언이
    공허하지 않다.
    """
    plan = plan_relink(JOB, _olds(3000, 5000, 7000), _news(3000, 7000, 5000))

    assert len(plan.relinked) >= 2
    indexes = [r.final_row_index for r in plan.relinked]
    assert indexes == sorted(indexes)


# --- ② 중복 금액 그룹 게이트 (§4 ②) ---


def test_duplicate_amount_group_with_different_counts_orphans_all_of_them():
    """옛 3줄이 같은 금액인데 새로 2줄만 잡히면 어느 것이 살아남았는지 데이터에 답이 없다."""
    plan = plan_relink(JOB, _olds(5000, 5000, 5000), _news(5000, 5000))

    assert sorted(o.pair_id for o in plan.orphaned) == [100, 101, 102]
    assert plan.relinked == ()


def test_duplicate_amount_group_with_equal_counts_relinks_in_order():
    """개수가 같으면 순서가 답해주므로 순서대로 승계한다."""
    plan = plan_relink(JOB, _olds(5000, 5000), _news(9000, 5000, 5000))

    assert plan.orphaned == ()
    assert _final_by_pair(plan) == {100: "job-42/row-1", 101: "job-42/row-2"}


def test_ambiguous_group_does_not_orphan_unrelated_amounts():
    """게이트는 개수가 어긋난 값에만 걸린다 — 다른 금액의 승계까지 말리지 않는다."""
    plan = plan_relink(JOB, _olds(5000, 5000, 8000), _news(5000, 8000))

    assert sorted(o.pair_id for o in plan.orphaned) == [100, 101]
    assert _final_by_pair(plan) == {102: "job-42/row-1"}


# --- ③ 앵커 부재는 미결 (§4 ③) ---


def test_old_pair_without_supply_is_orphaned():
    """옛 쌍의 supply가 NULL이면 짝지을 재료가 없다."""
    plan = plan_relink(JOB, _olds(None, 5000), _news(3000, 5000))

    assert [o.pair_id for o in plan.orphaned] == [100]
    assert _final_by_pair(plan) == {101: "job-42/row-1"}


def test_new_row_without_supply_never_receives_a_label():
    """새 줄의 supply가 None(금액 미인식)이면 그 줄로는 승계하지 않는다."""
    plan = plan_relink(JOB, _olds(3000), _news(None))

    assert [o.pair_id for o in plan.orphaned] == [100]
    assert plan.relinked == ()


def test_two_rows_missing_supply_do_not_match_each_other():
    """None끼리는 같은 값이 아니다 — 앵커 부재는 서로를 짝지어 주지 않는다."""
    plan = plan_relink(JOB, _olds(None, None), _news(None, None))

    assert plan.relinked == ()
    assert len(plan.orphaned) == 2


# --- 경계 케이스 ---


def test_new_job_without_pairs_yields_an_empty_plan():
    """신규 잡은 training_pairs가 없어 승계가 no-op이다(spec §1 세 번째 재사용)."""
    plan = plan_relink(JOB, [], _news(3000, 5000))

    assert plan == RelinkPlan(relinked=(), orphaned=())
    assert plan.should_release_gate is False


def test_warp_failure_orphans_every_pair():
    """행이 하나도 검출되지 않은 재처리(warp 실패)는 전량 미결로 드러난다 — 안전측."""
    plan = plan_relink(JOB, _olds(3000, 5000), [])

    assert len(plan.orphaned) == 2
    assert plan.should_release_gate is True


# --- 게이트 해제 판정 (§7 · ADR 0011) ---


def test_gate_stays_when_every_pair_is_relinked():
    """미결이 하나도 없는 잡은 게이트를 유지한다 — 전량 재검수 비용을 되살리지 않는다."""
    plan = plan_relink(JOB, _olds(3000), _news(3000))

    assert plan.should_release_gate is False


def test_gate_releases_when_any_pair_is_orphaned():
    """미결이 하나라도 나오면 사람이 볼 것이 생겼으므로 게이트를 푼다."""
    plan = plan_relink(JOB, _olds(3000, 5000), _news(3000))

    assert plan.should_release_gate is True


# --- 충돌 불가능성 불변식 4종 (테스트 전략 §) ---


def test_invariant_1_staging_coordinates_are_unique():
    """임시·orphan 좌표는 pair_id 기반이라 구조적으로 유일하다."""
    plan = plan_relink(JOB, _olds(3000, 3000, 5000), _news(3000, 5000))

    staged = [r.tmp_ref for r in plan.relinked] + [o.orphan_ref for o in plan.orphaned]
    assert len(staged) == len(set(staged))


def test_invariant_2_staging_coordinates_never_look_like_row_refs():
    """임시·orphan 좌표는 is_crop_ref를 통과하지 못한다 — 2단계 네임스페이스와 교차 불가."""
    plan = plan_relink(JOB, _olds(3000, None), _news(5000))

    for ref in [r.tmp_ref for r in plan.relinked] + [o.orphan_ref for o in plan.orphaned]:
        assert not is_crop_ref(ref), ref


def test_invariant_3_stage_one_covers_every_pair_of_the_job():
    """1단계 대상이 그 잡의 쌍 전량이다 — 이것 하나만 깨져도 duplicate key가 난다(§5)."""
    olds = _olds(3000, 5000, None, 5000)
    plan = plan_relink(JOB, olds, _news(5000, 3000))

    staged = {r.pair_id for r in plan.relinked} | {o.pair_id for o in plan.orphaned}
    assert staged == {p.pair_id for p in olds}


def test_invariant_4_final_coordinates_are_unique():
    """매칭이 1:1이므로 최종 좌표는 전부 유일하다."""
    plan = plan_relink(JOB, _olds(3000, 5000, 7000), _news(3000, 5000, 7000))

    finals = [r.final_ref for r in plan.relinked]
    assert len(finals) == len(set(finals))


# --- 계약 형태 ---


def test_relink_plan_is_frozen():
    plan = plan_relink(JOB, _olds(3000), _news(3000))
    with pytest.raises(FrozenInstanceError):
        plan.relinked = ()


def test_exclusion_reason_value_is_relink_failed():
    """미결 쌍의 배제 사유 값은 exclusion_reason VARCHAR(32)에 그대로 들어간다."""
    assert RELINK_FAILED == "relink_failed"
    assert len(RELINK_FAILED) <= 32
