"""warp 정합 검증 게이트 — 쿼드 오검출을 warp_ok=false로 강등한다(Issue #18).

`warp_ok`는 원래 "쿼드를 찾았다"만 뜻해, 잘못 찾은 쿼드도 통과시켜 템플릿 좌표가
배경을 가리키고 전 행 금액이 0으로 초안됐다(2026-07-27 큐레이션 분석 잡 34·39).
여기서는 워프 결과가 실제 전표 격자와 정합하는지 격자 지표(4필드)로 검사한다.

계층 분리:
  · evaluate_warp — cv2/numpy 무의존 순수함수. 코어(pillow만) venv에서 단위테스트한다.
  · compute_metrics — cv2 글루. 기존 격자 함수만 재사용하며 신규 검출 코드는 없다.

⚠️ 모듈 레벨에 cv2/numpy/grid_v4/canon을 import하지 않는다(infer_job.py와 동일 규약) —
   그래야 paddle-free venv에서 `from handwriting.warp_gate import evaluate_warp`가 성공한다.
"""

from dataclasses import dataclass

# ── 판정 임계 ────────────────────────────────────────────────────────────
# 2026-07-27 운영 warped.png 전수 실측으로 확정 — 분리 구간의 정상군 쪽 끝(정상군 최악값 +
# 10~20% 안전 마진, plan 선정 규칙)을 취한다. 실제 배치는 정상 쪽 15~18% / 파손 쪽 수백%로
# 비대칭이다(정상군 편향). 근거 리포트:
# docs/work/2026-07/2026-07-27-warp-validation-gate/calibration-2026-07-27.md
# (1차 실측 + 2차 캘리브 "최종 확정" 절 — gitignore 문서라 캘리브 표 원본은 orchestrator가
# PR·Issue #18에도 게시 예정, 이 문서 단독 의존 완화). 파손 워프 5건(잡 24·29·30·31·39)을
# hline_count·pitch_dev 두 지표로 전부 잡는다. 잡 34·38은 워프가 아니라 인식 단계 실패
# (Issue #16 동류)로 게이트의 강등 대상(suspect)에서는 제외됐다. 단 무회귀 분모에는
# 포함되어 pass를 유지해야 한다 — 38을 강등시키는 임계는 동일 지표의 정상 잡 35·36·37도
# 함께 강등시킨다. 게이트 로직 자체는 무변경.
# blue_ratio_min·blue_asym 2종은 잡 24(90° 누움에도 격자가 온전)와 정상군의 분포가 겹쳐
# 단독 판별력이 없지만, plan 선정 규칙(분포가 겹치는 지표도 게이트에서 빼지 않는다)대로
# 그대로 유지한다 — 판정은 마진이 확보된 hline_count/pitch_dev가 싣는다.
# 트레이드오프: MIN_HLINES 외 3종(MAX_PITCH_DEV·MIN_BLUE_RATIO·MAX_BLUE_ASYMMETRY)은
# 캘리브 셋(50잡)에서 이전 잠정값 대비 탐지 증분 0이다. 미관측 워프 실패 모드에 대한 방어
# 깊이를 위해 정상군 여유를 15~18%로 좁힌 의도적 트레이드오프이며, 운영에서 오강등이
# 관측되면 파손군 쪽으로 완화하는 방향으로 재조정한다.
# 잠정 상태: 배포 후 out-of-sample 재검증(신규 잡 누적 시 — Issue #18 체크리스트) 전까지
# 잠정 확정이다(추가 완화 금지, 강화만 허용).
# 재조정 명령: tools/warp_gate_report.py 모듈 docstring 참조(fetch → report --suspect <id...>).
MIN_HLINES = 14  # DATA_Y 구간 full-width 수평 격자선 최소 개수(정상최악 17·파손최선 6, 마진 17.6%)
# 14는 handwriting.canon.global_pitch의 실측 피치 산출 하한(len(ys) >= 14)과 같다 — 이 아래로
# 낮추면 pitch_dev 기준이 실측 피치에서 공칭 83.0으로 바뀐다.
MAX_PITCH_DEV = 0.125  # 피치 P 대비 중앙절대편차 상한(정상최악 0.1079·파손최선 1.000, 마진 15.8%)
MIN_BLUE_RATIO = 0.11  # 좌·우 파랑 격자 픽셀 비율 하한(정상최악 0.1303·파손최선 0.096, 마진 15.6%)
MAX_BLUE_ASYMMETRY = 0.64  # 좌우 파랑 비대칭도 상한(정상최악 0.5532·파손최선 0.8314, 마진 15.7%)

