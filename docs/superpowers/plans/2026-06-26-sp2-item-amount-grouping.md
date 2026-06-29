# SP2 품목·금액 결합 그룹핑 라벨링 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 품목칸+금액칸 두 신호로 행을 분류(새항목/합산/빈행)해 §3 그룹핑을 알고리즘에 내장하고, 박스를 실제 획에 스냅하며, 사람이 행 타입을 교정·export하는 라벨링 파이프라인을 만든다.

**Architecture:** `group.py`(순수: 분류·블록·박스스냅·교정)를 코어로, `rows.py`(이미지→배열: 금액칸 척추 행검출·밴드특징)와 `grouping.py`(오케스트레이터)가 이미지 층을 담당한다. `group_editor.py`가 인터랙티브 교정 HTML을 내고, `group_amounts.py`(2차 보조)가 금액합을 검증하며, `dataset_build.py`가 교정 GT를 ingest해 `dataset_v2`를 만든다.

**Tech Stack:** Python 3.12, OpenCV(headless)·NumPy, 순수 dataclass, 정적 HTML+JS(외부 의존 0), 기존 sp2_spike 모듈(`rectify`·`canon`·`grid_v4`·`labelset.stroke_frac`).

## Global Constraints

- **sp2_spike는 gitignore 로컬 실험물** — production 테스트 규약 비적용. 자동 테스트는 **순수 함수만**(group.py 분류/블록/스냅/교정, rows.segment_rows, group_amounts.sum_check). 이미지·VLM·HTML 태스크는 실행-검증(run + console/HTML 육안).
- **워프 좌표(900×2100 캔버스):** `WARP_W,WARP_H=900,2100` · `ITEM_X=(100,392)` · `AMOUNT_X=(612,896)` · `DATA_Y=(612,1948)`. 전부 `grid_v4`에서 import(하드코딩 금지).
- **불변성:** 모든 DTO는 `@dataclass(frozen=True)`. `group.py`는 부수효과 0(이미지/IO 무의존) — 이미지 접근은 `rows.py`/`grouping.py`에만.
- **교정 = 행 타입만**(박스는 알고리즘 자동 ink-snap). 밴드 검출 오류 전표는 `needs_review`로 빠져 사람 외부 처리(v1 범위).
- **2차 금액합은 보조 신호**(하드 게이트 아님).
- **기존 spike 스타일 유지:** 절대경로 OUT·`sys.path.insert`·짧은 파일명 등 인접 스크립트(`labelset.py`·`canon.py`) 관습을 따른다.
- **테스트 러너(spike 전용 venv):** `PY=/Users/gangsub/projects/sjmj-ai/apps/invoice-ocr/ml/poc/bin/python`. 실행 디렉터리는 `apps/invoice-ocr/ml/report/sp2_spike/item/`.

---

## File Structure

| 파일 | 책임 | 층 |
|---|---|---|
| `item/group.py` (신규) | 분류(`classify_types`)·블록(`form_blocks`)·박스스냅(`snap_box_v`)·조립(`_assemble`/`build_proposal`)·교정(`apply_corrections`)·직렬화(`proposal_to_dict`) + `Row`/`Proposal` DTO | **순수** |
| `item/rows.py` (신규) | `stroke_profile_col`·`segment_rows`(순수)·`detect_amount_rows`·`band_features` | 이미지→배열 |
| `item/grouping.py` (신규) | `propose(cname,warp,db_names,P)` 오케스트레이터 + 임계 상수(`ITEM_MIN`/`AMT_MIN`/`PAD`/`ON_THRESH`) | 오케스트레이터 |
| `item/group_amounts.py` (신규) | `sum_check`(순수) + 금액칸 VLM 판독 래퍼 | 2차 보조 |
| `item/group_editor.py` (신규) | Proposal[] → `grouping_editor.html`(행타입 교정·박스자동·금액/합표시) + corrections 다운로드 | IO/HTML |
| `item/dataset_build.py` (수정) | `grouping_corrections.json` ingest → `dataset_v2/<DB명>/` 크롭·라벨 | IO |
| `item/test_group.py` (신규) | group.py 순수 단위테스트 | 테스트 |
| `item/test_rows.py` (신규) | rows.segment_rows 순수 단위테스트 | 테스트 |
| `item/test_group_amounts.py` (신규) | sum_check 순수 단위테스트 | 테스트 |

---

## Task 1: group.py — 행 타입 분류 + 블록 형성 (순수 코어)

**Files:**
- Create: `apps/invoice-ocr/ml/report/sp2_spike/item/group.py`
- Test: `apps/invoice-ocr/ml/report/sp2_spike/item/test_group.py`

**Interfaces:**
- Produces: `ROW_NEW="new"`, `ROW_CONT="cont"`, `ROW_EMPTY="empty"`; `classify_types(item_inks: list[float], amt_inks: list[float], item_min: float, amt_min: float) -> list[str]`; `form_blocks(types: list[str]) -> list[list[int]]`.

- [ ] **Step 1: spike 테스트 venv에 pytest 확보 (이 태스크 1회 scaffolding)**

```bash
cd /Users/gangsub/projects/sjmj-ai/apps/invoice-ocr/ml
[ -d poc ] || uv venv poc --python 3.12
uv pip install -q --python poc/bin/python pytest
```

- [ ] **Step 2: 실패 테스트 작성** — `item/test_group.py`

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from group import (  # noqa: E402
    ROW_NEW, ROW_CONT, ROW_EMPTY, classify_types, form_blocks,
)


def test_classify_amount_blank_is_empty():
    # amt < amt_min → empty (데이터 밖), item_ink 무관
    types = classify_types([0.9, 0.0], [0.0, 0.0], item_min=0.05, amt_min=0.05)
    assert types == [ROW_EMPTY, ROW_EMPTY]


def test_classify_item_filled_is_new_blank_is_cont():
    # amt 있음 + item 있음 → new / amt 있음 + item 없음 → cont (위 합산)
    types = classify_types([0.9, 0.0], [0.9, 0.9], item_min=0.05, amt_min=0.05)
    assert types == [ROW_NEW, ROW_CONT]


def test_form_blocks_no_grouping():
    # 모두 new → 블록 N개, 각 블록 1행
    blocks = form_blocks([ROW_NEW, ROW_NEW, ROW_NEW])
    assert blocks == [[0], [1], [2]]


