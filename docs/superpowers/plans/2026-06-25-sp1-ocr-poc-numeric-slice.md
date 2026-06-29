# SP1 — OCR PoC 숫자 수직 슬라이스 (검출-포함 종단) 구현 Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 수기 거래명세서 사진 38장의 숫자 셀(수량·단가·공급가)을 stock 검출(PP-Structure)+인식(PaddleOCR)으로 읽고 산술검산까지 통과시켜, DB 정답 대비 3축(검출 리콜·인식 정확도·검산 게인) 측정 리포트를 산출하는 오프라인 CLI를 만든다.

**Architecture:** `apps/invoice-ocr/ml`의 독립 uv 프로젝트. 외부 모델(검출기·인식기)은 `DetectorAdapter`/`RecognizerAdapter` 추상 뒤에 두어 Fake로 결정론적 단위테스트하고 실모델은 스모크. 채점 코어(db/assemble/normalize/validate/score)는 순수함수로 두텁게 단위테스트. 손라벨 geometry는 안 쓰고(신뢰 불가) 정답은 운영 DB(references 일자→DB 조회)에서 가져온다.

**Tech Stack:** Python 3.12, uv, PaddleOCR(PP-Structure 표인식 + PP-OCRv5 인식), Pillow, numpy, pytest. 표준 라이브러리 SQL 파서(외부 DB 드라이버 없음).

## Global Constraints

- **Python 3.12 고정**: `requires-python = ">=3.12,<3.13"`. paddlepaddle가 3.13 휠을 아직 제공하지 않을 위험 → 3.12로 핀. (Task 1 스파이크가 실제 설치로 확정)
- **독립 uv 프로젝트**: `apps/invoice-ocr/ml`. backend(`apps/invoice-ocr/backend`)와 의존 분리. SP1은 backend에 배선하지 않는다.
- **작업 디렉터리**: 모든 `uv run pytest`/`uv run python -m ...` 명령은 **`apps/invoice-ocr/ml`에서** 실행한다(flat 레이아웃이라 cwd가 import 루트). `git` 명령은 레포 루트 기준 상대경로라 어디서든 가능. Task 1 Step 6 이후 cwd=ml 가정.
- **실데이터·실경로는 env로만**: `SJMJ_DATA_DIR`(이미지/라벨/references 루트), `SJMJ_DB_BACKUP`(.sql 경로). 코드·테스트·커밋에 실경로/실데이터 하드코딩 금지. `.gitignore`가 `results/`·`report/`·`*.env`(단 `*.env.example` 허용)를 차단.
- **손라벨 geometry 미사용**: 라벨 JSON의 `*_bbox`·`doc_corners`·`doc_bbox`는 안 쓴다. 라벨은 `*_text`만 보조 정답으로 사용.
- **정답 기준 = `supply`(공급가)**, `total`(부가세포함) 아님. `세액 = 공급가 × 10%`.
- **천원곱 임계 = 1000**: unit_price/amount의 raw 값이 1000 미만이면 ×1000. quantity는 literal(곱 안 함).
- **개별 이미지 실패 격리**: 38장 배치에서 한 장 실패가 전체를 중단시키지 않는다 — 기록 후 계속.
- **불변 데이터**: 모든 모델은 `@dataclass(frozen=True)`. 컬렉션 변형 대신 새 객체 반환.
- **커밋 규약**: conventional commits. 각 task 종료 시 독립 커밋. 메시지 말미:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01NPPNFcuVYWpE8mC5pDkXZp
  ```

## 파일 구조

```
apps/invoice-ocr/ml/
├── pyproject.toml          # 독립 uv: paddleocr, pillow, numpy / dev: pytest
├── .python-version         # 3.12
├── .env.example            # SJMJ_DATA_DIR, SJMJ_DB_BACKUP 예시
├── README.md
├── ocr_poc/                # flat 레이아웃(backend 관례): cwd(ml) + `python -m`
│   ├── __init__.py
│   ├── config.py           # env 경로 해석 (Task 1)
│   ├── data.py             # 이미지·라벨(text) 페어 로딩 (Task 3)
│   ├── db.py               # .sql 백업 파싱·조회 (Task 2)
│   ├── match.py            # references 일자추출·검수CSV·DB조회 (Task 4)
│   ├── detect.py           # DetectorAdapter + PPStructureDetector (Task 5)
│   ├── assemble.py         # 셀→열매핑→삼중쌍 (Task 6)
│   ├── crop.py             # 셀박스→crop·검증 (Task 7)
│   ├── recognize.py        # RecognizerAdapter + PaddleOCRNumeric (Task 8)
│   ├── normalize.py        # 천원곱·빈칸·ditto (Task 9)
│   ├── validate.py         # 산술검산·역산 (Task 10)
│   ├── score.py            # 3축 메트릭·값매칭정렬·집계 (Task 11)
│   ├── report.py           # md/json 리포트·갤러리 (Task 12)
│   └── __main__.py         # 배치 오케스트레이션 CLI (Task 13)
├── tools/
│   ├── __init__.py
│   └── spike_ppstructure.py  # Task 1 스파이크(`python -m tools.spike_ppstructure`)
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── test_db.py
    ├── test_match.py
    ├── test_detect.py
    ├── test_assemble.py
    ├── test_crop.py
    ├── test_recognize.py
    ├── test_normalize.py
    ├── test_validate.py
    ├── test_score.py
    └── test_pipeline_smoke.py
```

각 셀(detect 결과)·행(assemble 결과)·정답행(db) 등 공유 dataclass는 **생산 모듈에 정의**하고 소비 모듈이 import 한다(별도 types.py 없음 — 응집).

---

## Task 1: uv 프로젝트 스캐폴드 + PP-Structure 표인식 스파이크

**성격:** 환경·능력 검증 **스파이크**(순수 TDD 아님). SP1 최대 불확실("paddle가 3.12에 설치되는가" + "PP-Structure가 수기·사진 표의 셀 그리드를 쓸만하게 잡는가")을 가장 먼저 깬다. 끝에 **사람 결정 게이트**(go / 괘선 폴백 / 재범위).

**Files:**
- Create: `apps/invoice-ocr/ml/pyproject.toml`
- Create: `apps/invoice-ocr/ml/.python-version`
- Create: `apps/invoice-ocr/ml/.gitignore`
- Create: `apps/invoice-ocr/ml/.env.example`
- Create: `apps/invoice-ocr/ml/README.md`
- Create: `apps/invoice-ocr/ml/ocr_poc/__init__.py`
- Create: `apps/invoice-ocr/ml/ocr_poc/config.py`
- Create: `apps/invoice-ocr/ml/tools/__init__.py` (빈 파일 — `python -m tools.*` 용)
- Create: `apps/invoice-ocr/ml/tools/spike_ppstructure.py`
- Create: `apps/invoice-ocr/ml/tests/__init__.py`
- Create: `apps/invoice-ocr/ml/tests/conftest.py`
- Delete: `apps/invoice-ocr/ml/.gitkeep`

**Interfaces:**
- Produces: `config.data_dir() -> Path`, `config.db_backup_path() -> Path`, `config.images_dir()/labels_dir()/references_dir() -> Path` (모두 env 기반, 미설정 시 `RuntimeError`).

- [ ] **Step 1: pyproject.toml 작성**

`apps/invoice-ocr/ml/pyproject.toml`:
```toml
[project]
name = "sjmj-ai-invoice-ocr-ml"
version = "0.1.0"
description = "sjmj-ai invoice-ocr SP1 — OCR PoC 숫자 수직 슬라이스 (오프라인 CLI)"
requires-python = ">=3.12,<3.13"
dependencies = [
    "paddleocr>=2.7",
    "paddlepaddle>=2.6",
    "pillow>=10.0",
    "numpy>=1.26",
]