# 선이 2개 미만이라 피치를 계산할 수 없을 때 쓰는 최악값 — 지표는 항상 유한값이고
# 방향은 항상 '실패'다(예외를 던지지 않는다).
WORST_PITCH_DEV = 1.0


@dataclass(frozen=True)
class WarpGateMetrics:
    """표준(blue_mask) 격자 정합 지표 4종(전부 유한값)."""

    hline_count: int
    pitch_dev: float
    blue_ratio_left: float
    blue_ratio_right: float


@dataclass(frozen=True)
class WarpGateMetricsEnh:
    """enh(blue_mask_enh) 격자 정합 지표 4종 — 필드는 WarpGateMetrics와 동일하지만 별개 타입이다.

    Issue #60 Task 4 MEDIUM #2: `compute_metrics(w, enhanced=True)`의 결과를 표준
    `evaluate_warp`에 넣으면 enh blue_ratio가 표준 대비 약 9배라 MIN_BLUE_RATIO 바닥이
    무조건 충족돼 fail-open이 된다. 두 축을 같은 dataclass로 두면 이 오배선이 타입
    수준에서 전혀 드러나지 않는다 — 별개 타입으로 두고 `evaluate_warp`/`evaluate_warp_enh`
    양쪽에 isinstance 가드를 걸어 축 혼입을 조용히 통과시키지 않고 즉시 TypeError로 닫는다.
    """

    hline_count: int
    pitch_dev: float
    blue_ratio_left: float
    blue_ratio_right: float


def blue_asymmetry(left: float, right: float) -> float:
    """좌우 파랑 비율의 비대칭도를 반환한다(0=대칭, 1=한쪽 전무)."""
    hi = max(left, right)
    if hi <= 0.0:
        return 1.0
    return (hi - min(left, right)) / hi


def evaluate_warp(metrics: WarpGateMetrics) -> bool:
    """워프가 전표 격자와 정합하는지 판정한다. False면 warp_ok를 강등한다.

    Raises:
        TypeError: `metrics`가 `WarpGateMetricsEnh`이면 즉시 거부한다(Task 4 MEDIUM #2) —
            enh 지표는 스케일이 달라 표준 임계를 무조건 통과시키는 fail-open이 된다.
    """
    if isinstance(metrics, WarpGateMetricsEnh):
        raise TypeError(
            "evaluate_warp()에 enh 지표(WarpGateMetricsEnh)가 들어왔다 — evaluate_warp_enh()를 써야 "
            "한다. enh blue_ratio는 표준 대비 스케일이 달라 MIN_BLUE_RATIO를 무조건 통과시킨다."
        )
    if metrics.hline_count < MIN_HLINES:
        return False
    # NaN 입력에도 실패로 닫히도록 `>`/`<` 대신 `not (<=)`/`not (>=)`를 쓴다 — NaN 비교는
    # 항상 False이므로 `>`였다면 NaN이 게이트를 fail-open으로 통과했다.
    if not (metrics.pitch_dev <= MAX_PITCH_DEV):
        return False
    # 좌·우 각각을 독립 검사한다(게이트A 리뷰 finding 1) — `min(left, right)`는 NaN을
    # 인자 위치에 비대칭으로 흡수한다: min(x, nan)은 nan이 두 번째 인자일 때 x를 그대로
    # 반환한다(NaN 비교가 항상 False라 갱신되지 않음). blue_ratio_right가 NaN이어도
    # min()이 left만 보고 통과시켜 fail-open했다 — `and`로 두 필드를 각각 닫는다.
    if not (
        metrics.blue_ratio_left >= MIN_BLUE_RATIO and metrics.blue_ratio_right >= MIN_BLUE_RATIO
    ):
        return False
    return blue_asymmetry(metrics.blue_ratio_left, metrics.blue_ratio_right) <= MAX_BLUE_ASYMMETRY