def test_form_blocks_grouping_collapses_conts():
    # new,cont,cont,new,cont → 블록 2개 ([0,1,2],[3,4]) — inv_001식 약식분해
    blocks = form_blocks([ROW_NEW, ROW_CONT, ROW_CONT, ROW_NEW, ROW_CONT])
    assert blocks == [[0, 1, 2], [3, 4]]


def test_form_blocks_empty_breaks_block():
    # 빈행은 블록을 닫고 자기 자신은 어떤 블록에도 안 들어감
    blocks = form_blocks([ROW_NEW, ROW_CONT, ROW_EMPTY, ROW_NEW])
    assert blocks == [[0, 1], [3]]


def test_form_blocks_orphan_cont_is_own_block():
    # 선행 new 없는 cont → 자기 블록(이상신호 → 상위서 needs_review 유발)
    blocks = form_blocks([ROW_CONT, ROW_NEW])
    assert blocks == [[0], [1]]
```

- [ ] **Step 3: 실패 확인**

Run: `cd apps/invoice-ocr/ml/report/sp2_spike/item && $PY -m pytest test_group.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'group'` (또는 import 실패).

- [ ] **Step 4: 최소 구현** — `item/group.py`

```python
"""품목·금액 결합 그룹핑 — 순수 코어(이미지/IO 무의존).

행 분류 규칙(§3): 금액칸 ink 없음=빈행(empty), 있음 중 품목칸 ink 있음=새항목(new),
없음=위 블록에 합산(cont). 블록 = new + 뒤따르는 cont들.
"""
ROW_NEW = "new"
ROW_CONT = "cont"
ROW_EMPTY = "empty"


def classify_types(item_inks, amt_inks, item_min, amt_min):
    types = []
    for it, am in zip(item_inks, amt_inks):
        if am < amt_min:
            types.append(ROW_EMPTY)
        elif it >= item_min:
            types.append(ROW_NEW)
        else:
            types.append(ROW_CONT)
    return types


def form_blocks(types):
    blocks, cur = [], None
    for i, t in enumerate(types):
        if t == ROW_EMPTY:
            cur = None
            continue
        if t == ROW_NEW:
            cur = [i]
            blocks.append(cur)
        else:  # cont
            if cur is None:
                cur = [i]               # orphan cont → 자기 블록(이상신호)
                blocks.append(cur)
            else:
                cur.append(i)
    return blocks
```

- [ ] **Step 5: 통과 확인**

Run: `cd apps/invoice-ocr/ml/report/sp2_spike/item && $PY -m pytest test_group.py -v`
Expected: PASS (6 passed).

- [ ] **Step 6: 커밋**

```bash
cd /Users/gangsub/projects/sjmj-ai
git add apps/invoice-ocr/ml/report/sp2_spike/item/group.py apps/invoice-ocr/ml/report/sp2_spike/item/test_group.py
git commit -m "feat(sp2): group.py 행 타입 분류·블록 형성(순수 코어)"
```

---

## Task 2: group.py — 박스 스냅 + proposal 조립 (순수)

**Files:**
- Modify: `apps/invoice-ocr/ml/report/sp2_spike/item/group.py`
- Test: `apps/invoice-ocr/ml/report/sp2_spike/item/test_group.py`

**Interfaces:**
- Consumes: `classify_types`, `form_blocks`, `ROW_*` (Task 1).
- Produces: `Row` / `Proposal` frozen dataclass; `snap_box_v(stroke_rows: list[bool], y0: int, y1: int, pad: int) -> tuple[int,int]`; `build_proposal(bands, item_inks, amt_inks, stroke_rows_per_band, db_names, *, item_min, amt_min, pad) -> Proposal`. `Row(band, item_ink, amt_ink, rtype, box, block, db_idx, db_name)`. `Proposal(rows, n_blocks, dbn, status)` with `status in {"ok","needs_review"}` (`ok` iff `n_blocks == len(db_names)`).

- [ ] **Step 1: 실패 테스트 추가** — `item/test_group.py` 끝에 append

```python
from group import Row, Proposal, snap_box_v, build_proposal  # noqa: E402


def test_snap_box_trims_blank_top_and_bottom():
    # 밴드 [100,140), 획은 row 5..9에만 → top/bottom이 획에 스냅(+pad 2)
    stroke = [False] * 5 + [True] * 5 + [False] * 30
    top, bot = snap_box_v(stroke, 100, 140, pad=2)
    assert (top, bot) == (103, 112)


def test_snap_box_no_stroke_falls_back_to_band():
    assert snap_box_v([False, False, False], 100, 140, pad=2) == (100, 140)


def test_build_proposal_no_grouping_is_ok():
    bands = [(100, 140), (140, 180)]
    p = build_proposal(
        bands, item_inks=[0.9, 0.9], amt_inks=[0.9, 0.9],
        stroke_rows_per_band=[[True], [True]],
        db_names=["엔진오일", "타이어"], item_min=0.05, amt_min=0.05, pad=2)
    assert p.status == "ok" and p.n_blocks == 2 and p.dbn == 2
    assert [r.rtype for r in p.rows] == [ROW_NEW, ROW_NEW]
    assert p.rows[0].db_name == "엔진오일" and p.rows[1].db_name == "타이어"
    assert p.rows[0].box is not None


def test_build_proposal_grouping_maps_blocks_to_db():
    # new,cont,new → 블록 2개 == DB 2개 → ok, cont행은 box/db None
    bands = [(100, 140), (140, 180), (180, 220)]
    p = build_proposal(
        bands, item_inks=[0.9, 0.0, 0.9], amt_inks=[0.9, 0.9, 0.9],
        stroke_rows_per_band=[[True], [True], [True]],
        db_names=["요소수", "원터치"], item_min=0.05, amt_min=0.05, pad=2)
    assert p.status == "ok" and p.n_blocks == 2
    assert [r.rtype for r in p.rows] == [ROW_NEW, ROW_CONT, ROW_NEW]
    assert p.rows[0].db_name == "요소수" and p.rows[1].db_name is None
    assert p.rows[2].db_name == "원터치" and p.rows[1].box is None


def test_build_proposal_block_mismatch_is_needs_review():
    # 블록 3개 vs DB 2개 → needs_review
    bands = [(100, 140), (140, 180), (180, 220)]
    p = build_proposal(
        bands, item_inks=[0.9, 0.9, 0.9], amt_inks=[0.9, 0.9, 0.9],
        stroke_rows_per_band=[[True], [True], [True]],
        db_names=["a", "b"], item_min=0.05, amt_min=0.05, pad=2)
    assert p.status == "needs_review" and p.n_blocks == 3
