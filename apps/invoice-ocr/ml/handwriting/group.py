"""품목·금액 결합 그룹핑 — 순수 코어(이미지/IO 무의존).

행 분류 규칙(§3): 금액칸 ink 없음=빈행(empty), 있음 중 품목칸 ink 있음=새항목(new),
없음=위 블록에 합산(cont). 블록 = new + 뒤따르는 cont들.
"""

from dataclasses import dataclass

ROW_NEW = "new"
ROW_CONT = "cont"
ROW_EMPTY = "empty"
ROW_TOTAL = "total"  # 합계 금액 행 — 품목 아님(crop 제외), 블록 비참여, §6 합계검증 앵커


def classify_types(item_inks, amt_inks, item_min, amt_min):
    """행별 ink로 empty/new/cont 타입을 분류한다(§3)."""
    types = []
    for it, am in zip(item_inks, amt_inks, strict=False):
        if am < amt_min:
            types.append(ROW_EMPTY)
        elif it >= item_min:
            types.append(ROW_NEW)
        else:
            types.append(ROW_CONT)
    return types


def trim_to_data_block(types):
    """상단 첫 데이터행부터 '연속 데이터 블록'만 남기고 (타입, 절단한 cont 수)를 돌려준다.

    첫 빈행 이후(하단 노이즈: 합계·메모·전화번호)는 empty로 강제. 양식상 품목은 헤더 직후부터
    연속으로 내려가고 약식분해 연속행도 금액칸이 차 있어(cont) 데이터 블록은 amt-present 연속
    구간이다.

    여기에 하단 경계 규칙을 하나 더 얹는다 — 마지막 ROW_NEW 이후의 ROW_CONT는 ROW_EMPTY로
    강등한다. 위 트림은 ROW_EMPTY를 자를 지점으로 삼는데, 빈 칸의 격자선이 amt_min을 넘으면
    ROW_EMPTY가 소멸해 규칙이 통째로 무력화되고 표 하단의 합계행·빈 행이 마지막 품목행에
    합산된다(Issue #39). 마지막 품목행 이후의 cont는 실데이터 37잡 전수에서 합계행 또는 빈
    행뿐이었다.

    Args:
        types: classify_types가 낸 행 타입 시퀀스.

    Returns:
        (트림된 타입 리스트, 마지막 ROW_NEW 이후에서 강등한 ROW_CONT 개수) 한 쌍. 개수는
        절단 사후 진단 표기의 유일한 입력이다 — 강등은 read_fn 호출 자체를 없애 병합 원문에
        흔적을 남기지 않는다.
    """
    out = list(types)
    start = next((i for i, t in enumerate(out) if t in (ROW_NEW, ROW_CONT)), None)
    if start is None:
        return out, 0
    end = start
    while end < len(out) and out[end] in (ROW_NEW, ROW_CONT):
        end += 1
    for i in range(end, len(out)):
        if out[i] in (ROW_NEW, ROW_CONT):  # 하단 잡음만 empty화, total/empty 표식은 보존
            out[i] = ROW_EMPTY
    # ROW_NEW가 전무하면 무동작 — orphan cont 블록은 block_amounts가 이미 read_fn 없이 제외한다.
    last_new = max((i for i, t in enumerate(out) if t == ROW_NEW), default=None)
    if last_new is None:
        return out, 0
    trimmed = 0
    for i in range(last_new + 1, len(out)):
        if out[i] == ROW_CONT:
            out[i] = ROW_EMPTY
            trimmed += 1
    return out, trimmed


def form_blocks(types):
    """타입 시퀀스를 new+뒤따르는 cont 단위의 블록 인덱스 리스트로 묶는다."""
    blocks, cur = [], None
    for i, t in enumerate(types):
        if t in (ROW_EMPTY, ROW_TOTAL):
            cur = None
            continue
        if t == ROW_NEW:
            cur = [i]
            blocks.append(cur)
        else:  # cont
            if cur is None:
                cur = [i]  # orphan cont → 자기 블록(이상신호)
                blocks.append(cur)
            else:
                cur.append(i)
    return blocks