# ── enh 폴백 임계 ────────────────────────────────────────────────────────
# 표준 마스크 판정이 실패했을 때만 쓰는 2단 폴백의 임계(Issue #60). 지표 4종이 전부 같은
# blue_mask에서 파생돼 청색 채도 하나가 3지표를 동시에 무너뜨리는 문제(정상 전표 5회 연속
# 강등, 잡 59~63)를 마스크 축 추가로 푼다 — 임계 완화가 아니다.
# 2026-08-03 원본 62장 전량 재워프 실측으로 확정. 근거 리포트:
# docs/work/2026-07/2026-07-31-warp-gate-enh-fallback/calibration-2026-08-03.md (로컬 전용)
# 도출식(§4, M은 정상군 여유 마진율):
#   ENH_MIN_HLINES         = max(MIN_HLINES,       floor(정상최악_hline × (1 − M)))
#   ENH_MAX_PITCH_DEV      = min(MAX_PITCH_DEV,     round(정상최악_pitch × (1 + M), 4))
#   ENH_MIN_BLUE_RATIO     = round(정상최악_bluemin × (1 − M), 4)
#   ENH_MAX_BLUE_ASYMMETRY = round(정상최악_asym   × (1 + M), 4)
# 축별 선정 규칙이 다르다(spec §5.1):
#   · hline_count·pitch_dev — enh는 선을 더 뽑으므로 자연히 상향한다. 표준 임계보다 느슨해질
#     수 없다(max/min 클램프). #18의 "추가 완화 금지, 강화만" 봉인이 폴백으로 우회되지
#     않게 하는 장치이며, test_enh_thresholds_only_tighten_the_sealed_axes가 실행으로 고정한다.
#   · blue_ratio 2종 — 마스크가 바뀌면 값의 의미 자체가 달라져 동일 스케일 비교가 불가능하다.
#     enh 스케일에서 M=0.165(규칙 도출값) 기준으로 재캘리브했다. 단, calibration-2026-08-03.md
#     §4.2 실측상 blue_ratio_min·blue_asym 2종은 육안 후 라벨(파손 7건)에서 정상군과 분포가
#     겹쳐 전 모집단 비구속(단독 판별력 없음)이다 — 판정은 hline_count·pitch_dev가 싣는다.
#     그래도 제외하지 않고 유지한다(§6 권고 C 미채택, plan 선정 규칙: 겹치는 지표도 게이트에서
#     빼지 않는다).
# ⚠️ ENH_MIN_HLINES=15는 M=0.165 규칙 도출값(14)이 아니다. 14면 육안 파손 확정 잡 21이
#    폴백을 누출한다(잡 21은 전표가 캔버스 좌측 ~50%만 덮은 쿼드 오검출인데 h=14·pitch=0.043로
#    enh 지표가 정상처럼 보인다 — calibration-2026-08-03.md §4.2). 15면 오강등 8건(59~63·65~67)
#    전량 구제 + 파손 7건(17·21·24·29·30·31·39) 전량 차단이 성립한다. 잡 45(h=15) 대비 마진이
#    0%라 #18의 15~18% 마진 규칙 예외이며, 사람이 명시 승인했다(calibration-2026-08-03.md §6
#    권고 A, 2026-08-03). 근본 해결(캔버스 점유율 등 신규 축 도입)은 Issue #64로 분리했다 —
#    이 예외는 #64가 닫히기 전까지의 임시 방어선이다.
# 교차검증 축은 반드시 enh 기준 pitch_dev다 — 표준 기준으로 걸면 잡 63이 pitch_dev=1.0
# (선 부족의 종속 결과)이라 오강등 회수 자체가 무산된다.
ENH_MIN_HLINES = 15  # 규칙 도출값 14 아님 — #64 참조, 위 주석의 마진 예외 승인 사유
ENH_MAX_PITCH_DEV = 0.1046  # M=0.165
ENH_MIN_BLUE_RATIO = 0.0864  # M=0.165 · 전 모집단 비구속(판별력 없음, 위 주석 참조)
ENH_MAX_BLUE_ASYMMETRY = 0.5515  # M=0.165 · 전 모집단 비구속(판별력 없음, 위 주석 참조)


