# SP1 — OCR PoC 숫자 수직 슬라이스 설계 (검출→인식→산술검산)

- 작성일: 2026-06-25
- 상위: `sjmj-ai` / 첫 모듈 invoice-ocr / SP0 스캐폴드 위에 얹음
- 성격: **SP1 첫 컷 구현 설계 문서.** 다음 단계는 writing-plans로 TDD 구현 plan 작성.
- 근거 문서: [`2026-06-24-invoice-handwriting-ocr-poc-design.md`](./2026-06-24-invoice-handwriting-ocr-poc-design.md)(설계), [`2026-06-24-invoice-htr-tech-review.md`](./2026-06-24-invoice-htr-tech-review.md)(기술검토), [`2026-06-24-macmini-migration-overview.md`](./2026-06-24-macmini-migration-overview.md)(SP 경계), [`2026-06-24-sjmj-ai-sp0-scaffold-design.md`](./2026-06-24-sjmj-ai-sp0-scaffold-design.md)(스캐폴드)

> 이 문서는 sjmj-ai에서 직접 작성된 SP1 정본이다(복사본 아님). SJMJ-Web으로 미러링할지는 검토 게이트에서 결정.

---

## 0. 확정된 전제 (이 브레인스토밍의 결정)

| 결정 | 확정 | 근거 |
|------|------|------|
| **SP1 첫 범위** | **최소 수직 슬라이스** — 1단계만, **숫자 셀만** | 흘림체 품목명 인식·검출기(YOLO)·백엔드 비교(2단계)는 후속 SP. "측정 하니스가 38장 끝까지 한 번 도는 것"이 목표 |
| **실행 위치** | **개발 맥북 오프라인 배치** | 데이터(OneDrive)·DB 백업이 여기 있음. 38장 추론은 가벼움. macmini(M5 Max)는 VLM·학습 들어올 때(후속 SP) 투입 |
| **패키징** | `apps/invoice-ocr/ml` **독립 uv 프로젝트** | PaddleOCR 등 무거운 ML 의존을 lean FastAPI backend와 분리. SP1은 backend에 배선하지 않는 순수 오프라인 CLI |
| **숫자 인식기** | **Stock PaddleOCR(PP-OCRv5) + 숫자 post-filter**, 교체가능 어댑터 뒤 | 사전학습 CTC는 dictionary 교체만으론 진짜 charset 제한 불가(fine-tune 필요) → 최소 슬라이스에선 디코드 후 숫자 post-filter로 같은 효과·데이터 0. 후속 SP의 Qwen/TrOCR는 같은 인터페이스로 plug-in |
| **정답지** | **운영 DB 레코드** (references 인쇄일자 추출 → 사용자 검수 → DB 조회). 라벨 JSON = bbox·행정렬·보조 | "운영DB가 정답지 본체, 라벨은 보조"(원설계 §6). references 전체 OCR을 신뢰하지 않고 **쉬운 일자만 뽑아 DB로 나머지를 권위있게 가져옴** |

---

## 1. 목적

수기 거래명세서 사진의 **숫자 셀(수량·단가·공급가)**을 on-prem 인식기로 읽고 **산술검산**까지 통과시켜,
입력보조 시 사람 수정 부담을 얼마나 줄일 수 있는지의 **첫 측정치**를 확보한다.

- 검출(bbox)은 라벨 oracle을 써서 배제 → **검출 노이즈와 인식 품질을 분리 측정**(원설계 1단계 의도).
- 흘림체 품목명 인식은 이 슬라이스 **범위 밖**(후속 SP). 따라서 채점 대상은 숫자 필드.
- **성공 기준선은 미리 박지 않는다**(원설계 §1). 산출물은 "해석 가능한 측정 리포트" 자체.

---

## 2. 데이터 자산 (로컬 실측 확인)