```

- [ ] **Step 2: 실패 확인**

Run: `cd apps/invoice-ocr/ml/report/sp2_spike/item && $PY -m pytest test_group.py -v`
Expected: FAIL — `ImportError: cannot import name 'snap_box_v'`.

- [ ] **Step 3: 구현 추가** — `item/group.py` 상단에 import, 하단에 추가

`group.py` 맨 위에 추가:

```python
from dataclasses import dataclass
```

`group.py` 맨 아래에 추가:

```python
@dataclass(frozen=True)
class Row:
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
    rows: tuple
    n_blocks: int
    dbn: int
    status: str


def snap_box_v(stroke_rows, y0, y1, pad):
    """stroke_rows: 밴드 [y0,y1) 내 행별 획 유무(bool). 획 범위에 스냅+pad, 클립.
    획 없으면 (y0,y1) 폴백."""
    idx = [i for i, on in enumerate(stroke_rows) if on]
    if not idx:
        return (y0, y1)
    top = max(y0, y0 + idx[0] - pad)
    bot = min(y1, y0 + idx[-1] + 1 + pad)
    return (top, bot)


def _assemble(bands, item_inks, amt_inks, types, stroke_rows_per_band, db_names, pad):
    blocks = form_blocks(types)
    block_of = {idx: bi for bi, blk in enumerate(blocks) for idx in blk}
    rows = []
    for i, (y0, y1) in enumerate(bands):
        t = types[i]
        blk = block_of.get(i)
        box = db_idx = db_name = None
        if t == ROW_NEW and blk is not None:
            box = snap_box_v(stroke_rows_per_band[i], y0, y1, pad)
            db_idx = blk
            db_name = db_names[blk] if blk < len(db_names) else None
        rows.append(Row((y0, y1), item_inks[i], amt_inks[i], t, box, blk, db_idx, db_name))
    status = "ok" if len(blocks) == len(db_names) else "needs_review"
    return Proposal(tuple(rows), len(blocks), len(db_names), status)


def build_proposal(bands, item_inks, amt_inks, stroke_rows_per_band, db_names,
                   *, item_min, amt_min, pad):
    types = classify_types(item_inks, amt_inks, item_min, amt_min)
    return _assemble(bands, item_inks, amt_inks, types, stroke_rows_per_band, db_names, pad)
```

- [ ] **Step 4: 통과 확인**

Run: `cd apps/invoice-ocr/ml/report/sp2_spike/item && $PY -m pytest test_group.py -v`
Expected: PASS (11 passed).

- [ ] **Step 5: 커밋**

```bash
cd /Users/gangsub/projects/sjmj-ai
git add apps/invoice-ocr/ml/report/sp2_spike/item/group.py apps/invoice-ocr/ml/report/sp2_spike/item/test_group.py
git commit -m "feat(sp2): group.py 박스 ink-snap·proposal 조립·status 판정"
```

---

## Task 3: group.py — 교정 적용 + 직렬화 (순수)

**Files:**
- Modify: `apps/invoice-ocr/ml/report/sp2_spike/item/group.py`
- Test: `apps/invoice-ocr/ml/report/sp2_spike/item/test_group.py`

**Interfaces:**
- Consumes: `_assemble`, `Proposal`, `Row`, `ROW_*` (Task 2).
- Produces: `apply_corrections(proposal: Proposal, corrected_types: list[str], db_names, stroke_rows_per_band, *, pad) -> Proposal`; `proposal_to_dict(proposal: Proposal) -> dict`.

- [ ] **Step 1: 실패 테스트 추가** — `item/test_group.py` 끝에 append

```python
from group import apply_corrections, proposal_to_dict  # noqa: E402


def test_apply_corrections_overrides_types_and_rebuilds():
    # auto가 3 new(needs_review)였던 걸 사람이 가운데를 cont로 교정 → 블록 2 == DB 2 → ok
    bands = [(100, 140), (140, 180), (180, 220)]
    auto = build_proposal(
        bands, item_inks=[0.9, 0.9, 0.9], amt_inks=[0.9, 0.9, 0.9],
        stroke_rows_per_band=[[True], [True], [True]],
        db_names=["a", "b"], item_min=0.05, amt_min=0.05, pad=2)
    assert auto.status == "needs_review"
    fixed = apply_corrections(
        auto, corrected_types=[ROW_NEW, ROW_CONT, ROW_NEW],
        db_names=["a", "b"], stroke_rows_per_band=[[True], [True], [True]], pad=2)
    assert fixed.status == "ok" and fixed.n_blocks == 2
    assert [r.rtype for r in fixed.rows] == [ROW_NEW, ROW_CONT, ROW_NEW]
    assert fixed.rows[0].db_name == "a" and fixed.rows[2].db_name == "b"


def test_proposal_to_dict_roundtrips_fields():
    bands = [(100, 140)]
    p = build_proposal(
        bands, item_inks=[0.9], amt_inks=[0.9], stroke_rows_per_band=[[True]],
        db_names=["엔진오일"], item_min=0.05, amt_min=0.05, pad=2)
    d = proposal_to_dict(p)
    assert d["status"] == "ok" and d["dbn"] == 1
    r0 = d["rows"][0]
    assert r0["rtype"] == ROW_NEW and r0["db_name"] == "엔진오일"
    assert r0["band"] == [100, 140] and isinstance(r0["box"], list)
```

- [ ] **Step 2: 실패 확인**

Run: `cd apps/invoice-ocr/ml/report/sp2_spike/item && $PY -m pytest test_group.py -v`
Expected: FAIL — `ImportError: cannot import name 'apply_corrections'`.

- [ ] **Step 3: 구현 추가** — `item/group.py` 맨 아래에 추가

```python
def apply_corrections(proposal, corrected_types, db_names, stroke_rows_per_band, *, pad):
    """사람이 교정한 타입(같은 밴드)으로 proposal 재조립. 박스는 타입에서 재스냅."""
    bands = [r.band for r in proposal.rows]
    item_inks = [r.item_ink for r in proposal.rows]
    amt_inks = [r.amt_ink for r in proposal.rows]
    return _assemble(bands, item_inks, amt_inks, list(corrected_types),
                     stroke_rows_per_band, db_names, pad)


