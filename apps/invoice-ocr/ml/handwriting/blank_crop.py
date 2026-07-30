"""빈 크롭 자동 배제 — 크롭 잉크율 술어와 기계 쓰기 권한(ADR 0006, Issue #38).

계층 분리(warp_gate.py 관례 그대로):
  · is_blank / is_machine_writable / require_blank_ink_max — cv2·numpy 무의존 순수함수.
  · crop_ink_ratio — cv2 글루. rows._ink_mask/_remove_hlines만 재사용한다(신규 morphology 없음).

⚠️ 모듈 레벨에 cv2/numpy/rows를 import하지 않는다(warp_gate.py와 동일 규약) — 그래야
   paddle-free 코어 venv에서 `from handwriting.blank_crop import is_blank`가 성공한다.
"""

# 크롭 잉크율이 이 값 '이하'면 빈 크롭으로 본다.
# ⚠️ 캘리브레이션 전이라 아직 None이다. 확정 절차는
#    docs/work/2026-07/2026-07-30-blank-crop-auto-exclusion/plan.md "머지 후 운영 단계" 참조.
#    확정 시 warp_gate.py 관례대로 이 주석에 정상최악·빈크롭최선·마진%를 박는다.
BLANK_INK_MAX: float | None = None

BLANK_CROP = "blank_crop"  # 현재 유일한 배제 사유 값(기계 판정)
STATUS_INCLUDED = "included"
STATUS_EXCLUDED = "excluded"


def require_blank_ink_max() -> float:
    """확정된 임계를 반환한다.

    Returns:
        캘리브레이션으로 확정된 크롭 잉크율 상한.

    Raises:
        RuntimeError: BLANK_INK_MAX가 아직 None일 때. 임계 없이 배제를 쓰면 운영 DB의
            학습쌍을 근거 없이 뒤집게 되므로 즉시 멈춘다(spec §8 fail-fast).
    """
    if BLANK_INK_MAX is None:
        raise RuntimeError(
            "BLANK_INK_MAX가 미확정이다 — labels.csv 캘리브레이션 후 blank_crop.py에 확정할 것"
        )
    return BLANK_INK_MAX


def is_blank(ratio: float, threshold: float) -> bool:
    """크롭 잉크율이 임계 이하인지 판정한다(유한 실수만 받는다).

    NaN은 비교가 항상 False라 '빈 크롭 아님'으로 닫힌다 — 오탐 0을 우선하는 방향과 같다.
    판정 불가(크롭 없음·손상)는 여기서 표현하지 않는다. 잉크를 재기 전에 도구가 보류로
    가른다(spec §8) — 세 값 술어를 만들지 않는다.
    """
    return ratio <= threshold


def is_machine_writable(status: str, reason: str | None) -> bool:
    """이 쌍을 기계가 갱신해도 되는지 판정한다 — §6 불변식의 단일 소유자.

    사유가 비어 있음이 곧 "사람 소유" 표식이다(ADR 0006). 규칙은 2×2 전수다:
      (excluded, None)       사람이 배제        → 불가(어떤 경우에도 덮지 않는다)
      (excluded, blank_crop) 기계가 배제        → 가능
      (included, blank_crop) 사람이 되돌림      → 불가(영구 보호 · 오탐 관측치)
      (included, None)       정상 후보          → 가능
    미지의 status·reason 조합은 fail-closed로 거부한다.
    """
    if reason is None:
        return status == STATUS_INCLUDED
    if reason == BLANK_CROP:
        return status == STATUS_EXCLUDED
    return False