| 자산 | 경로 | 내용 | 역할 |
|------|------|------|------|
| 이미지 | `$SJMJ_DATA_DIR/images/inv_*.jpg` | 수기 원본 38장 (1920×2560) | 입력 |
| 라벨 | `$SJMJ_DATA_DIR/labels/inv_*.json` | 행별 oracle bbox + text (item/quantity/unit_price/amount) | bbox·행정렬·보조 정답 |
| 정본 | `$SJMJ_DATA_DIR/references/inv_*.jpg` | 디지털 정본 38장 (~1206×1109, 인쇄) | **인쇄 일자 추출원** |
| 운영 DB 백업 | `$SJMJ_DB_BACKUP` (= `SJMJ-Web/database/db-2026-06-24-backup.sql`) | invoices 400건 / invoice_items 1,302행 (2025-08-18~2026-06-24) | **정답지 본체** |

- 데이터는 레포 밖(OneDrive·타 레포). 경로는 env(`SJMJ_DATA_DIR`, `SJMJ_DB_BACKUP`)로 주입, 실데이터·실경로는 gitignore 유지.
- 라벨 JSON 구조: `doc_bbox`/`doc_corners`(원근), `rows[]`{`row_id`, `*_bbox`, `*_text`}. **날짜 필드 없음.**
- DB 정답 스키마: `invoices`(`issue_date`, `grand_total`, `total_supply`), `invoice_items`(`name`, `quantity`, `unit_price`, **`supply`**, `vat`, `total`). 채점 기준 = `supply`(공급가).

### 2.1 사진 촬영일 메타데이터 — 전수 조사 결과 (이 데이터셋 한정)

사진↔DB 매칭의 자연스러운 키는 "촬영일 = `issue_date`"이지만, **이 38장 데이터셋에는 촬영일을 복원할 메타데이터가 없다**:

- EXIF `DateTimeOriginal`/`DateTimeDigitized`/`DateTime`: **38장 전부 없음**. EXIF APP1은 200바이트 최소본(Orientation·크기·GPSIFD 포인터·LightSource뿐, 카메라/날짜/GPS좌표 전무) → 라벨 파이프라인이 1920×2560 정규화·재저장하며 스트립.
- 파일 mtime: **38장 전부 2026-01-09 30초 구간**(데이터셋 가공 시각, 촬영일 아님). birth: 2026-06-24(OneDrive 복사).

> ⚠️ **이 부재는 이 가공된 데이터셋의 속성일 뿐이다.** **추후 제공되는 사진셋(휴대폰 원본 촬영본)에는 EXIF `DateTimeOriginal`이 포함될 수 있다.** 즉 "촬영일 메타 ↔ `issue_date`" 매칭 전략 자체는 유효하며, 관건은 운영 수집 루프에서 **사진을 raw로 보존(재저장으로 EXIF 제거 금지)**하는 것이다. 후속 매칭 SP는 메타 날짜를 **있으면 우선 사용, 없으면 본 SP1의 references 일자 추출 경로로 폴백**하도록 설계한다.

---

## 3. 정답지 매칭 (이번 라운드: references 일자 → DB 조회)

촬영일 메타가 없으므로, 이 라운드는 **references 정본의 인쇄 일자**를 키로 DB를 조회해 정답지를 만든다.

### 3.1 흐름 (HITL 검수 체크포인트 포함)

```
references/inv_*.jpg
  │ [match: 1단계] stock OCR(인쇄 텍스트) → 날짜 정규식(YYYY-MM-DD 등) → inv별 일자 후보 + grand_total 후보
  ▼
reviewed_dates.csv  (검수용 산출물: image_id, 추출일자, grand_total, DB후보수, 매칭상태)
  │ ★ 사용자 검수 체크포인트 — 사용자가 일자/매칭을 1회 확인·수정
  ▼
  │ [match: 2단계] 검수된 일자 + grand_total 로 DB 유일 조회
  ▼
ground_truth[image_id] = DB invoice + invoice_items  (정답지)
```

### 3.2 매칭 키 — 일자 단독 불가, **일자 + grand_total** 필요 (실측 근거)

DB invoices를 실측한 결과 **고유 일자 163개 중 106개가 같은 날 2건 이상(최대 하루 8건)**. 따라서 일자만으로는 후보가 여럿이라 유일 식별 불가.

