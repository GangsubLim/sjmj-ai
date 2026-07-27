"""tools.bank_update 단위테스트 (DB/모델 비의존 — 합성 데이터 + Fake 임베딩만)."""

from tools.bank_update import (
    BankDiff,
    bank_current_map,
    diff_bank,
    is_crop_ref,
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