@dataclass(frozen=True)
class Row:
    """행 한 줄의 밴드·ink·타입·박스·블록·DB 매핑 정보."""

    band: tuple
    item_ink: float
    amt_ink: float
    rtype: str
    box: tuple | None
    block: int | None
    db_idx: int | None
    db_name: str | None


@dataclass(frozen=True)
class Proposal:
    """그룹핑 결과 — 행 목록·블록 수·DB 항목 수·상태·하단에서 절단한 cont 수."""

    rows: tuple
    n_blocks: int
    dbn: int
    status: str
    # 마지막 품목행 이후에서 잘라낸 cont 수(#39). 절단은 read_fn 호출을 없애 병합 원문에
    # 흔적을 남기지 않으므로, 이 값이 block_amounts의 진단 표기로 이어지는 유일한 통로다.
    trimmed_cont: int = 0


def snap_box_v(stroke_rows, y0, y1, pad):
    """밴드 내 획 범위에 박스를 스냅(+pad, 클립)한다.

    stroke_rows는 밴드 [y0,y1) 내 행별 획 유무(bool). 획이 없으면 (y0,y1) 폴백.
    """
    idx = [i for i, on in enumerate(stroke_rows) if on]
    if not idx:
        return (y0, y1)
    top = max(y0, y0 + idx[0] - pad)
    bot = min(y1, y0 + idx[-1] + 1 + pad)
    return (top, bot)


def _assemble(
    bands,
    item_inks,
    amt_inks,
    types,
    stroke_rows_per_band,
    db_names,
    pad,
    db_skips=(),
    *,
    trimmed_cont=0,
):
    blocks = form_blocks(types)
    block_of = {idx: bi for bi, blk in enumerate(blocks) for idx in blk}
    skips = set(db_skips)
    # 합쳐쓴 항목 등으로 손글씨 행이 없는 DB명은 건너뛴다 → 블록은 남은 DB명에 순서 매핑
    available = [(i, nm) for i, nm in enumerate(db_names) if i not in skips]
    rows = []
    for i, (y0, y1) in enumerate(bands):
        t = types[i]
        blk = block_of.get(i)
        box = db_idx = db_name = None
        if t == ROW_NEW and blk is not None:
            box = snap_box_v(stroke_rows_per_band[i], y0, y1, pad)
            if blk < len(available):
                db_idx, db_name = available[blk]
        elif t == ROW_TOTAL:
            box = (y0, y1)  # 합계: 셀 전체(소비측에서 좌측 품목영역까지 전폭 렌더/크롭)
        rows.append(Row((y0, y1), item_inks[i], amt_inks[i], t, box, blk, db_idx, db_name))
    status = "ok" if len(blocks) == len(available) else "needs_review"
    return Proposal(tuple(rows), len(blocks), len(available), status, trimmed_cont)


def build_proposal(
    bands,
    item_inks,
    amt_inks,
    stroke_rows_per_band,
    db_names,
    *,
    item_min,
    amt_min,
    pad,
    db_skips=(),
):
    """ink로 행을 분류·트림한 뒤 DB명에 블록을 매핑한 Proposal을 만든다."""
    types, trimmed_cont = trim_to_data_block(classify_types(item_inks, amt_inks, item_min, amt_min))
    return _assemble(
        bands,
        item_inks,
        amt_inks,
        types,
        stroke_rows_per_band,
        db_names,
        pad,
        db_skips,
        trimmed_cont=trimmed_cont,
    )


def apply_corrections(
    proposal, corrected_types, db_names, stroke_rows_per_band, *, pad, db_skips=()
):
    """사람이 교정한 타입(같은 밴드)으로 proposal을 재조립한다.

    박스는 타입에서 재스냅한다. db_skips는 손글씨 행이 없는 DB 인덱스(합쳐쓴 항목 등)로 매핑에서
    제외한다. trimmed_cont는 전달하지 않는다(=0) — 교정된 타입에는 트림을 재적용하지 않으므로
    기계 절단 표기를 사람 판단 위에 덧씌우면 진단이 거짓 신호가 된다(#39 §2.1).
    """
    bands = [r.band for r in proposal.rows]
    item_inks = [r.item_ink for r in proposal.rows]
    amt_inks = [r.amt_ink for r in proposal.rows]
    return _assemble(
        bands,
        item_inks,
        amt_inks,
        list(corrected_types),
        stroke_rows_per_band,
        db_names,
        pad,
        db_skips,
    )


