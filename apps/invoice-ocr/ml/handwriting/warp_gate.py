"""warp 정합 검증 게이트 — 쿼드 오검출을 warp_ok=false로 강등한다(Issue #18).

`warp_ok`는 원래 "쿼드를 찾았다"만 뜻해, 잘못 찾은 쿼드도 통과시켜 템플릿 좌표가
배경을 가리키고 전 행 금액이 0으로 초안됐다(2026-07-27 큐레이션 분석 잡 34·39).
여기서는 워프 결과가 실제 전표 격자와 정합하는지 격자 지표 3종으로 검사한다.

계층 분리:
  · evaluate_warp — cv2/numpy 무의존 순수함수. 코어(pillow만) venv에서 단위테스트한다.
  · compute_metrics — cv2 글루. 기존 격자 함수만 재사용하며 신규 검출 코드는 없다.

⚠️ 모듈 레벨에 cv2/numpy/grid_v4/canon을 import하지 않는다(infer_job.py와 동일 규약) —
   그래야 paddle-free venv에서 `from handwriting.warp_gate import evaluate_warp`가 성공한다.
"""

from dataclasses import dataclass

# ── 판정 임계 ────────────────────────────────────────────────────────────
# TODO(warp-gate): 아래 4개는 캘리브레이션 전 잠정값이다. 운영 warped.png 전수 실측
# (`uv run python -m tools.warp_gate_report report`)으로 분리 마진 최대 지점을 골라
# 확정하고, 이 주석을 근거 리포트 경로로 교체한다.
MIN_HLINES = 10  # DATA_Y 구간 full-width 수평 격자선 최소 개수
MAX_PITCH_DEV = 0.25  # 인접 선 간격의 피치 P 대비 중앙절대편차(P 정규화) 상한
MIN_BLUE_RATIO = 0.004  # 좌·우 반쪽 각각의 파랑 격자 픽셀 비율 하한
MAX_BLUE_ASYMMETRY = 0.60  # 좌우 파랑 비율 비대칭도 상한(0=대칭, 1=한쪽 전무)

# 선이 2개 미만이라 피치를 계산할 수 없을 때 쓰는 최악값 — 지표는 항상 유한값이고
# 방향은 항상 '실패'다(예외를 던지지 않는다).
WORST_PITCH_DEV = 1.0


@dataclass(frozen=True)
class WarpGateMetrics:
    """워프 결과의 격자 정합 지표 4종(전부 유한값)."""

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
    """워프가 전표 격자와 정합하는지 판정한다. False면 warp_ok를 강등한다."""
    if metrics.hline_count < MIN_HLINES:
        return False
    # NaN 입력에도 실패로 닫히도록 `>`/`<` 대신 `not (<=)`/`not (>=)`를 쓴다 — NaN 비교는
    # 항상 False이므로 `>`였다면 NaN이 게이트를 fail-open으로 통과했다.
    if not (metrics.pitch_dev <= MAX_PITCH_DEV):
        return False
    if not (min(metrics.blue_ratio_left, metrics.blue_ratio_right) >= MIN_BLUE_RATIO):
        return False
    return blue_asymmetry(metrics.blue_ratio_left, metrics.blue_ratio_right) <= MAX_BLUE_ASYMMETRY
