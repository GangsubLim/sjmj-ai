# sjmj-ai 단계별 진행 로드맵 (설계)

- 작성일: 2026-06-27
- 성격: **마이그레이션 실행 로드맵 설계 문서.** "원본 SJMJ-Web 기능·UI를 그대로 유지한 채 Python+React로 리팩토링해 macmini에 배포(Phase 1) → ML PoC 연결(Phase 2)"의 단계별 진행 계획. 각 하위 단계는 자체 spec → plan → TDD 구현 사이클을 가진다.
- 관련 정본: scaffold [`2026-06-24-sjmj-ai-sp0-scaffold-design.md`](./2026-06-24-sjmj-ai-sp0-scaffold-design.md)(SP0~SP5 분해), 개괄 [`2026-06-24-macmini-migration-overview.md`](./2026-06-24-macmini-migration-overview.md), ML 확정구조 [`2026-06-26-invoice-ocr-ml-confirmed-architecture.md`](./2026-06-26-invoice-ocr-ml-confirmed-architecture.md).
- 검증 기준일: 2026-06-27. 원본 라우트·스키마 수치는 이 날짜 `~/projects/SJMJ-Web` 실측.

---

## 0. 한 줄 결론

토대는 깔렸다(SP0 인프라 셸 + SP1 ML PoC 검증 완료). 남은 것은 **두 단계의 순차 실행**이다:

1. **Phase 1** — 원본 SJMJ-Web(PHP+React)을 **기능·UI 동등한** Python(FastAPI)+React 앱으로 리팩토링해 macmini에 **실데이터로** 배포.
2. **Phase 2** — 검증된 ML PoC를 Phase 1이 남겨둔 **이음새**에 끼워 production 서비스 + 피드백 루프로 승격.

Phase 1에는 ML 연계를 위한 **이음새만 선반영**(스키마 자리·env 경계·매칭키 인덱스)하고, 실제 ML 코드는 Phase 2로 미룬다.

---

## 1. 명명 정리 (선결 — 혼동 제거)

기존 문서에서 **"SP2"가 두 의미**로 쓰여 혼동을 준다:

- scaffold의 **SP2** = 백엔드 Python 재작성 + DB 이전
- ML 문서의 **"sp2"** = 손글씨 인식 트랙(spike)

이 로드맵은 충돌을 피해 **Phase 1 / Phase 2 / Phase 3**으로 부르고, 하위 단계를 `1A`·`1B`…로 둔다. scaffold SP 매핑은 각 단계에 병기한다.

### 이미 완료된 토대

| 자산 | 위치 | 상태 |
|---|---|---|
| SP0 인프라 셸 | FastAPI `/health` + Vite 빈 셸 + macmini launchd **템플릿**(`ai.sjmj.backend.plist.template`) | ⚠️ 부분 — 셸·헬스 커밋, launchd는 **템플릿만** 존재. 실제 plist 설치·env 주입은 1D 작업 |
| SP1 ML PoC | `apps/invoice-ocr/ml/ocr_poc`(숫자 53 tests) + `report/sp2_spike`(손글씨 27 tests) | ✅ 검증 — Phase 2가 끌어쓸 자산 |

---

## 2. 전체 흐름

```
[완료] SP0 인프라 셸 · SP1 ML PoC(ocr_poc 53T + 손글씨 spike 27T)
  ↓
Phase 1  운영 앱 리팩토링 & macmini 배포   (PHP+React → Python+React, 기능/UI 그대로)
  1A DB 이전·정본화 → 1B 백엔드 API 포팅(30라우트 계약 1:1) → 1C 프론트 이식·연결 → 1D macmini 통합배포 검증
  ↓
Phase 2  ML 연결   (PoC → production 서비스 + 피드백 루프)
  2A ML 서비스화(어댑터 승격·torch/MLX 분리) → 2B worker 추론 큐 → 2C 검수 UI·확정저장 → 2D 피드백 루프 자동화
  ↓
Phase 3  운영 굳히기  (tag-CI·자동롤백·백업·모니터링·Tailscale 외부)
```

### 확정된 진행 결정 (이 세션)

| 결정 | 확정 | 근거 |
|---|---|---|
| Phase 1 내부 순서 | **백엔드/DB 먼저(수평) → 프론트 api 조정 연결** | 프론트 14,200 LOC를 '그대로 유지'하므로, 작업의 본질은 "FastAPI가 PHP와 동일한 JSON 계약을 내게 하기". 계약-우선 수평 진행이 깔끔. |
| 운영 데이터 이전 | **Phase 1에서 이전** | ML 피드백 루프의 GT(정답지)가 운영DB(`total_supply` 매칭)이므로 일찍 옮길수록 Phase 2 학습데이터 축적이 즉시 가능. 민감정보는 on-prem이라 오히려 안전. |
| Phase 1 ML 선반영 | **이음새만** | 스키마 자리·env 경계·매칭키 인덱스·분리 서빙 자리만 확보. 실제 ML 코드·worker는 Phase 2. |

