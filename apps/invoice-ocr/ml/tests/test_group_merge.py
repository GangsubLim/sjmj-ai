"""cont행 금액 병합(약식 분해) — 합성 데이터만, 모델·이미지 무의존.

spec §5 테스트 1~7 대응: 1~4 = merge_amounts(병합 규칙), 5~7 = block_amounts(소비 오케스트레이션).
"""

import pytest

from handwriting.amount_read import DegenerateOutputError, read_amount_with_retry
from handwriting.group import (
    ROW_NEW,
    ROW_TOTAL,
    Row,
    apply_corrections,
    block_amounts,
    build_proposal,
    form_blocks,
    merge_amounts,
    trim_to_data_block,
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
    # '?'는 그 자리 행을 가리킨다 — 읽힌 값만 앞으로 몰고 '?'를 뒤에 붙이면 검수자가 오독한다.
    assert merge_amounts([(None, "—"), (40, "40")]) == (40, "?+40")


def test_all_none_yields_none_amount_and_unknown_raw():
    # spec §5-4: 전부 None이면 금액 None(기존 new행 단독 None 거동과 동일).
    assert merge_amounts([(None, "—"), (None, "!!!")]) == (None, "?+?")


def test_zero_amount_is_summed_not_treated_as_missing():
    # 금액 0은 유효값 — truthiness가 아니라 is not None으로 걸러야 한다(OCR "0" → 0 도달 가능).
    # 0이 합계를 좌우하는 케이스여야 변이가 갈린다: truthiness 필터면 vals가 비어 None이 나온다.
    assert merge_amounts([(0, "0"), (0, "0")]) == (0, "0+0")
    assert merge_amounts([(0, "0")]) == (0, "0")
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


def test_bang_spam_in_a_cont_cell_propagates_instead_of_becoming_a_question_mark():
    """리뷰 지적 1의 회귀 가드 — 다행 블록의 스팸이 merge_amounts의 '?'에 가려지면 안 된다.

    실물(잡 40 `"?+?"` · 잡 78 `"?+?+?"`)이 보여주듯 병합 뒤에는 스팸 원문이 이미 소실된다.
    감지가 병합 이전(셀 레벨)이어야만 이 케이스가 잡힌다.
    """
    rows = rows_from_types(["new", "cont"])

    def read_fn(row):
        raw = "160" if row.rtype == ROW_NEW else "!" * 32
        return read_amount_with_retry(lambda _attempt, r=raw: r)

    with pytest.raises(DegenerateOutputError):
        block_amounts(rows, read_fn)


# --- 하단 경계 트림 (#39) ---

JOB27_TYPES = ["new", "new", "cont", "cont"] + ["new"] * 5 + ["cont"] * 4
"""report.md §7 잡 27 재현 — 13밴드, 마지막 품목행(idx 8) 아래 합계행 1 + 빈 칸 3이 전부 cont."""

JOB27_READS = {
    (0, 10): (380, "380"),
    (10, 20): (160, "160"),
    (20, 30): (40, "40"),
    (30, 40): (30, "30"),
    (40, 50): (10, "10"),
    (50, 60): (168, "168"),
    (60, 70): (20, "20"),
    (70, 80): (190, "190"),
    (80, 90): (15, "15"),
}
"""잡 27의 확정 액면(report.md §2). 밴드 90 이후(합계행·빈 칸)는 일부러 비워 둔다 —
절단이 풀리면 read_fn이 그 밴드를 읽으려다 KeyError로 즉시 드러난다."""


def build_proposal_from_types(types, db_names=()):
    """types를 그대로 재현하는 합성 ink로 build_proposal을 태운다(classify 경로 경유).

    item 0.13/0.02는 ITEM_MIN=0.04 양쪽, amt 0.12는 AMT_MIN=0.045 초과 — 빈 칸의 격자선이
    금액 잉크로 계상돼 ROW_EMPTY가 소멸한 잡 27 상황(report.md §7)을 그대로 만든다.
    db_names를 주면 블록↔DB 매핑까지 살아나 status·db_idx 불변식을 함께 핀할 수 있다.
    """
    bands = [(i * BAND_H, i * BAND_H + BAND_H) for i in range(len(types))]
    item_inks = [0.13 if t == "new" else 0.02 for t in types]
    amt_inks = [0.0 if t == "empty" else 0.12 for t in types]
    stroke_rows = [[True] * BAND_H for _ in types]
    return build_proposal(
        bands, item_inks, amt_inks, stroke_rows, list(db_names), item_min=0.04, amt_min=0.045, pad=0
    )


def test_trailing_cont_after_the_last_item_row_is_demoted_to_empty():
    # spec §2 (수용 기준 1): 마지막 new 뒤 cont는 표 하단(합계행·빈 행)이라 블록 구성원이 아니다.
    types, trimmed = trim_to_data_block(["new", "cont", "cont", "cont"])

    assert types == ["new", "empty", "empty", "empty"]
    assert trimmed == 3
    assert form_blocks(types) == [[0]]


def test_middle_block_keeps_its_cont_rows_when_a_later_item_row_exists():
    # spec 수용 기준 5 (양성 가드): 마지막이 아닌 블록의 new+cont×2는 병합을 유지해야 한다.
    # 기존 test_job27_pattern_...은 마지막 원소가 new라 이 손실을 검출하지 못한다.
    types, trimmed = trim_to_data_block(["new", "cont", "cont", "new", "cont", "cont"])

    assert types == ["new", "cont", "cont", "new", "empty", "empty"]
    assert trimmed == 2
    assert form_blocks(types) == [[0, 1, 2], [3]]


def test_no_item_row_at_all_leaves_the_sequence_untouched():
    # spec §2: ROW_NEW 전무면 무동작 — orphan cont 블록은 block_amounts가 이미 read_fn 없이 제외.
    types, trimmed = trim_to_data_block(["cont", "cont"])

    assert types == ["cont", "cont"]
    assert trimmed == 0


def test_last_row_being_an_item_row_leaves_nothing_to_trim():
    types, trimmed = trim_to_data_block(["new", "cont", "new"])

    assert types == ["new", "cont", "new"]
    assert trimmed == 0


def test_existing_bottom_noise_trim_still_owns_rows_below_the_first_empty():
    # 기존 트림과의 공존: 첫 빈행 아래는 예전대로 empty로 강제되고, 그 결과 마지막 new 이후에
    # cont가 남지 않아 새 규칙은 발동조차 하지 않는다(trimmed == 0).
    types, trimmed = trim_to_data_block(["new", "cont", "new", "empty", "new", "cont"])

    assert types == ["new", "cont", "new", "empty", "empty", "empty"]
    assert trimmed == 0


def test_job27_totals_row_is_not_merged_into_the_last_item_row():
    # spec 수용 기준 2·3 (report.md §7 재현): 마지막 품목행(액면 15) 아래 합계행·빈 칸 4행이
    # cont로 분류돼도 병합에 섞이지 않고, 중간 블록의 정상 병합은 그대로 남는다.
    prop = build_proposal_from_types(JOB27_TYPES, [f"n{i}" for i in range(7)])
    reader = FakeReader(dict(JOB27_READS))

    news, amounts = block_amounts(prop.rows, reader)

    assert [r.rtype for r in prop.rows][8:] == ["new", "empty", "empty", "empty", "empty"]
    assert [r.band for r in news][-1] == (80, 90)
    # 수용 기준 2의 15,000은 액면 15에 infer_job.THOUSAND_MULT(×1000)를 곱한 값 —
    # 코어는 액면만 다루므로(read_amount 주석 "천원곱 미적용") 여기서는 15를 단언한다.
    assert amounts[-1][0] == 15  # 병합 937이 아니라 마지막 품목행 단독 액면
    assert amounts[1] == (230, "160+40+30")  # 중간 블록 정상 병합은 무회귀
    # 강등은 블록 구성원만 줄이고 블록 수·상태·DB 매핑은 건드리지 않는다 —
    # build_proposal 소비자 5곳(특히 dataset_build 학습셋)의 무회귀가 이 불변식에 걸려 있다.
    assert prop.n_blocks == 7
    assert prop.status == "ok"
    assert [r.db_idx for r in prop.rows if r.rtype == "new"] == [0, 1, 2, 3, 4, 5, 6]


# --- 절단 진단 표기 (#39 §2.1) ---


def test_trimmed_count_reaches_the_proposal():
    # 개수가 Proposal까지 오지 못하면 표기 경로가 통째로 성립하지 않는다.
    prop = build_proposal_from_types(["new", "cont", "cont"])

    assert prop.trimmed_cont == 2


def test_human_corrected_types_carry_no_trim_count():
    # apply_corrections는 사람이 정한 타입을 그대로 쓰고 트림을 재적용하지 않는다 —
    # 사람 판단 위에 기계 절단 표기를 덧씌우면 진단이 거짓말을 한다.
    prop = build_proposal_from_types(["new", "cont", "cont"])
    stroke_rows = [[True] * BAND_H for _ in prop.rows]

    fixed = apply_corrections(prop, ["new", "cont", "cont"], [], stroke_rows, pad=0)

    assert fixed.trimmed_cont == 0
    assert [r.rtype for r in fixed.rows] == ["new", "cont", "cont"]


def test_trimmed_block_raw_carries_the_truncation_note():
    # spec §2.1 (수용 기준 4): 절단은 read_fn 호출을 없애 raw에서 병합 흔적을 지우므로,
    # 절단 개수를 마지막 new행 원문에 남겨 사후 추적한다. 도달 지점은 ocr_jobs.result_json(영구)
    # · 큐레이션 확정 전 상세 화면(Task 3) · 오프라인 리포트(tools/curation_report.py:512 ·
    # tools/curation_render.py:373)이며, 확정 후 상세는 pair 화이트리스트라 표기가 오지 않는다.
    rows = rows_from_types(["new"])
    reader = FakeReader({(0, 10): (15, "15")})

    news, amounts = block_amounts(rows, reader, trimmed_cont=4)

    assert amounts == [(15, "15 (cont×4 절단)")]
    assert [r.band for r in news] == [(0, 10)]


def test_note_lands_on_the_last_item_row_only():
    # 절단은 표 하단에서만 일어난다 — 앞 블록 원문까지 오염되면 진단이 아니라 잡음이다.
    rows = rows_from_types(["new", "new"])
    reader = FakeReader({(0, 10): (380, "380"), (10, 20): (15, "15")})

    _news, amounts = block_amounts(rows, reader, trimmed_cont=2)

    assert amounts == [(380, "380"), (15, "15 (cont×2 절단)")]


def test_no_note_and_no_value_change_when_nothing_was_trimmed():
    # 절단 0이면 현행 원문·금액이 그대로여야 한다(46건 병합 행 전부에 표기가 붙으면 안 된다).
    rows = rows_from_types(["new", "cont"])
    reader = FakeReader({(0, 10): (160, "160"), (10, 20): (40, "40")})

    _news, amounts = block_amounts(rows, reader)

    assert amounts == [(200, "160+40")]


def test_empty_raw_note_has_no_leading_space():
    # Minor 3: 미판독(raw="")에 절단 표기가 붙으면 raw+TRIM_NOTE가 맨 앞 공백째로
    # 남는다 — lstrip 처리로 선행 공백 없이 "(cont×N 절단)"만 남아야 한다.
    rows = rows_from_types(["new"])
    reader = FakeReader({(0, 10): (None, "")})

    _news, amounts = block_amounts(rows, reader, trimmed_cont=3)

    assert amounts == [(None, "(cont×3 절단)")]


def test_job27_note_records_the_four_trimmed_rows():
    # 개수가 실제 절단 수(합계행 1 + 빈 칸 3)와 맞아야 진단으로 쓸 수 있다.
    prop = build_proposal_from_types(JOB27_TYPES)
    reader = FakeReader(dict(JOB27_READS))

    _news, amounts = block_amounts(prop.rows, reader, trimmed_cont=prop.trimmed_cont)

    assert amounts[-1] == (15, "15 (cont×4 절단)")
    assert amounts[1] == (230, "160+40+30")  # 중간 블록 원문은 표기 없이 그대로