def evaluate_warp_enh(metrics: WarpGateMetricsEnh) -> bool:
    """Enh 마스크로 재측정한 지표로 폴백 판정한다(표준 판정 실패 시에만 호출된다).

    NaN 안전 관용구(`not (<=)` / `not (>=)`)는 evaluate_warp와 동일하다 — NaN 비교가 항상
    False라 `>`/`<`였다면 NaN이 fail-open으로 통과한다.

    Raises:
        TypeError: `metrics`가 `WarpGateMetricsEnh`가 아니면 즉시 거부한다(Task 4 MEDIUM #2의
            대칭 가드) — 표준 지표를 잘못 넣으면 축 혼입이 조용히 넘어가는 대신 즉시 드러난다.
    """
    if not isinstance(metrics, WarpGateMetricsEnh):
        raise TypeError(
            "evaluate_warp_enh()는 WarpGateMetricsEnh만 받는다 — compute_metrics(enhanced=True)의 "
            "결과를 써야 한다."
        )
    if metrics.hline_count < ENH_MIN_HLINES:
        return False
    if not (metrics.pitch_dev <= ENH_MAX_PITCH_DEV):
        return False
    if not (
        metrics.blue_ratio_left >= ENH_MIN_BLUE_RATIO
        and metrics.blue_ratio_right >= ENH_MIN_BLUE_RATIO
    ):
        return False
    return (
        blue_asymmetry(metrics.blue_ratio_left, metrics.blue_ratio_right) <= ENH_MAX_BLUE_ASYMMETRY
    )


