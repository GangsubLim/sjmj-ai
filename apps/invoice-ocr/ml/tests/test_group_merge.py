"""cont행 금액 병합(약식 분해) — 합성 데이터만, 모델·이미지 무의존.

spec §5 테스트 1~7 대응: 1~4 = merge_amounts(병합 규칙), 5~7 = block_amounts(소비 오케스트레이션).
"""

import pytest

from handwriting.group import (
    ROW_NEW,
    ROW_TOTAL,
    Row,
    block_amounts,
    build_proposal,
    form_blocks,
    merge_amounts,
)

BAND_H = 10


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


def rows_from_types(types):
    """types 시퀀스를 form_blocks 규칙대로 block이 부여된 합성 Row 리스트로 만든다.

    box는 _assemble(group.py:115-120)을 충실히 재현한다 — new행뿐 아니라 total행도 truthy.
    (total에 box가 없으면 선별 술어가 느슨해져도 테스트가 통과해 negative case가 무력해진다.)
    """
    blocks = form_blocks(types)
    block_of = {i: bi for bi, blk in enumerate(blocks) for i in blk}
    return [
        Row(
            band=(i * BAND_H, i * BAND_H + BAND_H),
            item_ink=0.0,
            amt_ink=0.0,
            rtype=t,
            box=(i * BAND_H, i * BAND_H + BAND_H) if t in (ROW_NEW, ROW_TOTAL) else None,
            block=block_of.get(i),
            db_idx=None,
            db_name=None,
        )
        for i, t in enumerate(types)
    ]


class FakeReader:
    """read_fn 대역 — 밴드별 고정 응답을 돌려주고 호출된 밴드를 기록한다."""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def __call__(self, row):
        self.calls.append(row.band)
        return self.table[row.band]


def test_block_amounts_reads_every_band_and_merges_per_new_row():
    # spec §5-5: new+cont+cont의 세 밴드가 모두 소비되고 결과는 new행당 하나.
    rows = rows_from_types(["new", "cont", "cont"])
    reader = FakeReader({(0, 10): (160, "160"), (10, 20): (40, "40"), (20, 30): (30, "30")})

    news, amounts = block_amounts(rows, reader)

    assert reader.calls == [(0, 10), (10, 20), (20, 30)]
    assert [r.band for r in news] == [(0, 10)]
    assert amounts == [(230, "160+40+30")]


def test_orphan_cont_block_is_excluded_without_reading():
    # spec §5-6: orphan cont 밴드는 read_fn 호출도 출력도 없다(정렬 어긋남 차단).
    # FakeReader.table에 (0, 10)이 없으므로 호출되면 KeyError로 즉시 드러난다.
    rows = rows_from_types(["cont", "new", "cont"])
    reader = FakeReader({(10, 20): (100, "100"), (20, 30): (20, "20")})

    news, amounts = block_amounts(rows, reader)

    assert reader.calls == [(10, 20), (20, 30)]
    assert [r.band for r in news] == [(10, 20)]
    assert amounts == [(120, "100+20")]


def test_total_and_empty_rows_are_never_read_or_emitted():
    # 선별 술어 회귀 가드: total행은 _assemble이 box를 채우므로(group.py:119-120) box만으로
    # 거르면 합계가 품목으로 새어나온다. rtype 조건이 빠지면 이 테스트가 깨진다.
    rows = rows_from_types(["new", "cont", "total", "empty"])
    reader = FakeReader({(0, 10): (160, "160"), (10, 20): (40, "40")})

    news, amounts = block_amounts(rows, reader)

    assert reader.calls == [(0, 10), (10, 20)]  # total/empty 밴드는 OCR 호출조차 없다
    assert [r.rtype for r in news] == ["new"]
    assert amounts == [(200, "160+40")]


def test_read_fn_exception_propagates_to_job_level():
    # spec §4.4: 셀별 try/except 금지 — 계통적 실패가 '그럴듯한 부분합'으로 은폐되면 안 된다.
    # new행은 정상 반환하고 cont행에서만 raise해야, cont 읽기만 삼키는 변이가 RED로 잡힌다.
    rows = rows_from_types(["new", "cont"])

    def boom(row):
        if row.rtype != ROW_NEW:
            raise RuntimeError("mlx generate 실패")
        return 160, "160"

    with pytest.raises(RuntimeError):
        block_amounts(rows, boom)


def test_job27_pattern_merges_item_blank_rows_into_preceding_item():
    # spec §5-7 (수용 기준 3): 잡 27 유형 — 품목칸이 빈 두 행(40·30)이 위 품목(160)에 합산.
    # classify_types → form_blocks 실제 경로를 build_proposal로 경유한다.
    bands = [(0, 10), (10, 20), (20, 30), (30, 40)]
    item_inks = [0.20, 0.00, 0.00, 0.20]
    amt_inks = [0.20, 0.20, 0.20, 0.20]
    stroke_rows = [[True] * BAND_H for _ in bands]
    prop = build_proposal(
        bands, item_inks, amt_inks, stroke_rows, [], item_min=0.04, amt_min=0.045, pad=0
    )
    reader = FakeReader(
        {(0, 10): (160, "160"), (10, 20): (40, "40"), (20, 30): (30, "30"), (30, 40): (50, "50")}
    )

    news, amounts = block_amounts(prop.rows, reader)

    assert [r.rtype for r in prop.rows] == ["new", "cont", "cont", "new"]  # 분류 자체는 불변
    assert len(news) == 2
    assert amounts[0] == (230, "160+40+30")  # 160 단독이 아니라 병합값
    assert amounts[1] == (50, "50")