### 단계별 구현 도구 선택

가르는 기준 한 줄: **"독립 단위로 병렬 쪼개지나?"** → dynamic workflow(팬아웃). **"하나의 응집된 변경이고 리뷰·머지가 핵심인가?"** → standard pipeline(순차 TDD + 리뷰 게이트).

| 단계 | 권장 도구 | 한 줄 이유 |
|---|---|---|
| 1A DB 이전·정본화 | **직접 + 검증 스크립트**(PR 리뷰 선택) | 병렬도 응집 코드 변경도 아님 → 무결성은 자동 검증 스크립트가 보험(1D와 동형) |
| 1B 백엔드 API 포팅 | **workflow → pipeline (하이브리드)** | 6 리소스 독립 → 병렬 포팅+계약검증, 머지는 PR 리뷰 게이트 |
| 1C 프론트 이식·연결 | **직접 (+ e2e만 workflow)** | 무변경 이식이라 TDD 적음, 7페이지 e2e만 병렬 검증 |
| 1D macmini 배포 검증 | **직접** | launchd·env·Tailscale은 스크립트+수동 검증, 파이프라인 부적합 |
| 2A ML 서비스화 | **standard pipeline** (+앞단 workflow 설계패널) | 어댑터 승격·분리 서빙 응집 변경, 토폴로지는 판정패널 비교 |
| 2B worker 추론 큐 | **standard pipeline** | 큐·잡 상태머신 단일 응집 신규 컴포넌트 |
| 2C 검수 UI | **standard pipeline** | HITL 흐름·상태 정확성 중요 |
| 2D 피드백 루프 자동화 | **standard pipeline** | 재학습 자동 트리거·검산 게이트 리뷰가 핵심 |

> workflow가 빛나는 폭(breadth) 작업은 사실상 **1B 하나**(+ 검증 스윕·설계 패널 보조). 1A·1C·1D는 병렬도 응집 코드 변경도 아니라 **직접**(검증 스크립트·e2e 보조), 2A~2D는 리뷰 게이트가 보험이 되는 응집 변경이라 **standard pipeline**.

---

## 3. 원본 자산 인벤토리 (이전 대상, 2026-06-27 실측)

### 백엔드 (PHP 4-layer) — 30 라우트 / 6 리소스

| 리소스 | 라우트 수 | 비고 |
|---|---|---|
| invoices | 7 | index/show/store/update/destroy + `export` + `{id}/duplicate` |
| companies | 6 | CRUD + `{id}/invoices` |
| items | 5 | CRUD |
| settings | 5 | issuer get/update + `issuer/stamp` 업로드 + app get/update |
| salespeople | 4 | index/store/update/destroy |
| sales-records | 3 | index/store/destroy |

계층: `Router → Controller → Service → Repository`(PDO). 컨트롤러 6종.

### 프론트 (React 19) — 7 페이지

`작성(/)` · `수정(/edit/:id)` · `목록(/list)` · `거래처(/companies)` · `품목(/items)` · `실적(/sales-performance)` · `설정(/settings)`. 스택: Vite 7 + TS strict + Tailwind v4 + shadcn/ui + Zustand + react-router-dom + html-to-image/jsPDF(클라이언트 PDF). ~14,200 LOC. **유지 가치 큼 → 무변경 이식.**

### DB (MySQL)

- 정의: `database/schema.sql` + 마이그레이션 `.sql` **9종**(`migration_001~006` + `migration_add_deduction`·`migration_add_recipient2`·`migration_poc_to_production`). 그 중 `migration_poc_to_production`은 ML 관련. 거래처/품목 확장·app_settings·fax/sms·vehicle_no·실적(salespeople/sales_records)·deduction·recipient2 등을 담는다.
- 운영 덤프: `database/db-2026-06-24-backup.sql`(운영 400건+).
- **`invoices.total_supply` 컬럼은 이미 존재하나 `invoices`에 인덱스는 없다**(현재 인덱스는 FK `issuer_id`뿐, 2026-06-27 `schema.sql` 실측) → ML 매칭키 이음새는 "컬럼 추가"가 아니라 **"기존 컬럼에 인덱스 신규 추가"**.

---

## 4. Phase 1 — 운영 앱 리팩토링 & macmini 배포