- **유일 식별 키 = 검수된 `issue_date` + `grand_total`**.
- `grand_total`은 **라벨 금액(`amount_text`) 합**에서 독립 산출(라벨 숫자 99.5~100% 감사검증됨, 0단계). 인식 결과를 쓰지 않으므로 정답지 순환참조 없음.
- 0단계 감사에서 38개 라벨 중 37개가 `grand_total`만으로 DB 매칭됨 → 일자는 충돌 해소·검수 확인용 축으로 결합.
- 매칭 실패/모호(0건 또는 다건)는 `reviewed_dates.csv`에 상태로 노출 → 사용자 검수에서 확정. (예: 0단계 미해결 inv_015 매칭실패·inv_030 분해차이)

### 3.3 왜 references **전체 OCR**이 아니라 **일자만** 뽑나

references(인쇄 정본) 전체를 OCR해 모든 필드를 정답으로 쓰면 OCR 오류가 정답지에 섞인다. 대신 **작고 검수하기 쉬운 일자 한 필드만** 뽑고(인쇄 텍스트라 stock OCR 신뢰도 높음), 사용자가 일자를 1회 검수한 뒤 **나머지 정답(수량·단가·공급가·품목)은 DB에서 권위있게** 가져온다.

---

## 4. 아키텍처 (최소 슬라이스 파이프라인)

```
38× (이미지 + 라벨JSON) + references + DB백업
  │ [data]  이미지↔라벨 페어 로딩·검증 (env 주입)
  │ [db]    DB 백업(.sql) INSERT 파싱 → invoices/invoice_items 인메모리
  │ [match] references 일자 추출 → 검수 → 일자+grand_total DB 조회 → 정답지 매핑
  ▼
[crop]   라벨 oracle bbox(quantity/unit_price/amount) → 원해상도 셀 crop
  │       ※ de-skew/검출기 없음 — bbox가 이미 원본 좌표계
  ▼
[recognize]  RecognizerAdapter 인터페이스 → PaddleOCRNumeric 구현
  │           셀 crop → raw 디코드 → 숫자 charset post-filter → raw 문자열
  ▼
[normalize]  symbolic 규칙: 천원곱(price/amount)·빈칸=0·〃(ditto) 전파 → 정수값
  ▼
[validate]   산술검산 수량×단가=공급가 / 세액=공급가×10%
  │           단일 셀 오류 행 → 역산 복원, 검산결과 = 신뢰도 게이트(정답 보증 아님)
  ▼
[score]      DB 정답 대비 2축 채점(raw read vs read+검산), 공급가 기준·필드별
  │           (라벨↔DB 불일치는 보조 교차검증으로 함께 집계 = 감사 재확인)
  ▼
[report]     리포트(md+json) + 오답 갤러리(crop·예측·정답)
```

### 4.1 모듈 경계 (단일 책임, recognizer만 부수효과)

| 모듈 | 책임 | 의존 |
|------|------|------|
| `data.py` | 이미지·라벨 페어 로딩·검증, env 경로 해석 | 파일시스템 |
| `db.py` | `.sql` 백업의 invoices/invoice_items INSERT 파싱 → 조회(by date+grand_total, by invoice_id). NULL·따옴표·콤마 처리 견고히 | 표준 라이브러리만 |
| `match.py` | references 일자 추출 + 검수 산출물 입출력 + DB 유일조회 → 정답지 매핑 | `db`, recognizer |
| `crop.py` | 라벨 bbox → 셀 crop. degenerate/경계밖 bbox 검증·스킵·기록 | Pillow |
| `recognize.py` | `RecognizerAdapter`(추상) + `PaddleOCRNumeric`(post-filter) | PaddleOCR |
| `normalize.py` | 약식 symbolic 규칙(천원곱·빈칸=0·〃). **순수함수** | — |
| `validate.py` | 산술검산·역산 복원·신뢰도 게이트. **순수함수** | — |
| `score.py` | DB 정답 대비 2축 메트릭·행 정렬·집계. **순수함수** | — |
| `report.py` | md/json 리포트 + 오답 갤러리 | Pillow |
| `__main__.py` | 38장 배치 오케스트레이션 CLI | 위 전부 |

