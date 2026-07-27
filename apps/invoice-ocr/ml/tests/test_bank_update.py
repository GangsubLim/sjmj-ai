"""tools.bank_update 단위테스트 (DB/모델 비의존 — 합성 데이터 + Fake 임베딩만)."""

from dataclasses import FrozenInstanceError

import pytest

from tools.bank_update import (
    BankDiff,
    MergePlan,
    bank_current_map,
    diff_bank,
    diff_from_records,
    inv_of,
    is_crop_ref,
    merge_plan,
    partition_valid,
    plan_records,
    prune_missing_crops,
    select_desired,
)


def _pair(**over):
    base = {
        "id": 1,
        "crop_ref": "job-1/row-0",
        "job_id": 1,
        "row_index": 0,
        "draft_label": "엔진오일",
        "final_label": "엔진오일",
        "canonical_label": "엔진오일",
        "supply": 100000,
        "status": "included",
        "reviewed_at": None,
    }
    return {**base, **over}


# --- crop_ref 형식 판정 (구세대 뱅크 key 보호) ---


def test_is_crop_ref_accepts_curation_key_format():
    assert is_crop_ref("job-42/row-0") is True
    assert is_crop_ref("job-7/row-12") is True


def test_is_crop_ref_rejects_legacy_bank_keys():
    assert is_crop_ref("2026-05-12_A/엔진오일_3") is False
    assert is_crop_ref("job-42") is False
    assert is_crop_ref("job-42/row-x") is False
    assert is_crop_ref("job-42/row-0\n") is False


# --- ADR 0004 검수 게이트 ---


def test_select_desired_drops_pairs_from_unreviewed_jobs():
    pairs = [_pair(job_id=1), _pair(id=2, job_id=2, crop_ref="job-2/row-0")]
    assert [p["crop_ref"] for p in select_desired(pairs, {1})] == ["job-1/row-0"]


def test_select_desired_drops_excluded_pairs():
    pairs = [_pair(), _pair(id=2, crop_ref="job-1/row-1", status="excluded")]
    assert [p["crop_ref"] for p in select_desired(pairs, {1})] == ["job-1/row-0"]


def test_select_desired_does_not_mutate_input():
    pairs = [_pair()]
    select_desired(pairs, {1})
    assert pairs[0]["status"] == "included"


# --- 뱅크 current 맵 ---


def test_bank_current_map_keeps_only_crop_ref_keys():
    labs = ["엔진오일", "타이어", "공임"]
    keys = ["job-1/row-0", "2026-05-12_A/타이어_1", "job-2/row-3"]
    assert bank_current_map(labs=labs, keys=keys) == {
        "job-1/row-0": "엔진오일",
        "job-2/row-3": "공임",
    }


# --- sync diff (§2 집합 연산) ---


def test_diff_bank_adds_refs_missing_from_bank():
    diff = diff_bank({}, {"job-1/row-0": "안가방"})
    assert diff.add == ("job-1/row-0",)
    assert diff.replace == () and diff.remove == () and diff.unchanged == ()


def test_diff_bank_removes_refs_no_longer_desired():
    diff = diff_bank({"job-1/row-0": "안가방"}, {})
    assert diff.remove == ("job-1/row-0",)
    assert diff.add == ()


def test_diff_bank_replaces_when_canonical_label_changed():
    diff = diff_bank({"job-1/row-0": "안가방"}, {"job-1/row-0": "안전가방"})
    assert diff.replace == ("job-1/row-0",)
    assert diff.unchanged == ()


def test_diff_bank_is_empty_when_states_match():
    state = {"job-1/row-0": "안가방", "job-2/row-1": "공임"}
    diff = diff_bank(state, dict(state))
    assert diff.add == () and diff.replace == () and diff.remove == ()
    assert diff.unchanged == ("job-1/row-0", "job-2/row-1")


def test_diff_bank_orders_results_deterministically():
    diff = diff_bank({}, {"job-2/row-0": "a", "job-1/row-0": "b"})
    assert diff.add == ("job-1/row-0", "job-2/row-0")


def test_bank_diff_is_frozen():
    diff = BankDiff(add=(), replace=(), remove=(), unchanged=())
    try:
        diff.add = ("x",)
    except Exception as exc:
        assert "frozen" in str(exc).lower() or exc.__class__.__name__ == "FrozenInstanceError"
    else:
        raise AssertionError("BankDiff는 frozen이어야 한다")


# --- plan 검증 (§3 plan 3단계) ---


def test_partition_valid_excludes_empty_canonical_label():
    desired = [_pair(canonical_label=None), _pair(id=2, crop_ref="job-1/row-1")]
    valid, invalid = partition_valid(desired)
    assert [p["crop_ref"] for p in valid] == ["job-1/row-1"]
    assert invalid[0]["reason"] == "empty_label"