목표: **원본 SJMJ-Web과 기능·UI가 동등한 앱이 macmini에서 실데이터로 가동.** scaffold SP2(백엔드/DB) + SP3(프론트).

### 1A. DB 이전 & 스키마 정본화 (scaffold SP2-DB)

> **구현 도구: 직접 + 검증 스크립트(PR 리뷰는 선택)** — 본 로드맵의 분기 기준("독립 단위로 병렬 쪼개지나 / 응집 코드 변경이라 리뷰·머지가 핵심인가")에 1A는 어느 쪽에도 맞지 않는다(병렬 아님, 응집 코드 변경 아님 — SQL 덤프 적재 + 스키마 이전 + 빈 테이블 2개 + 인덱스 1개). 무결성(row count·FK)은 코드 리뷰가 아니라 **자동 검증 스크립트**가 잡으며 DoD도 그렇게 정의돼 있다. 따라서 1D와 동일한 "직접 + 검증 스크립트" 패턴이 자연스럽다. 운영데이터 손상 우려는 검증 스크립트가 1차 보험이고, 안심을 위해 이전 diff에 대한 PR 리뷰를 **선택적으로** 붙인다.

- `SJMJ-Web/database/`(schema.sql + 마이그레이션 9종) → `sjmj-ai/db/`로 이전. 마이그레이션 도구는 **기존 순번 .sql 관습 유지**(KISS — 이미 9개가 그 형식, Alembic 신규 도입 안 함).
- 운영 덤프 `db-2026-06-24-backup.sql`을 macmini MySQL에 적재.
- **ML 이음새 선반영(이것만, 코드 없음)**:
  - `invoices.total_supply` 매칭키에 인덱스 **신규 추가**(현재 무인덱스 — 사진↔DB 조회 성능).
  - `ocr_jobs`(추론 잡 상태) · `ocr_corrections`(교정 피드백) 테이블을 **빈 스키마로 미리 추가**. Phase 2(2B/2C)가 채운다.
- **검증**: 원본 MySQL row count == 이전 후 row count, FK 무결성 통과(자동 검증 스크립트).

### 1B. 백엔드 API 포팅 (FastAPI, 계약 1:1) (scaffold SP2)

> **구현 도구: dynamic workflow → standard pipeline (하이브리드)** — 6 리소스 × 4계층이 서로 독립이라 workflow 팬아웃이 핵심 레버리지(리소스당 1 에이전트가 포팅+계약 골든검증 병렬). 산출은 standard pipeline의 PR→리뷰→merge 게이트에 태운다. 본 로드맵에서 workflow가 빛나는 유일한 폭(breadth) 작업.

- **0단계 — vertical slice 먼저(수평 팬아웃 앞)**: 리소스 1개(예: `companies`)를 **API + 해당 프론트 페이지까지 end-to-end**로 먼저 관통시켜 "프론트가 무수정으로 붙는다"는 계약 가정을 **조기 반증**한다. 이 한 줄기가 통과해야 나머지 5 리소스를 수평 팬아웃한다(가정이 틀렸을 때 비용을 1C 종료가 아니라 1B 착수 시점에 노출).
- PHP 4-layer → FastAPI `router → service → repository` 대응. 6 리소스 30 라우트 전수 포팅.
- **합격 기준 = 계약 동등성**: 응답 JSON 필드명·envelope·상태코드가 PHP와 1:1 (기존 React가 무수정으로 붙어야 함).
- **비-JSON 표면도 계약에 명시(JSON envelope 밖)**:
  - `settings/issuer/stamp` — **multipart 업로드**(요청 Content-Type·필드명·저장 경로·응답 형태).
  - `invoices/export` — **파일 다운로드**(응답 Content-Type·`Content-Disposition`·바이너리 본문). 무변경 프론트가 가장 깨지기 쉬운 지점이므로 envelope 1:1 기준이 구조적으로 누락하지 않도록 별도 계약 항목으로 둔다.
- **골든 비교 = DoD(선택 아님)**: PHP **228 테스트**의 기대 응답을 FastAPI **계약 골든**으로 포팅한다 — "명세 참고용"과 "PHP↔FastAPI 골든 응답 비교"를 하나로 합쳐, 가장 강력한 회귀 안전망(PHP 기대값)을 버리지 않는다. FastAPI 테스트는 이 골든을 기준으로 신규 작성(80% 커버리지, TDD).
- **ML 이음새**: `SJMJ_DATA_DIR`/`SJMJ_DB_BACKUP` env를 `config.py` 경계에 통합(ocr_poc와 동일 규약), stamp 업로드 등 파일 경계 정리.
- **검증**: 리소스별 계약 골든 비교 통과(JSON 30라우트 + 비-JSON 2표면) = 합격 기준.

