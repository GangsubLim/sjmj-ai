"""cont행 금액 병합(약식 분해) — 합성 데이터만, 모델·이미지 무의존.

spec §5 테스트 1~7 대응: 1~4 = merge_amounts(병합 규칙), 5~7 = block_amounts(소비 오케스트레이션).
"""

from handwriting.group import merge_amounts


def test_single_row_block_preserves_current_value_and_raw():
    # spec §5-1 회귀 방지: 1행 블록은 현행 값·표기를 그대로 보존한다.
    assert merge_amounts([(160, "160")]) == (160, "160")
    assert merge_amounts([(None, "—")]) == (None, "—")


def test_merges_new_and_cont_amounts_with_plus_joined_raw():
    # spec §5-2 (수용 기준 1): new+cont+cont → 금액 합 + "a+b+c" 표기.
    assert merge_amounts([(160, "160"), (40, "40"), (30, "30")]) == (230, "160+40+30")


def test_partial_none_sums_read_values_and_marks_unknown():
    # spec §5-3: 읽힌 값만 부분 합산, 못 읽은 항목은 '?'.
    assert merge_amounts([(160, "160"), (40, "40"), (None, "—")]) == (200, "160+40+?")


def test_all_none_yields_none_amount_and_unknown_raw():
    # spec §5-4: 전부 None이면 금액 None(기존 new행 단독 None 거동과 동일).
    assert merge_amounts([(None, "—"), (None, "!!!")]) == (None, "?+?")


def test_zero_amount_is_summed_not_treated_as_missing():
    # 금액 0은 유효값 — truthiness가 아니라 is not None으로 걸러야 한다(OCR "0" → 0 도달 가능).
    assert merge_amounts([(0, "0"), (40, "40")]) == (40, "0+40")
