"""warp 정합 게이트 — 판정 순수함수 단위테스트(cv2/numpy 무의존)."""

from handwriting.warp_gate import (
    ENH_MAX_BLUE_ASYMMETRY,
    ENH_MAX_PITCH_DEV,
    ENH_MIN_BLUE_RATIO,
    ENH_MIN_HLINES,
    MAX_BLUE_ASYMMETRY,
    MAX_PITCH_DEV,
    MIN_BLUE_RATIO,
    MIN_HLINES,
    WarpGateMetrics,
    WarpGateMetricsEnh,
    blue_asymmetry,
    evaluate_warp,
    evaluate_warp_enh,
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


# --- evaluate_warp_enh (enh 폴백 판정, cv2/numpy 무의존) ---

# blue_ratio는 마스크 평균이라 도메인이 [0, 1]이다. 확정 ENH_MIN_BLUE_RATIO(=0.0864)에
# 표준의 GOOD_RATIO_FACTOR(=8)를 곱하면 0.6912로 도메인 안에 넉넉히 들어온다.
# min(1.0, ...)으로 클램프하지 않는다 — 클램프는 재캘리브가 픽스처를 도메인 밖으로 밀어내는
# 사건을 조용히 흡수해, 아래 가드 테스트의 `<= 1.0` 경보를 정의상 절대 울리지 않게 만든다
# (표준 축의 test_asymmetry_boundary_case_stays_above_blue_ratio_floor는 그 경보가 실제로
# RED를 내는 진짜 가드다). 도메인 이탈은 그 가드 테스트가 RED로 알린다.
ENH_GOOD_RATIO = ENH_MIN_BLUE_RATIO * GOOD_RATIO_FACTOR


def _enh_metrics(**over) -> WarpGateMetricsEnh:
    base = {
        "hline_count": ENH_MIN_HLINES + 5,
        "pitch_dev": ENH_MAX_PITCH_DEV / 2,
        "blue_ratio_left": ENH_GOOD_RATIO,
        "blue_ratio_right": ENH_GOOD_RATIO,
    }
    return WarpGateMetricsEnh(**{**base, **over})


def test_enh_thresholds_only_tighten_the_sealed_axes():
    # #18의 봉인 규칙(추가 완화 금지, 강화만)이 폴백 경로로 우회되지 않게 실행 가능한
    # 불변식으로 고정한다(spec §5.1). blue 2종은 마스크가 달라 스케일 비교가 불가능하므로
    # 이 가드의 대상이 아니다.
    assert ENH_MIN_HLINES >= MIN_HLINES
    assert ENH_MAX_PITCH_DEV <= MAX_PITCH_DEV


def test_enh_thresholds_are_calibration_pinned():
    # 캘리브 핀(calibration-2026-08-03.md §6 권고 A · 사람 승인 · Issue #64 · 잡 21 누출).
    # 위 test_enh_thresholds_only_tighten_the_sealed_axes의 `>= MIN_HLINES` 부등식은
    # ENH_MIN_HLINES=14(도출식 값)도 허용한다 — 14면 파손 확정 잡 21(캔버스 좌측 ~50%만
    # 덮은 쿼드 오검출, h=14·pitch=0.043)이 폴백을 누출한다(warp_gate.py:135-141 주석 참조).
    # 마진 0%·gap 1개 선(#64)이라 가장 깨지기 쉬운 상수이므로 리터럴로 못 박는다.
    # 의도적 재캘리브면 이 핀도 함께 고쳐라 — 정확히 원하는 동작이다.
    assert (ENH_MIN_HLINES, ENH_MAX_PITCH_DEV, ENH_MIN_BLUE_RATIO, ENH_MAX_BLUE_ASYMMETRY) == (
        15,
        0.1046,
        0.0864,
        0.5515,
    )


def test_enh_passes_on_healthy_enh_metrics():
    assert evaluate_warp_enh(_enh_metrics()) is True


def test_enh_rejects_below_hline_floor():
    assert evaluate_warp_enh(_enh_metrics(hline_count=ENH_MIN_HLINES - 1)) is False


def test_enh_accepts_exactly_at_hline_floor():
    assert evaluate_warp_enh(_enh_metrics(hline_count=ENH_MIN_HLINES)) is True


def test_enh_rejects_above_pitch_ceiling():
    assert evaluate_warp_enh(_enh_metrics(pitch_dev=ENH_MAX_PITCH_DEV * 2)) is False


def test_enh_accepts_exactly_at_pitch_ceiling():
    assert evaluate_warp_enh(_enh_metrics(pitch_dev=ENH_MAX_PITCH_DEV)) is True


def test_enh_rejects_blue_ratio_below_floor_on_either_side():
    low = ENH_MIN_BLUE_RATIO / 2
    # 좌우 동시 low가 먼저다 — 비대칭도가 0이라 실패 사유가 하한 위반으로 격리된다(표준 축의
    # test_fails_when_grid_globally_sparse와 같은 패턴). 한쪽만 낮추면 비대칭도까지 함께
    # 위반하므로, 하한 검사 블록을 통째로 지워도 마지막 비대칭 검사가 거부해 테스트가 초록으로
    # 남는다 — 그 케이스만으로는 하한을 고정하지 못한다.
    assert evaluate_warp_enh(_enh_metrics(blue_ratio_left=low, blue_ratio_right=low)) is False
    # 아래 2건은 하한이 좌·우 어느 쪽에도 걸리는지(축 편향 없음) 확인용이다.
    assert evaluate_warp_enh(_enh_metrics(blue_ratio_left=low)) is False
    assert evaluate_warp_enh(_enh_metrics(blue_ratio_right=low)) is False


def test_enh_accepts_exactly_at_blue_ratio_floor():
    m = _enh_metrics(blue_ratio_left=ENH_MIN_BLUE_RATIO, blue_ratio_right=ENH_MIN_BLUE_RATIO)
    assert evaluate_warp_enh(m) is True


def test_enh_rejects_excessive_asymmetry():
    left = ENH_GOOD_RATIO
    right = left * (1 - ENH_MAX_BLUE_ASYMMETRY) * 0.99  # 비대칭만 초과시키고 하한은 지킨다
    assert right >= ENH_MIN_BLUE_RATIO  # 실패 사유가 비대칭임을 지역적으로 고정
    assert evaluate_warp_enh(_enh_metrics(blue_ratio_left=left, blue_ratio_right=right)) is False


def test_enh_passes_just_inside_asymmetry_limit():
    # 표준 축의 test_passes_just_inside_asymmetry_limit에 대응하는 통과 방향 경계(마진 1%
    # 관용구 동일 — 등호 경계 자체는 부동소수 왕복 오차에 취약해 안/밖 쌍으로 표현한다).
    # 이게 없으면 enh 비대칭 축을 지나는 유일한 케이스가 좌우 완전 대칭인 _enh_metrics()
    # 기본값(비대칭도 0)뿐이라, ENH_MAX_BLUE_ASYMMETRY가 과도하게 강화되는 변경(예: 절반)을
    # 아무 테스트도 잡지 못한다.
    left = ENH_GOOD_RATIO
    right = left * (1 - ENH_MAX_BLUE_ASYMMETRY * 0.99)
    assert right >= ENH_MIN_BLUE_RATIO  # 통과 사유가 하한 미달로 뒤바뀌지 않음을 지역적으로 고정
    assert evaluate_warp_enh(_enh_metrics(blue_ratio_left=left, blue_ratio_right=right)) is True


def test_enh_asymmetry_fixture_stays_inside_the_blue_ratio_domain():
    # 표준 축의 test_asymmetry_boundary_case_stays_above_blue_ratio_floor에 대응하는 enh 가드.
    # 깨지면 테스트가 아니라 ENH_GOOD_RATIO 정의 또는 ENH_MAX_BLUE_ASYMMETRY를 재검토한다.
    assert ENH_MIN_BLUE_RATIO < ENH_GOOD_RATIO <= 1.0
    assert ENH_GOOD_RATIO * (1 - ENH_MAX_BLUE_ASYMMETRY) * 0.99 >= ENH_MIN_BLUE_RATIO


def test_enh_is_fail_closed_on_nan_pitch():
    assert evaluate_warp_enh(_enh_metrics(pitch_dev=float("nan"))) is False


def test_enh_is_fail_closed_on_nan_blue_ratio():
    assert evaluate_warp_enh(_enh_metrics(blue_ratio_left=float("nan"))) is False
    assert evaluate_warp_enh(_enh_metrics(blue_ratio_right=float("nan"))) is False


def test_enh_rejects_the_all_zero_mask_degenerate_case():
    # blue_mask_enh의 `mx < 1` 조기 반환(전부 0 마스크)은 예외가 아니라 자동 fail이어야 한다
    # (spec §5.4).
    assert (
        evaluate_warp_enh(
            WarpGateMetricsEnh(
                hline_count=0, pitch_dev=1.0, blue_ratio_left=0.0, blue_ratio_right=0.0
            )
        )
        is False
    )


def test_evaluate_warp_rejects_enh_metrics_structurally():
    # Task 4 MEDIUM #2: compute_metrics(enhanced=True)의 결과를 표준 evaluate_warp에 실수로
    # 넣으면 enh blue_ratio 스케일 때문에 MIN_BLUE_RATIO 바닥이 무조건 충족돼 fail-open이
    # 된다. 타입을 분리해 이 오배선을 조용한 오판정이 아니라 즉시 TypeError로 닫는다.
    import pytest

    with pytest.raises(TypeError):
        evaluate_warp(_enh_metrics())


def test_evaluate_warp_enh_rejects_standard_metrics_structurally():
    # 대칭 가드 — evaluate_warp_enh에 표준(WarpGateMetrics) 지표가 잘못 들어오는 것도 같은
    # 방식으로 구조적으로 막는다.
    import pytest

    with pytest.raises(TypeError):
        evaluate_warp_enh(_metrics())


# --- compute_metrics (cv2 글루) ---
# cv2가 없는 코어 venv에서는 이 절만 스킵된다. 판정 순수함수 테스트는 계속 돈다.


def _compute(img, **kw):
    import pytest

    pytest.importorskip("cv2", exc_type=ImportError)
    from handwriting.warp_gate import compute_metrics

    return compute_metrics(img, **kw)


def _faint_grid():
    """(b−r) = 10인 합성 격자 — blue_mask의 `> 10`은 통째로 놓치고 blue_mask_enh만 살린다.

    표준 마스크에서 세 필드가 전부 0이므로, enhanced=True에서 하나라도 0으로 남으면
    그 필드가 표준 마스크에서 왔다는 뜻이 된다(spec §5.5 — 마스크 출처를 필드 단위로 고정).
    """
    import pytest

    pytest.importorskip("cv2", exc_type=ImportError)
    np = pytest.importorskip("numpy")

    from handwriting.grid_v4 import WARP_H, WARP_W

    img = np.full((WARP_H, WARP_W, 3), 255, np.uint8)
    for k in range(16):
        y = 620 + k * 83
        img[y : y + 28, 0:WARP_W] = (250, 120, 240)  # b=250, r=240 → b−r = 10 (경계 '초과' 미달)
    return img


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


def test_standard_mask_sees_nothing_in_the_faint_grid():
    m = _compute(_faint_grid())
    assert m.hline_count == 0
    assert m.blue_ratio_left == 0.0
    assert m.blue_ratio_right == 0.0


def test_enhanced_mask_feeds_every_field_of_the_metrics():
    # 세 필드가 '모두 같은 enh 마스크에서' 나오는지 고정한다. hline만 enh로 갈아끼우고
    # blue_ratio를 표준으로 남기는 오배선(#16의 FaintOn 부분 회수와 같은 함정)을 잡는다.
    # 개수는 참고값(이 합성에서 실측 16)일 뿐 단언은 '표준 0 / enh 양수'로 건다 —
    # morphology 파라미터가 바뀌어도 부서지지 않게.
    m = _compute(_faint_grid(), enhanced=True)
    assert m.hline_count > 0
    assert m.blue_ratio_left > 0.0
    assert m.blue_ratio_right > 0.0


def test_compute_metrics_enhanced_output_is_rejected_by_the_standard_gate(make_warped):
    # Task 4 MEDIUM #2를 실제 compute_metrics 경계에서 고정한다 — enhanced=True 산출을
    # evaluate_warp(표준)에 실수로 넣으면 TypeError로 즉시 닫혀야 한다(fail-open 대신).
    import pytest

    m = _compute(make_warped(), enhanced=True)
    assert isinstance(m, WarpGateMetricsEnh)
    with pytest.raises(TypeError):
        evaluate_warp(m)


def test_compute_metrics_is_independent_of_ambient_faint_state():
    # FaintOn을 경유하지 않으므로 ambient _FAINT와 무관하게 같은 값이 나오고,
    # 전역 상태를 변경하지도 않는다(spec §5.2·§5.4 — 전역 mutation 0). make_warped()의
    # 정상 격자(표준·enh 마스크가 둘 다 16선)는 이 가드에 못 쓴다 — hline_ys(조건부
    # FaintOn 로직)로 되돌아가는 회귀가 있어도 두 마스크의 선 개수가 같아 ambient
    # _FAINT값과 무관하게 결과가 같아지므로 검출력이 없다(리뷰 H1). _faint_grid()는
    # 표준 마스크 0선/enh 16선으로 갈라져 있어, hline_ys 경유 시 ambient _FAINT에 따라
    # 값이 달라지는 회귀를 실제로 잡는다.
    # ⚠️ 순서 주의 — _faint_grid()가 먼저다. grid_v4는 모듈 최상단에서 cv2/numpy를 import하므로,
    # `import handwriting.grid_v4`가 앞서면 _faint_grid() 안의 importorskip보다 먼저 실행돼
    # 코어(pillow만) venv에서 이 테스트가 skip이 아니라 ModuleNotFoundError로 실패한다
    # (위 절 머리말이 선언한 "cv2 없는 코어 venv에서는 이 절만 스킵된다" 계약 위반).
    img = _faint_grid()

    import handwriting.grid_v4 as grid_v4

    with grid_v4.FaintOn(True):
        inside = _compute(img)
        assert grid_v4._FAINT is True  # 게이트가 전역을 되돌려놓지 않았음을 확인
    assert inside == _compute(img)
    assert grid_v4._FAINT is False


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