def test_partition_valid_excludes_whitespace_only_label():
    valid, invalid = partition_valid([_pair(canonical_label="   ")])
    assert valid == []
    assert invalid[0]["reason"] == "empty_label"


def test_partition_valid_strips_label_whitespace():
    valid, _ = partition_valid([_pair(canonical_label="  안가방 ")])
    assert valid[0]["canonical_label"] == "안가방"


def test_partition_valid_does_not_mutate_input():
    desired = [_pair(canonical_label="  안가방 ")]
    partition_valid(desired)
    assert desired[0]["canonical_label"] == "  안가방 "


# --- 크롭 존재 검사 범위 (spec §3 plan 3단계 — 추가·교체 대상 한정) ---


def test_prune_missing_crops_drops_add_targets_without_png():
    diff = BankDiff(add=("job-1/row-0", "job-2/row-0"), replace=(), remove=(), unchanged=())
    pruned, missing = prune_missing_crops(diff, lambda ref: ref != "job-2/row-0")
    assert pruned.add == ("job-1/row-0",)
    assert missing == ("job-2/row-0",)


def test_prune_missing_crops_never_removes_existing_bank_entries():
    """뱅크에 이미 있는 ref는 크롭 PNG가 사라져도 제거 대상이 되면 안 된다."""
    diff = BankDiff(add=(), replace=(), remove=("job-9/row-0",), unchanged=("job-8/row-0",))
    pruned, missing = prune_missing_crops(diff, lambda _ref: False)
    assert pruned.remove == ("job-9/row-0",)
    assert pruned.unchanged == ("job-8/row-0",)
    assert missing == ()


def test_prune_missing_crops_is_noop_when_all_crops_exist():
    diff = BankDiff(add=("job-1/row-0",), replace=("job-2/row-0",), remove=(), unchanged=())
    pruned, missing = prune_missing_crops(diff, lambda _ref: True)
    assert pruned == diff and missing == ()


# --- plan.jsonl 레코드 (plan ↔ apply 계약) ---


def test_plan_records_carry_action_and_label():
    diff = BankDiff(add=("job-1/row-0",), replace=(), remove=(), unchanged=())
    assert plan_records(diff, {"job-1/row-0": "안가방"}) == [
        {"action": "add", "crop_ref": "job-1/row-0", "label": "안가방"}
    ]


def test_plan_records_remove_entries_have_no_label():
    diff = BankDiff(add=(), replace=(), remove=("job-9/row-0",), unchanged=())
    assert plan_records(diff, {}) == [
        {"action": "remove", "crop_ref": "job-9/row-0", "label": None}
    ]


def test_diff_from_records_roundtrips_plan_records():
    diff = BankDiff(
        add=("job-1/row-0",), replace=("job-2/row-1",), remove=("job-3/row-2",), unchanged=()
    )
    labels = {"job-1/row-0": "안가방", "job-2/row-1": "공임"}
    assert diff_from_records(plan_records(diff, labels)) == diff


# --- 병합 계획 ---


def test_merge_plan_keeps_legacy_and_unchanged_entries():
    keys = ["legacy_key_1", "job-1/row-0", "job-2/row-0"]
    diff = BankDiff(add=(), replace=(), remove=("job-2/row-0",), unchanged=("job-1/row-0",))
    plan = merge_plan(keys, diff)
    assert plan.keep_indices == (0, 1)
    assert plan.append_refs == ()


def test_merge_plan_drops_then_reappends_replaced_refs():
    keys = ["job-1/row-0"]
    diff = BankDiff(add=(), replace=("job-1/row-0",), remove=(), unchanged=())
    plan = merge_plan(keys, diff)
    assert plan.keep_indices == ()
    assert plan.append_refs == ("job-1/row-0",)


def test_merge_plan_appends_added_and_replaced_sorted():
    diff = BankDiff(add=("job-2/row-0",), replace=("job-1/row-0",), remove=(), unchanged=())
    assert merge_plan([], diff).append_refs == ("job-1/row-0", "job-2/row-0")


def test_merge_plan_reapply_does_not_duplicate_added_keys():
    """스테일 plan.jsonl 재적용 시 add 대상이 이미 뱅크 keys에 있어도 중복 없이 수렴해야 한다."""
    keys = ["job-1/row-0"]
    diff = BankDiff(add=("job-1/row-0",), replace=(), remove=(), unchanged=())
    plan = merge_plan(keys, diff)
    resulting_keys = [keys[i] for i in plan.keep_indices] + list(plan.append_refs)
    assert resulting_keys.count("job-1/row-0") == 1


def test_merge_plan_is_frozen_dataclass():
    plan = MergePlan(keep_indices=(), append_refs=())
    with pytest.raises(FrozenInstanceError):
        plan.keep_indices = (0,)


def test_inv_of_extracts_job_prefix():
    assert inv_of("job-42/row-3") == "job-42"