def proposal_to_dict(proposal):
    """Proposal을 JSON 직렬화 가능한 dict로 변환한다."""
    return {
        "status": proposal.status,
        "n_blocks": proposal.n_blocks,
        "dbn": proposal.dbn,
        "rows": [
            {
                "band": list(r.band),
                "item_ink": round(r.item_ink, 4),
                "amt_ink": round(r.amt_ink, 4),
                "rtype": r.rtype,
                "box": list(r.box) if r.box else None,
                "block": r.block,
                "db_idx": r.db_idx,
                "db_name": r.db_name,
            }
            for r in proposal.rows
        ],
    }


def merge_amounts(entries):
    """블록 내 행별 (금액|None, 원문)을 병합해 (병합금액|None, 병합원문)을 반환한다.

    entries는 밴드 순서(new 먼저, cont 순차). 금액은 None을 제외한 부분 합산이며 전부 None이면
    None. 원문은 1행이면 그대로 보존하고, 2행 이상이면 '+'로 join하되 None 항목은 '?'로 적는다.

    Args:
        entries: 블록 내 행별 (금액|None, OCR 원문) 시퀀스.

    Returns:
        (병합금액|None, 병합원문) 한 쌍.
    """
    vals = [v for v, _ in entries if v is not None]
    total = sum(vals) if vals else None
    if len(entries) == 1:
        return total, entries[0][1]
    return total, "+".join(txt if v is not None else "?" for v, txt in entries)


# 절단 진단 접미 — 마지막 품목행 이후에서 잘라낸 cont 수를 병합 원문 끝에 남긴다(#39 §2.1).
# raw는 표시·전달 전용이라 하류가 재파싱하지 않으므로 계약 변경 없이 result_json에 실린다.
TRIM_NOTE = " (cont×{n} 절단)"


def block_amounts(rows, read_fn, *, trimmed_cont=0):
    """new행마다 자신 + 같은 블록 cont행을 read_fn으로 읽어 금액을 병합한다(약식 분해 합산).

    new행 선별 술어(new + box 보유)를 이 함수가 단독 소유해 호출부와 발산하지 않게 한다.

    Args:
        rows: build_proposal이 만든 Row 시퀀스(밴드 순서).
        read_fn: Row → (금액|None, 원문). 금액칸 OCR 주입점(테스트는 Fake로 대체).
            멤버 cont행은 box=None이므로(_assemble은 new/total에만 box 부여) crop 좌표는
            r.box가 아니라 r.band를 써야 한다.
        trimmed_cont: trim_to_data_block이 마지막 품목행 이후에서 잘라낸 cont 수(#39).
            0보다 크면 마지막 new행의 병합 원문 끝에 TRIM_NOTE를 붙인다 — 금액값·행 개수·
            read_fn 호출 집합에는 관여하지 않는 순수 진단 표기다.

    Returns:
        (news, amounts) — news는 출력 대상 new행 리스트, amounts는 같은 순서의 병합 결과.
        orphan cont 블록(new 없이 cont로 시작)은 read_fn을 호출하지 않고 제외한다.
    """
    members = {}
    for r in rows:
        if r.block is not None:
            members.setdefault(r.block, []).append(r)
    news, amounts = [], []
    for r in rows:
        if r.rtype != ROW_NEW or not r.box:
            continue
        news.append(r)
        amounts.append(merge_amounts([read_fn(m) for m in members[r.block]]))
    if trimmed_cont and amounts:
        amt, raw = amounts[-1]
        # raw가 미판독으로 빈 문자열이면 결합 시 선행 공백이 남으므로 lstrip으로 제거한다
        # (정상 케이스는 raw가 비지 않아 lstrip이 아무 것도 지우지 않는다).
        amounts[-1] = (amt, (raw + TRIM_NOTE.format(n=trimmed_cont)).lstrip())
    return news, amounts