- **교체가능 어댑터**: `RecognizerAdapter.recognize(crop) -> str` 추상 뒤에 PaddleOCR. 후속 SP의 Qwen2.5-VL/TrOCR가 같은 인터페이스로 들어오면 2단계 백엔드 비교가 파이프라인 변경 없이 성립.
- **행 정렬**: 라벨 `row_id` ↔ DB `invoice_items.item_order` 순서 정렬. grand_total/supply 합 일치로 정렬 정합성 검증.

---

## 5. 채점 설계

- **정답지 = DB 레코드**(매칭본). `supply`(공급가) 기준. `total`(부가세포함)과 혼동 금지.
- **2축 분리**: `raw read 정확도`(인식기 순정) vs `read+산술검산 후 정확도`(검산 게인). 검산 게인은 "단일 셀 오류 행"에 한정된 조건부 레버임을 명시 — 2셀 오류·행 누락은 별도 집계, 상호보상 거짓양성(곱은 같으나 둘 다 오류)도 별도 표시.
- **채점 무게**: 약식 분해 모호성(inv_030: 4×100,000 vs 1×400,000, 공급가 동일) 때문에 (수량,단가) 개별보다 **(공급가, 금액)** 정합에 무게.
- **메트릭**: 셀 정확도(필드별) / 행 완전일치율 / 명세서 완전일치율 / 검산 통과율 — micro·macro.
- **실재 vs 역산 caveat**: 일부 셀(예: inv_001 단가)은 사진에 없을 수 있음(라벨/DB가 역산값). 이 슬라이스는 bbox 있는 셀을 모두 raw read·채점하되 **이 caveat를 리포트에 명시**하고 raw 정확도를 "셀 존재 가정 하"로 보고. 정밀 실재 태깅은 후속 SP. 공급가/금액은 거의 항상 실재라 1차 지표가 견고.
- **보조 교차검증**: 라벨 텍스트 ↔ DB 정답 불일치를 함께 집계 → 0단계 감사(99.5~100%)를 이 슬라이스가 자동 재확인.

---

## 6. 에러 처리 & 엣지케이스

- **약식 규칙 카탈로그**: 첫 컷 규칙을 명시 코드화하고 적용률·예외를 리포트에 집계 → 원설계 "0단계 잔여: 약식 예외율 통계"를 슬라이스가 데이터로 산출.
  - 천원곱: unit_price/amount는 천원 단위 → raw가 임계 미만이면 ×1000 (quantity는 literal, 곱 안 함)
  - 빈칸=0, `〃`(ditto) → 윗행 값 전파
- **PaddleOCR 환경 실패**(Apple Silicon 설치·모델 다운로드)는 SP1의 실재 리스크 → plan **첫 task = "PaddleOCR로 셀 1장 읽기 스파이크"**로 환경을 가장 먼저 검증(가장 불확실한 가정부터 깸).
- **개별 이미지 실패 격리**: 38장 배치에서 한 장이 깨져도 중단 금지 — error 기록 후 계속, 리포트에 실패 목록.
- **bbox 이상**(degenerate/경계밖): crop 단계 검증·스킵·기록.
- **DB 매칭 실패/모호**: `reviewed_dates.csv`에 상태 노출 → 검수에서 확정. 미해결 항목은 채점 모집단에서 제외하고 별도 집계.
- **DB 파싱**: 백업 형식(phpMyAdmin)의 NULL·이스케이프·멀티행 VALUES를 견고히 파싱(러프 정규식은 400행 중 일부 누락 — 전 행 파싱 보장 필요).

---

## 7. 테스트 & 검증

SP0와 달리 SP1은 검증 로직(normalize/validate/score)이 핵심이므로 **순수 코어는 단위테스트로 두텁게**, 외부 의존(인식기·모델)은 분리한다.