def proposal_to_dict(proposal):
    return {
        "status": proposal.status,
        "n_blocks": proposal.n_blocks,
        "dbn": proposal.dbn,
        "rows": [
            {"band": list(r.band), "item_ink": round(r.item_ink, 4),
             "amt_ink": round(r.amt_ink, 4), "rtype": r.rtype,
             "box": list(r.box) if r.box else None, "block": r.block,
             "db_idx": r.db_idx, "db_name": r.db_name}
            for r in proposal.rows
        ],
    }
```

- [ ] **Step 4: 통과 확인**

Run: `cd apps/invoice-ocr/ml/report/sp2_spike/item && $PY -m pytest test_group.py -v`
Expected: PASS (13 passed).

- [ ] **Step 5: 커밋**

```bash
cd /Users/gangsub/projects/sjmj-ai
git add apps/invoice-ocr/ml/report/sp2_spike/item/group.py apps/invoice-ocr/ml/report/sp2_spike/item/test_group.py
git commit -m "feat(sp2): group.py 교정 적용·proposal 직렬화"
```

---

## Task 4: rows.py — 금액칸 척추 행검출 + 밴드 특징

**Files:**
- Create: `apps/invoice-ocr/ml/report/sp2_spike/item/rows.py`
- Test: `apps/invoice-ocr/ml/report/sp2_spike/item/test_rows.py`

**Interfaces:**
- Produces: `stroke_profile_col(warp, x0, x1) -> np.ndarray`(y축 1D 획비율); `segment_rows(profile, P, y0, y1, on, min_gap) -> list[tuple[int,int]]`(순수); `detect_amount_rows(warp, P) -> list[tuple[int,int]]`; `band_features(warp, bands) -> tuple[list[float], list[float], list[list[bool]]]`(item_inks, amt_inks, stroke_rows_per_band).
- Consumes: `grid_v4.ITEM_X`(없으면 `(100,392)` 상수), `grid_v4.AMOUNT_X`, `grid_v4.DATA_Y`; `labelset.stroke_frac`.

- [ ] **Step 1: numpy/opencv 확보 (이 태스크 scaffolding)**

```bash
cd /Users/gangsub/projects/sjmj-ai/apps/invoice-ocr/ml
uv pip install -q --python poc/bin/python numpy opencv-python-headless Pillow
```

- [ ] **Step 2: 실패 테스트 작성** — `item/test_rows.py` (segment_rows만 순수 검증)

```python
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from rows import segment_rows  # noqa: E402