### 1C. 프론트 이식 & 연결 (scaffold SP3)

> **구현 도구: 직접 (+ e2e만 workflow)** — 무변경 이식이라 TDD 로직이 적어 두 파이프라인 모두 과함. 파일 복사 + api base URL 조정은 직접, 단 7페이지 e2e 검증 스윕은 workflow로 병렬화 가치 있음.

- `SJMJ-Web/frontend/src` → `apps/invoice-ocr/frontend`로 **그대로 이식**(shadcn·Tailwind v4·Zustand·react-router·PDF 의존 동일). 7페이지 무변경.
- API 클라이언트 **base URL/proxy만 조정**(`/api`→:8400). SP0의 Vite proxy·정적 dist 서빙이 그대로 수용.
- **검증**: 7페이지 e2e(작성·수정·목록·거래처·품목·실적·설정) + PDF 생성 동작.

### 1D. macmini 통합 배포 & 컷오버 검증

> **구현 도구: 직접** — launchd·env·Tailscale은 코드 TDD가 아니라 스크립트 + 환경 의존 수동 검증. 순차·환경 의존이라 두 파이프라인 모두 부적합.

- launchd 서비스(`ai.sjmj.backend`)에 DB 연결 env 주입, frontend dist 빌드 서빙. Tailscale 내부 접속 실사용 검증.
- 정식 tag-CI·백업·롤백은 **Phase 3로 미룸**(YAGNI — 우선 동작하는 단일 서비스 + 실데이터).

> **Phase 1 종료 기준(DoD)**: macmini에서 Tailscale 내부 접속으로 7페이지가 원본과 동일하게 동작하고, 운영 실데이터가 조회·작성·PDF 출력된다.

---

## 5. Phase 2 — ML 연결 (이음새에 PoC 끼우기)

목표: 검증된 PoC를 Phase 1 이음새에 결합해 production 추론 + 피드백 루프 완성. scaffold SP4 + ML 서빙. 상세 계약은 ML 확정구조 §4·§8 참조.

### 2A. ML 서비스화

> **구현 도구: standard pipeline (+ 앞단 workflow 설계패널)** — 어댑터 승격·분리 서빙은 응집·정확성 critical 변경. 단 torch/MLX 서빙 토폴로지는 workflow judge-panel로 안 N개 비교 후 pipeline 구현.

- `ml/infer_photo.process_one`(사진→품목+금액 JSON)을 추론 계약으로 감싼다.
- **SP2 spike를 `RecognizerAdapter` 뒤로 승격**(production 규약·테스트 부여, 현재 gitignore spike). 승격 = `report/` 밖으로 파일 이동이므로 **gitignore 재설계를 동반**(§7) — 대용량 모델이 git에 딸려 들어가지 않도록 파일별 명시 제외로 전환.
- **torch/MLX 분리 서빙(필수 제약, §8)**: 단일 프로세스에서 transformers ViT(품목 인코더) MPS forward 후 MLX(Qwen) generate 호출 시 출력 깨짐(`!!!`). 품목(torch CPU)·금액(MLX Metal)을 **분리 프로세스/서비스**로 — 이는 launchd 잡 분리 + 프로세스 supervisor 도입을 동반하는 인프라 재작업이다("자리"가 아님).
- 모델·뱅크 영속: `ft_prod.pt`(347,595,503바이트 ≈ **331MiB**, `ls -lh`는 348MB·`du -h`는 331M)·`bank.npz`·dataset 전부 gitignore → 별도 볼륨/스토리지 전략.

### 2B. worker 추론 큐 (scaffold SP4)

> **구현 도구: standard pipeline** — 큐·잡 상태 머신은 단일 응집 신규 컴포넌트, 순차 TDD + 리뷰.

- 사진 업로드 → worker 큐 → ML 추론 → 초안 JSON(품목 top-5 + 금액). 1A의 `ocr_jobs` 테이블 사용.

### 2C. 검수 UI & 운영DB 확정 저장 (scaffold SP4)

> **구현 도구: standard pipeline** — HITL 교정 흐름·상태 정확성이 중요한 UI 신규(기존 작성페이지 연계).

- 검수 화면(spike의 `grouping_editor`/`label_inspect` 역할)을 프론트에 추가. **기존 작성 페이지와 연계한 HITL 입력보조**(pre-fill + top-5 드롭다운 — 재현 행 타이핑 ~60% 절감).
- 사람 교정 → 운영DB 확정 + `ocr_corrections` 적재.

