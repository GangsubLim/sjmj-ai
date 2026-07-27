"""tools.bank_update 단위테스트 (DB/모델 비의존 — 합성 데이터 + Fake 임베딩만)."""

from dataclasses import FrozenInstanceError
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from tools.bank_update import (
    EMB_DIM,
    BankDiff,
    MergePlan,
    backup_bank,
    bank_current_map,
    diff_bank,
    diff_from_records,
    inv_of,
    is_crop_ref,
    load_bank,
    merge_plan,
    partition_valid,
    plan_records,
    prune_missing_crops,
    save_bank_atomic,
    select_desired,
    validate_bank_arrays,
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


# --- npz IO 헬퍼 ---


def _emb(n, dim=EMB_DIM, base=1.0):
    """행마다 값이 다른 결정론적 (n, dim) float32 임베딩 (n=0도 shape 유지)."""
    return np.array([[base + i] * dim for i in range(n)], dtype="float32").reshape(n, dim)


def _write_bank(path, refs, labs):
    """합성 bank.npz를 쓴다 — 운영 스키마(emb/lab/inv/keys) 그대로."""
    invs = [r.split("/", 1)[0] for r in refs]
    np.savez(
        path,
        emb=_emb(len(refs)),
        lab=np.array(labs, object),
        inv=np.array(invs, object),
        keys=np.array(refs, object),
    )
    return path


# --- 뱅크 로드 fail-fast (§5) ---


def test_load_bank_fails_fast_when_file_missing(tmp_path):
    with pytest.raises(RuntimeError, match="뱅크 파일 없음"):
        load_bank(tmp_path / "bank.npz")


def test_load_bank_fails_fast_when_array_key_missing(tmp_path):
    path = tmp_path / "bank.npz"
    np.savez(path, emb=_emb(1), lab=np.array(["a"], object))
    with pytest.raises(RuntimeError, match="키 구조 불일치"):
        load_bank(path)


def test_load_bank_returns_four_aligned_sequences(tmp_path):
    path = _write_bank(tmp_path / "bank.npz", ["job-1/row-0", "job-2/row-1"], ["안가방", "공임"])
    emb, labs, invs, keys = load_bank(path)
    assert emb.shape == (2, EMB_DIM)
    assert labs == ["안가방", "공임"]
    assert invs == ["job-1", "job-2"]
    assert keys == ["job-1/row-0", "job-2/row-1"]


# --- 저장 전 정합 검증 (§5 — 워커는 구조 불량을 추론 시점까지 잠복시킨다) ---


def test_validate_rejects_array_length_mismatch():
    with pytest.raises(RuntimeError, match="길이 불일치"):
        validate_bank_arrays(_emb(2), ["a"], ["job-1", "job-2"], ["k1", "k2"])


def test_validate_rejects_wrong_embedding_dim():
    with pytest.raises(RuntimeError, match="차원 이상"):
        validate_bank_arrays(_emb(1, dim=64), ["a"], ["job-1"], ["k1"])


def test_validate_rejects_non_finite_embedding():
    emb = _emb(1)
    emb[0][0] = np.nan
    with pytest.raises(RuntimeError, match="NaN"):
        validate_bank_arrays(emb, ["a"], ["job-1"], ["k1"])


def test_validate_rejects_duplicate_crop_ref_keys():
    """crop_ref 형식 key(job-N/row-M)가 중복되면 sync 멱등성이 깨지므로 저장 전 차단한다."""
    with pytest.raises(RuntimeError, match="중복"):
        validate_bank_arrays(
            _emb(2), ["a", "b"], ["job-1", "job-1"], ["job-1/row-0", "job-1/row-0"]
        )


def test_validate_accepts_well_formed_bank():
    assert validate_bank_arrays(_emb(2), ["a", "b"], ["job-1", "job-2"], ["k1", "k2"]) is None


# --- 백업 / 원자적 쓰기 / 롤백 ---


def test_backup_bank_copy_is_byte_identical(tmp_path):
    src = _write_bank(tmp_path / "bank.npz", ["job-1/row-0"], ["안가방"])
    dst = backup_bank(src)
    assert dst.exists() and dst != src
    assert dst.read_bytes() == src.read_bytes()
    assert dst.name.startswith("bank.") and dst.name.endswith(".npz.bak")


def test_save_bank_atomic_roundtrips(tmp_path):
    path = tmp_path / "bank.npz"
    save_bank_atomic(path, _emb(1), ["안가방"], ["job-1"], ["job-1/row-0"])
    emb, labs, invs, keys = load_bank(path)
    assert labs == ["안가방"] and invs == ["job-1"] and keys == ["job-1/row-0"]
    assert emb.shape == (1, EMB_DIM)


def test_save_bank_atomic_leaves_no_tmp_file(tmp_path):
    path = tmp_path / "bank.npz"
    save_bank_atomic(path, _emb(1), ["안가방"], ["job-1"], ["job-1/row-0"])
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bank.npz"]


def test_save_bank_atomic_keeps_original_when_validation_fails(tmp_path):
    path = _write_bank(tmp_path / "bank.npz", ["job-1/row-0"], ["안가방"])
    before = path.read_bytes()
    with pytest.raises(RuntimeError):
        save_bank_atomic(path, _emb(2), ["안가방"], ["job-1"], ["job-1/row-0"])
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bank.npz"]


def test_backup_bank_fails_fast_when_backup_already_exists(tmp_path, monkeypatch):
    """같은 초에 재실행해 백업 파일명이 충돌하면 무경고 덮어쓰기 대신 즉시 실패한다."""
    src = _write_bank(tmp_path / "bank.npz", ["job-1/row-0"], ["안가방"])

    class _FixedDatetime:
        @classmethod
        def now(cls):
            return datetime(2026, 1, 1, 0, 0, 0)

    monkeypatch.setattr("tools.bank_update.datetime", _FixedDatetime)
    backup_bank(src)
    with pytest.raises(RuntimeError, match="이미 존재"):
        backup_bank(src)


def test_save_bank_atomic_removes_tmp_file_when_savez_fails(tmp_path, monkeypatch):
    """savez 도중 실패해도 tmp 파일이 남지 않고 원본 뱅크가 보존돼야 한다."""
    path = _write_bank(tmp_path / "bank.npz", ["job-1/row-0"], ["안가방"])
    before = path.read_bytes()

    def _boom(file, **_kwargs):
        Path(file).touch()  # 실제 savez처럼 부분 파일을 남기고 실패하는 상황 재현
        raise OSError("disk full")

    monkeypatch.setattr(np, "savez", _boom)
    with pytest.raises(OSError, match="disk full"):
        save_bank_atomic(path, _emb(1), ["공임"], ["job-2"], ["job-2/row-0"])

    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bank.npz"]


def test_save_bank_atomic_normalizes_embedding_dtype_to_float32(tmp_path):
    """float64로 들어온 임베딩도 저장 시 float32로 정규화돼야 한다(dtype 승격 방지)."""
    path = tmp_path / "bank.npz"
    emb64 = _emb(1).astype("float64")
    save_bank_atomic(path, emb64, ["안가방"], ["job-1"], ["job-1/row-0"])
    loaded_emb, _, _, _ = load_bank(path)
    assert loaded_emb.dtype == np.dtype("float32")


def test_backup_restores_previous_bank_on_rollback(tmp_path):
    path = _write_bank(tmp_path / "bank.npz", ["job-1/row-0"], ["안가방"])
    bak = backup_bank(path)
    save_bank_atomic(path, _emb(1, base=9.0), ["공임"], ["job-2"], ["job-2/row-0"])
    assert load_bank(path)[1] == ["공임"]

    path.write_bytes(bak.read_bytes())  # 런북 롤백 절차와 동일
    assert load_bank(path)[1] == ["안가방"]
