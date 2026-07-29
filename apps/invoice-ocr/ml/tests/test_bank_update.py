"""tools.bank_update 단위테스트 (DB/모델 비의존 — 합성 데이터 + Fake 임베딩만)."""

import hashlib
import json
from dataclasses import FrozenInstanceError
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from tools.bank_update import (
    EMB_DIM,
    BankDiff,
    MergePlan,
    _mysql,
    apply_sync,
    backup_bank,
    bank_current_map,
    cmd_apply,
    cmd_plan,
    cmd_score,
    diff_bank,
    diff_from_records,
    excluded_indices,
    has_peer_sample,
    inv_of,
    is_crop_ref,
    load_bank,
    main,
    merge_plan,
    parse_reviewed_job_ids,
    partition_crop_ref,
    partition_valid,
    plan_records,
    prune_missing_crops,
    render_score_md,
    require_env,
    require_removal_confirmation,
    save_bank_atomic,
    score_one,
    score_summary,
    select_desired,
    topk_dedup,
    topk_excluding_self,
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


# --- crop_ref 형식 게이트 (M3: plan 단계에서 걸러 apply의 late fail을 방지) ---


def test_partition_crop_ref_separates_malformed_crop_ref():
    pairs = [_pair(), _pair(id=2, crop_ref="legacy_key_1")]
    valid, invalid = partition_crop_ref(pairs)
    assert [p["crop_ref"] for p in valid] == ["job-1/row-0"]
    assert invalid[0]["reason"] == "bad_crop_ref"


def test_partition_crop_ref_keeps_all_when_well_formed():
    pairs = [_pair(), _pair(id=2, crop_ref="job-2/row-1")]
    valid, invalid = partition_crop_ref(pairs)
    assert len(valid) == 2 and invalid == []


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


# --- plan.jsonl 레코드 검증 (외부 입력 fail-fast — 조용히 버리지 않는다) ---


def test_diff_from_records_rejects_record_missing_required_key():
    with pytest.raises(ValueError, match="필수 키 누락"):
        diff_from_records([{"action": "add"}])


def test_diff_from_records_rejects_non_dict_record():
    """plan.jsonl 한 줄이 JSON 문자열/배열이면 `k not in record`가 부분문자열·원소 검사로
    통과해 이후 인덱싱에서 원시 TypeError가 난다 — 어느 레코드가 문제인지 담아 즉시 실패해야 한다."""
    with pytest.raises(ValueError, match="객체가 아님"):
        diff_from_records(["action crop_ref"])
    with pytest.raises(ValueError, match="객체가 아님"):
        diff_from_records([["action", "crop_ref"]])


def test_diff_from_records_rejects_unknown_action():
    with pytest.raises(ValueError, match="미지의 action"):
        diff_from_records([{"action": "bogus", "crop_ref": "job-1/row-0"}])


def test_diff_from_records_rejects_invalid_crop_ref_format():
    with pytest.raises(ValueError, match="crop_ref 형식 불량"):
        diff_from_records([{"action": "add", "crop_ref": "legacy_key_1"}])


def test_diff_from_records_rejects_add_record_missing_label():
    with pytest.raises(ValueError, match="유효한 label 없음"):
        diff_from_records([{"action": "add", "crop_ref": "job-1/row-0"}])


def test_diff_from_records_rejects_replace_record_with_non_string_label():
    """`match="label"`은 과대 매칭이다 — 모든 에러 메시지가 끝에 record를 담고 그 record에
    label 키가 있어, label 검증이 사라져도 다른 분기가 대신 실패하면 통과한다."""
    with pytest.raises(ValueError, match="유효한 label 없음"):
        diff_from_records([{"action": "replace", "crop_ref": "job-1/row-0", "label": 123}])


def test_diff_from_records_allows_remove_record_without_label_key():
    diff = diff_from_records([{"action": "remove", "crop_ref": "job-1/row-0"}])
    assert diff.remove == ("job-1/row-0",)


def test_diff_from_records_allows_remove_record_with_null_label():
    diff = diff_from_records([{"action": "remove", "crop_ref": "job-1/row-0", "label": None}])
    assert diff.remove == ("job-1/row-0",)


def test_diff_from_records_rejects_duplicate_crop_ref():
    """같은 crop_ref가 2회 이상 등장하면 뱅크 key UNIQUE 전제(멱등성)가 깨지므로 거부한다."""
    records = [
        {"action": "add", "crop_ref": "job-1/row-0", "label": "안가방"},
        {"action": "remove", "crop_ref": "job-1/row-0"},
    ]
    with pytest.raises(ValueError, match="중복"):
        diff_from_records(records)


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


def test_inv_of_rejects_bootstrap_key_instead_of_silently_returning_it():
    """부트스트랩 key('2025-08-18_inv011_0')는 슬래시가 없어 split이 key 자신을 돌려준다.

    조용히 성공하면 어떤 뱅크 항목과도 일치하지 않는 제외 집합이 무성으로 비어버린다
    (spec §2 — 이번 이슈의 초안이 실제로 그 함정에 빠졌다).
    """
    with pytest.raises(ValueError, match="crop_ref 형식이 아님"):
        inv_of("2025-08-18_inv011_0")


# --- npz IO 헬퍼 ---


def _emb(n, dim=EMB_DIM, base=1.0):
    """행마다 값이 다른 결정론적 (n, dim) float32 임베딩 (n=0도 shape 유지)."""
    return np.array([[base + i] * dim for i in range(n)], dtype="float32").reshape(n, dim)


def _write_bank(path, refs, labs, emb=None):
    """합성 bank.npz를 쓴다 — 운영 스키마(emb/lab/inv/keys) 그대로.

    emb를 주면 그대로 쓴다(retrieval 채점처럼 임베딩 값이 결과를 좌우하는 테스트용).
    """
    invs = [r.split("/", 1)[0] for r in refs]
    np.savez(
        path,
        emb=_emb(len(refs)) if emb is None else np.asarray(emb, dtype="float32"),
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


# --- apply sync (Fake 임베딩 주입 — worker/poll.py의 infer_fn 주입 선례) ---


def _ref_value(path):
    """crop 경로 → ref마다 다른 결정론적 정수값(float32에서 정확히 표현되는 범위)."""
    return float(int(hashlib.sha1(str(path).encode()).hexdigest()[:8], 16) % 100000)


def _fake_embed(paths):
    """crop_ref → 고정 벡터(spec §6). 경로에서 값을 유도해 '어느 행이 어느 ref인지' 검증 가능."""
    return np.array([[_ref_value(p)] * EMB_DIM for p in paths], dtype="float32").reshape(
        len(paths), EMB_DIM
    )


def _rec(action, crop_ref, label=None):
    return {"action": action, "crop_ref": crop_ref, "label": label}


def _touch_crop(root, ref):
    """crop_ref에 대응하는 빈 crop PNG를 만든다(M2 크롭 존재 검사를 통과시키는 테스트 fixture)."""
    path = Path(root) / f"{ref}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def test_apply_sync_appends_new_pair_with_label_and_inv(tmp_path):
    bank = _write_bank(tmp_path / "bank.npz", ["legacy_a"], ["공임"])
    _touch_crop(tmp_path, "job-7/row-2")
    summary = apply_sync(bank, [_rec("add", "job-7/row-2", "안가방")], tmp_path, _fake_embed)
    emb, labs, invs, keys = load_bank(bank)
    assert keys == ["legacy_a", "job-7/row-2"]
    assert labs == ["공임", "안가방"]
    assert invs == ["legacy_a", "job-7"]
    assert emb.shape == (2, EMB_DIM)
    assert summary["added"] == 1 and summary["before"] == 1 and summary["after"] == 2


def test_apply_sync_removes_pair_no_longer_desired(tmp_path):
    bank = _write_bank(tmp_path / "bank.npz", ["job-1/row-0", "job-2/row-0"], ["안가방", "공임"])
    summary = apply_sync(bank, [_rec("remove", "job-1/row-0")], tmp_path, _fake_embed)
    assert load_bank(bank)[3] == ["job-2/row-0"]
    assert summary["removed"] == 1


def test_apply_sync_replaces_label_without_growing_bank(tmp_path):
    bank = _write_bank(tmp_path / "bank.npz", ["job-1/row-0"], ["안가방"])
    _touch_crop(tmp_path, "job-1/row-0")
    apply_sync(bank, [_rec("replace", "job-1/row-0", "안전가방")], tmp_path, _fake_embed)
    _, labs, _, keys = load_bank(bank)
    assert keys == ["job-1/row-0"] and labs == ["안전가방"]


def test_apply_sync_never_touches_legacy_keys(tmp_path):
    bank = _write_bank(
        tmp_path / "bank.npz", ["2026-05-12_A/공임_1", "job-1/row-0"], ["공임", "안가방"]
    )
    apply_sync(bank, [_rec("remove", "job-1/row-0")], tmp_path, _fake_embed)
    assert load_bank(bank)[3] == ["2026-05-12_A/공임_1"]


def test_apply_sync_is_noop_when_records_empty(tmp_path):
    bank = _write_bank(tmp_path / "bank.npz", ["job-1/row-0"], ["안가방"])
    before = bank.read_bytes()
    summary = apply_sync(bank, [], tmp_path, _fake_embed)
    assert bank.read_bytes() == before
    assert summary["backup"] is None
    assert summary["added"] == 0 and summary["before"] == summary["after"] == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bank.npz"]


def test_apply_sync_creates_backup_before_write(tmp_path):
    bank = _write_bank(tmp_path / "bank.npz", ["job-1/row-0"], ["안가방"])
    _touch_crop(tmp_path, "job-2/row-0")
    summary = apply_sync(bank, [_rec("add", "job-2/row-0", "공임")], tmp_path, _fake_embed)
    bak = Path(summary["backup"])
    assert bak.exists()
    assert load_bank(bak)[3] == ["job-1/row-0"]


def test_apply_sync_aborts_when_embedding_count_mismatches(tmp_path):
    bank = _write_bank(tmp_path / "bank.npz", ["job-1/row-0"], ["안가방"])
    _touch_crop(tmp_path, "job-2/row-0")
    before = bank.read_bytes()

    def _short_embed(_paths):
        return np.zeros((0, EMB_DIM), dtype="float32")

    with pytest.raises(RuntimeError, match="임베딩 shape 불일치"):
        apply_sync(bank, [_rec("add", "job-2/row-0", "공임")], tmp_path, _short_embed)
    assert bank.read_bytes() == before


def test_apply_sync_aborts_when_embedding_dim_mismatches(tmp_path):
    """어댑터가 잘못된 차원(64)을 반환하면 reshape로 얼버무리지 않고 즉시 중단해야 한다."""
    bank = _write_bank(tmp_path / "bank.npz", ["job-1/row-0"], ["안가방"])
    _touch_crop(tmp_path, "job-2/row-0")
    before = bank.read_bytes()

    def _wrong_dim_embed(paths):
        return np.zeros((len(paths), 64), dtype="float32")

    with pytest.raises(RuntimeError, match="임베딩 shape 불일치"):
        apply_sync(bank, [_rec("add", "job-2/row-0", "공임")], tmp_path, _wrong_dim_embed)
    assert bank.read_bytes() == before
    # M3: 임베딩 실패 시 백업은 그 이전에 시도되지 않으므로 고아 .bak이 남지 않아야 한다.
    assert not any(p.name.endswith(".npz.bak") for p in tmp_path.iterdir())


def test_apply_sync_passes_crop_paths_in_append_order(tmp_path):
    bank = _write_bank(tmp_path / "bank.npz", [], [])
    seen = []

    def _spy_embed(paths):
        seen.extend(str(p) for p in paths)
        return _fake_embed(paths)

    _touch_crop(tmp_path / "crops", "job-2/row-0")
    _touch_crop(tmp_path / "crops", "job-1/row-0")
    records = [_rec("add", "job-2/row-0", "공임"), _rec("add", "job-1/row-0", "안가방")]
    apply_sync(bank, records, tmp_path / "crops", _spy_embed)
    assert seen == [
        str(tmp_path / "crops" / "job-1/row-0.png"),
        str(tmp_path / "crops" / "job-2/row-0.png"),
    ]


def test_apply_sync_keeps_emb_rows_aligned_after_middle_removal(tmp_path):
    """중간 항목 제거 시 남은 임베딩 행이 라벨과 어긋나면 무증상 오라벨 추론이 된다."""
    bank = _write_bank(
        tmp_path / "bank.npz",
        ["job-1/row-0", "job-2/row-0", "job-3/row-0"],
        ["안가방", "공임", "타이어"],
    )
    before_emb = load_bank(bank)[0]
    apply_sync(bank, [_rec("remove", "job-2/row-0")], tmp_path, _fake_embed)
    emb, labs, _, keys = load_bank(bank)
    assert keys == ["job-1/row-0", "job-3/row-0"]
    assert labs == ["안가방", "타이어"]
    assert emb[0][0] == before_emb[0][0]
    assert emb[1][0] == before_emb[2][0]


def test_apply_sync_appends_embedding_of_the_matching_crop(tmp_path):
    bank = _write_bank(tmp_path / "bank.npz", [], [])
    _touch_crop(tmp_path / "crops", "job-2/row-0")
    _touch_crop(tmp_path / "crops", "job-1/row-0")
    records = [_rec("add", "job-2/row-0", "공임"), _rec("add", "job-1/row-0", "안가방")]
    apply_sync(bank, records, tmp_path / "crops", _fake_embed)
    emb, labs, _, keys = load_bank(bank)
    assert keys == ["job-1/row-0", "job-2/row-0"] and labs == ["안가방", "공임"]
    for i, ref in enumerate(keys):
        assert emb[i][0] == _ref_value(tmp_path / "crops" / f"{ref}.png")


def test_apply_sync_leaves_no_diff_when_rerun_against_same_desired(tmp_path):
    """spec §2: 같은 입력으로 재실행하면 diff가 공집합(멱등)."""
    bank = _write_bank(tmp_path / "bank.npz", ["job-1/row-0"], ["안가방"])
    _touch_crop(tmp_path, "job-2/row-0")
    desired = {"job-1/row-0": "안가방", "job-2/row-0": "공임"}
    apply_sync(bank, [_rec("add", "job-2/row-0", "공임")], tmp_path, _fake_embed)
    _, labs, _, keys = load_bank(bank)
    diff = diff_bank(bank_current_map(labs=labs, keys=keys), desired)
    assert diff.add == () and diff.replace == () and diff.remove == ()


def test_apply_sync_aborts_and_keeps_bank_when_backup_fails(tmp_path, monkeypatch):
    """spec §5: 백업 실패 → apply 중단(뱅크 무변경)."""
    bank = _write_bank(tmp_path / "bank.npz", ["job-1/row-0"], ["안가방"])
    _touch_crop(tmp_path, "job-2/row-0")
    before = bank.read_bytes()

    def _boom(*_args, **_kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr("tools.bank_update.shutil.copy2", _boom)
    with pytest.raises(OSError):
        apply_sync(bank, [_rec("add", "job-2/row-0", "공임")], tmp_path, _fake_embed)
    assert bank.read_bytes() == before


def test_apply_sync_aborts_when_crop_file_missing(tmp_path):
    """M2: 크롭 PNG가 없으면 백업·임베딩·모델 로딩 이전에 즉시 중단하고 뱅크를 보존한다."""
    bank = _write_bank(tmp_path / "bank.npz", ["job-1/row-0"], ["안가방"])
    before = bank.read_bytes()

    with pytest.raises(RuntimeError, match="크롭 파일 없음"):
        apply_sync(bank, [_rec("add", "job-2/row-0", "공임")], tmp_path, _fake_embed)

    assert bank.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bank.npz"]


# --- 제거 안전장치 (스테일·오설정 plan의 대량 삭제 차단) ---


def test_require_removal_confirmation_rejects_remove_plan_without_yes():
    """apply는 plan.jsonl만 신뢰하므로, 제거는 명시 승인 없이는 실행되면 안 된다."""
    with pytest.raises(RuntimeError, match="제거 2건"):
        require_removal_confirmation(
            [_rec("remove", "job-1/row-0"), _rec("remove", "job-2/row-0")], confirmed=False
        )


def test_require_removal_confirmation_allows_add_only_plan_without_yes():
    records = [_rec("add", "job-1/row-0", "안가방"), _rec("replace", "job-2/row-0", "공임")]
    assert require_removal_confirmation(records, confirmed=False) is None


def test_require_removal_confirmation_allows_remove_plan_with_yes():
    assert require_removal_confirmation([_rec("remove", "job-1/row-0")], confirmed=True) is None


def test_require_removal_confirmation_validates_records_before_counting():
    """형식 불량 plan은 승인 여부와 무관하게 여기서 먼저 죽는다(뱅크를 열기 전)."""
    with pytest.raises(ValueError, match="crop_ref 형식 불량"):
        require_removal_confirmation([_rec("remove", "legacy_key_1")], confirmed=True)


# --- score: leave-self-out retrieval (§3 score) ---

_LABS = ["안가방", "공임", "안가방"]
_KEYS = ["job-1/row-0", "job-2/row-0", "job-3/row-0"]

# 부트스트랩(구세대) 네임스페이스 — key에 슬래시가 없고 inv는 .jpg가 붙는다(spec §2 실측).
_BOOT_KEYS = ["2025-08-18_inv011_0", "2025-08-18_inv011_1", "2025-08-20_inv012_0"]
_BOOT_INVS = ["2025-08-18_inv011.jpg", "2025-08-18_inv011.jpg", "2025-08-20_inv012.jpg"]


def test_excluded_indices_reads_the_bank_inv_column_not_the_key():
    """전표 판정 근거는 뱅크 inv 열이다 — key 파싱 구현이면 여기서 죽는다(spec §2·§5-1).

    부트스트랩 key에는 슬래시가 없어 inv_of(key) 방식은 key 자신을 돌려주고, 그 값은
    inv 열('...jpg')과 절대 일치하지 않아 제외가 조용히 0건이 된다.
    """
    got = excluded_indices(
        _BOOT_KEYS,
        _BOOT_INVS,
        self_ref="2025-08-18_inv011_0",
        self_inv="2025-08-18_inv011.jpg",
    )
    assert got == {0, 1}


def test_excluded_indices_crop_ref_axis_excludes_only_the_query_itself():
    assert excluded_indices(_BOOT_KEYS, _BOOT_INVS, self_ref="2025-08-18_inv011_0") == {0}


def test_excluded_indices_returns_empty_set_for_a_holdout_query():
    """뱅크에 자기도 동일 전표도 없는 hold-out 표본 — 빈 집합은 정상이다(D3)."""
    assert excluded_indices(_BOOT_KEYS, _BOOT_INVS, self_ref="job-9/row-0") == set()


def test_excluded_indices_rejects_invoice_axis_without_the_inv_column():
    """전표 축을 요청했는데 판단 재료가 없다 — §2의 무성 실패 재발 방지(D3)."""
    with pytest.raises(ValueError, match="invs가 None"):
        excluded_indices(
            _BOOT_KEYS, None, self_ref="2025-08-18_inv011_0", self_inv="2025-08-18_inv011.jpg"
        )


def test_excluded_indices_unions_both_axes_when_they_point_elsewhere():
    """self_ref·self_inv 두 축이 서로소일 때 합집합이어야 한다(덮어쓰기 뮤테이션 방지).

    self_ref는 인덱스 2만, self_inv는 인덱스 0,1만 가리켜 두 축이 겹치지 않는다 — 기존
    테스트는 두 축 결과가 포함관계({0} ⊂ {0,1})라 `out |= {...}`를 `out = {...}`로 바꿔도
    (= self_inv가 주어지면 self_ref 축을 버림) 통과해버려 이 뮤테이션을 잡지 못했다.
    """
    got = excluded_indices(
        _BOOT_KEYS,
        _BOOT_INVS,
        self_ref="2025-08-20_inv012_0",  # 인덱스 2
        self_inv="2025-08-18_inv011.jpg",  # 인덱스 0,1
    )
    assert got == {0, 1, 2}


def test_excluded_indices_rejects_scoring_with_nothing_excluded():
    """제외 없이 채점하면 자기 자신이 항상 1등이라 조용한 오답이 된다(D3)."""
    with pytest.raises(ValueError, match="self_ref/self_inv"):
        excluded_indices(_BOOT_KEYS, _BOOT_INVS)


def test_excluded_indices_rejects_mismatched_keys_and_invs_lengths():
    """판단 재료 두 열이 어긋나면 엉뚱한 인덱스를 제외한다(D3)."""
    with pytest.raises(ValueError, match="길이 불일치"):
        excluded_indices(_BOOT_KEYS, _BOOT_INVS[:2], self_ref="2025-08-18_inv011_0")


def test_topk_dedup_skips_excluded_indices():
    """§5-7 이관 — 자기 제외가 '축 판단'이 아니라 '집합 소비'로 바뀐다."""
    preds = topk_dedup([0.99, 0.5, 0.8], _LABS, {0})
    assert [lb for lb, _ in preds] == ["안가방", "공임"]
    assert preds[0][1] == 0.8


def test_topk_dedup_dedups_labels_like_infer_photo():
    """§5-7 이관 — 중복 제거 규칙은 운영 retrieval(handwriting/infer_photo.py)과 동일하다."""
    labs = ["안가방", "안가방", "공임"]
    preds = topk_dedup([0.9, 0.8, 0.7], labs, set())
    assert [lb for lb, _ in preds] == ["안가방", "공임"]


def test_topk_dedup_respects_k():
    """§5-7 이관."""
    assert len(topk_dedup([0.9, 0.8, 0.7], ["a", "b", "c"], set(), 2)) == 2


def test_topk_dedup_is_empty_when_everything_is_excluded():
    """§5-7 이관 — 자기만 뱅크에 있을 때 빈 결과."""
    assert topk_dedup([0.99], ["안가방"], {0}) == []


def test_topk_dedup_rejects_labs_shorter_than_sims():
    """§5-7 이관(분할) — 길이 검증이 excluded_indices와 topk_dedup 둘에 나뉘어 남는다."""
    with pytest.raises(ValueError, match="길이 불일치"):
        topk_dedup([0.9, 0.8], ["안가방"], set())


def test_topk_dedup_rejects_labs_longer_than_sims_instead_of_dropping_the_tail():
    """D3 — 짧은 labs는 IndexError로 티가 나지만 긴 labs는 아무 신호 없이 꼬리가 버려진다.

    정렬 범위가 len(sims)이기 때문이다. 그래서 별도 케이스로 둔다(spec §5-2).
    """
    with pytest.raises(ValueError, match="길이 불일치"):
        topk_dedup([0.9], ["안가방", "공임"], set())


def test_topk_dedup_rejects_excluded_index_out_of_range():
    """D3 부분 방어 — 항목 1개만큼 줄어든 스테일 제외 집합(경계값 max(excluded) == len(sims))을 잡는다."""
    with pytest.raises(ValueError, match="범위"):
        topk_dedup([0.9, 0.8], ["안가방", "공임"], {2})


def test_topk_excluding_self_skips_the_query_crop_itself():
    preds = topk_excluding_self([0.99, 0.5, 0.8], _LABS, _KEYS, "job-1/row-0", 5)
    assert [lb for lb, _ in preds] == ["안가방", "공임"]
    assert preds[0][1] == 0.8


def test_topk_excluding_self_dedups_labels_like_infer_photo():
    labs = ["안가방", "안가방", "공임"]
    keys = ["job-1/row-0", "job-2/row-0", "job-3/row-0"]
    preds = topk_excluding_self([0.9, 0.8, 0.7], labs, keys, "job-9/row-0", 5)
    assert [lb for lb, _ in preds] == ["안가방", "공임"]


def test_topk_excluding_self_respects_k():
    labs = ["a", "b", "c"]
    keys = ["job-1/row-0", "job-2/row-0", "job-3/row-0"]
    assert len(topk_excluding_self([0.9, 0.8, 0.7], labs, keys, "job-9/row-0", 2)) == 2


def test_topk_excluding_self_is_empty_when_only_self_in_bank():
    assert topk_excluding_self([0.99], ["안가방"], ["job-1/row-0"], "job-1/row-0", 5) == []


def test_topk_excluding_self_rejects_mismatched_lengths():
    with pytest.raises(ValueError, match="길이 불일치"):
        topk_excluding_self([0.9, 0.8], ["안가방"], ["job-1/row-0"], "job-1/row-0", 5)


def test_has_peer_sample_false_for_single_sample_label():
    assert has_peer_sample("job-1/row-0", "안가방", ["안가방"], ["job-1/row-0"]) is False


def test_has_peer_sample_true_when_other_crop_shares_label():
    assert has_peer_sample("job-1/row-0", "안가방", _LABS, _KEYS) is True


def test_score_one_counts_coverage_even_when_only_self_carries_label():
    """단일 샘플 라벨 — 커버리지는 hit, leave-self-out retrieval은 구조적 miss."""
    rec = score_one([0.99], ["안가방"], ["job-1/row-0"], "job-1/row-0", "안가방")
    assert rec["in_bank"] is True
    assert rec["top1"] is False and rec["top5"] is False
    assert rec["has_peer"] is False


def test_score_one_marks_out_of_bank_when_label_absent():
    rec = score_one([0.5], ["공임"], ["job-2/row-0"], "job-1/row-0", "안가방")
    assert rec["in_bank"] is False and rec["top1"] is False


def test_score_one_hits_top1_with_peer_sample():
    rec = score_one([0.99, 0.5, 0.8], _LABS, _KEYS, "job-1/row-0", "안가방")
    assert rec["top1"] is True and rec["top5"] is True and rec["has_peer"] is True


def test_score_one_reports_leave_self_out_top1_sim():
    # self(job-1/row-0, 0.99)를 뺀 top1은 job-3/row-0의 '안가방'(0.8)
    rec = score_one([0.99, 0.5, 0.8], _LABS, _KEYS, "job-1/row-0", "안가방")
    assert rec["top1_sim"] == pytest.approx(0.8)


def test_score_one_top1_sim_is_none_when_no_candidate_remains():
    # 단일 샘플 라벨 — self를 빼면 후보가 0이라 유사도를 말할 수 없다
    rec = score_one([0.99], ["안가방"], ["job-1/row-0"], "job-1/row-0", "안가방")
    assert rec["top1_sim"] is None


def test_score_one_top1_sim_follows_self_exclusion_not_raw_max():
    # raw max는 self의 0.99지만, self 제외 후 top1은 '공임'(0.9)으로 바뀐다.
    # top1_sim은 예측(top1)과 같은 후보를 가리켜야 한다.
    rec = score_one([0.99, 0.9, 0.3], _LABS, _KEYS, "job-1/row-0", "안가방")
    assert rec["top1"] is False
    assert rec["top1_sim"] == pytest.approx(0.9)


def test_score_summary_splits_peer_denominator():
    records = [
        {"in_bank": True, "top1": True, "top5": True, "has_peer": True},
        {"in_bank": True, "top1": False, "top5": False, "has_peer": False},
        {"in_bank": False, "top1": False, "top5": False, "has_peer": False},
    ]
    s = score_summary(records)
    assert s["n"] == 3 and s["in_bank"] == 2 and s["out_of_bank"] == 1
    assert s["top1"] == 1 and s["peer_n"] == 1 and s["peer_top1"] == 1


def _score_rec(*, in_bank=False, top1=False, top5=False, has_peer=False):
    return {"in_bank": in_bank, "top1": top1, "top5": top5, "has_peer": has_peer}


def test_render_score_md_renders_every_cell_with_before_after_percentages():
    """헤더 문자열만 보면 before 열 누락·before/after 뒤바뀜·퍼센트 오산을 못 잡는다."""
    before = score_summary(
        [_score_rec(in_bank=True, top1=True, top5=True, has_peer=True)] + [_score_rec()] * 3
    )
    after = score_summary(
        [
            _score_rec(in_bank=True, top1=True, top5=True, has_peer=True),
            _score_rec(in_bank=True, top5=True, has_peer=True),
            _score_rec(in_bank=True),
            _score_rec(in_bank=True),
        ]
    )
    md = render_score_md(before, after, {"bank_before": 100, "bank_after": 120})

    assert "- 뱅크 크기: 100 → 120" in md
    assert "- 채점 대상(desired 쌍): 4건" in md
    assert "| 커버리지 in-bank(self 포함) | 1/4 (25.0%) | 4/4 (100.0%) |" in md
    assert "| 커버리지 out_of_bank | 3/4 (75.0%) | 0/4 (0.0%) |" in md
    assert "| leave-self-out top-1 | 1/4 (25.0%) | 1/4 (25.0%) |" in md
    assert "| leave-self-out top-5 | 1/4 (25.0%) | 2/4 (50.0%) |" in md
    assert "| peer 존재 한정 top-1 | 1/1 (100.0%) | 1/2 (50.0%) |" in md
    assert "| peer 존재 한정 top-5 | 1/1 (100.0%) | 2/2 (100.0%) |" in md


def test_render_score_md_renders_dash_when_denominator_is_zero():
    """채점 대상 0건에서도 ZeroDivisionError 없이 '0/0 (—)'로 렌더돼야 한다."""
    empty = score_summary([])
    md = render_score_md(empty, empty, {})
    assert "- 뱅크 크기: ? → ?" in md
    assert "| 커버리지 in-bank(self 포함) | 0/0 (—) | 0/0 (—) |" in md
    assert "| peer 존재 한정 top-1 | 0/0 (—) | 0/0 (—) |" in md


# --- DB TSV 파싱 / env 경계 / CLI (mysql 호출 자체는 단위테스트 범위 밖) ---


def test_mysql_fails_fast_when_backend_env_file_missing(tmp_path, monkeypatch):
    """H1: backend env 파일이 없으면 source 실패가 조용히 무시되지 않고 셸 실행 전에 즉시 죽는다."""

    def _boom(*_args, **_kwargs):
        raise AssertionError("backend env 존재 검사 전에 subprocess가 실행되면 안 된다")

    monkeypatch.setattr("tools.bank_update.subprocess.run", _boom)
    with pytest.raises(RuntimeError, match="backend env 파일 없음"):
        _mysql(str(tmp_path / "missing.env"), "SELECT 1")


def test_mysql_query_failure_exposes_only_last_two_stderr_lines(tmp_path, monkeypatch):
    """M2: 예외 메시지에 stderr 전체 대신 마지막 2줄만 담아 비밀번호 파편 유출 표면을 줄인다."""
    env_path = tmp_path / "backend.env"
    env_path.write_text("DB_HOST=127.0.0.1\n")

    class _FakeProc:
        returncode = 1
        stdout = b""
        stderr = b"line1 secret-looking-text\nline2\nline3 last\n"

    monkeypatch.setattr("tools.bank_update.subprocess.run", lambda *a, **k: _FakeProc())
    with pytest.raises(RuntimeError) as exc_info:
        _mysql(str(env_path), "SELECT 1")
    message = str(exc_info.value)
    assert "line1 secret-looking-text" not in message
    assert "line2" in message and "line3 last" in message


def test_parse_reviewed_job_ids_parses_batch_tsv():
    assert parse_reviewed_job_ids("id\n3\n7\n12\n") == {3, 7, 12}


def test_parse_reviewed_job_ids_is_empty_when_no_rows():
    assert parse_reviewed_job_ids("") == set()
    assert parse_reviewed_job_ids("id\n") == set()


def test_parse_reviewed_job_ids_ignores_header_regardless_of_position():
    assert parse_reviewed_job_ids("3\n7\n") == {3, 7}


def test_require_env_fails_fast_with_variable_name(monkeypatch):
    monkeypatch.delenv("SJMJ_ML_MODELS_DIR", raising=False)
    with pytest.raises(RuntimeError, match="SJMJ_ML_MODELS_DIR"):
        require_env("SJMJ_ML_MODELS_DIR")


def test_require_env_returns_value_when_set(monkeypatch):
    monkeypatch.setenv("SJMJ_ML_MODELS_DIR", "/tmp/models")
    assert require_env("SJMJ_ML_MODELS_DIR") == "/tmp/models"


def test_main_requires_a_subcommand():
    with pytest.raises(SystemExit):
        main([])


def test_main_rejects_unknown_subcommand():
    with pytest.raises(SystemExit):
        main(["frobnicate"])


def test_main_apply_rejects_backend_env_option():
    """M6: apply는 조용히 무시하던 --backend-env/--out을 더 이상 받지 않는다(범위 밖 옵션 제거)."""
    with pytest.raises(SystemExit):
        main(["apply", "--plan", "plan.jsonl", "--backend-env", "x.env"])


# --- CLI 오케스트레이션 (M4: fetch_* monkeypatch + 합성 뱅크 + Fake 임베딩) ---


def test_cmd_plan_writes_plan_jsonl_and_only_prunes_missing_crops_for_add_or_replace(
    tmp_path, monkeypatch
):
    """crop 존재 검사는 diff 이후 add/replace에만 적용돼야 한다 — unchanged 항목은 크롭
    PNG가 없어도 plan.jsonl에 remove로 새어 나오면 안 된다."""
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    data_dir = tmp_path / "data"
    crops_root = data_dir / "ocr_crops"
    crops_root.mkdir(parents=True)
    _write_bank(models_dir / "bank.npz", ["job-1/row-0"], ["안가방"])
    _touch_crop(crops_root, "job-2/row-0")

    pairs = [
        _pair(crop_ref="job-1/row-0", canonical_label="안가방"),  # unchanged, 크롭 PNG 없음
        _pair(id=2, job_id=2, crop_ref="job-2/row-0", canonical_label="공임"),  # 신규 add
    ]
    monkeypatch.setattr("tools.bank_update.fetch_pairs", lambda backend_env: pairs)
    monkeypatch.setattr("tools.bank_update.fetch_reviewed_job_ids", lambda backend_env: {1, 2})
    monkeypatch.setenv("SJMJ_DATA_DIR", str(data_dir))
    monkeypatch.setenv("SJMJ_ML_MODELS_DIR", str(models_dir))

    out_dir = tmp_path / "out"
    cmd_plan(SimpleNamespace(backend_env="dummy.env", out=out_dir))

    records = [
        json.loads(ln) for ln in (out_dir / "plan.jsonl").read_text().splitlines() if ln.strip()
    ]
    assert records == [{"action": "add", "crop_ref": "job-2/row-0", "label": "공임"}]


def _apply_workspace(tmp_path, monkeypatch, bank_refs, bank_labs):
    """cmd_apply용 합성 운영 트리(models/bank.npz + data/ocr_crops) + env·Fake 임베딩 주입."""
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    crops_root = tmp_path / "data" / "ocr_crops"
    crops_root.mkdir(parents=True)
    _write_bank(models_dir / "bank.npz", bank_refs, bank_labs)
    monkeypatch.setattr("tools.bank_update.prod_embed_fn", lambda _models_dir: _fake_embed)
    monkeypatch.setenv("SJMJ_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SJMJ_ML_MODELS_DIR", str(models_dir))
    return models_dir, crops_root


def test_cmd_apply_consumes_plan_jsonl_and_updates_bank(tmp_path, monkeypatch):
    models_dir, crops_root = _apply_workspace(tmp_path, monkeypatch, ["job-1/row-0"], ["안가방"])
    _touch_crop(crops_root, "job-2/row-0")

    plan_path = tmp_path / "plan.jsonl"
    plan_path.write_text('{"action": "add", "crop_ref": "job-2/row-0", "label": "공임"}\n')

    cmd_apply(SimpleNamespace(plan=plan_path, yes=False))

    _, labs, _, keys = load_bank(models_dir / "bank.npz")
    assert keys == ["job-1/row-0", "job-2/row-0"]
    assert labs == ["안가방", "공임"]


def test_cmd_apply_reports_worker_restart_requirement(tmp_path, monkeypatch, capsys):
    """워커는 기동 시 1회만 뱅크를 적재하므로, 재시작 전까지 갱신은 추론에 반영되지 않는다."""
    _, crops_root = _apply_workspace(tmp_path, monkeypatch, ["job-1/row-0"], ["안가방"])
    _touch_crop(crops_root, "job-2/row-0")
    plan_path = tmp_path / "plan.jsonl"
    plan_path.write_text('{"action": "add", "crop_ref": "job-2/row-0", "label": "공임"}\n')

    cmd_apply(SimpleNamespace(plan=plan_path, yes=False))

    out = capsys.readouterr().out
    assert "ml-worker" in out and "재시작" in out


def test_cmd_apply_refuses_remove_plan_without_yes(tmp_path, monkeypatch):
    """스테일·오설정 plan(예: 잘못된 --backend-env로 reviewed 0건 → 전량 remove)의 대량 삭제 차단."""
    models_dir, _ = _apply_workspace(
        tmp_path, monkeypatch, ["job-1/row-0", "job-2/row-0"], ["안가방", "공임"]
    )
    bank = models_dir / "bank.npz"
    before = bank.read_bytes()
    plan_path = tmp_path / "plan.jsonl"
    plan_path.write_text(
        '{"action": "remove", "crop_ref": "job-1/row-0"}\n'
        '{"action": "remove", "crop_ref": "job-2/row-0"}\n'
    )

    with pytest.raises(RuntimeError, match="--yes"):
        cmd_apply(SimpleNamespace(plan=plan_path, yes=False))

    assert bank.read_bytes() == before
    assert sorted(p.name for p in models_dir.iterdir()) == ["bank.npz"]


def test_cmd_apply_applies_remove_plan_when_confirmed(tmp_path, monkeypatch):
    models_dir, _ = _apply_workspace(
        tmp_path, monkeypatch, ["job-1/row-0", "job-2/row-0"], ["안가방", "공임"]
    )
    plan_path = tmp_path / "plan.jsonl"
    plan_path.write_text('{"action": "remove", "crop_ref": "job-1/row-0"}\n')

    cmd_apply(SimpleNamespace(plan=plan_path, yes=True))

    assert load_bank(models_dir / "bank.npz")[3] == ["job-2/row-0"]


def test_main_apply_defaults_to_refusing_removals(monkeypatch):
    """--yes는 opt-in이어야 한다 — 기본값이 뒤집히면 제거 안전장치가 통째로 무력화된다."""
    parsed = {}
    monkeypatch.setattr(
        "tools.bank_update.cmd_apply", lambda args: parsed.update(yes=args.yes, plan=args.plan)
    )
    main(["apply", "--plan", "plan.jsonl"])
    assert parsed["yes"] is False

    main(["apply", "--plan", "plan.jsonl", "--yes"])
    assert parsed["yes"] is True


# --- score CLI 오케스트레이션 (행 정렬이 어긋나면 예외 없이 점수만 조용히 틀린다) ---

_SCORE_SLOT = {"job-1/row-0": 0, "job-2/row-0": 1, "job-3/row-0": 1}


def _onehot(slot):
    vec = np.zeros(EMB_DIM, dtype="float32")
    vec[slot] = 1.0
    return vec


_PEER_SIM = 0.8


def _peer_of(slot):
    """slot 쿼리와 sim=_PEER_SIM만큼만 닮은 단위벡터(나머지는 미사용 축으로 채운다).

    피어를 self와 같은 벡터(sim 1.0)로 두면 leave-self-out이 검증되지 않는다 — self 제외를
    통째로 지워도 top1_sim이 1.0으로 같기 때문. 유사도를 1.0에서 떼어놔야 '자기 자신이
    빠졌는지'가 값으로 드러난다.
    """
    vec = np.zeros(EMB_DIM, dtype="float32")
    vec[slot] = _PEER_SIM
    vec[EMB_DIM - 1] = (1.0 - _PEER_SIM**2) ** 0.5
    return vec


def _onehot_embed(paths):
    """crop 경로 → ref별 one-hot 벡터. 상수 벡터와 달리 '어느 쿼리 행인지'가 점수에 드러난다."""
    rows = [_onehot(_SCORE_SLOT[f"{Path(p).parent.name}/{Path(p).stem}"]) for p in paths]
    return np.array(rows, dtype="float32").reshape(len(rows), EMB_DIM)


def test_cmd_score_scores_before_and_after_with_row_aligned_queries(tmp_path, monkeypatch):
    """queries[i]는 valid[i]의 임베딩이어야 한다 — 어긋나면 after top-1이 조용히 무너진다."""
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    data_dir = tmp_path / "data"
    crops_root = data_dir / "ocr_crops"
    crops_root.mkdir(parents=True)
    for ref in ("job-1/row-0", "job-2/row-0"):
        _touch_crop(crops_root, ref)

    # job-3은 job-2의 피어(같은 라벨)지만 벡터는 self와 다르게 둔다 — _peer_of 참조.
    before_bank = _write_bank(tmp_path / "before.npz", ["job-3/row-0"], ["공임"], emb=[_peer_of(1)])
    after_bank = _write_bank(
        models_dir / "bank.npz",
        ["job-3/row-0", "job-1/row-0", "job-2/row-0"],
        ["공임", "안가방", "공임"],
        emb=[_peer_of(1), _onehot(0), _onehot(1)],
    )

    pairs = [
        _pair(crop_ref="job-1/row-0", canonical_label="안가방"),
        _pair(id=2, job_id=2, crop_ref="job-2/row-0", canonical_label="공임"),
    ]
    monkeypatch.setattr("tools.bank_update.fetch_pairs", lambda backend_env: pairs)
    monkeypatch.setattr("tools.bank_update.fetch_reviewed_job_ids", lambda backend_env: {1, 2})
    monkeypatch.setattr("tools.bank_update.prod_embed_fn", lambda _models_dir: _onehot_embed)
    monkeypatch.setenv("SJMJ_DATA_DIR", str(data_dir))
    monkeypatch.setenv("SJMJ_ML_MODELS_DIR", str(models_dir))

    out_dir = tmp_path / "out"
    cmd_score(
        SimpleNamespace(backend_env="dummy.env", out=out_dir, before=before_bank, after=after_bank)
    )

    rows = [
        json.loads(ln) for ln in (out_dir / "score.jsonl").read_text().splitlines() if ln.strip()
    ]
    by_ref = {(r["side"], r["crop_ref"]): r for r in rows}
    assert len(rows) == 4

    # before: 안가방은 뱅크에 없고(out_of_bank), 공임은 peer(job-3/row-0)로 맞춘다.
    assert by_ref[("before", "job-1/row-0")]["in_bank"] is False
    assert by_ref[("before", "job-2/row-0")]["top1"] is True

    # after: 안가방이 들어와 커버리지는 hit이지만 자기 자신뿐이라 leave-self-out은 구조적 miss.
    after_1 = by_ref[("after", "job-1/row-0")]
    assert after_1["in_bank"] is True and after_1["has_peer"] is False and after_1["top1"] is False
    after_2 = by_ref[("after", "job-2/row-0")]
    assert after_2["top1"] is True and after_2["preds"][0] == "공임"

    # score.jsonl에 유사도가 남아야 임계 산정이 가능하다(이 도구 확장의 존재 이유).
    # after_2: self(job-2, sim 1.0)를 빼야 top1이 job-3/row-0의 '공임'(_PEER_SIM)이 된다 —
    # self 제외가 없으면 여기서 1.0이 나온다. 즉 이 단언이 leave-self-out의 판별자다.
    assert after_2["top1_sim"] == pytest.approx(_PEER_SIM)
    # after_1: self(job-1) 제외해도 job-2/job-3('공임', 다른 슬롯이라 sim 0.0)가 후보로 남는다
    # — has_peer(동일 라벨 '안가방' 존재)는 False지만 top1_sim은 후보 유무만 본다.
    assert after_1["top1_sim"] == pytest.approx(0.0)

    md = (out_dir / "score.md").read_text()
    assert "- 뱅크 크기: 1 → 3" in md
    assert "| 커버리지 in-bank(self 포함) | 1/2 (50.0%) | 2/2 (100.0%) |" in md


def test_cmd_score_skips_pairs_without_crop_png(tmp_path, monkeypatch):
    """크롭이 없는 쌍은 임베딩할 수 없어 채점 대상에서 빠진다(0건이어도 리포트는 렌더된다)."""
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    data_dir = tmp_path / "data"
    (data_dir / "ocr_crops").mkdir(parents=True)
    bank = _write_bank(models_dir / "bank.npz", ["job-3/row-0"], ["공임"], emb=[_onehot(1)])

    monkeypatch.setattr(
        "tools.bank_update.fetch_pairs", lambda backend_env: [_pair(canonical_label="안가방")]
    )
    monkeypatch.setattr("tools.bank_update.fetch_reviewed_job_ids", lambda backend_env: {1})
    monkeypatch.setattr("tools.bank_update.prod_embed_fn", lambda _models_dir: _onehot_embed)
    monkeypatch.setenv("SJMJ_DATA_DIR", str(data_dir))
    monkeypatch.setenv("SJMJ_ML_MODELS_DIR", str(models_dir))

    out_dir = tmp_path / "out"
    cmd_score(SimpleNamespace(backend_env="dummy.env", out=out_dir, before=bank, after=bank))

    assert (out_dir / "score.jsonl").read_text().strip() == ""
    assert (
        "| 커버리지 in-bank(self 포함) | 0/0 (—) | 0/0 (—) |" in (out_dir / "score.md").read_text()
    )