### 2D. 피드백 루프 자동화

> **구현 도구: standard pipeline** — 재학습 자동 트리거·검산 게이트는 안전장치 리뷰가 핵심인 응집 변경.

- 교정 누적 임계 트리거 → `train_contrastive --production` 재학습 잡 → `ft_prod.pt`+`bank.npz` 갱신(DB 쌓일수록 신규→재현 전환으로 정확도 개선 기대).
  - **가정(미검증)**: 운영 400건 + 신규 교정 규모가 contrastive 재학습을 유의미하게 돌리기에 충분한지는 데이터 규모 근거가 아직 없다. 재학습 트리거의 최소 교정 건수 임계는 Phase 2에서 소규모 학습 곡선으로 실측해 정한다("쌓일수록 자동 개선"을 무조건 전제하지 않음).
- `group_amounts.py` 금액합 자동검산 게이트(현재 미구현, §8) 구현.

---

## 6. Phase 3 — 운영 굳히기 (scaffold SP5)

tag-driven 배포 CI(`deploy-macmini.yml`) + 자동 롤백, `mysqldump` 백업·오프머신 복제, 모니터링, Tailscale 외부 e2e.

---

## 7. ML 이음새 — Phase 1이 남기고 Phase 2가 채우는 것

| 이음새 | Phase 1(남김) | Phase 2(채움) |
|---|---|---|
| 인식기 경계 | `RecognizerAdapter` Protocol 자리(ocr_poc에 이미 존재) | SP2 손글씨 인식기를 어댑터 뒤로 plug-in |
| env 경계 | `SJMJ_DATA_DIR`·`SJMJ_DB_BACKUP`을 backend config에 통합 | worker/ml 컨테이너에 동일 env 주입 |
| 스키마 | `ocr_jobs`·`ocr_corrections` 빈 테이블 + `total_supply` **신규 인덱스**(현재 무인덱스) | 잡 상태·교정 적재 |
| 매칭키 | `total_supply`(공급가합, 컬럼은 존재·인덱스는 신규) | 사진↔DB GT 매칭(`grand_total` 금지) |
| 서빙 토폴로지 | 단일 backend 서비스(launchd) | 품목(torch)·금액(MLX) 분리 프로세스 추가 — **"자리"가 아니라 프로세스 supervisor 도입을 동반하는 인프라 재작업**(§8 하드 제약) |
| 모델·데이터 볼륨 | `ft_prod.pt`·`bank.npz`·dataset이 `report/` 폴더 통째 gitignore로 **간접 제외**(파일별 명시 아님) | spike를 `report/` 밖 production 경로로 승격 = **gitignore 재설계 동반**(승격 순간 ~331MiB 모델이 git에 딸려 들어갈 위험) + 별도 볼륨/스토리지 전략. **Phase 2 착수 전 블로커(§8)** |

---

## 8. 리스크 / 가정

- **계약 드리프트**: 1B에서 PHP 응답과 미세 불일치 시 무변경 이식한 프론트가 깨짐 → PHP 228 테스트 기대값을 포팅한 **골든 비교를 DoD로 강제**(선택 아님). JSON 30라우트 + 비-JSON 2표면(`issuer/stamp` 업로드·`invoices/export` 다운로드) 모두 포함.
- **운영 데이터 무결성**: 1A 이전 시 row count·FK 검증을 DoD로 강제(자동 검증 스크립트). 민감정보(거래처·차량번호)는 on-prem 유지.
- **torch/MLX 공존 불가**: Phase 2 서빙 설계의 하드 제약. 단일 프로세스 통합 금지(실측). 분리는 프로세스 supervisor 도입을 동반하는 인프라 재작업.
- **모델·데이터 영속화 + gitignore**: `ft_prod.pt`(~331MiB) 등 전부 레포 밖 → Phase 2 착수 전 볼륨 전략 필수. 현재 `report/` 폴더 통째 gitignore로 **간접 제외**라, 2A의 production 승격(파일을 `report/` 밖으로 이동) 시 모델이 git에 딸려 들어갈 위험 → 승격은 **gitignore 재설계 동반**.

---

> **요약**: SP0 인프라 + SP1 ML PoC 토대 위에서, **Phase 1(백엔드/DB 먼저 → 프론트 무변경 이식 → macmini 실데이터 배포)** 으로 원본과 동등한 앱을 올리고, **Phase 2**에서 검증된 PoC를 Phase 1이 남긴 이음새(§7)에 끼워 production ML + 피드백 루프로 승격한다. 각 하위 단계는 자체 spec → plan → TDD 사이클로 진행한다.
