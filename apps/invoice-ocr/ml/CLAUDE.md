# invoice-ocr / ml

수기(손글씨) 거래명세서 OCR. 오프라인 배치 CLI + 인식기 스파이크. 외부 API 0, 전 과정 로컬.

## 두 트랙

| 트랙 | 위치 | 상태 | 내용 |
|---|---|---|---|
| **SP1 본 파이프라인** | `ocr_poc/` | git-tracked · 테스트됨 · production | 숫자 셀 OCR 수직 슬라이스. 어댑터 뒤에 실모델(PaddleOCR 3.x) |
| **SP2 스파이크** | `report/sp2_spike/` | **gitignore · 로컬 전용 · 실험** | 손글씨 VLM 인식(Qwen3-VL) + 작성자-특화 품목 인식(few-shot). 현재 브랜치 작업 |

- SP2 결과·방향 spec: `docs/superpowers/specs/2026-06-25-sp2-handwriting-recognizer-findings.md` (레포 내 단일 정본).
- **SP2 코드는 본 파이프라인이 아니다.** 스파이크 스크립트(`report/sp2_spike/**`)는 gitignore된 실험물이라 `ocr_poc/`의 production 규약(테스트·불변성)을 따르지 않는다. SP2 산출을 본선에 올릴 땐 `RecognizerAdapter` 뒤로 통합한다(spec §8-8).

## 디렉터리 지도

```
ocr_poc/        SP1 본 파이프라인 (git-tracked, 아래 "아키텍처" 참조)
tests/          pytest. test_*.py 12개. ocr_poc/* 1:1 대응
tools/          spike_ppstructure.py — 환경/검출 스파이크 (paddle 필요)
report/         리포트 산출물 + report/sp2_spike/ (SP2 실험)  ← gitignore
results/        reviewed_dates.csv 등 중간 산출  ← gitignore
review/         검수 HTML/몽타주  ← gitignore
data/           이관 사진·DB 백업  ← gitignore
poc/ .venv/     ⚠️ venv (소스 아님, 편집 금지)  ← gitignore
```

git-tracked는 `ocr_poc/ tests/ tools/ pyproject.toml README.md .env.example` 뿐. 나머지는 전부 로컬·gitignore.

## 실행

```bash
uv sync                    # 코어(경량): pillow + pytest 만
uv sync --extra ml         # paddle 계열 (실모델 실행 시)
uv run pytest              # 테스트 (실데이터 비의존, 합성만)
cp .env.example .env       # 데이터/DB 경로 주입

uv run python -m ocr_poc match-extract   # references OCR → results/reviewed_dates.csv
# (사람이 reviewed_dates.csv 검수)
uv run python -m ocr_poc run             # 검수CSV + DB → 배치 추론 → report/
```

## 아키텍처 (`ocr_poc/`)

**파이프라인 흐름** (`__main__.py:run_pipeline` — 어댑터/이미지오프너 주입받는 순수 오케스트레이션, 모킹 end-to-end 스모크 가능):

```
load_samples → detect(셀) → 헤더/위치 column_map → assemble_rows(삼중쌍)
            → recognize(셀별) → normalize → score   [개별 이미지 실패는 격리]
```

**어댑터 패턴 (핵심).** 모델은 Protocol 뒤에 숨겨 파이프라인을 모델 교체에 무관하게 둔다. SP2의 Qwen/TrOCR도 같은 인터페이스로 plug-in.
- `DetectorAdapter`: `FakeDetector`(테스트) / `TextDetCellDetector`(실모델). `detect.py`
- `RecognizerAdapter`: `FakeRecognizer`(테스트) / `PaddleOCRNumeric`·`ReferenceOCR`(실모델). `recognize.py`
- 실모델 어댑터는 **엔진 지연 로딩** — import·init이 `recognize()`/`detect()` 첫 호출 시점에 일어나 테스트가 paddle 없이 돈다.

**정답지(GT) = references→DB 매칭** (`match.py`, `db.py`). 손라벨의 행순서·geometry는 신뢰하지 않는다.
- references(인쇄 정본) OCR → 발행일 + 공급가합 후보 → DB `find_by_date_and_total_supply` 유일조회.
- 행정렬도 라벨 순서가 아니라 `amount`(=supply) **값**으로 매칭(`score.align_rows`).

**순수함수 계층** (모델·IO 무의존, 단위테스트 집중): `normalize.py`(천원곱·빈칸0·〃전파) · `validate.py`(산술검산·역산복원) · `score.py`(3축 채점) · `assemble.py`(열매핑).

## 컨벤션 / 함정 (이 디렉터리 특수)

- **코어는 paddle-free 경량.** `dependencies`는 pillow 뿐. ML 의존은 `[ml]` extra, VLM은 별도 venv. 코어에 무거운 의존을 추가하지 말 것.
- **불변성·순수함수.** 모든 DTO는 `@dataclass(frozen=True)`. normalize/validate/score/assemble은 부수효과 0. 이 규약을 깨지 말 것.
- **데이터·DB·산출물은 전부 레포 밖/gitignore.** 경로는 `.env`(`SJMJ_DATA_DIR`, `SJMJ_DB_BACKUP`)로만 주입. 코드에 절대경로 하드코딩 금지. `config.py`가 경계에서 검증(미설정 시 RuntimeError).
- **PaddleOCR 3.x API.** 2.x의 `PaddleOCR(...).ocr(det=False)`는 제거됨 → 컴포넌트(`TextRecognition`/`TextDetection`)를 직접 호출. crop은 PIL.RGB → BGR ndarray로 변환해 넘긴다.
- **EXIF 정위치 로드 필수.** cv2.imread는 EXIF를 무시해 양식이 90° 누움. `PIL.ImageOps.exif_transpose` 사용.
- **GT 매칭키 = `total_supply`(공급가합, VAT 제외)**, `grand_total`(VAT 포함) 아님. 라벨 amount 합 == DB total_supply. 매칭키를 grand_total로 바꾸면 조회 0건.
- **약식 분해(grouping).** 손글씨에서 품목칸이 빈 행의 금액은 **바로 위 품목에 합산**된다(DB는 합산값, 손글씨는 세부). set 채점은 이를 못 봐 과소채점하므로 수치를 단정 인용하지 말 것(spec §3·§6·§8-A).
- **`poc/`는 venv다(소스 아님).** 이름이 헷갈리지만 `report/sp2_spike/`의 PoC와 무관한 가상환경 디렉터리.

## 테스트

`uv run pytest`. `conftest.py`는 **합성 데이터만**(실 이미지/DB 비의존). Fake 어댑터로 결정론 확보. 새 production 코드는 `tests/test_*.py`에 1:1 단위테스트를 동반한다.