**자동 테스트**
- **순수함수 단위테스트(핵심)**: `normalize`(천원곱·ditto·빈칸), `validate`(정상행/단일셀오류/2셀오류/상호보상 거짓양성/역산), `score`(정합 판정·2축 집계), `db`(샘플 INSERT 파싱·조회). AAA 패턴.
- **recognizer는 어댑터 모킹**: 파이프라인 로직은 가짜 recognizer로 결정론화.
- **배치 스모크**: 모킹 어댑터로 소수 장 end-to-end → 리포트 생성·크래시 없음.
- 커버리지: normalize/validate/score/db 순수 코어 80%+. 어댑터·CLI 글루·PaddleOCR 실모델 read는 스모크 수준(전역 testing 규칙 "스캐폴딩은 스모크" 정신).

**수동 검증(성공 기준)**
1. `match` 1단계 → `reviewed_dates.csv` 생성, 38행 일자·매칭상태 채워짐.
2. (검수 후) `match` 2단계 → 정답지 매핑 성립, 미매칭은 명시.
3. 배치 1회 → 38장 리포트(`report/sp1-report.md`) 생성: 2축 메트릭 표·검산 게인·약식 적용률·실패목록·오답 갤러리.
4. 리포트의 라벨↔DB 보조 교차검증이 0단계 감사 수치(공급가 100% 등)를 재확인.

---

## 8. 디렉터리 구조 & 산출물

```
apps/invoice-ocr/ml/
├── pyproject.toml          # 독립 uv: paddleocr, pillow, numpy / dev: pytest
├── uv.lock
├── .env.example            # SJMJ_DATA_DIR(OneDrive), SJMJ_DB_BACKUP 예시
├── README.md               # 실행법(uv run python -m ocr_poc ...)
├── src/ocr_poc/
│   ├── __init__.py
│   ├── __main__.py         # 배치 오케스트레이션 CLI
│   ├── data.py  db.py  match.py  crop.py
│   ├── recognize.py  normalize.py  validate.py  score.py  report.py
└── tests/
    ├── test_normalize.py  test_validate.py  test_score.py  test_db.py
    └── test_pipeline_smoke.py
results/   # gitignore: 셀 crop·추출 캐시·reviewed_dates.csv
report/    # gitignore: sp1-report.md / *.json / 오답 갤러리
```

- **backend FastAPI에 배선하지 않음**(순수 오프라인). 추론서비스 통합은 SP2/SP4.
- 빈 플레이스홀더였던 `ml/.gitkeep`은 본 구조로 대체.

---

## 9. 이후 SP로의 연결점 & 한계

- **검출기(YOLO)**: SP1은 라벨 oracle bbox 사용. DocLayout-YOLO/YOLOv11 fine-tune은 후속 SP — 본 슬라이스가 "인식이 병목인지 검출이 병목인지"를 먼저 분리 측정해 줌.
- **품목명 인식**: 흘림체 한국어 품목명(진짜 병목, 기술검토 §5)은 후속 SP. RecognizerAdapter·정규화 사전(운영DB 386종)이 그때 결합.
- **백엔드 비교(2단계)**: Qwen2.5-VL-7B(MLX)·TrOCR가 같은 어댑터 인터페이스로 plug-in → 동일 채점셋·동일 검산 게이트로 비교. macmini 투입 시점.
- **사진↔DB 자동매칭기**: 본 SP1의 references-일자 경로를 일반화. **제약 명시 — 매칭 SP는 사진 메타 날짜에 의존하지 말 것. 이 데이터셋엔 없음(§2.1). 미래 셋은 EXIF `DateTimeOriginal`이 있으면 우선 사용, 없으면 references 인쇄일자 추출로 폴백. 일자 단독은 충돌(106/163)하므로 항상 `grand_total` 결합.**
- **데이터 증폭·재학습**: 운영DB 400건+α 페어화·fine-tune은 실현가능성 입증 후(원설계 3단계).

---

## 10. 다음 단계

이 spec 합의 후 → writing-plans로 TDD 구현 plan 작성. plan 첫 task는 **PaddleOCR 환경 스파이크**(가장 불확실한 가정부터). 이후 db 파싱 → match(검수 체크포인트) → crop → recognize → normalize → validate → score → report 순.