def _profile(centers, height, P):
    """centers 위치에 폭 ~P/3 짜리 bump를 둔 1D 프로파일."""
    prof = np.zeros(height)
    half = max(1, P // 6)
    for c in centers:
        prof[c - half:c + half] = 0.5
    return prof


def test_segment_rows_one_band_per_amount_run():
    # P=80, 행 중심 660/740/820 → 밴드 3개, 각 폭 ~P
    prof = _profile([660, 740, 820], height=2100, P=80)
    bands = segment_rows(prof, P=80, y0=612, y1=1948, on=0.2, min_gap=48)
    assert len(bands) == 3
    centers = [(a + b) // 2 for a, b in bands]
    assert all(abs(cc - c) <= 5 for cc, c in zip(centers, [660, 740, 820]))


def test_segment_rows_merges_close_runs_within_min_gap():
    # 한 행의 숫자가 두 토막(차 30 < min_gap 48)으로 갈라져도 한 밴드로 병합
    prof = _profile([700, 730], height=2100, P=80)
    bands = segment_rows(prof, P=80, y0=612, y1=1948, on=0.2, min_gap=48)
    assert len(bands) == 1


def test_segment_rows_ignores_outside_data_y():
    # DATA_Y 밖(상단 합계영역) bump는 무시
    prof = _profile([200, 700], height=2100, P=80)
    bands = segment_rows(prof, P=80, y0=612, y1=1948, on=0.2, min_gap=48)
    assert len(bands) == 1
```

- [ ] **Step 3: 실패 확인**

Run: `cd apps/invoice-ocr/ml/report/sp2_spike/item && $PY -m pytest test_rows.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rows'`.

- [ ] **Step 4: 구현** — `item/rows.py`

```python
"""금액칸 척추 행검출 + 밴드 특징(이미지→배열).

금액칸(AMOUNT_X)이 가장 또박또박 쓰여(숫자 84.8%) 행의 '척추'로 가장 신뢰도 높다.
금액칸 stroke 세로 프로파일의 런(run) 중심을 행 앵커로, 고정피치 P로 밴드를 만든다.
"""
import sys
from pathlib import Path

import cv2
import numpy as np

SP2 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SP2))
sys.path.insert(0, str(Path(__file__).parent))
from grid_v4 import AMOUNT_X, DATA_Y  # noqa: E402
from labelset import stroke_frac  # noqa: E402

ITEM_X = (100, 392)
Y0, Y1 = DATA_Y


def stroke_profile_col(warp, x0, x1):
    """열 [x0,x1)에서 y축 국소대비 획 비율(1D). 전역 그림자·인쇄선 배제."""
    col = warp[:, x0:x1]
    gray = cv2.cvtColor(col, cv2.COLOR_BGR2GRAY).astype(np.int16)
    blur = cv2.GaussianBlur(gray, (0, 0), 11).astype(np.int16)
    return ((gray - blur) < -28).mean(axis=1)


def segment_rows(profile, P, y0, y1, on, min_gap):
    """금액칸 1D 프로파일 → 데이터 행 밴드[(a,b)]. 런 중심을 피치 P 폭 밴드로,
    min_gap 이내 인접 런은 한 행으로 병합."""
    mask = profile >= on
    mask[:y0] = False
    mask[y1:] = False
    centers, i, n = [], 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            w = profile[i:j]
            c = i + (int(np.average(np.arange(j - i), weights=w)) if w.sum() > 0 else (j - i) // 2)
            centers.append(c)
            i = j
        else:
            i += 1
    merged = []
    for c in centers:
        if merged and c - merged[-1] < min_gap:
            merged[-1] = (merged[-1] + c) // 2
        else:
            merged.append(c)
    half = int(P / 2)
    return [(max(y0, c - half), min(y1, c + half)) for c in merged]


def detect_amount_rows(warp, P, *, on=0.20, min_gap=None):
    prof = stroke_profile_col(warp, AMOUNT_X[0], AMOUNT_X[1])
    return segment_rows(prof, P, Y0, Y1, on, min_gap or int(P * 0.6))


def band_features(warp, bands):
    """밴드별 (item_ink, amt_ink, 품목칸 행별 획 bool) — build_proposal 입력."""
    item_inks, amt_inks, stroke_rows = [], [], []
    ix0, ix1 = ITEM_X
    ax0, ax1 = AMOUNT_X
    for a, b in bands:
        item_inks.append(stroke_frac(warp[a:b, ix0:ix1]))
        amt_inks.append(stroke_frac(warp[a:b, ax0:ax1]))
        cell = warp[a:b, ix0:ix1]
        gray = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY).astype(np.int16)
        blur = cv2.GaussianBlur(gray, (0, 0), 11).astype(np.int16)
        stroke_rows.append((((gray - blur) < -28).mean(axis=1) > 0.02).tolist())
    return item_inks, amt_inks, stroke_rows
```

- [ ] **Step 5: 통과 확인**

Run: `cd apps/invoice-ocr/ml/report/sp2_spike/item && $PY -m pytest test_rows.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: 커밋**

```bash
cd /Users/gangsub/projects/sjmj-ai
git add apps/invoice-ocr/ml/report/sp2_spike/item/rows.py apps/invoice-ocr/ml/report/sp2_spike/item/test_rows.py
git commit -m "feat(sp2): rows.py 금액칸 척추 행검출·밴드특징(segment_rows TDD)"
```

---

## Task 5: grouping.py — 오케스트레이터 + 74장 census

**Files:**
- Create: `apps/invoice-ocr/ml/report/sp2_spike/item/grouping.py`

**Interfaces:**
- Consumes: `rectify.form_quad_robust/deskew_angle/rotate`, `grid_v4.warp`, `canon.global_pitch`, `rows.detect_amount_rows/band_features`, `group.build_proposal`, `dataset_build.load_bgr_path/ORG`, `photomatch.db_invoices`(또는 `manifest.json`).
- Produces: `ITEM_MIN=0.04`, `AMT_MIN=0.04`, `PAD=3`, `ON_THRESH=0.20` 상수; `rectify_warp(cname) -> np.ndarray`; `propose(warp, db_names, P) -> Proposal`; `main()` (74장 proposal census 출력).

이 태스크는 **이미지 통합 seam**이다 — 자동 테스트 대신 실행-검증(console census). proposal이 합리적인지 다음 태스크(에디터) 전에 수치로 먼저 확인한다.

- [ ] **Step 1: 구현** — `item/grouping.py`

```python
"""품목·금액 결합 그룹핑 오케스트레이터 — rectify→금액척추 행검출→이중신호 proposal.

임계(ITEM_MIN/AMT_MIN/PAD/ON_THRESH)는 교정 GT(grouping_corrections.json)로 튜닝하는
설정값이다. group.py는 임계 무지(인자로만 받음).
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from grid_v4 import warp  # noqa: E402
from rectify import form_quad_robust, deskew_angle, rotate  # noqa: E402
from canon import global_pitch  # noqa: E402
from rows import detect_amount_rows, band_features  # noqa: E402
from group import build_proposal  # noqa: E402
from dataset_build import load_bgr_path, ORG  # noqa: E402

ITEM_MIN = 0.04
AMT_MIN = 0.04
PAD = 3
ON_THRESH = 0.20


def rectify_warp(cname):
    bgr = load_bgr_path(ORG / cname)
    w0 = warp(bgr, form_quad_robust(bgr))
    return rotate(w0, deskew_angle(w0))


def propose(warp_img, db_names, P):
    bands = detect_amount_rows(warp_img, P, on=ON_THRESH)
    item_inks, amt_inks, stroke_rows = band_features(warp_img, bands)
    return build_proposal(bands, item_inks, amt_inks, stroke_rows, db_names,
                          item_min=ITEM_MIN, amt_min=AMT_MIN, pad=PAD)


def all_warps_and_pitch():
    """74장 워프 + 전역 피치. (cname -> warp, P, manifest)"""
    from rows import stroke_profile_col  # noqa
    manifest = json.load(open(ORG / "manifest.json"))
    cnames = sorted(manifest)
    warps, ys_all = {}, {}
    for cn in cnames:
        w = rectify_warp(cn)
        warps[cn] = w
        # 피치 추정용 금액칸 행선 근사(detect 전이라 임시로 hline 대신 amount run 사용 안 함)
        from grid_v4 import hline_ys
        from canon import Y0 as _Y0, Y1 as _Y1
        ys_all[cn] = [y for y in hline_ys(w) if _Y0 - 40 <= y <= _Y1 + 40]
    P = global_pitch(ys_all)
    return cnames, warps, manifest, P


def main():
    cnames, warps, manifest, P = all_warps_and_pitch()
    ok = 0
    print(f"P={P:.1f} ITEM_MIN={ITEM_MIN} AMT_MIN={AMT_MIN}\n")
    print(f"{'cname':<26}{'rows':>5}{'blk':>5}{'DBn':>5}  status")
    for cn in cnames:
        names = manifest[cn]["items"]
        p = propose(warps[cn], names, P)
        ok += p.status == "ok"
        ndata = sum(1 for r in p.rows if r.rtype != "empty")
        print(f"{cn:<26}{ndata:>5}{p.n_blocks:>5}{len(names):>5}  {p.status}")
    print(f"\nstatus==ok : {ok}/{len(cnames)}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 실행-검증**

Run: `cd apps/invoice-ocr/ml/report/sp2_spike/item && $PY grouping.py`
Expected: 74장 각 행에 `cname / 데이터행수 / 블록수 / DBn / status`가 출력되고, 끝에 `status==ok : N/74`. 확인 포인트 — (a) 그룹핑 없는 전표는 블록수==DBn==데이터행수, (b) inv_001류 그룹핑 전표는 데이터행수 > 블록수 == DBn(이전 labelset에서 skip되던 게 ok로 잡히면 성공 신호). N이 0이거나 전부 needs_review면 임계(ITEM_MIN/AMT_MIN/ON_THRESH) 조정 필요.

- [ ] **Step 3: 커밋**

```bash
cd /Users/gangsub/projects/sjmj-ai
git add apps/invoice-ocr/ml/report/sp2_spike/item/grouping.py
git commit -m "feat(sp2): grouping.py 오케스트레이터·74장 proposal census"
```

---

## Task 6: group_editor.py — 인터랙티브 교정 HTML

**Files:**
- Create: `apps/invoice-ocr/ml/report/sp2_spike/item/group_editor.py`

**Interfaces:**
- Consumes: `grouping.all_warps_and_pitch/propose`, `group.proposal_to_dict`, `rows.ITEM_X`, `grid_v4.AMOUNT_X/WARP_W`.
- Produces: `report/grouping_editor.html` — 전표별 워프+오버레이 + 행 칩(품목/금액 썸네일·타입·DB명), 클릭→타입순환(JS), export→`grouping_corrections.json` 다운로드.

이미지+HTML 태스크 → 실행 후 브라우저 육안 검증.

- [ ] **Step 1: 구현** — `item/group_editor.py`

```python
"""교정 에디터 — Proposal[] → grouping_editor.html.

좌: 워프+오버레이(타입색 밴드·박스). 우: 데이터행 칩(품목·금액 썸네일, 타입뱃지, DB명).
행 클릭 → new→cont→empty→new 순환(JS가 블록·DB라벨 즉시 재계산). 'export' →
grouping_corrections.json({cname:{bands,types}}) Blob 다운로드. 박스는 타입에서 자동.
"""
import base64
import json
import sys
from pathlib import Path

import cv2

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from grid_v4 import AMOUNT_X  # noqa: E402
from rows import ITEM_X  # noqa: E402
from group import proposal_to_dict, ROW_NEW, ROW_CONT, ROW_EMPTY  # noqa: E402
from grouping import all_warps_and_pitch, propose  # noqa: E402

OUT = HERE.parents[2] / "report" / "grouping_editor.html"
TYPE_COL = {ROW_NEW: (0, 170, 0), ROW_CONT: (255, 150, 0), ROW_EMPTY: (160, 160, 160)}


def b64(img, w=None):
    if w:
        img = cv2.resize(img, (w, int(img.shape[0] * w / img.shape[1])))
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return base64.b64encode(buf).decode()


def overlay(warp, proposal):
    ov = warp.copy()
    for r in proposal.rows:
        a, b = r.band
        col = TYPE_COL[r.rtype]
        cv2.line(ov, (ITEM_X[0], a), (AMOUNT_X[1], a), col, 1)
        if r.box:
            cv2.rectangle(ov, (ITEM_X[0], r.box[0]), (ITEM_X[1], r.box[1]), (0, 170, 0), 2)
    return ov


def main():
    cnames, warps, manifest, P = all_warps_and_pitch()
    data = {}
    cards = []
    for cn in cnames:
        names = manifest[cn]["items"]
        p = propose(warps[cn], names, P)
        data[cn] = {"dict": proposal_to_dict(p), "names": names}
        ov = overlay(warps[cn], p)
        chips = ""
        for i, r in enumerate(p.rows):
            a, b = r.band
            it = b64(warps[cn][a:b, ITEM_X[0]:ITEM_X[1]], 120)
            am = b64(warps[cn][a:b, AMOUNT_X[0]:AMOUNT_X[1]], 90)
            chips += (f'<div class="chip {r.rtype}" data-cn="{cn}" data-i="{i}" onclick="cyc(this)">'
                      f'<img src="data:image/jpeg;base64,{it}"><img src="data:image/jpeg;base64,{am}">'
                      f'<span class=ty>{r.rtype}</span><span class=db></span></div>')
        cards.append(
            f'<div class=card data-cn="{cn}"><div class=hd>{cn} '
            f'<span class=st>{p.status}</span> <small>blk {p.n_blocks}/DB {len(names)}</small> '
            f'<small class=it>{" · ".join(names)}</small></div>'
            f'<div class=body><img class=ov src="data:image/jpeg;base64,{b64(ov, 300)}">'
            f'<div class=chips>{chips}</div></div></div>')

    payload = json.dumps(data, ensure_ascii=False)
    OUT.write_text(_HTML.replace("/*DATA*/", payload).replace("<!--CARDS-->", "".join(cards)),
                   encoding="utf-8")
    print(f"전표 {len(cnames)} · P={P:.1f} → {OUT}")


_HTML = r"""<!doctype html><meta charset=utf-8><title>그룹핑 교정 에디터</title>
<style>
body{font:12px/1.4 -apple-system,sans-serif;background:#eee;margin:0}
header{position:sticky;top:0;background:#1f2937;color:#fff;padding:9px 14px;z-index:9;display:flex;gap:10px;align-items:center}
.btn{padding:5px 11px;border:0;border-radius:5px;cursor:pointer;background:#16a34a;color:#fff;font-weight:600}
#wrap{padding:10px;max-width:1180px;margin:0 auto}
.card{background:#fff;border-radius:9px;margin-bottom:10px;box-shadow:0 1px 3px #0002;border-left:5px solid #ccc}
.card.ok{border-color:#16a34a} .card.needs_review{border-color:#f59e0b}
.hd{padding:7px 11px;border-bottom:1px solid #eee;background:#fafafa}
.st{padding:1px 7px;border-radius:4px;color:#fff;background:#999;font-weight:600}
.it{color:#6b7280} .body{display:flex;gap:10px;padding:10px;align-items:flex-start}
.ov{border:1px solid #ddd;border-radius:4px}
.chips{flex:1;display:flex;flex-direction:column;gap:3px}
.chip{display:flex;align-items:center;gap:6px;border:1px solid #ddd;border-radius:5px;padding:2px 5px;cursor:pointer}
.chip img{height:26px;border:1px solid #eee}
.chip.new{background:#dcfce7} .chip.cont{background:#fef3c7} .chip.empty{background:#f3f4f6;opacity:.6}
.ty{font-weight:600;width:38px} .db{color:#06c}
</style>
<header><b>그룹핑 교정 에디터</b><span id=stat></span><span style=flex:1></span>
<button class=btn onclick=exp()>export corrections</button></header>
<div id=wrap><!--CARDS--></div>
<script>
const DATA=/*DATA*/;
const ORDER=["new","cont","empty"];
function recompute(cn){
  const rows=DATA[cn].dict.rows, names=DATA[cn].names;
  let blk=-1, cur=null, nblk=0;
  const map=[];
  for(const r of rows){
    if(r.rtype=="empty"){cur=null;map.push(null);continue;}
    if(r.rtype=="new"){blk=nblk++;cur=blk;map.push(blk);}
    else{if(cur==null){blk=nblk++;cur=blk;}map.push(cur);}
  }
  // DB명 = 블록 순서
  const card=document.querySelector(`.card[data-cn="${cn}"]`);
  const chips=card.querySelectorAll(".chip");
  chips.forEach((c,i)=>{
    const r=rows[i];
    c.className="chip "+r.rtype;
    c.querySelector(".ty").textContent=r.rtype;
    c.querySelector(".db").textContent=(r.rtype=="new"&&map[i]<names.length)?names[map[i]]:"";
  });
  const ok=nblk==names.length;
  card.classList.toggle("ok",ok);card.classList.toggle("needs_review",!ok);
  card.querySelector(".st").textContent=ok?"ok":"needs_review";
}
function cyc(el){
  const cn=el.dataset.cn, i=+el.dataset.i;
  const r=DATA[cn].dict.rows[i];
  r.rtype=ORDER[(ORDER.indexOf(r.rtype)+1)%3];
  recompute(cn);
}
function exp(){
  const out={};
  for(const cn in DATA){
    out[cn]={bands:DATA[cn].dict.rows.map(r=>r.band),
             types:DATA[cn].dict.rows.map(r=>r.rtype)};
  }
  const blob=new Blob([JSON.stringify(out,null,1)],{type:"application/json"});
  const a=document.createElement("a");a.href=URL.createObjectURL(blob);
  a.download="grouping_corrections.json";a.click();
}
for(const cn in DATA) recompute(cn);
</script>"""


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 실행-검증**

Run: `cd apps/invoice-ocr/ml/report/sp2_spike/item && $PY group_editor.py && open ../../grouping_editor.html`
Expected: 브라우저에 74 전표 카드. 확인 — (a) 좌측 워프 오버레이의 박스가 **글씨에 타이트**(빈 시작 없음), (b) 빈 품목칸 행이 cont(노랑)로 표시되고 박스 없음, (c) 행 칩 클릭 시 타입이 new→cont→empty 순환하며 DB명·status가 즉시 갱신, (d) export 클릭 시 `grouping_corrections.json` 다운로드. 박스가 빈 곳에서 시작하거나 cont가 new로 잡히면 임계 조정 후 재실행.

- [ ] **Step 3: 커밋**

```bash
cd /Users/gangsub/projects/sjmj-ai
git add apps/invoice-ocr/ml/report/sp2_spike/item/group_editor.py
git commit -m "feat(sp2): group_editor.py 인터랙티브 행타입 교정 HTML·corrections export"
```

---

## Task 7: group_amounts.py — 금액합 정합 (2차 보조)

**Files:**
- Create: `apps/invoice-ocr/ml/report/sp2_spike/item/group_amounts.py`
- Test: `apps/invoice-ocr/ml/report/sp2_spike/item/test_group_amounts.py`

**Interfaces:**
- Produces: `sum_check(row_values: list[int], row_blocks: list[int], db_amounts: list[int]) -> list[bool]`(순수 — 블록별 합 == DB금액); `read_amounts(warp) -> list[int]`(금액칸 VLM 판독 래퍼, 통합).
- Consumes: 기존 숫자 파이프라인 strip/VLM 경로(`report/sp2_spike/build_strips.py`·`bench.py` 또는 동등). VLM 부분은 수동 검증.

- [ ] **Step 1: 실패 테스트 작성** — `item/test_group_amounts.py`

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from group_amounts import sum_check  # noqa: E402


def test_sum_check_grouping_block_sums_match_db():
    # 데이터행 값 [270,110,120,55] 블록0, [60] 블록1 → DB [555,60]
    vals = [270, 110, 120, 55, 60]
    blocks = [0, 0, 0, 0, 1]
    assert sum_check(vals, blocks, [555, 60]) == [True, True]


def test_sum_check_flags_mismatch():
    vals = [100, 700]
    blocks = [0, 1]
    assert sum_check(vals, blocks, [100, 701]) == [True, False]
```

- [ ] **Step 2: 실패 확인**

Run: `cd apps/invoice-ocr/ml/report/sp2_spike/item && $PY -m pytest test_group_amounts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'group_amounts'`.

- [ ] **Step 3: 구현** — `item/group_amounts.py`

```python
"""2차 보조 — 금액칸 값 판독 후 블록별 합 == DB 항목합 정합(false confidence 인지, 하드게이트 아님)."""
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))


def sum_check(row_values, row_blocks, db_amounts):
    """데이터행 값/블록 → 블록별 합 == db_amounts[블록] 여부 리스트."""
    sums = {}
    for v, b in zip(row_values, row_blocks):
        sums[b] = sums.get(b, 0) + v
    return [sums.get(i, 0) == db_amounts[i] for i in range(len(db_amounts))]


def read_amounts(warp):
    """금액칸 strip → VLM 위→아래 정수 리스트(기존 숫자 파이프라인 경로 재사용).
    판독수 != 데이터행수면 정렬 불확실 → 호출측에서 부분표시."""
    from amount_strip import warp_to_amount_strip  # build_strips 동등 헬퍼
    from probe_vlm import read_integers          # 기존 단발 probe
    return read_integers(warp_to_amount_strip(warp))
```

주의: `read_amounts`의 import 대상(`amount_strip`/`probe_vlm`)은 기존 숫자 스파이크 스크립트에 맞춰 조정한다. 함수가 없으면 `report/sp2_spike/build_strips.py`·`probe_vlm.py`의 실제 함수명으로 교체(이 줄은 통합 시 확정). VLM 호출은 자동 테스트 대상 아님.

- [ ] **Step 4: 통과 확인 (순수부)**

Run: `cd apps/invoice-ocr/ml/report/sp2_spike/item && $PY -m pytest test_group_amounts.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: 커밋**

```bash
cd /Users/gangsub/projects/sjmj-ai
git add apps/invoice-ocr/ml/report/sp2_spike/item/group_amounts.py apps/invoice-ocr/ml/report/sp2_spike/item/test_group_amounts.py
git commit -m "feat(sp2): group_amounts.py 블록 금액합 정합(sum_check TDD)·VLM 판독 래퍼"
```

---

## Task 8: dataset_build.py — 교정 GT ingest → dataset_v2

**Files:**
- Modify: `apps/invoice-ocr/ml/report/sp2_spike/item/dataset_build.py`

**Interfaces:**
- Consumes: `grouping.all_warps_and_pitch/propose/PAD`, `group.apply_corrections/proposal`, `rows.ITEM_X/band_features`, 교정 파일 `item/grouping_corrections.json`(있으면).
- Produces: `dataset_v2/<DB명>/<cname>_<k>.png`(`new` 행 박스 크롭) + 통계 출력. 교정 없으면 auto proposal 사용.

기존 `dataset_build.py`의 라벨 생성부를 그룹핑 proposal 기반으로 교체. 통합 태스크 → 실행-검증.

- [ ] **Step 1: 현재 라벨 생성부 확인**

Run: `cd apps/invoice-ocr/ml/report/sp2_spike/item && $PY -c "import dataset_build,inspect; print(inspect.getsourcefile(dataset_build))"`
그리고 `dataset_build.py`에서 `select_items`/`labelset` 호출로 `dataset_v2`를 쓰는 구간을 찾는다(라벨 크롭 작성 루프). 이 구간을 Step 2 함수로 교체한다.

- [ ] **Step 2: 그룹핑 기반 라벨 작성 함수 추가** — `item/dataset_build.py`에 추가

```python
def build_labelset_grouped():
    """그룹핑 proposal(+교정) 기반 dataset_v2 생성. corrections 있으면 우선."""
    import json
    from grouping import all_warps_and_pitch, propose, PAD
    from rows import band_features
    from group import apply_corrections

    cnames, warps, manifest, P = all_warps_and_pitch()
    corr_path = HERE / "grouping_corrections.json"
    corr = json.load(open(corr_path)) if corr_path.exists() else {}

    out = ORG.parents[0] / "dataset_v2"   # 기존 dataset_v2 경로 규약에 맞춰 조정
    out.mkdir(exist_ok=True)
    for d in out.glob("*"):
        for f in d.glob("*"):
            f.unlink()

    trusted = labeled = 0
    for cn in cnames:
        names = manifest[cn]["items"]
        p = propose(warps[cn], names, P)
        if cn in corr:
            _, _, stroke_rows = band_features(warps[cn], [tuple(b) for b in corr[cn]["bands"]])
            p = apply_corrections(p, corr[cn]["types"], names, stroke_rows, pad=PAD)
        if p.status != "ok":
            continue
        trusted += 1
        for r in p.rows:
            if r.rtype == "new" and r.box and r.db_name:
                a, b = r.box
                crop = warps[cn][a:b, ITEM_X[0] - 4:ITEM_X[1] + 4]
                dd = out / _safe(r.db_name)
                dd.mkdir(exist_ok=True)
                cv2.imwrite(str(dd / f"{cn}_{r.db_idx}.png"), crop)
                labeled += 1
    bylabel = {d.name: len(list(d.glob("*.png"))) for d in out.glob("*") if d.is_dir()}
    multi = {k: v for k, v in bylabel.items() if v >= 2}
    print(f"[grouped] trusted {trusted}/{len(cnames)} · crop {labeled} · "
          f"고유라벨 {len(bylabel)} · 재현(2+) {len(multi)}")
```

주의: `ITEM_X` import(`from rows import ITEM_X`), `_safe`(기존 `safe`/`labelset.safe` 재사용 — 실제 함수명으로 교체), `out` 경로(기존 `dataset_v2` 규약)는 `dataset_build.py` 현 구조에 맞춰 확정한다. `__main__`에서 `build_labelset_grouped()`를 호출하도록 추가(기존 호출은 보존하거나 `--grouped` 플래그로 분기).

- [ ] **Step 3: 실행-검증**

Run: `cd apps/invoice-ocr/ml/report/sp2_spike/item && $PY dataset_build.py --grouped`
Expected: `[grouped] trusted N/74 · crop M · 고유라벨 U · 재현(2+) R`. 확인 — 현재 라벨셋(trusted 68/74·crop 314·고유 142)과 비교해 **그룹핑 전표가 trusted로 추가**되면 성공. crop 개별 파일 몇 개를 열어 박스가 글씨에 타이트한지 육안 확인.

- [ ] **Step 4: 커밋**

```bash
cd /Users/gangsub/projects/sjmj-ai
git add apps/invoice-ocr/ml/report/sp2_spike/item/dataset_build.py
git commit -m "feat(sp2): dataset_build 그룹핑 proposal+교정 GT ingest → dataset_v2"
```

---

## Self-Review

**Spec coverage:**
- §0 두 결함 → Task 2(박스 스냅, 결함1)·Task 1(이중신호 분류, 결함2). ✅
- §2 금액칸 척추 접근 → Task 4(`detect_amount_rows`). ✅
- §3 컴포넌트 표 → Task 1–8이 각 파일 생성/수정. ✅
- §4 데이터 흐름 → Task 5(오케스트레이터)가 전체 연결. ✅
- §5 핵심 규칙(분류·블록·status·박스) → Task 1·2. ✅
- §6 2차 금액합 → Task 7. ✅
- §7 교정 에디터 UX(타입순환·export) → Task 6. ✅
- §8 dataset_build 통합 → Task 8. ✅
- §9 한계(타입만 교정·밴드오류 needs_review) → Task 6 제약·Task 5 census로 표면화. ✅
- §10 테스트(순수함수만) → Task 1–4·7 TDD, 5·6·8 실행-검증. ✅
- §11 검증 루프 → Task 8 통계 비교. ✅

**Placeholder scan:** Task 7·8에 "기존 함수명에 맞춰 조정" 주의가 있으나, 이는 기존 숫자 파이프라인/`dataset_build` 실구조를 구현 시점에 grep해 확정해야 하는 **통합 seam**이라 명시적 지시로 남김(placeholder 아님 — 무엇을·어디서 확인할지 적시). 순수 로직(sum_check·라벨 작성 골격)은 완전 코드 제공.

**Type consistency:** `Row(band,item_ink,amt_ink,rtype,box,block,db_idx,db_name)`·`Proposal(rows,n_blocks,dbn,status)`·`build_proposal/apply_corrections/_assemble` 인자명·`status∈{ok,needs_review}`·`ROW_*` 상수가 Task 1–8·JS recompute 전반에서 일관. `propose(warp,db_names,P)`·`band_features→(item_inks,amt_inks,stroke_rows)`·`detect_amount_rows(warp,P)`·`segment_rows(profile,P,y0,y1,on,min_gap)` 시그니처 일치. ✅
