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
# blue_ratio는 마스크 픽셀 비율(마스크 평균)이라 도메인이 [0, 1]이다 — FACTOR는 이
# 도메인 밖으로 GOOD_RATIO를 밀어내지 않는 값이어야 한다(아래 가드 테스트가 고정).
GOOD_RATIO_FACTOR = 8
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


def test_fails_when_blue_ratio_right_is_nan():
    # 게이트A 리뷰: `min(left, nan)`은 left를 그대로 반환한다(NaN 비교는 항상 False라
    # min()이 두 번째 인자의 NaN을 흡수). 이후 blue_asymmetry(left, nan)도 0.0으로
    # 흡수돼(hi=max(left, nan)==left) 좌우 대칭으로 오판 — fail-open 회귀 고정.
    assert evaluate_warp(_metrics(blue_ratio_right=float("nan"))) is False


def test_fails_when_blue_ratio_left_is_nan():
    # 인자 순서를 뒤집어도 동일하게 닫혀야 한다 — min()의 NaN 흡수는 인자 위치에
    # 비대칭이므로(첫 인자 NaN은 min()이 그대로 전파해 우연히 닫혔었다) 양쪽 다 고정한다.
    assert evaluate_warp(_metrics(blue_ratio_left=float("nan"))) is False


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
    # 가드다. 대응은 경계 테스트 수정이 아니라 FACTOR 조정(단 GOOD_RATIO <= 1.0 유지)
    # 또는 MAX_BLUE_ASYMMETRY 재검토다.
    assert GOOD_RATIO <= 1.0  # blue_ratio는 마스크 평균이라 도메인이 [0, 1]
    assert (1 - MAX_BLUE_ASYMMETRY) * GOOD_RATIO_FACTOR >= 2


# --- compute_metrics (cv2 글루) ---
# cv2가 없는 코어 venv에서는 이 절만 스킵된다. 판정 순수함수 테스트는 계속 돈다.


def _compute(img):
    import pytest

    pytest.importorskip("cv2", exc_type=ImportError)
    from handwriting.warp_gate import compute_metrics

    return compute_metrics(img)


def test_compute_metrics_on_healthy_grid_passes_gate(make_warped):
    m = _compute(make_warped())
    assert m.hline_count == 16
    assert m.pitch_dev == 0.0
    assert m.blue_ratio_left == m.blue_ratio_right > MIN_BLUE_RATIO
    assert evaluate_warp(m) is True


def test_compute_metrics_flags_half_width_grid(make_warped):
    # 잡 39 유형 — 좌반(x<450)에만 격자. 선 개수·피치는 정상이지만 우반이 비어 있다.
    m = _compute(make_warped(x_end=450))
    assert m.hline_count == 16
    assert m.blue_ratio_right == 0.0
    assert evaluate_warp(m) is False


def test_compute_metrics_flags_sparse_grid(make_warped):
    # 잡 34 유형 — 배경 과포함으로 격자가 희박하고 간격이 공칭 피치와 발산.
    m = _compute(make_warped(n_lines=6, pitch=40, x_end=350, thickness=3))
    assert m.hline_count == 6
    assert m.pitch_dev > MAX_PITCH_DEV
    assert evaluate_warp(m) is False


def test_compute_metrics_returns_worst_pitch_when_no_lines(make_warped):
    # 선 2개 미만이면 예외 없이 최악값 — 지표는 항상 유한값(spec §5).
    from handwriting.warp_gate import WORST_PITCH_DEV

    blank = make_warped(n_lines=0)
    m = _compute(blank)
    assert m.hline_count == 0
    assert m.pitch_dev == WORST_PITCH_DEV
    assert m.blue_ratio_left == 0.0
    assert evaluate_warp(m) is False


def test_compute_metrics_raises_on_wrong_shape():
    # 900x2100이 아닌 입력은 데이터 조건이 아니라 호출자 버그다 — 빈 슬라이스 .mean()이
    # NaN 지표를 조용히 만드는 대신 fail-fast해야 한다.
    import pytest

    np = pytest.importorskip("numpy")

    bad = np.zeros((300, 200, 3), dtype=np.uint8)
    with pytest.raises(ValueError, match="900"):
        _compute(bad)


def test_compute_metrics_filters_doubled_lines_from_pitch():
    # 기존 이중선 배제 선례(grid_v4.main의 `b - a < 40` 스킵 — 진단 CLI, canon.global_pitch의
    # 50~130 gap 필터)와 마찬가지로 게이트도 이중선(간격<40px)을 배제해야 한다. 게이트가
    # 원시 gap 전량으로 MAD를 재면 정상 행검출인 이중검출 전표를 오탐한다.
    import pytest

    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2", exc_type=ImportError)
    from handwriting.grid_v4 import WARP_H, WARP_W

    img = np.full((WARP_H, WARP_W, 3), 255, np.uint8)
    y_start, pitch, n_lines, thickness, gap = 620, 83, 16, 3, 10
    for k in range(n_lines):
        y = y_start + k * pitch
        img[y : y + thickness, 0:WARP_W] = (255, 120, 40)
        img[y + gap : y + gap + thickness, 0:WARP_W] = (255, 120, 40)  # 이중선(gap<40)

    m = _compute(img)
    assert m.hline_count == 32  # 원시 검출 개수는 이중선까지 그대로 반영한다
    assert m.pitch_dev <= MAX_PITCH_DEV  # 이중선 gap이 MAD 계산에서 배제돼야 오탐하지 않는다


def test_compute_metrics_forces_deterministic_faint_state(make_warped, monkeypatch):
    # docstring이 "두 함수가 순수함수라 동일 동작"이라 주장했지만 hline_ys는 grid_v4의
    # 모듈 전역 _FAINT를 읽는다(FaintOn으로 토글). ambient 상태와 무관하게 항상
    # FaintOn(False)로 고정 호출됨을 spy로 고정한다(spec §3.1: 게이트는 결정론적).
    import handwriting.grid_v4 as grid_v4

    seen_faint_states = []
    original_hline_ys = grid_v4.hline_ys

    def spy(warped):
        seen_faint_states.append(grid_v4._FAINT)
        return original_hline_ys(warped)

    monkeypatch.setattr(grid_v4, "hline_ys", spy)

    with grid_v4.FaintOn(True):
        _compute(make_warped())

    assert seen_faint_states == [False]


def test_compute_metrics_clamps_nan_pitch_dev_to_worst(make_warped, monkeypatch):
    # `min(pitch_dev, WORST_PITCH_DEV)`는 pitch_dev가 NaN이면 NaN을 그대로 반환한다
    # (`min(nan, 1.0) == nan`). evaluate_warp의 `not (<=)` 관용구와 통일된 NaN-안전
    # 클램프여야 한다.
    import pytest

    np = pytest.importorskip("numpy")
    from handwriting.warp_gate import WORST_PITCH_DEV

    monkeypatch.setattr(np, "median", lambda *args, **kwargs: float("nan"))

    m = _compute(make_warped())
    assert m.pitch_dev == WORST_PITCH_DEV