def compute_metrics(warped_bgr, *, enhanced: bool = False) -> WarpGateMetrics | WarpGateMetricsEnh:
    """워프된 BGR(WARP_W×WARP_H)에서 게이트 지표 4종을 뽑는다(cv2 글루).

    기존 격자 함수만 재사용한다 — grid_v4.hlines_from_mask, grid_v4.blue_mask(또는
    blue_mask_enh), canon.global_pitch. global_pitch는 운영 행검출
    (infer_photo.extract_rows_for_job)과 **동일 호출**을 써서 게이트가 다운스트림이 실제로 쓸
    피치를 검증하게 한다. 선이 14개 미만이면 global_pitch가 양식 공칭 피치(83.0)를 돌려주므로,
    그때 pitch_dev는 '공칭 대비 편차'가 된다.

    grid_v4.hline_ys가 아니라 hlines_from_mask(mask_fn(w))를 직접 부른다. hline_ys의
    FaintOn 경로는 "enh가 DATA_Y 안에서 선을 더 줄 때만 채택"이라는 **조건부** 로직이라
    폴백에 쓰면 축이 혼합되고, _FAINT는 모듈 전역 mutation이다. 여기서는 전역 상태를
    읽지도 쓰지도 않는다(결정론).

    ⚠️ 여기의 handwriting.canon/grid_v4는 infer_photo가 쓰는 top-level canon/grid_v4와 다른
       모듈 객체다(canon이 sys.path.insert로 top-level import를 한다) — 아래 테스트가
       단언하는 `_FAINT`도 이 모듈 전용이다.

    Args:
        warped_bgr: 워프·deskew된 BGR 이미지.
        enhanced: True면 대비향상 마스크(blue_mask_enh)로 **네 지표 전부**를 재측정한다
            (Issue #60 2단 폴백). 마스크를 한 번만 만들어 hline과 blue_ratio가 같은 축에서
            나오도록 강제한다 — 축이 섞이면 '일관 재측정'이라는 폴백의 전제가 깨진다.

    Raises:
        ValueError: `warped_bgr`가 `(WARP_H, WARP_W)` 크기가 아닐 때. 크기가 다른 입력은
            데이터 조건이 아니라 호출자 버그이므로 빈 슬라이스로 NaN 지표를 만드는 대신
            fail-fast한다.
    """
    import numpy as np

    from handwriting.canon import global_pitch
    from handwriting.grid_v4 import (
        DATA_Y,
        WARP_H,
        WARP_W,
        blue_mask,
        blue_mask_enh,
        hlines_from_mask,
    )

    if warped_bgr.shape[:2] != (WARP_H, WARP_W):
        raise ValueError(f"warped_bgr must be {WARP_H}x{WARP_W}, got {warped_bgr.shape[:2]}")

    y0, y1 = DATA_Y
    mask = (blue_mask_enh if enhanced else blue_mask)(warped_bgr)
    ys = sorted(y for y in hlines_from_mask(mask) if y0 - 40 <= y <= y1 + 40)
    if len(ys) >= 2:
        # 이중선(간격<40px) gap은 MAD 계산에서 배제한다 — 원시 gap 전량으로 MAD를 재면
        # 정상 행검출인 이중검출 전표를 오탐한다. 하한 40px는 기존 이중선 배제 선례
        # (grid_v4.main의 `b - a < 40` 스킵 — 운영 경로가 아니라 진단 CLI다,
        # canon.global_pitch의 50~130 gap 필터)에서 가져왔을 뿐 어느 쪽과도 같은 필터는
        # 아니다. 상한이 없어 행선 누락이 만든 과대 gap은 피치 '추정'(global_pitch의
        # 50~130)에서는 빠지고 MAD에는 남지만, MAD가 중앙값이라 소수 outlier는 흡수되고
        # gap 다수가 어긋난 파손 워프에서만 pitch_dev가 오른다 — 게이트가 원하는 방향이다.
        # 이 필터를 바꾸면 MAX_PITCH_DEV(50잡 실측 캘리브)가 무효가 된다.
        gaps = [g for g in np.diff(ys) if g >= 40]
        if gaps:
            pitch = global_pitch({"x": ys})
            pitch_dev = float(np.median(np.abs(np.array(gaps) - pitch)) / pitch)
            # NaN-안전 클램프 — evaluate_warp의 `not (<=)` 관용구와 통일한다.
            # `min(pitch_dev, WORST_PITCH_DEV)`는 pitch_dev가 NaN이면 NaN을 그대로 반환한다.
            pitch_dev = pitch_dev if pitch_dev <= WORST_PITCH_DEV else WORST_PITCH_DEV
        else:
            pitch_dev = WORST_PITCH_DEV
    else:
        pitch_dev = WORST_PITCH_DEV

    band = mask[y0:y1] > 0
    half = WARP_W // 2
    # enhanced=True는 WarpGateMetricsEnh를 낸다(Task 4 MEDIUM #2) — 표준 evaluate_warp에
    # 잘못 흘러들면 isinstance 가드가 즉시 TypeError로 거부한다.
    cls = WarpGateMetricsEnh if enhanced else WarpGateMetrics
    return cls(
        hline_count=len(ys),
        pitch_dev=pitch_dev,
        blue_ratio_left=float(band[:, :half].mean()),
        blue_ratio_right=float(band[:, half:].mean()),
    )
