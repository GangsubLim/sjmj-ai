"""빈 크롭 판정 순수 술어 단위테스트(cv2/numpy 무의존 — 코어 venv에서도 돈다)."""

import math

import pytest

from handwriting.blank_crop import (
    BLANK_CROP,
    is_blank,
    is_machine_writable,
    require_blank_ink_max,
)

# --- is_machine_writable: §6 2×2 전수 (불변식의 고정점) ---


@pytest.mark.parametrize(
    ("status", "reason", "expected"),
    [
        ("excluded", None, False),  # 사람이 배제 — 절대 건드리지 않는다
        ("excluded", BLANK_CROP, True),  # 기계가 배제, 사람 미개입 — 갱신 가능
        ("included", BLANK_CROP, False),  # 사람이 되돌림 — 영구 보호(오탐 관측치)
        ("included", None, True),  # 정상 후보 — 새로 배제 가능
    ],
)
def test_is_machine_writable_covers_all_four_cells(status, reason, expected):
    assert is_machine_writable(status, reason) is expected


def test_is_machine_writable_rejects_unknown_reason():
    # 미래에 사유 값이 늘어도 기계가 남의 판정을 덮지 않도록 fail-closed.
    assert is_machine_writable("excluded", "crop_defect") is False
    assert is_machine_writable("included", "crop_defect") is False


def test_is_machine_writable_rejects_unknown_status():
    assert is_machine_writable("pending", None) is False


# --- is_blank: 임계 경계 ---


def test_is_blank_is_true_at_and_below_threshold():
    assert is_blank(0.001, 0.01) is True
    assert is_blank(0.01, 0.01) is True  # 상한 '이하'가 빈 크롭


def test_is_blank_is_false_above_threshold():
    assert is_blank(0.011, 0.01) is False


def test_is_blank_is_false_for_nan():
    # NaN 비교는 항상 False → '빈 크롭 아님'으로 닫힌다. 오탐 0 우선(spec §7)과 같은 방향.
    assert is_blank(math.nan, 0.01) is False


# --- 임계 미확정 fail-fast ---


def test_require_blank_ink_max_raises_while_uncalibrated():
    with pytest.raises(RuntimeError, match="BLANK_INK_MAX"):
        require_blank_ink_max()


def test_require_blank_ink_max_returns_value_once_set(monkeypatch):
    monkeypatch.setattr("handwriting.blank_crop.BLANK_INK_MAX", 0.012)
    assert require_blank_ink_max() == 0.012