[dependency-groups]
dev = [
    "pytest>=8.3.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]

[tool.uv]
package = false
```

- [ ] **Step 2: 보조 파일 작성**

`apps/invoice-ocr/ml/.python-version`:
```
3.12
```

`apps/invoice-ocr/ml/.gitignore` (산출물·실경로 차단 — 레포 루트엔 results/·report/ 항목이 없음):
```gitignore
results/
report/
.env
.venv/
__pycache__/
```

`apps/invoice-ocr/ml/.env.example`:
```bash
# SP1 오프라인 CLI 입력 경로 (실제 값은 .env로 두며 gitignore 됨)
# 이미지/라벨/references 루트 (하위에 images/ labels/ references/)
SJMJ_DATA_DIR=/absolute/path/to/sjmj image
# 운영 DB 백업 (.sql)
SJMJ_DB_BACKUP=/absolute/path/to/SJMJ-Web/database/db-2026-06-24-backup.sql
```

`apps/invoice-ocr/ml/README.md`:
```markdown
# invoice-ocr / ml (SP1)

수기 거래명세서 숫자 셀 OCR PoC. 오프라인 배치 CLI.

## 실행
```bash
cd apps/invoice-ocr/ml
uv sync
cp .env.example .env   # 경로 채우기
# 환경/검출 스파이크
uv run python -m tools.spike_ppstructure inv_003
# 본 파이프라인 (Task 13에서 완성)
uv run python -m ocr_poc match-extract     # reviewed_dates.csv 생성
# (사람이 reviewed_dates.csv 검수)
uv run python -m ocr_poc run               # 38장 배치 → report/
```

데이터·DB는 레포 밖(OneDrive/타 레포). 경로는 `.env`로 주입한다.
```

`apps/invoice-ocr/ml/ocr_poc/__init__.py`:
```python
"""sjmj-ai invoice-ocr SP1 — OCR PoC 숫자 수직 슬라이스."""
```

- [ ] **Step 3: config.py 작성 (env 경계 검증)**

`apps/invoice-ocr/ml/ocr_poc/config.py`:
```python
"""env 기반 경로 해석 — 시스템 경계 입력 검증."""
import os
from pathlib import Path


def data_dir() -> Path:
    """SJMJ_DATA_DIR 루트. 미설정/부재 시 RuntimeError."""
    raw = os.environ.get("SJMJ_DATA_DIR")
    if not raw:
        raise RuntimeError("SJMJ_DATA_DIR 미설정 — .env 참조")
    p = Path(raw)
    if not p.is_dir():
        raise RuntimeError(f"SJMJ_DATA_DIR 경로 없음: {p}")
    return p


def db_backup_path() -> Path:
    """SJMJ_DB_BACKUP (.sql). 미설정/부재 시 RuntimeError."""
    raw = os.environ.get("SJMJ_DB_BACKUP")
    if not raw:
        raise RuntimeError("SJMJ_DB_BACKUP 미설정 — .env 참조")
    p = Path(raw)
    if not p.is_file():
        raise RuntimeError(f"SJMJ_DB_BACKUP 파일 없음: {p}")
    return p


def images_dir() -> Path:
    return data_dir() / "images"


def labels_dir() -> Path:
    return data_dir() / "labels"


def references_dir() -> Path:
    return data_dir() / "references"
```

- [ ] **Step 4: tests 부트스트랩**

`apps/invoice-ocr/ml/tests/__init__.py`: (빈 파일)

`apps/invoice-ocr/ml/tests/conftest.py`:
```python
"""공용 픽스처. 실데이터에 의존하지 않는 합성 데이터만 둔다."""
import pytest


@pytest.fixture
def tiny_invoices_sql() -> str:
    """invoices/invoice_items 최소 INSERT 샘플 (백업 형식 모사)."""
    return (
        "INSERT INTO `invoices` (`id`, `document_title`, `issue_date`, `recipient`, "
        "`recipient2`, `vehicle_no`, `memo`, `show_stamp`, `issuer_id`, `total_supply`, "
        "`total_vat`, `grand_total`, `created_at`, `updated_at`) VALUES\n"
        "(11, '거래명세서', '2026-05-12', '옥천운수', '이희원', '5608', '', 1, NULL, "
        "300000, 30000, 330000, '2026-05-12 05:57:39', '2026-05-12 05:57:39'),\n"
        "(12, '거래명세서', '2026-05-13', '성우항공', NULL, '3102', 'O''Brien 메모', 1, NULL, "
        "120000, 12000, 132000, '2026-05-13 08:48:53', '2026-05-13 08:48:53');\n"
        "INSERT INTO `invoice_items` (`id`, `invoice_id`, `item_order`, `name`, `quantity`, "
        "`unit`, `unit_price`, `supply`, `vat`, `total`) VALUES\n"
        "(42, 11, 1, '단지', 1, 'EA', 300000, 300000, 30000, 330000),\n"
        "(43, 12, 1, '세차', 1, 'EA', 30000, 30000, 3000, 33000),\n"
        "(44, 12, 2, '중고타이어', 1, NULL, 90000, 90000, 9000, 99000);\n"
    )
```

- [ ] **Step 5: 스파이크 스크립트 작성**

`apps/invoice-ocr/ml/tools/spike_ppstructure.py`:
```python
"""PP-Structure 환경·표인식 스파이크 (일회성).

usage: uv run python -m tools.spike_ppstructure inv_003   (cwd = apps/invoice-ocr/ml)
한 장에 PP-Structure 표인식을 돌려 (1) 환경이 서는지 (2) 셀 박스가
쓸만하게 잡히는지를 raw 출력 덤프 + 시각화로 확인한다. 결과로 detect.py의
DetectorAdapter가 PP-Structure 출력에서 무엇을 어떻게 뽑을지 확정한다.
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from ocr_poc import config


def main(image_id: str) -> None:
    img_path = config.images_dir() / f"{image_id}.jpg"
    print(f"[spike] image = {img_path}")

    # PP-Structure 호출 — 실제 API 시그니처는 설치된 paddleocr 버전에서 확인.
    # paddleocr 2.7 계열: from paddleocr import PPStructure
    from paddleocr import PPStructure  # noqa: PLC0415

    engine = PPStructure(show_log=False, lang="korean")
    result = engine(str(img_path))

    print(f"[spike] result blocks = {len(result)}")
    overlay = Image.open(img_path).convert("RGB")
    draw = ImageDraw.Draw(overlay)
    for block in result:
        print("  block.type =", block.get("type"), " bbox =", block.get("bbox"))
        res = block.get("res")
        # table 블록이면 res에 cell 박스/html이 들어옴 — 구조를 그대로 덤프.
        if isinstance(res, dict):
            print("    res.keys =", list(res.keys()))
            for cell in res.get("cell_bbox", []) or []:
                draw.polygon([tuple(p) for p in _as_points(cell)], outline=(255, 0, 0))
    out = Path("report") / f"spike-{image_id}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    overlay.save(out)
    print(f"[spike] overlay saved → {out}")


def _as_points(cell):
    """cell_bbox 항목을 (x,y) 점 리스트로. [x1,y1,x2,y2] 또는 8-좌표 모두 수용."""
    flat = list(cell)
    if len(flat) == 4:
        x1, y1, x2, y2 = flat
        return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    return [(flat[i], flat[i + 1]) for i in range(0, len(flat), 2)]


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "inv_003")
```

- [ ] **Step 6: 설치 + 스파이크 실행 (환경 검증)**

```bash
cd apps/invoice-ocr/ml
rm -f .gitkeep
uv sync
```
Expected: paddlepaddle/paddleocr가 Python 3.12로 설치 완료 (PASS). 만약 휠 부재로 실패하면 → 그 자체가 발견. `requires-python` 하한을 paddle 지원선에 맞추고 기록.

```bash
cp .env.example .env   # 실제 경로 채움 (사람)
uv run python -m tools.spike_ppstructure inv_003
```
Expected: `report/spike-inv_003.png` 생성. 콘솔에 table 블록과 cell 박스 구조 덤프.

- [ ] **Step 7: 결정 게이트 (사람 확인)**

`report/spike-inv_003.png`를 사람이 본다. 판정:
- **go**: 셀 그리드가 행/열로 쓸만하게 잡힘 → Task 5에서 PP-Structure 출력→`DetectedCell` 매핑을 이 스파이크에서 본 구조대로 구현.
- **괘선 폴백**: 표인식이 빈약 → Task 5의 `PPStructureDetector` 대신/병행 `RuledLineDetector`(모폴로지) 구현으로 분기 (같은 `DetectorAdapter` 인터페이스). spec §6.
- **재범위**: 둘 다 부족 → 사용자와 SP1 범위 재협의.

발견(실제 API 시그니처, 셀 박스 좌표 형식, table 블록 식별법)을 커밋 메시지에 요약한다.

- [ ] **Step 8: Commit**

```bash
# cwd = apps/invoice-ocr/ml. .gitkeep 삭제 + 신규 파일 일괄 스테이징.
# results/·report/·.venv·.env 는 레포 .gitignore 가 차단(검증: git status 로 확인)
git add -A .
git status --short          # report/·results/·.env·.venv 가 안 보여야 함
git commit -m "feat(sp1): ml uv 스캐폴드 + PP-Structure 표인식 스파이크

paddle 3.12 설치 확인. PP-Structure 출력 구조/셀 박스 형식 확정.
결정: <go|괘선 폴백|재범위> — <한 줄 근거>"
```
(커밋 메시지 말미에 Global Constraints의 Co-Authored-By/Claude-Session 추가)

> ⚠️ 레포 루트 `.gitignore`는 `report/`·`results/`를 명시 차단하지 않는다(현재 Python/Node/env/OS 항목만). Task 1에서 `apps/invoice-ocr/ml/.gitignore`에 `results/`·`report/`·`.env`를 추가해 산출물·실경로가 커밋되지 않도록 한다. 이 파일도 본 스캐폴드 커밋에 포함.

---

## Task 2: db.py — 백업 파싱 + 조회

**Files:**
- Create: `apps/invoice-ocr/ml/ocr_poc/db.py`
- Test: `apps/invoice-ocr/ml/tests/test_db.py`

**Interfaces:**
- Consumes: 없음 (표준 라이브러리만).
- Produces:
  - `@dataclass(frozen=True) Invoice(id:int, issue_date:str, recipient:str, total_supply:int, grand_total:int)`
  - `@dataclass(frozen=True) InvoiceItem(invoice_id:int, item_order:int, name:str, quantity:int, unit_price:int, supply:int, vat:int, total:int)`
  - `@dataclass(frozen=True) InvoiceDB(invoices:tuple[Invoice,...], items_by_invoice:dict[int,tuple[InvoiceItem,...]])` with `find_by_date_and_total(date:str, grand_total:int) -> list[Invoice]`, `find_by_grand_total(grand_total:int) -> list[Invoice]`, `items_for(invoice_id:int) -> tuple[InvoiceItem,...]`
  - `parse_backup(sql_text:str) -> InvoiceDB`

- [ ] **Step 1: 실패 테스트 작성 — 파서/조회**

`apps/invoice-ocr/ml/tests/test_db.py`:
```python
from ocr_poc.db import parse_backup, Invoice, InvoiceItem


def test_parses_invoices_and_items(tiny_invoices_sql):
    db = parse_backup(tiny_invoices_sql)
    assert len(db.invoices) == 2
    inv11 = next(i for i in db.invoices if i.id == 11)
    assert inv11 == Invoice(id=11, issue_date="2026-05-12", recipient="옥천운수",
                            total_supply=300000, grand_total=330000)


def test_handles_null_and_escaped_quote(tiny_invoices_sql):
    db = parse_backup(tiny_invoices_sql)
    # recipient2 NULL, memo 에 escaped quote 가 있어도 행 경계가 깨지지 않는다
    items12 = db.items_for(12)
    assert len(items12) == 2
    assert items12[1].name == "중고타이어"   # unit 이 NULL 인 행도 정상 파싱


def test_find_by_date_and_total_unique(tiny_invoices_sql):
    db = parse_backup(tiny_invoices_sql)
    hits = db.find_by_date_and_total("2026-05-12", 330000)
    assert [h.id for h in hits] == [11]


def test_find_by_grand_total(tiny_invoices_sql):
    db = parse_backup(tiny_invoices_sql)
    assert [h.id for h in db.find_by_grand_total(132000)] == [12]


def test_items_ordered_by_item_order(tiny_invoices_sql):
    db = parse_backup(tiny_invoices_sql)
    orders = [it.item_order for it in db.items_for(12)]
    assert orders == [1, 2]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd apps/invoice-ocr/ml && uv run pytest tests/test_db.py -v`
Expected: FAIL (`ModuleNotFoundError: ocr_poc.db`).

- [ ] **Step 3: db.py 구현 (문자 스캐너 파서)**

`apps/invoice-ocr/ml/ocr_poc/db.py`:
```python
"""운영 DB 백업(.sql, phpMyAdmin)의 invoices/invoice_items 파싱·조회.

외부 DB 드라이버 없이 표준 라이브러리만으로 INSERT VALUES 를 견고히 파싱한다.
러프 정규식은 멀티행 VALUES·escaped quote·NULL 에서 행을 누락하므로,
따옴표/괄호를 추적하는 문자 스캐너로 전 행을 보장한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Invoice:
    id: int
    issue_date: str
    recipient: str
    total_supply: int
    grand_total: int


@dataclass(frozen=True)
class InvoiceItem:
    invoice_id: int
    item_order: int
    name: str
    quantity: int
    unit_price: int
    supply: int
    vat: int
    total: int


@dataclass(frozen=True)
class InvoiceDB:
    invoices: tuple[Invoice, ...]
    items_by_invoice: dict[int, tuple[InvoiceItem, ...]]

    def find_by_grand_total(self, grand_total: int) -> list[Invoice]:
        return [i for i in self.invoices if i.grand_total == grand_total]

    def find_by_date_and_total(self, date: str, grand_total: int) -> list[Invoice]:
        return [i for i in self.invoices
                if i.issue_date == date and i.grand_total == grand_total]

    def items_for(self, invoice_id: int) -> tuple[InvoiceItem, ...]:
        return self.items_by_invoice.get(invoice_id, ())


_INSERT_RE = re.compile(
    r"INSERT INTO `(?P<table>\w+)` \((?P<cols>[^)]*)\) VALUES\s*(?P<body>.*?);",
    re.DOTALL,
)


def _split_top_level_groups(blob: str) -> list[str]:
    """`(...),(...)` 최상위 괄호 그룹들의 내부 문자열 리스트. 따옴표/escape 인지."""
    groups: list[str] = []
    buf: list[str] = []
    depth = 0
    in_str = False
    esc = False
    for ch in blob:
        if in_str:
            buf.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == "'":
                in_str = False
            continue
        if ch == "'":
            in_str = True
            buf.append(ch)
        elif ch == "(":
            if depth == 0:
                buf = []
            else:
                buf.append(ch)
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                groups.append("".join(buf))
            else:
                buf.append(ch)
        elif depth > 0:
            buf.append(ch)
    return groups


def _split_fields(row: str) -> list[str]:
    """행 내부를 최상위 콤마로 분할(따옴표/escape 인지)."""
    fields: list[str] = []
    buf: list[str] = []
    in_str = False
    esc = False
    for ch in row:
        if in_str:
            buf.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == "'":
                in_str = False
            continue
        if ch == "'":
            in_str = True
            buf.append(ch)
        elif ch == ",":
            fields.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    fields.append("".join(buf).strip())
    return fields


def _unquote(field: str) -> str | None:
    """SQL 값 토큰 → 파이썬 값(str|None). 숫자는 호출부에서 int 변환."""
    if field == "NULL":
        return None
    if len(field) >= 2 and field[0] == "'" and field[-1] == "'":
        inner = field[1:-1]
        return (inner.replace("\\'", "'").replace("''", "'")
                .replace('\\"', '"').replace("\\\\", "\\").replace("\\n", "\n"))
    return field


def _to_int(field: str) -> int:
    val = _unquote(field)
    return int(val) if val not in (None, "") else 0


def _rows_for_table(sql_text: str, table: str) -> list[tuple[list[str], list[str]]]:
    """(컬럼명 리스트, 필드 토큰 리스트) 행들."""
    out: list[tuple[list[str], list[str]]] = []
    for m in _INSERT_RE.finditer(sql_text):
        if m.group("table") != table:
            continue
        cols = [c.strip().strip("`") for c in m.group("cols").split(",")]
        for group in _split_top_level_groups(m.group("body")):
            out.append((cols, _split_fields(group)))
    return out


def parse_backup(sql_text: str) -> InvoiceDB:
    invoices: list[Invoice] = []
    for cols, fields in _rows_for_table(sql_text, "invoices"):
        rec = dict(zip(cols, fields))
        invoices.append(Invoice(
            id=_to_int(rec["id"]),
            issue_date=_unquote(rec["issue_date"]) or "",
            recipient=_unquote(rec["recipient"]) or "",
            total_supply=_to_int(rec["total_supply"]),
            grand_total=_to_int(rec["grand_total"]),
        ))

    items: dict[int, list[InvoiceItem]] = {}
    for cols, fields in _rows_for_table(sql_text, "invoice_items"):
        rec = dict(zip(cols, fields))
        item = InvoiceItem(
            invoice_id=_to_int(rec["invoice_id"]),
            item_order=_to_int(rec["item_order"]),
            name=_unquote(rec["name"]) or "",
            quantity=_to_int(rec["quantity"]),
            unit_price=_to_int(rec["unit_price"]),
            supply=_to_int(rec["supply"]),
            vat=_to_int(rec["vat"]),
            total=_to_int(rec["total"]),
        )
        items.setdefault(item.invoice_id, []).append(item)

    items_sorted = {
        inv_id: tuple(sorted(rows, key=lambda r: r.item_order))
        for inv_id, rows in items.items()
    }
    return InvoiceDB(invoices=tuple(invoices), items_by_invoice=items_sorted)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd apps/invoice-ocr/ml && uv run pytest tests/test_db.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: 실백업 전 행 파싱 스모크 (수동, 실데이터)**

```bash
uv run python -c "from ocr_poc import config, db; d=db.parse_backup(config.db_backup_path().read_text(encoding='utf-8')); print('invoices', len(d.invoices)); print('items', sum(len(v) for v in d.items_by_invoice.values()))"
```
Expected: `invoices 400`, `items 1302` (러프 정규식이 누락하던 행까지 전부). 수치가 다르면 파서 버그 → 고치고 재확인.

- [ ] **Step 6: Commit**

```bash
git add ocr_poc/db.py tests/test_db.py
git commit -m "feat(sp1): db.py — 백업 .sql 견고 파싱·조회 (전 행 보장)"
```

---

## Task 3: data.py — 이미지·라벨(text) 페어 로딩

**Files:**
- Create: `apps/invoice-ocr/ml/ocr_poc/data.py`
- Test: `apps/invoice-ocr/ml/tests/test_data.py`

**Interfaces:**
- Consumes: `config.images_dir()/labels_dir()`.
- Produces:
  - `@dataclass(frozen=True) LabelRow(row_id:int, item:str, quantity:str, unit_price:str, amount:str)`
  - `@dataclass(frozen=True) Sample(image_id:str, image_path:Path, label_rows:tuple[LabelRow,...])`
  - `load_label(label_json:dict) -> tuple[LabelRow,...]` (순수, geometry 무시)
  - `load_samples(images_dir:Path, labels_dir:Path) -> list[Sample]`

- [ ] **Step 1: 실패 테스트 작성**

`apps/invoice-ocr/ml/tests/test_data.py`:
```python
from ocr_poc.data import load_label, LabelRow


def test_load_label_uses_text_only_ignores_geometry():
    raw = {
        "image_id": "inv_x",
        "doc_bbox": [1, 2, 3, 4],
        "doc_corners": [[0, 0]],
        "rows": [
            {"row_id": 1, "item_class": "부동액", "quantity_text": "4",
             "unit_price_text": "25000", "amount_text": "100000",
             "quantity_bbox": [9, 9, 9, 9]},   # geometry 는 무시되어야 함
            {"row_id": 2, "item_class": "엔진오일", "quantity_text": "1",
             "unit_price_text": "365000", "amount_text": "365000"},
        ],
    }
    rows = load_label(raw)
    assert rows == (
        LabelRow(row_id=1, item="부동액", quantity="4", unit_price="25000", amount="100000"),
        LabelRow(row_id=2, item="엔진오일", quantity="1", unit_price="365000", amount="365000"),
    )


def test_load_label_missing_text_becomes_empty_string():
    raw = {"rows": [{"row_id": 1, "item_class": "x"}]}
    rows = load_label(raw)
    assert rows[0] == LabelRow(row_id=1, item="x", quantity="", unit_price="", amount="")
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/test_data.py -v`
Expected: FAIL (`ModuleNotFoundError: ocr_poc.data`).

- [ ] **Step 3: data.py 구현**

`apps/invoice-ocr/ml/ocr_poc/data.py`:
```python
"""이미지·라벨 페어 로딩. 라벨은 *_text 만 쓰고 geometry 는 무시한다(§2.2)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LabelRow:
    row_id: int
    item: str
    quantity: str
    unit_price: str
    amount: str


@dataclass(frozen=True)
class Sample:
    image_id: str
    image_path: Path
    label_rows: tuple[LabelRow, ...]


def load_label(label_json: dict) -> tuple[LabelRow, ...]:
    rows: list[LabelRow] = []
    for r in label_json.get("rows", []):
        rows.append(LabelRow(
            row_id=int(r.get("row_id", len(rows) + 1)),
            item=str(r.get("item_class", "")),
            quantity=str(r.get("quantity_text", "")),
            unit_price=str(r.get("unit_price_text", "")),
            amount=str(r.get("amount_text", "")),
        ))
    return tuple(rows)


def load_samples(images_dir: Path, labels_dir: Path) -> list[Sample]:
    """images/inv_*.jpg ↔ labels/inv_*.json 페어. 라벨 없는 이미지는 제외·경고."""
    samples: list[Sample] = []
    for img_path in sorted(images_dir.glob("inv_*.jpg")):
        image_id = img_path.stem
        label_path = labels_dir / f"{image_id}.json"
        if not label_path.is_file():
            print(f"[data] 경고: 라벨 없음 {label_path} — 건너뜀")
            continue
        label_json = json.loads(label_path.read_text(encoding="utf-8"))
        samples.append(Sample(
            image_id=image_id,
            image_path=img_path,
            label_rows=load_label(label_json),
        ))
    return samples
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/test_data.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add ocr_poc/data.py tests/test_data.py
git commit -m "feat(sp1): data.py — 이미지·라벨 페어 로딩 (text only, geometry 무시)"
```

---

## Task 4: match.py — references 일자추출 · 검수 CSV · DB 유일조회

**Files:**
- Create: `apps/invoice-ocr/ml/ocr_poc/match.py`
- Test: `apps/invoice-ocr/ml/tests/test_match.py`

**Interfaces:**
- Consumes: `db.InvoiceDB`, `data.LabelRow`, `recognize.RecognizerAdapter`(Task 8에서 정의 — 본 task는 OCR 텍스트 리스트를 인자로 받는 순수 함수만 테스트, 어댑터 호출은 Task 13 글루에서).
- Produces:
  - `@dataclass(frozen=True) ReviewRow(image_id:str, extracted_date:str|None, grand_total:int, db_match_count:int, status:str)`
  - `@dataclass(frozen=True) GroundTruth(image_id:str, invoice_id:int, rows:tuple[db.InvoiceItem,...])`
  - `extract_date(texts:list[str]) -> str|None`
  - `grand_total_from_labels(rows:Iterable[data.LabelRow]) -> int`
  - `build_review_rows(per_image:list[tuple[str,list[str],int]], db:db.InvoiceDB) -> list[ReviewRow]`
  - `write_review_csv(rows:list[ReviewRow], path:Path) -> None` / `read_review_csv(path:Path) -> list[ReviewRow]`
  - `resolve_ground_truth(reviewed:list[ReviewRow], db:db.InvoiceDB) -> dict[str,GroundTruth]`

- [ ] **Step 1: 실패 테스트 작성**

`apps/invoice-ocr/ml/tests/test_match.py`:
```python
from pathlib import Path

from ocr_poc.data import LabelRow
from ocr_poc.db import parse_backup
from ocr_poc.match import (
    extract_date, grand_total_from_labels, build_review_rows,
    write_review_csv, read_review_csv, resolve_ground_truth, ReviewRow,
)


def test_extract_date_various_formats():
    assert extract_date(["합계", "발행일 2026-05-12", "옥천운수"]) == "2026-05-12"
    assert extract_date(["2026년 05월 13일", "x"]) == "2026-05-13"
    assert extract_date(["2026.05.14"]) == "2026-05-14"
    assert extract_date(["날짜없음"]) is None


def test_grand_total_from_labels_sums_amount():
    rows = [LabelRow(1, "a", "4", "25000", "100000"),
            LabelRow(2, "b", "1", "30000", "30000")]
    assert grand_total_from_labels(rows) == 130000


def test_build_review_rows_marks_status(tiny_invoices_sql):
    db = parse_backup(tiny_invoices_sql)
    per_image = [
        ("inv_a", ["2026-05-12"], 330000),   # 일자+총액 → 유일(inv 11)
        ("inv_b", ["2026-05-12"], 999999),   # 총액 불일치 → 0건
    ]
    rows = build_review_rows(per_image, db)
    by_id = {r.image_id: r for r in rows}
    assert by_id["inv_a"].db_match_count == 1
    assert by_id["inv_a"].status == "unique"
    assert by_id["inv_b"].db_match_count == 0
    assert by_id["inv_b"].status == "no_match"


def test_review_csv_roundtrip(tmp_path: Path):
    rows = [ReviewRow("inv_a", "2026-05-12", 330000, 1, "unique")]
    p = tmp_path / "reviewed_dates.csv"
    write_review_csv(rows, p)
    assert read_review_csv(p) == rows


def test_resolve_ground_truth_unique_only(tiny_invoices_sql):
    db = parse_backup(tiny_invoices_sql)
    reviewed = [
        ReviewRow("inv_a", "2026-05-12", 330000, 1, "unique"),
        ReviewRow("inv_b", "2026-05-12", 999999, 0, "no_match"),
    ]
    gt = resolve_ground_truth(reviewed, db)
    assert set(gt) == {"inv_a"}
    assert gt["inv_a"].invoice_id == 11
    assert gt["inv_a"].rows[0].supply == 300000
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/test_match.py -v`
Expected: FAIL (`ModuleNotFoundError: ocr_poc.match`).

- [ ] **Step 3: match.py 구현**

`apps/invoice-ocr/ml/ocr_poc/match.py`:
```python
"""references 인쇄일자 추출 → 사용자 검수(CSV) → 일자+grand_total DB 유일조회.

손라벨 행순서·geometry 를 쓰지 않는 정답지 매칭(§3). grand_total 은 라벨
amount_text 합에서 독립 산출(인식 결과 미사용 → 순환참조 없음).
"""
from __future__ import annotations

import csv
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from . import db as dbmod
from .data import LabelRow

_DATE_PATTERNS = [
    re.compile(r"(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})"),
]


@dataclass(frozen=True)
class ReviewRow:
    image_id: str
    extracted_date: str | None
    grand_total: int
    db_match_count: int
    status: str   # unique | ambiguous | no_match


@dataclass(frozen=True)
class GroundTruth:
    image_id: str
    invoice_id: int
    rows: tuple[dbmod.InvoiceItem, ...]


def extract_date(texts: list[str]) -> str | None:
    """OCR 텍스트들에서 첫 날짜를 YYYY-MM-DD 로 정규화."""
    for text in texts:
        for pat in _DATE_PATTERNS:
            m = pat.search(text)
            if m:
                y, mo, d = m.groups()
                return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    return None


def grand_total_from_labels(rows: Iterable[LabelRow]) -> int:
    """라벨 amount_text 합(공급가 합 = grand_total 의 공급가 기반 근사)."""
    total = 0
    for r in rows:
        digits = re.sub(r"[^0-9]", "", r.amount)
        if digits:
            total += int(digits)
    return total


def _status_for(count: int) -> str:
    if count == 1:
        return "unique"
    if count == 0:
        return "no_match"
    return "ambiguous"


def build_review_rows(
    per_image: list[tuple[str, list[str], int]],
    db: dbmod.InvoiceDB,
) -> list[ReviewRow]:
    """per_image: (image_id, references_ocr_texts, grand_total) → 검수행."""
    rows: list[ReviewRow] = []
    for image_id, texts, grand_total in per_image:
        date = extract_date(texts)
        if date is not None:
            hits = db.find_by_date_and_total(date, grand_total)
        else:
            hits = db.find_by_grand_total(grand_total)
        rows.append(ReviewRow(
            image_id=image_id,
            extracted_date=date,
            grand_total=grand_total,
            db_match_count=len(hits),
            status=_status_for(len(hits)),
        ))
    return rows


_CSV_FIELDS = ["image_id", "extracted_date", "grand_total", "db_match_count", "status"]


def write_review_csv(rows: list[ReviewRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({
                "image_id": r.image_id,
                "extracted_date": r.extracted_date or "",
                "grand_total": r.grand_total,
                "db_match_count": r.db_match_count,
                "status": r.status,
            })


def read_review_csv(path: Path) -> list[ReviewRow]:
    rows: list[ReviewRow] = []
    with path.open(newline="", encoding="utf-8") as f:
        for rec in csv.DictReader(f):
            rows.append(ReviewRow(
                image_id=rec["image_id"],
                extracted_date=rec["extracted_date"] or None,
                grand_total=int(rec["grand_total"]),
                db_match_count=int(rec["db_match_count"]),
                status=rec["status"],
            ))
    return rows


def resolve_ground_truth(
    reviewed: list[ReviewRow],
    db: dbmod.InvoiceDB,
) -> dict[str, GroundTruth]:
    """검수된 행 중 일자+총액으로 유일 식별되는 것만 정답지로 채택."""
    out: dict[str, GroundTruth] = {}
    for r in reviewed:
        if r.extracted_date is None:
            hits = db.find_by_grand_total(r.grand_total)
        else:
            hits = db.find_by_date_and_total(r.extracted_date, r.grand_total)
        if len(hits) != 1:
            continue
        inv = hits[0]
        out[r.image_id] = GroundTruth(
            image_id=r.image_id,
            invoice_id=inv.id,
            rows=db.items_for(inv.id),
        )
    return out
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/test_match.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add ocr_poc/match.py tests/test_match.py
git commit -m "feat(sp1): match.py — 일자추출·검수CSV·일자+총액 DB 유일조회"
```

---

## Task 5: detect.py — DetectorAdapter + PPStructureDetector

**Files:**
- Create: `apps/invoice-ocr/ml/ocr_poc/detect.py`
- Test: `apps/invoice-ocr/ml/tests/test_detect.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) DetectedCell(row_index:int, col_index:int, bbox:tuple[float,float,float,float])`
  - `class DetectorAdapter(Protocol): def detect(self, image_path:str) -> list[DetectedCell]`
  - `class FakeDetector` (고정 셀 반환 — 파이프라인 결정론화)
  - `class PPStructureDetector` (Task 1 스파이크로 확정한 PP-Structure 출력 매핑)

- [ ] **Step 1: 실패 테스트 작성 (계약 + Fake)**

`apps/invoice-ocr/ml/tests/test_detect.py`:
```python
from ocr_poc.detect import DetectedCell, FakeDetector


def test_fake_detector_returns_seeded_cells():
    cells = [DetectedCell(0, 2, (10, 10, 20, 20)),
             DetectedCell(0, 3, (20, 10, 30, 20))]
    det = FakeDetector({"inv_x.jpg": cells})
    assert det.detect("/any/path/inv_x.jpg") == cells


def test_fake_detector_unknown_image_returns_empty():
    det = FakeDetector({})
    assert det.detect("/any/inv_none.jpg") == []
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/test_detect.py -v`
Expected: FAIL (`ModuleNotFoundError: ocr_poc.detect`).

- [ ] **Step 3: detect.py 구현**

`apps/invoice-ocr/ml/ocr_poc/detect.py`:
```python
"""표 검출 어댑터. 실모델(PP-Structure)은 어댑터 뒤에 숨겨 파이프라인을
검출기 교체에 무관하게 만든다(후속 SP의 YOLO 등 plug-in).

DetectedCell.bbox 는 원본 이미지 좌표계의 (x1,y1,x2,y2).
row_index/col_index 는 표 격자에서의 0-based 위치.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class DetectedCell:
    row_index: int
    col_index: int
    bbox: tuple[float, float, float, float]


class DetectorAdapter(Protocol):
    def detect(self, image_path: str) -> list[DetectedCell]:
        ...


class FakeDetector:
    """이미지 파일명 → 고정 셀 리스트. 단위/스모크 테스트 결정론화."""

    def __init__(self, by_basename: dict[str, list[DetectedCell]]):
        self._by_basename = by_basename

    def detect(self, image_path: str) -> list[DetectedCell]:
        return list(self._by_basename.get(os.path.basename(image_path), []))


class PPStructureDetector:
    """PaddleOCR PP-Structure 표인식 → DetectedCell 리스트.

    실제 출력 매핑은 Task 1 스파이크로 확정한 구조에 맞춘다. 엔진 로딩은
    지연(첫 detect)해 import·테스트 비용을 낮춘다.
    """

    def __init__(self, lang: str = "korean"):
        self._lang = lang
        self._engine = None

    def _ensure_engine(self):
        if self._engine is None:
            from paddleocr import PPStructure  # noqa: PLC0415
            self._engine = PPStructure(show_log=False, lang=self._lang)
        return self._engine

    def detect(self, image_path: str) -> list[DetectedCell]:
        engine = self._ensure_engine()
        result = engine(image_path)
        cells: list[DetectedCell] = []
        for block in result:
            if block.get("type") != "table":
                continue
            res = block.get("res") or {}
            for grid_cell in self._iter_grid_cells(res):
                cells.append(grid_cell)
        return cells

    @staticmethod
    def _iter_grid_cells(res: dict):
        """PP-Structure table res → DetectedCell. 셀 박스를 y→행, x→열로 격자화.

        스파이크에서 본 cell_bbox 형식(4-좌표 또는 8-좌표)을 (x1,y1,x2,y2)로
        환산한 뒤, y 중심으로 행을, x 중심으로 열을 군집해 인덱스를 매긴다.
        """
        boxes = res.get("cell_bbox") or []
        rects: list[tuple[float, float, float, float]] = []
        for b in boxes:
            flat = list(b)
            if len(flat) == 4:
                rects.append((flat[0], flat[1], flat[2], flat[3]))
            else:
                xs = flat[0::2]
                ys = flat[1::2]
                rects.append((min(xs), min(ys), max(xs), max(ys)))
        row_idx = _cluster_index([(r[1] + r[3]) / 2 for r in rects])
        col_idx = _cluster_index([(r[0] + r[2]) / 2 for r in rects])
        for rect, ri, ci in zip(rects, row_idx, col_idx):
            yield DetectedCell(row_index=ri, col_index=ci, bbox=rect)


def _cluster_index(centers: list[float], gap_ratio: float = 0.5) -> list[int]:
    """1D 중심값들을 정렬·간격 기준으로 군집해 0-based 인덱스 부여."""
    if not centers:
        return []
    order = sorted(range(len(centers)), key=lambda i: centers[i])
    spans = [centers[order[i + 1]] - centers[order[i]] for i in range(len(order) - 1)]
    typical = sorted(spans)[len(spans) // 2] if spans else 0.0
    threshold = max(typical * gap_ratio, 1.0)
    idx = [0] * len(centers)
    cur = 0
    for pos, oi in enumerate(order):
        if pos > 0 and centers[oi] - centers[order[pos - 1]] > threshold:
            cur += 1
        idx[oi] = cur
    return idx
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/test_detect.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: 실모델 스모크 (수동, 실데이터)**

```bash
uv run python -c "from ocr_poc import config, detect; d=detect.PPStructureDetector(); c=d.detect(str(config.images_dir()/'inv_003.jpg')); print('cells', len(c)); print(c[:3])"
```
Expected: 셀이 0개 아님(표 검출됨). 0개면 Task 1 게이트의 괘선 폴백으로 분기. (이 스모크는 자동 테스트에 넣지 않음 — 실모델/실데이터 의존)

- [ ] **Step 6: Commit**

```bash
git add ocr_poc/detect.py tests/test_detect.py
git commit -m "feat(sp1): detect.py — DetectorAdapter + PPStructureDetector(격자화)"
```

---

## Task 6: assemble.py — 셀→열매핑→삼중쌍

**Files:**
- Create: `apps/invoice-ocr/ml/ocr_poc/assemble.py`
- Test: `apps/invoice-ocr/ml/tests/test_assemble.py`

**Interfaces:**
- Consumes: `detect.DetectedCell`.
- Produces:
  - `@dataclass(frozen=True) AssembledRow(row_index:int, quantity:DetectedCell|None, unit_price:DetectedCell|None, amount:DetectedCell|None)`
  - `infer_column_map(header_texts:dict[int,str]) -> dict[str,int]` (열 인덱스→필드. 헤더 텍스트 키워드 매칭)
  - `assemble_rows(cells:list[detect.DetectedCell], column_map:dict[str,int]) -> list[AssembledRow]`

- [ ] **Step 1: 실패 테스트 작성**

`apps/invoice-ocr/ml/tests/test_assemble.py`:
```python
from ocr_poc.detect import DetectedCell
from ocr_poc.assemble import infer_column_map, assemble_rows, AssembledRow


def test_infer_column_map_by_header_keywords():
    headers = {0: "품목", 1: "수량", 2: "단가", 3: "공급가액"}
    assert infer_column_map(headers) == {"quantity": 1, "unit_price": 2, "amount": 3}


def test_infer_column_map_tolerates_금액_synonym():
    headers = {0: "품 목", 2: "수량", 4: "단 가", 5: "금액"}
    assert infer_column_map(headers) == {"quantity": 2, "unit_price": 4, "amount": 5}


def test_assemble_groups_cells_into_rows_by_column_map():
    cmap = {"quantity": 1, "unit_price": 2, "amount": 3}
    cells = [
        DetectedCell(0, 1, (0, 0, 1, 1)), DetectedCell(0, 2, (1, 0, 2, 1)),
        DetectedCell(0, 3, (2, 0, 3, 1)),
        DetectedCell(1, 1, (0, 1, 1, 2)), DetectedCell(1, 3, (2, 1, 3, 2)),  # 단가 누락
    ]
    rows = assemble_rows(cells, cmap)
    assert rows[0] == AssembledRow(0, cells[0], cells[1], cells[2])
    assert rows[1] == AssembledRow(1, cells[3], None, cells[4])


def test_assemble_skips_non_mapped_columns():
    cmap = {"quantity": 1, "unit_price": 2, "amount": 3}
    cells = [DetectedCell(0, 0, (0, 0, 1, 1)),   # 품목 열 → 무시
             DetectedCell(0, 1, (1, 0, 2, 1))]
    rows = assemble_rows(cells, cmap)
    assert rows[0] == AssembledRow(0, cells[1], None, None)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/test_assemble.py -v`
Expected: FAIL (`ModuleNotFoundError: ocr_poc.assemble`).

- [ ] **Step 3: assemble.py 구현**

`apps/invoice-ocr/ml/ocr_poc/assemble.py`:
```python
"""검출 셀을 표 열(수량/단가/공급가)에 매핑해 행별 삼중쌍으로 묶는다.

손라벨을 안 쓰므로 열 식별은 헤더 텍스트 키워드(+ 위치)로 한다. 순수함수."""
from __future__ import annotations

from dataclasses import dataclass

from .detect import DetectedCell

_HEADER_KEYWORDS = {
    "quantity": ("수량", "수 량"),
    "unit_price": ("단가", "단 가"),
    "amount": ("공급가", "공급가액", "금액", "공 급 가"),
}


@dataclass(frozen=True)
class AssembledRow:
    row_index: int
    quantity: DetectedCell | None
    unit_price: DetectedCell | None
    amount: DetectedCell | None


def infer_column_map(header_texts: dict[int, str]) -> dict[str, int]:
    """헤더 텍스트(열 인덱스→문자열) → {field: col_index}. 못 찾은 field 는 제외."""
    norm = {ci: t.replace(" ", "") for ci, t in header_texts.items()}
    out: dict[str, int] = {}
    for field, keywords in _HEADER_KEYWORDS.items():
        kws = tuple(k.replace(" ", "") for k in keywords)
        for ci in sorted(norm):
            if any(k in norm[ci] for k in kws):
                out[field] = ci
                break
    return out


def assemble_rows(
    cells: list[DetectedCell],
    column_map: dict[str, int],
) -> list[AssembledRow]:
    """매핑된 열의 셀만 행 인덱스로 묶어 삼중쌍 생성."""
    col_to_field = {ci: field for field, ci in column_map.items()}
    by_row: dict[int, dict[str, DetectedCell]] = {}
    for cell in cells:
        field = col_to_field.get(cell.col_index)
        if field is None:
            continue
        by_row.setdefault(cell.row_index, {})[field] = cell
    rows: list[AssembledRow] = []
    for ri in sorted(by_row):
        slot = by_row[ri]
        rows.append(AssembledRow(
            row_index=ri,
            quantity=slot.get("quantity"),
            unit_price=slot.get("unit_price"),
            amount=slot.get("amount"),
        ))
    return rows
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/test_assemble.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add ocr_poc/assemble.py tests/test_assemble.py
git commit -m "feat(sp1): assemble.py — 헤더 기반 열매핑·행별 삼중쌍"
```

---

## Task 7: crop.py — 셀박스 → crop · 검증

**Files:**
- Create: `apps/invoice-ocr/ml/ocr_poc/crop.py`
- Test: `apps/invoice-ocr/ml/tests/test_crop.py`

**Interfaces:**
- Produces:
  - `is_valid_bbox(bbox:tuple[float,float,float,float], img_w:int, img_h:int) -> bool`
  - `crop_cell(image:PIL.Image.Image, bbox:tuple[float,float,float,float]) -> PIL.Image.Image|None`

- [ ] **Step 1: 실패 테스트 작성**

`apps/invoice-ocr/ml/tests/test_crop.py`:
```python
from PIL import Image

from ocr_poc.crop import is_valid_bbox, crop_cell


def test_is_valid_bbox_rejects_degenerate_and_oob():
    assert is_valid_bbox((10, 10, 20, 20), 100, 100) is True
    assert is_valid_bbox((20, 10, 10, 20), 100, 100) is False   # x2<x1
    assert is_valid_bbox((10, 10, 10, 20), 100, 100) is False   # 폭 0
    assert is_valid_bbox((-1, 10, 20, 20), 100, 100) is False   # 경계밖
    assert is_valid_bbox((10, 10, 20, 200), 100, 100) is False  # 경계밖


def test_crop_cell_returns_subimage():
    img = Image.new("RGB", (100, 100), (255, 255, 255))
    out = crop_cell(img, (10, 20, 40, 50))
    assert out is not None
    assert out.size == (30, 30)


def test_crop_cell_none_on_invalid():
    img = Image.new("RGB", (100, 100))
    assert crop_cell(img, (40, 20, 10, 50)) is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/test_crop.py -v`
Expected: FAIL (`ModuleNotFoundError: ocr_poc.crop`).

- [ ] **Step 3: crop.py 구현**

`apps/invoice-ocr/ml/ocr_poc/crop.py`:
```python
"""셀 박스 → 원해상도 crop. degenerate/경계밖 박스는 None(스킵)."""
from __future__ import annotations

from PIL import Image


def is_valid_bbox(bbox: tuple[float, float, float, float], img_w: int, img_h: int) -> bool:
    x1, y1, x2, y2 = bbox
    if x2 <= x1 or y2 <= y1:
        return False
    if x1 < 0 or y1 < 0 or x2 > img_w or y2 > img_h:
        return False
    return True


def crop_cell(image: Image.Image, bbox: tuple[float, float, float, float]) -> Image.Image | None:
    if not is_valid_bbox(bbox, image.width, image.height):
        return None
    x1, y1, x2, y2 = bbox
    return image.crop((int(x1), int(y1), int(x2), int(y2)))
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/test_crop.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add ocr_poc/crop.py tests/test_crop.py
git commit -m "feat(sp1): crop.py — 셀박스 crop + degenerate/경계밖 검증"
```

---

## Task 8: recognize.py — RecognizerAdapter + PaddleOCRNumeric

**Files:**
- Create: `apps/invoice-ocr/ml/ocr_poc/recognize.py`
- Test: `apps/invoice-ocr/ml/tests/test_recognize.py`

**Interfaces:**
- Produces:
  - `class RecognizerAdapter(Protocol): def recognize(self, crop) -> str`
  - `class FakeRecognizer` (시드 텍스트)
  - `numeric_postfilter(raw:str) -> str` (숫자 외 문자 제거 — charset 제한 효과)
  - `class PaddleOCRNumeric` (실모델 + post-filter)

- [ ] **Step 1: 실패 테스트 작성 (post-filter + Fake)**

`apps/invoice-ocr/ml/tests/test_recognize.py`:
```python
from ocr_poc.recognize import numeric_postfilter, FakeRecognizer


def test_numeric_postfilter_strips_non_digits():
    assert numeric_postfilter("₩25,000") == "25000"
    assert numeric_postfilter("o3o oo0") == "30"   # 오인식 문자 사이 숫자만(3,0)
    assert numeric_postfilter("없음") == ""
    assert numeric_postfilter("100 000") == "100000"


def test_fake_recognizer_returns_seeded_text():
    rec = FakeRecognizer(["25000", "", "100000"])
    assert rec.recognize(object()) == "25000"
    assert rec.recognize(object()) == ""
    assert rec.recognize(object()) == "100000"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/test_recognize.py -v`
Expected: FAIL (`ModuleNotFoundError: ocr_poc.recognize`).

- [ ] **Step 3: recognize.py 구현**

`apps/invoice-ocr/ml/ocr_poc/recognize.py`:
```python
"""숫자 인식 어댑터. stock PaddleOCR(PP-OCRv5) 디코드 후 숫자 post-filter 로
charset 을 사실상 제한(fine-tune 없이 데이터 0). 후속 SP 의 Qwen/TrOCR 는
같은 RecognizerAdapter 인터페이스로 plug-in.
"""
from __future__ import annotations

import re
from typing import Protocol

_NON_DIGIT = re.compile(r"[^0-9]")


def numeric_postfilter(raw: str) -> str:
    """디코드 문자열에서 숫자만 남긴다(천원곱·빈칸 처리는 normalize 담당)."""
    return _NON_DIGIT.sub("", raw)


class RecognizerAdapter(Protocol):
    def recognize(self, crop) -> str:
        ...


class FakeRecognizer:
    """시드 텍스트를 순서대로 반환. 파이프라인 결정론화."""

    def __init__(self, texts: list[str]):
        self._texts = list(texts)
        self._i = 0

    def recognize(self, crop) -> str:
        if self._i >= len(self._texts):
            return ""
        out = self._texts[self._i]
        self._i += 1
        return out


class PaddleOCRNumeric:
    """PP-OCRv5 인식 + 숫자 post-filter. 엔진 지연 로딩."""

    def __init__(self, lang: str = "korean"):
        self._lang = lang
        self._engine = None

    def _ensure_engine(self):
        if self._engine is None:
            import numpy as np  # noqa: PLC0415
            from paddleocr import PaddleOCR  # noqa: PLC0415
            self._np = np
            self._engine = PaddleOCR(show_log=False, lang=self._lang, use_angle_cls=False)
        return self._engine

    def recognize(self, crop) -> str:
        engine = self._ensure_engine()
        arr = self._np.asarray(crop.convert("RGB"))
        result = engine.ocr(arr, det=False, cls=False)
        raw = self._first_text(result)
        return numeric_postfilter(raw)

    @staticmethod
    def _first_text(result) -> str:
        """PaddleOCR rec-only 출력에서 첫 텍스트. 버전별 형태 방어적 처리."""
        if not result:
            return ""
        first = result[0]
        if isinstance(first, (list, tuple)) and first:
            cand = first[0]
            if isinstance(cand, (list, tuple)) and cand:
                return str(cand[0])
            return str(cand)
        return str(first)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/test_recognize.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: 실모델 스모크 (수동)**

```bash
uv run python -c "from PIL import Image; from ocr_poc.recognize import PaddleOCRNumeric; r=PaddleOCRNumeric(); print(repr(r.recognize(Image.new('RGB',(80,32),(255,255,255)))))"
```
Expected: 크래시 없이 문자열 반환(빈 이미지라 '' 가능). 실 crop 정확도는 Task 13 배치에서 측정.

- [ ] **Step 6: Commit**

```bash
git add ocr_poc/recognize.py tests/test_recognize.py
git commit -m "feat(sp1): recognize.py — RecognizerAdapter + PaddleOCRNumeric(post-filter)"
```

---

## Task 9: normalize.py — 천원곱 · 빈칸=0 · ditto

**Files:**
- Create: `apps/invoice-ocr/ml/ocr_poc/normalize.py`
- Test: `apps/invoice-ocr/ml/tests/test_normalize.py`

**Interfaces:**
- Produces:
  - `THOUSAND_THRESHOLD = 1000`
  - `@dataclass(frozen=True) NormRow(quantity:int|None, unit_price:int|None, amount:int|None, applied:tuple[str,...])`
  - `normalize_value(raw:str, field:str, prev:int|None) -> tuple[int|None, str|None]` (값, 적용규칙명)
  - `normalize_rows(raw_rows:list[dict[str,str]]) -> list[NormRow]`

`field` ∈ {"quantity","unit_price","amount"}. raw 는 recognizer 출력(숫자/빈문자/ditto 마크).

- [ ] **Step 1: 실패 테스트 작성**

`apps/invoice-ocr/ml/tests/test_normalize.py`:
```python
from ocr_poc.normalize import normalize_value, normalize_rows, NormRow


def test_quantity_is_literal_no_thousand_mult():
    assert normalize_value("4", "quantity", None) == (4, None)


def test_unit_price_thousand_mult_below_threshold():
    assert normalize_value("25", "unit_price", None) == (25000, "thousand_mult")
    assert normalize_value("365000", "unit_price", None) == (365000, None)


def test_amount_thousand_mult():
    assert normalize_value("100", "amount", None) == (100000, "thousand_mult")


def test_blank_is_zero():
    assert normalize_value("", "amount", None) == (0, "blank_zero")


def test_ditto_propagates_prev():
    assert normalize_value("〃", "unit_price", 25000) == (25000, "ditto")
    assert normalize_value("″", "unit_price", 30000) == (30000, "ditto")


def test_normalize_rows_propagates_ditto_down_column():
    raw_rows = [
        {"quantity": "4", "unit_price": "25", "amount": "100"},
        {"quantity": "1", "unit_price": "〃", "amount": "25"},
    ]
    rows = normalize_rows(raw_rows)
    assert rows[0] == NormRow(4, 25000, 100000, ("thousand_mult", "thousand_mult"))
    assert rows[1].unit_price == 25000   # 윗행 단가 전파
    assert "ditto" in rows[1].applied
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/test_normalize.py -v`
Expected: FAIL (`ModuleNotFoundError: ocr_poc.normalize`).

- [ ] **Step 3: normalize.py 구현**

`apps/invoice-ocr/ml/ocr_poc/normalize.py`:
```python
"""약식 symbolic 정규화: 천원곱(단가/공급가)·빈칸=0·〃(ditto) 전파. 순수함수.

적용 규칙명을 함께 반환해 리포트가 약식 적용률을 집계할 수 있게 한다(§6)."""
from __future__ import annotations

import re
from dataclasses import dataclass

THOUSAND_THRESHOLD = 1000
_DITTO_MARKS = ("〃", "″", '"', "“", "”")
_THOUSAND_FIELDS = ("unit_price", "amount")


@dataclass(frozen=True)
class NormRow:
    quantity: int | None
    unit_price: int | None
    amount: int | None
    applied: tuple[str, ...]


def normalize_value(raw: str, field: str, prev: int | None) -> tuple[int | None, str | None]:
    """raw(인식 출력) → (정수값, 적용규칙명|None)."""
    text = (raw or "").strip()
    if text in _DITTO_MARKS:
        return prev, "ditto"
    digits = re.sub(r"[^0-9]", "", text)
    if digits == "":
        return 0, "blank_zero"
    value = int(digits)
    if field in _THOUSAND_FIELDS and 0 < value < THOUSAND_THRESHOLD:
        return value * 1000, "thousand_mult"
    return value, None


def normalize_rows(raw_rows: list[dict[str, str]]) -> list[NormRow]:
    """열별로 ditto 전파를 유지하며 행들을 정규화."""
    prev = {"quantity": None, "unit_price": None, "amount": None}
    out: list[NormRow] = []
    for raw in raw_rows:
        applied: list[str] = []
        values: dict[str, int | None] = {}
        for field in ("quantity", "unit_price", "amount"):
            val, rule = normalize_value(raw.get(field, ""), field, prev[field])
            values[field] = val
            if val is not None:
                prev[field] = val
            if rule is not None:
                applied.append(rule)
        out.append(NormRow(values["quantity"], values["unit_price"],
                           values["amount"], tuple(applied)))
    return out
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/test_normalize.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add ocr_poc/normalize.py tests/test_normalize.py
git commit -m "feat(sp1): normalize.py — 천원곱·빈칸=0·ditto 전파 (적용규칙 집계)"
```

---

## Task 10: validate.py — 산술검산 · 역산 복원

**Files:**
- Create: `apps/invoice-ocr/ml/ocr_poc/validate.py`
- Test: `apps/invoice-ocr/ml/tests/test_validate.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) ValidationResult(consistent:bool, kind:str, recovered:tuple[int,int,int]|None, fixed_field:str|None)`
  - `validate_row(qty:int|None, price:int|None, supply:int|None) -> ValidationResult`
  - `kind` ∈ {"ok","single_cell_recoverable","ambiguous","multi_error","incomplete"}

**역산 의미 규칙(고정):** 읽은 **supply(공급가)를 신뢰 앵커**로 삼는다(채점 무게도 공급가 우선, §5). 행이 `qty*price == supply` 를 위반할 때, `quantity` 또는 `unit_price` **한쪽만** 정수로 고쳐 supply 에 맞출 수 있고 그 후보가 **유일**하면 `single_cell_recoverable`, 둘 다 가능하면 `ambiguous`, 둘 다 불가하면 `multi_error`. 셀 결측은 `incomplete`. 복원은 신뢰도 게이트일 뿐 정답 보증이 아니다(§4·§5).

- [ ] **Step 1: 실패 테스트 작성**

`apps/invoice-ocr/ml/tests/test_validate.py`:
```python
from ocr_poc.validate import validate_row, ValidationResult


def test_consistent_row():
    r = validate_row(4, 25000, 100000)
    assert r == ValidationResult(True, "ok", None, None)


def test_single_cell_recoverable_unit_price_wrong():
    # qty=4, supply=10000 을 신뢰 → unit_price 를 2500 으로 고치면 일관(유일 복원)
    r = validate_row(4, 25000, 10000)
    assert r.consistent is False
    assert r.kind == "single_cell_recoverable"
    assert r.recovered == (4, 2500, 10000)
    assert r.fixed_field == "unit_price"


def test_ambiguous_when_both_single_fixes_possible():
    # 2*3=6≠12; quantity→12/3=4, unit_price→12/2=6 둘 다 정수 → 모호(잘못된 복원 방지)
    r = validate_row(2, 3, 12)
    assert r.kind == "ambiguous"
    assert r.recovered is None


def test_multi_error_no_single_fix():
    # 7*13=91≠100; 100/13, 100/7 모두 비정수 → 단일복원 불가
    r = validate_row(7, 13, 100)
    assert r.kind == "multi_error"
    assert r.recovered is None


def test_incomplete_when_cell_missing():
    assert validate_row(None, 25000, 100000).kind == "incomplete"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/test_validate.py -v`
Expected: FAIL (`ModuleNotFoundError: ocr_poc.validate`).

- [ ] **Step 3: validate.py 구현**

`apps/invoice-ocr/ml/ocr_poc/validate.py`:
```python
"""산술검산(수량×단가=공급가)과 단일 셀 역산 복원. 순수함수.

읽은 supply 를 신뢰 앵커로 두고, quantity/unit_price 중 한쪽만 고쳐
supply 에 맞출 수 있는 유일 후보가 있을 때만 복원안을 제안한다. 검산
결과는 신뢰도 게이트이지 정답 보증이 아니다(§4·§5).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ValidationResult:
    consistent: bool
    kind: str
    recovered: tuple[int, int, int] | None
    fixed_field: str | None


def validate_row(qty: int | None, price: int | None, supply: int | None) -> ValidationResult:
    if qty is None or price is None or supply is None:
        return ValidationResult(False, "incomplete", None, None)

    if qty * price == supply:
        return ValidationResult(True, "ok", None, None)

    candidates: list[tuple[str, tuple[int, int, int]]] = []
    # quantity 만 고쳐 supply 에 맞추기
    if price != 0 and supply % price == 0:
        candidates.append(("quantity", (supply // price, price, supply)))
    # unit_price 만 고쳐 supply 에 맞추기
    if qty != 0 and supply % qty == 0:
        candidates.append(("unit_price", (qty, supply // qty, supply)))

    unique = {trip: field for field, trip in candidates}
    if len(unique) == 1:
        trip = next(iter(unique))
        return ValidationResult(False, "single_cell_recoverable", trip, unique[trip])
    if len(unique) >= 2:
        return ValidationResult(False, "ambiguous", None, None)
    return ValidationResult(False, "multi_error", None, None)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/test_validate.py -v`
Expected: PASS (5 tests: ok / single(unit_price) / ambiguous / multi_error / incomplete).

- [ ] **Step 5: Commit**

```bash
git add ocr_poc/validate.py tests/test_validate.py
git commit -m "feat(sp1): validate.py — 산술검산 + 유일 단일셀 역산 복원"
```

---

## Task 11: score.py — 3축 메트릭 · 값매칭 행정렬 · 집계

**Files:**
- Create: `apps/invoice-ocr/ml/ocr_poc/score.py`
- Test: `apps/invoice-ocr/ml/tests/test_score.py`

**Interfaces:**
- Consumes: `db.InvoiceItem`(정답행), 예측행은 `(quantity:int|None, unit_price:int|None, amount:int|None, detected:dict[str,bool])` 형태의 `PredRow`.
- Produces:
  - `@dataclass(frozen=True) PredRow(quantity:int|None, unit_price:int|None, amount:int|None, detected_quantity:bool, detected_unit_price:bool, detected_amount:bool)`
  - `align_rows(preds:list[PredRow], truth:list[db.InvoiceItem]) -> list[tuple[PredRow|None, db.InvoiceItem]]`
  - `@dataclass(frozen=True) InvoiceScore(detect_total:int, detect_hit:int, recog_total:int, recog_correct:int, valgain_correct:int, rows_exact:int, rows_total:int)`
  - `score_invoice(preds, truth) -> InvoiceScore`
  - `aggregate(scores:list[InvoiceScore]) -> dict[str,float]`

채점 무게: (공급가, 금액) 정합 우선 — 행정렬 키는 `amount`(=supply). 검출 리콜은 정답 셀 중 `detected_*` True 비율, 인식 정확도는 검출된 셀 한정 정답일치율, 검산게인은 검산복원 반영 후 정답일치율.

- [ ] **Step 1: 실패 테스트 작성**

`apps/invoice-ocr/ml/tests/test_score.py`:
```python
from ocr_poc.db import InvoiceItem
from ocr_poc.score import PredRow, align_rows, score_invoice, aggregate


def _item(order, qty, price, supply):
    return InvoiceItem(invoice_id=1, item_order=order, name="x", quantity=qty,
                       unit_price=price, supply=supply, vat=supply // 10,
                       total=supply + supply // 10)


def test_align_rows_by_amount_value():
    truth = [_item(1, 4, 25000, 100000), _item(2, 1, 30000, 30000)]
    preds = [PredRow(1, 30000, 30000, True, True, True),     # 30000 → truth[1]
             PredRow(4, 25000, 100000, True, True, True)]     # 100000 → truth[0]
    aligned = align_rows(preds, truth)
    assert aligned[0][0].amount == 100000 and aligned[0][1].item_order == 1
    assert aligned[1][0].amount == 30000 and aligned[1][1].item_order == 2


def test_score_invoice_counts_three_axes():
    truth = [_item(1, 4, 25000, 100000)]
    preds = [PredRow(4, 25000, 100000, True, True, True)]   # 전부 검출·정답
    s = score_invoice(preds, truth)
    assert s.detect_total == 3 and s.detect_hit == 3
    assert s.recog_total == 3 and s.recog_correct == 3
    assert s.rows_exact == 1 and s.rows_total == 1


def test_score_invoice_undetected_cell_excluded_from_recog():
    truth = [_item(1, 4, 25000, 100000)]
    # unit_price 미검출(detected False) → 검출 리콜 2/3, 인식 모집단 2
    preds = [PredRow(4, None, 100000, True, False, True)]
    s = score_invoice(preds, truth)
    assert s.detect_total == 3 and s.detect_hit == 2
    assert s.recog_total == 2 and s.recog_correct == 2
    assert s.rows_exact == 0   # 행 완전일치 아님(셀 누락)


def test_score_invoice_missing_truth_row_counts_detect_miss():
    truth = [_item(1, 4, 25000, 100000), _item(2, 1, 30000, 30000)]
    preds = [PredRow(4, 25000, 100000, True, True, True)]   # 둘째 행 자체 미검출
    s = score_invoice(preds, truth)
    assert s.detect_total == 6 and s.detect_hit == 3       # 6셀 중 3셀만 검출
    assert s.rows_total == 2 and s.rows_exact == 1


def test_aggregate_micro_ratios():
    from ocr_poc.score import InvoiceScore
    scores = [InvoiceScore(3, 3, 3, 3, 3, 1, 1), InvoiceScore(3, 2, 2, 1, 2, 0, 1)]
    agg = aggregate(scores)
    assert agg["detection_recall"] == 5 / 6
    assert agg["recognition_accuracy"] == 4 / 5
    assert agg["row_exact_rate"] == 1 / 2
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/test_score.py -v`
Expected: FAIL (`ModuleNotFoundError: ocr_poc.score`).

- [ ] **Step 3: score.py 구현**

`apps/invoice-ocr/ml/ocr_poc/score.py`:
```python
"""DB/라벨 정답 대비 3축 채점: 검출 리콜 / 인식 정확도(검출 한정) / 검산 게인.

행정렬은 손라벨 순서를 신뢰하지 않고 amount(=supply) 값으로 매칭한다(§5).
모두 순수함수.
"""
from __future__ import annotations

from dataclasses import dataclass

from .db import InvoiceItem

_FIELDS = ("quantity", "unit_price", "amount")


@dataclass(frozen=True)
class PredRow:
    quantity: int | None
    unit_price: int | None
    amount: int | None
    detected_quantity: bool
    detected_unit_price: bool
    detected_amount: bool


@dataclass(frozen=True)
class InvoiceScore:
    detect_total: int
    detect_hit: int
    recog_total: int
    recog_correct: int
    valgain_correct: int
    rows_exact: int
    rows_total: int


def align_rows(
    preds: list[PredRow],
    truth: list[InvoiceItem],
) -> list[tuple[PredRow | None, InvoiceItem]]:
    """정답행마다 amount 값이 같은 예측행을 1:1 매칭. 없으면 None."""
    remaining = list(preds)
    aligned: list[tuple[PredRow | None, InvoiceItem]] = []
    for t in truth:
        match = None
        for p in remaining:
            if p.amount == t.supply:
                match = p
                break
        if match is not None:
            remaining.remove(match)
        aligned.append((match, t))
    return aligned


def _pred_field(p: PredRow, field: str) -> int | None:
    return getattr(p, field)


def _pred_detected(p: PredRow, field: str) -> bool:
    return getattr(p, f"detected_{field}")


def _truth_field(t: InvoiceItem, field: str) -> int:
    return t.quantity if field == "quantity" else (
        t.unit_price if field == "unit_price" else t.supply)


def score_invoice(preds: list[PredRow], truth: list[InvoiceItem]) -> InvoiceScore:
    aligned = align_rows(preds, truth)
    detect_total = detect_hit = 0
    recog_total = recog_correct = valgain_correct = 0
    rows_exact = 0
    for pred, t in aligned:
        row_correct = True
        for field in _FIELDS:
            detect_total += 1
            detected = pred is not None and _pred_detected(pred, field)
            if not detected:
                detect_hit += 0
                row_correct = False
                continue
            detect_hit += 1
            recog_total += 1
            correct = _pred_field(pred, field) == _truth_field(t, field)
            recog_correct += int(correct)
            valgain_correct += int(correct)
            if not correct:
                row_correct = False
        rows_exact += int(row_correct)
    return InvoiceScore(
        detect_total=detect_total, detect_hit=detect_hit,
        recog_total=recog_total, recog_correct=recog_correct,
        valgain_correct=valgain_correct,
        rows_exact=rows_exact, rows_total=len(truth),
    )


def _ratio(num: int, den: int) -> float:
    return num / den if den else 0.0


def aggregate(scores: list[InvoiceScore]) -> dict[str, float]:
    d_tot = sum(s.detect_total for s in scores)
    d_hit = sum(s.detect_hit for s in scores)
    r_tot = sum(s.recog_total for s in scores)
    r_cor = sum(s.recog_correct for s in scores)
    v_cor = sum(s.valgain_correct for s in scores)
    rows_tot = sum(s.rows_total for s in scores)
    rows_exact = sum(s.rows_exact for s in scores)
    return {
        "detection_recall": _ratio(d_hit, d_tot),
        "recognition_accuracy": _ratio(r_cor, r_tot),
        "validation_gain_accuracy": _ratio(v_cor, r_tot),
        "row_exact_rate": _ratio(rows_exact, rows_tot),
    }
```

> 참고: 본 task의 `valgain_correct`는 검산 미반영 시 `recog_correct`와 동일하다. Task 13 글루에서 `validate`의 단일셀 복원을 예측값에 반영한 뒤 `score_invoice`를 한 번 더 호출해 검산 게인을 별도 산출한다(같은 함수, 다른 입력). `score.py`는 입력에 충실한 순수함수로 유지한다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/test_score.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add ocr_poc/score.py tests/test_score.py
git commit -m "feat(sp1): score.py — 검출리콜/인식정확도/검산게인 3축 + 값매칭 행정렬"
```

---

## Task 12: report.py — md/json 리포트 + 오답 갤러리

**Files:**
- Create: `apps/invoice-ocr/ml/ocr_poc/report.py`
- Test: `apps/invoice-ocr/ml/tests/test_report.py`

**Interfaces:**
- Consumes: `score.aggregate` 출력(dict), 실패목록, 약식 적용률.
- Produces:
  - `@dataclass(frozen=True) ReportData(metrics:dict[str,float], per_image:list[dict], failures:list[dict], rule_counts:dict[str,int])`
  - `render_markdown(data:ReportData) -> str`
  - `render_json(data:ReportData) -> str`
  - `write_report(data:ReportData, out_dir:Path) -> None`

- [ ] **Step 1: 실패 테스트 작성**

`apps/invoice-ocr/ml/tests/test_report.py`:
```python
import json
from pathlib import Path

from ocr_poc.report import ReportData, render_markdown, render_json, write_report


def _data():
    return ReportData(
        metrics={"detection_recall": 0.83, "recognition_accuracy": 0.8,
                 "validation_gain_accuracy": 0.9, "row_exact_rate": 0.5},
        per_image=[{"image_id": "inv_003", "rows": 10, "rows_exact": 6}],
        failures=[{"image_id": "inv_015", "reason": "no_match"}],
        rule_counts={"thousand_mult": 12, "blank_zero": 3, "ditto": 1},
    )


def test_render_markdown_has_three_axis_and_failures():
    md = render_markdown(_data())
    assert "검출 리콜" in md and "0.83" in md
    assert "인식 정확도" in md
    assert "검산" in md
    assert "inv_015" in md          # 실패목록 노출
    assert "thousand_mult" in md    # 약식 적용률


def test_render_json_roundtrips():
    payload = json.loads(render_json(_data()))
    assert payload["metrics"]["detection_recall"] == 0.83
    assert payload["rule_counts"]["thousand_mult"] == 12


def test_write_report_creates_files(tmp_path: Path):
    write_report(_data(), tmp_path)
    assert (tmp_path / "sp1-report.md").is_file()
    assert (tmp_path / "sp1-report.json").is_file()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/test_report.py -v`
Expected: FAIL (`ModuleNotFoundError: ocr_poc.report`).

- [ ] **Step 3: report.py 구현**

`apps/invoice-ocr/ml/ocr_poc/report.py`:
```python
"""측정 리포트(md+json) 산출. 오답 갤러리/검출 시각화 저장은 별도 헬퍼."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_METRIC_LABELS = {
    "detection_recall": "검출 리콜",
    "recognition_accuracy": "인식 정확도(검출 한정)",
    "validation_gain_accuracy": "검산 후 정확도(게인)",
    "row_exact_rate": "행 완전일치율",
}


@dataclass(frozen=True)
class ReportData:
    metrics: dict[str, float]
    per_image: list[dict]
    failures: list[dict]
    rule_counts: dict[str, int]


def render_markdown(data: ReportData) -> str:
    lines = ["# SP1 측정 리포트", "", "## 3축 메트릭", "", "| 지표 | 값 |", "| --- | --- |"]
    for key, label in _METRIC_LABELS.items():
        if key in data.metrics:
            lines.append(f"| {label} | {data.metrics[key]:.2f} |")
    lines += ["", "## 약식 규칙 적용", "", "| 규칙 | 횟수 |", "| --- | --- |"]
    for rule, cnt in sorted(data.rule_counts.items()):
        lines.append(f"| {rule} | {cnt} |")
    lines += ["", "## 명세서별", "", "| image | rows | rows_exact |", "| --- | --- | --- |"]
    for r in data.per_image:
        lines.append(f"| {r['image_id']} | {r.get('rows', 0)} | {r.get('rows_exact', 0)} |")
    lines += ["", "## 실패/제외 목록", ""]
    if data.failures:
        for f in data.failures:
            lines.append(f"- {f['image_id']}: {f.get('reason', '')}")
    else:
        lines.append("- 없음")
    return "\n".join(lines) + "\n"


def render_json(data: ReportData) -> str:
    return json.dumps({
        "metrics": data.metrics,
        "per_image": data.per_image,
        "failures": data.failures,
        "rule_counts": data.rule_counts,
    }, ensure_ascii=False, indent=2)


def write_report(data: ReportData, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sp1-report.md").write_text(render_markdown(data), encoding="utf-8")
    (out_dir / "sp1-report.json").write_text(render_json(data), encoding="utf-8")
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/test_report.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add ocr_poc/report.py tests/test_report.py
git commit -m "feat(sp1): report.py — 3축 md/json 리포트 + 약식 적용·실패목록"
```

---

## Task 13: __main__.py — 배치 오케스트레이션 CLI + 파이프라인 스모크

**Files:**
- Create: `apps/invoice-ocr/ml/ocr_poc/__main__.py`
- Test: `apps/invoice-ocr/ml/tests/test_pipeline_smoke.py`

**Interfaces:**
- Consumes: 전 모듈. 두 서브커맨드: `match-extract`(references OCR→reviewed_dates.csv), `run`(검수 후 38장 배치→report/).
- Produces:
  - `run_pipeline(samples, ground_truth, detector, recognizer, image_opener) -> tuple[list[score.InvoiceScore], report.ReportData]` (의존성 주입으로 테스트 가능)

- [ ] **Step 1: 실패 스모크 테스트 작성 (모킹 어댑터 end-to-end)**

`apps/invoice-ocr/ml/tests/test_pipeline_smoke.py`:
```python
from PIL import Image

from ocr_poc.data import Sample, LabelRow
from ocr_poc.db import InvoiceItem
from ocr_poc.detect import DetectedCell, FakeDetector
from ocr_poc.recognize import FakeRecognizer
from ocr_poc.match import GroundTruth
from ocr_poc.__main__ import run_pipeline


def test_run_pipeline_end_to_end_with_fakes(tmp_path):
    # 1행 명세서: 수량4 단가25000 공급가100000, 표 헤더 + 1 데이터행
    sample = Sample(image_id="inv_x", image_path=tmp_path / "inv_x.jpg",
                    label_rows=(LabelRow(1, "부동액", "4", "25000", "100000"),))
    Image.new("RGB", (200, 100), (255, 255, 255)).save(sample.image_path)

    # 검출: 헤더행(row0) 3열 + 데이터행(row1) 3열
    cells = [
        DetectedCell(0, 1, (10, 0, 20, 10)), DetectedCell(0, 2, (20, 0, 30, 10)),
        DetectedCell(0, 3, (30, 0, 40, 10)),
        DetectedCell(1, 1, (10, 20, 20, 30)), DetectedCell(1, 2, (20, 20, 30, 30)),
        DetectedCell(1, 3, (30, 20, 40, 30)),
    ]
    detector = FakeDetector({"inv_x.jpg": cells})
    # 인식 순서: 헤더(수량/단가/공급가) → 데이터(4 / 25 / 100)
    recognizer = FakeRecognizer(["수량", "단가", "공급가", "4", "25", "100"])

    gt = {"inv_x": GroundTruth("inv_x", 1, (
        InvoiceItem(1, 1, "부동액", 4, 25000, 100000, 10000, 110000),))}

    scores, report_data = run_pipeline([sample], gt, detector, recognizer, Image.open)

    assert len(scores) == 1
    # 천원곱으로 25→25000, 100→100000 정규화되어 전부 정답
    assert report_data.metrics["detection_recall"] == 1.0
    assert report_data.metrics["recognition_accuracy"] == 1.0
    assert report_data.metrics["row_exact_rate"] == 1.0
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/test_pipeline_smoke.py -v`
Expected: FAIL (`ModuleNotFoundError` 또는 `run_pipeline` 미정의).

- [ ] **Step 3: __main__.py 구현**

`apps/invoice-ocr/ml/ocr_poc/__main__.py`:
```python
"""38장 배치 오케스트레이션 CLI.

서브커맨드:
  match-extract : references OCR → reviewed_dates.csv (검수 전 단계)
  run           : 검수된 CSV + DB → 38장 배치 추론 → report/

run_pipeline 는 어댑터/이미지오프너를 주입받는 순수 오케스트레이션이라
모킹으로 end-to-end 스모크가 가능하다.
"""
from __future__ import annotations

import sys
from pathlib import Path

from . import config, report, score
from .assemble import assemble_rows, infer_column_map
from .crop import crop_cell
from .data import Sample, load_samples
from .detect import DetectorAdapter
from .match import GroundTruth
from .normalize import normalize_rows
from .recognize import RecognizerAdapter

_RESULTS = Path("results")
_REPORT = Path("report")


def _recognize_cell(image, cell, recognizer: RecognizerAdapter) -> str:
    crop = crop_cell(image, cell.bbox)
    if crop is None:
        return ""
    return recognizer.recognize(crop)


def run_pipeline(
    samples: list[Sample],
    ground_truth: dict[str, GroundTruth],
    detector: DetectorAdapter,
    recognizer: RecognizerAdapter,
    image_opener,
) -> tuple[list[score.InvoiceScore], report.ReportData]:
    invoice_scores: list[score.InvoiceScore] = []
    per_image: list[dict] = []
    failures: list[dict] = []
    rule_counts: dict[str, int] = {}

    for sample in samples:
        gt = ground_truth.get(sample.image_id)
        if gt is None:
            failures.append({"image_id": sample.image_id, "reason": "no_ground_truth"})
            continue
        try:
            image = image_opener(sample.image_path)
            cells = detector.detect(str(sample.image_path))
            if not cells:
                failures.append({"image_id": sample.image_id, "reason": "no_table_detected"})
                continue

            header_row = min(c.row_index for c in cells)
            header_texts = {
                c.col_index: _recognize_cell(image, c, recognizer)
                for c in cells if c.row_index == header_row
            }
            column_map = infer_column_map(header_texts)
            if "amount" not in column_map:
                failures.append({"image_id": sample.image_id, "reason": "column_map_failed"})
                continue

            data_cells = [c for c in cells if c.row_index != header_row]
            arows = assemble_rows(data_cells, column_map)

            raw_rows: list[dict[str, str]] = []
            detected_flags: list[dict[str, bool]] = []
            for ar in arows:
                raw = {}
                det = {}
                for field, cell in (("quantity", ar.quantity),
                                    ("unit_price", ar.unit_price),
                                    ("amount", ar.amount)):
                    if cell is None:
                        raw[field] = ""
                        det[field] = False
                    else:
                        raw[field] = _recognize_cell(image, cell, recognizer)
                        det[field] = True
                raw_rows.append(raw)
                detected_flags.append(det)

            norm = normalize_rows(raw_rows)
            for nr in norm:
                for rule in nr.applied:
                    rule_counts[rule] = rule_counts.get(rule, 0) + 1

            preds = [
                score.PredRow(
                    quantity=n.quantity, unit_price=n.unit_price, amount=n.amount,
                    detected_quantity=d["quantity"], detected_unit_price=d["unit_price"],
                    detected_amount=d["amount"],
                )
                for n, d in zip(norm, detected_flags)
            ]
            s = score.score_invoice(preds, list(gt.rows))
            invoice_scores.append(s)
            per_image.append({"image_id": sample.image_id,
                              "rows": s.rows_total, "rows_exact": s.rows_exact})
        except Exception as exc:   # 개별 이미지 실패 격리(§6)
            failures.append({"image_id": sample.image_id, "reason": f"error: {exc}"})

    metrics = score.aggregate(invoice_scores)
    data = report.ReportData(metrics=metrics, per_image=per_image,
                             failures=failures, rule_counts=rule_counts)
    return invoice_scores, data


def _cmd_run() -> None:
    from .db import parse_backup
    from .match import read_review_csv, resolve_ground_truth
    from .detect import PPStructureDetector
    from .recognize import PaddleOCRNumeric
    from PIL import Image

    db = parse_backup(config.db_backup_path().read_text(encoding="utf-8"))
    reviewed = read_review_csv(_RESULTS / "reviewed_dates.csv")
    gt = resolve_ground_truth(reviewed, db)
    samples = load_samples(config.images_dir(), config.labels_dir())
    _, data = run_pipeline(samples, gt, PPStructureDetector(),
                           PaddleOCRNumeric(), Image.open)
    report.write_report(data, _REPORT)
    print(f"[run] report → {_REPORT/'sp1-report.md'}  (정답지 {len(gt)}/{len(samples)})")


def _cmd_match_extract() -> None:
    """references OCR → grand_total(라벨 합) → reviewed_dates.csv. (검수 전)"""
    from .db import parse_backup
    from .match import build_review_rows, grand_total_from_labels, write_review_csv
    from .recognize import PaddleOCRNumeric
    from .detect import PPStructureDetector  # references 도 표라 PP-Structure 텍스트 활용 가능
    from PIL import Image

    db = parse_backup(config.db_backup_path().read_text(encoding="utf-8"))
    samples = load_samples(config.images_dir(), config.labels_dir())
    recognizer = PaddleOCRNumeric()
    detector = PPStructureDetector()
    per_image = []
    for sample in samples:
        ref_path = config.references_dir() / f"{sample.image_id}.jpg"
        texts: list[str] = []
        if ref_path.is_file():
            image = Image.open(ref_path)
            for cell in detector.detect(str(ref_path)):
                crop = crop_cell(image, cell.bbox)
                if crop is not None:
                    texts.append(recognizer.recognize(crop))
        grand_total = grand_total_from_labels(sample.label_rows)
        per_image.append((sample.image_id, texts, grand_total))
    rows = build_review_rows(per_image, db)
    write_review_csv(rows, _RESULTS / "reviewed_dates.csv")
    print(f"[match-extract] reviewed_dates.csv → {_RESULTS}  ({len(rows)}행) — 검수 후 run")


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "match-extract":
        _cmd_match_extract()
    elif cmd == "run":
        _cmd_run()
    else:
        print("usage: python -m ocr_poc [match-extract|run]")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

> 주의: `match-extract`의 references 날짜 OCR은 `PaddleOCRNumeric`(숫자 post-filter)로는 날짜 문자를 못 읽는다. references용으로는 raw 텍스트 인식이 필요하므로 **Step 3b**에서 `recognize.py`에 `PaddleOCRText`(post-filter 미적용, 같은 엔진 raw 디코드)를 추가하고 `_cmd_match_extract`가 그것을 쓰도록 교체한다. 단위테스트는 불필요(실모델 글루) — `run_pipeline` 스모크가 핵심 경로를 덮는다.

- [ ] **Step 3b: recognize.py에 PaddleOCRText 추가 (references 날짜용)**

`recognize.py` 말미에 추가:
```python
class PaddleOCRText(PaddleOCRNumeric):
    """post-filter 없이 raw 텍스트 반환(references 날짜 추출용)."""

    def recognize(self, crop) -> str:
        engine = self._ensure_engine()
        arr = self._np.asarray(crop.convert("RGB"))
        result = engine.ocr(arr, det=False, cls=False)
        return self._first_text(result)
```
그리고 `__main__._cmd_match_extract`의 `recognizer = PaddleOCRNumeric()`를 `PaddleOCRText()`로 교체(import도 함께).

- [ ] **Step 4: 스모크 테스트 통과 확인**

Run: `uv run pytest tests/test_pipeline_smoke.py -v`
Expected: PASS (1 test).

- [ ] **Step 5: 전체 테스트 + 실배치 수동 검증**

```bash
uv run pytest -q
```
Expected: 전 테스트 PASS.

실데이터 수동 검증(성공 기준, §7):
```bash
uv run python -m ocr_poc match-extract     # results/reviewed_dates.csv 38행
# (사람이 reviewed_dates.csv 의 일자·매칭상태 검수·수정)
uv run python -m ocr_poc run               # report/sp1-report.md 생성
```
Expected: 리포트에 3축 메트릭·약식 적용률·실패목록 채워짐. 라벨↔DB 교차검증이 0단계 감사(공급가 ~100%)를 재확인.

- [ ] **Step 6: Commit**

```bash
git add ocr_poc/__main__.py ocr_poc/recognize.py tests/test_pipeline_smoke.py
git commit -m "feat(sp1): __main__.py — 배치 CLI(match-extract/run) + 파이프라인 스모크"
```

---

## 자체 점검 메모 (작성자용)

- **spec 커버리지**: §2 데이터→data.py(T3)·db.py(T2); §3 매칭→match.py(T4); §4 검출→detect.py(T5)·assemble.py(T6)·crop.py(T7)·recognize.py(T8); §5 채점→score.py(T11); §6 에러처리→crop 검증(T7)·개별실패격리(T13)·첫 스파이크(T1); §7 테스트→각 task TDD + 스모크(T13); §8 디렉터리→T1 스캐폴드. 전 절 대응 task 존재.
- **타입 일관성**: `DetectedCell`(detect)→assemble/crop/__main__; `NormRow`(normalize)→__main__; `PredRow`/`InvoiceScore`(score)→__main__; `GroundTruth`(match)→__main__. 시그니처 교차 확인됨.
- **검산 게인 별도 산출**: score.py는 입력충실 순수함수, 검산 반영은 __main__ 글루에서 별도 입력으로 재호출(T11 주석). SP1 1차 리포트는 raw 기준이 기본이고, 검산 게인 통합은 run에서 second-pass로 확장 가능(YAGNI: 1차는 raw 3축 우선).
- **남은 알려진 단순화**: (a) references 날짜 OCR 정확도는 검수 게이트가 흡수; (b) 열매핑 헤더 인식 실패는 해당 장 제외·집계; (c) 검산 second-pass 통합은 1차 리포트 후 필요 시.
