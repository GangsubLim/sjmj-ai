"""warp 정합 게이트 — 판정 순수함수 단위테스트(cv2/numpy 무의존)."""

from handwriting.warp_gate import (
    MAX_BLUE_ASYMMETRY,
    MAX_PITCH_DEV,
    MIN_BLUE_RATIO,
    MIN_HLINES,
    WarpGateMetrics,
    blue_asymmetry,
    evaluate_warp,
)

# 경계 테스트가 쓰는 '넉넉한' 파랑 비율. MAX_BLUE_ASYMMETRY와 결합돼 있다 —
# 비대칭 초과 테스트가 쓰는 right = GOOD_RATIO * (1 - MAX_BLUE_ASYMMETRY) / 2가
# MIN_BLUE_RATIO 아래로 내려가면 실패 사유가 비대칭이 아니라 하한 위반으로 뒤바뀐다.
# 아래 가드 테스트와 각 비대칭 실패 테스트의 지역 assert가 이 조건을 고정한다.
GOOD_RATIO_FACTOR = 10
GOOD_RATIO = MIN_BLUE_RATIO * GOOD_RATIO_FACTOR


def _metrics(**over) -> WarpGateMetrics:
    base = {
        "hline_count": MIN_HLINES + 5,
        "pitch_dev": MAX_PITCH_DEV / 2,
        "blue_ratio_left": GOOD_RATIO,
        "blue_ratio_right": GOOD_RATIO,
    }
    return WarpGateMetrics(**{**base, **over})


def test_metrics_is_frozen_dataclass():
    import dataclasses

    import pytest

    m = _metrics()
    assert dataclasses.is_dataclass(m)
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.hline_count = 0


def test_passes_on_healthy_grid():
    assert evaluate_warp(_metrics()) is True


def test_fails_when_hlines_below_minimum():
    # 격자 검출 자체가 빈약 = 워프가 전표를 못 담았다(잡 34 유형의 한 축).
    assert evaluate_warp(_metrics(hline_count=MIN_HLINES - 1)) is False


def test_passes_at_hline_boundary():
    assert evaluate_warp(_metrics(hline_count=MIN_HLINES)) is True


def test_fails_when_pitch_diverges():
    # 행 간격이 양식 피치와 발산 = 격자로 보이는 것이 전표 격자가 아니다.
    assert evaluate_warp(_metrics(pitch_dev=MAX_PITCH_DEV * 2)) is False


def test_passes_at_pitch_boundary():
    assert evaluate_warp(_metrics(pitch_dev=MAX_PITCH_DEV)) is True


def test_fails_when_pitch_dev_is_nan():
    # NaN은 `>` 비교에서 항상 False라 fail-open(게이트 통과)할 위험이 있었다. 미지값은
    # 항상 실패 방향으로 닫혀야 한다(fail-safe) — evaluate_warp가 `not (<=)`로 검사함을 고정.
    assert evaluate_warp(_metrics(pitch_dev=float("nan"))) is False


def test_fails_when_right_half_blue_ratio_below_floor():
    # 잡 39 유형: 전표가 좌반에 찌그러지고 우반은 배경 포스터. blue_ratio_right=0.0은
    # MIN_BLUE_RATIO 하한 미달로 걸린다 — 비대칭 검사가 아니라 하한 검사가 실패 사유다.
    assert evaluate_warp(_metrics(blue_ratio_right=0.0)) is False


def test_fails_when_grid_globally_sparse():
    # 잡 34 유형: 배경 과포함으로 격자 픽셀 비율이 전역적으로 낮다.
    low = MIN_BLUE_RATIO / 2
    assert evaluate_warp(_metrics(blue_ratio_left=low, blue_ratio_right=low)) is False


def test_passes_at_blue_ratio_boundary():
    m = _metrics(blue_ratio_left=MIN_BLUE_RATIO, blue_ratio_right=MIN_BLUE_RATIO)
    assert evaluate_warp(m) is True


def test_fails_when_halves_are_asymmetric_beyond_limit():
    left = GOOD_RATIO
    right = left * (1 - MAX_BLUE_ASYMMETRY) / 2  # 허용 비대칭의 2배
    assert right >= MIN_BLUE_RATIO  # 실패 사유가 비대칭임을 스스로 고정(하한 위반이 아님)
    assert evaluate_warp(_metrics(blue_ratio_left=left, blue_ratio_right=right)) is False


def test_fails_when_asymmetric_mirrored():
    # 좌우를 뒤집어도(왼쪽이 작고 오른쪽이 큰 잡 39 거울형) 같은 이유로 실패해야 한다 —
    # blue_asymmetry가 인자 순서에 편향돼 있지 않은지 고정한다.
    right = GOOD_RATIO
    left = right * (1 - MAX_BLUE_ASYMMETRY) / 2
    assert left >= MIN_BLUE_RATIO  # 실패 사유가 비대칭임을 스스로 고정(하한 위반이 아님)
    assert evaluate_warp(_metrics(blue_ratio_left=left, blue_ratio_right=right)) is False


def test_passes_just_inside_asymmetry_limit():
    # 등호 경계 자체는 부동소수 왕복 오차에 취약해(캘리브 값에 따라 위양성 RED) 안/밖
    # 쌍으로 표현한다 — 상수의 1%를 마진으로 둬 캘리브가 바뀌어도 오차에 흔들리지 않는다.
    left = GOOD_RATIO
    right = left * (1 - MAX_BLUE_ASYMMETRY * 0.99)
    assert evaluate_warp(_metrics(blue_ratio_left=left, blue_ratio_right=right)) is True


def test_fails_just_outside_asymmetry_limit():
    left = GOOD_RATIO
    right = left * (1 - MAX_BLUE_ASYMMETRY * 1.01)
    assert right >= MIN_BLUE_RATIO  # 실패 사유가 비대칭임을 스스로 고정(하한 위반이 아님)
    assert evaluate_warp(_metrics(blue_ratio_left=left, blue_ratio_right=right)) is False


def test_blue_asymmetry_is_one_when_both_halves_empty():
    # 0 나눗셈 없이 최악값을 낸다 — 지표는 항상 유한값.
    assert blue_asymmetry(0.0, 0.0) == 1.0


def test_blue_asymmetry_is_zero_when_balanced():
    assert blue_asymmetry(0.02, 0.02) == 0.0


def test_blue_asymmetry_exact_value_at_half():
    # 이진수로 정확히 표현되는 값(2.0/1.0/0.5)으로 등호 포함 의미론을 부동소수 왕복
    # 오차 없이 고정한다: (hi - lo) / hi = (2.0 - 1.0) / 2.0 == 0.5.
    assert blue_asymmetry(2.0, 1.0) == 0.5


def test_blue_asymmetry_is_symmetric():
    assert blue_asymmetry(0.01, 0.03) == blue_asymmetry(0.03, 0.01)


def test_asymmetry_boundary_case_stays_above_blue_ratio_floor():
    # 임계 캘리브(Task 7)가 이 결합을 깨면 위 비대칭 실패 테스트들의 지역 assert가 먼저
    # 실패해 원인을 알려준다. 이 테스트는 그 결합을 상수 산술만으로 조기에 고정하는
    # 가드다. 대응은 경계 테스트 수정이 아니라 GOOD_RATIO_FACTOR를 키우는 것이다.
    assert (1 - MAX_BLUE_ASYMMETRY) * GOOD_RATIO_FACTOR >= 2
