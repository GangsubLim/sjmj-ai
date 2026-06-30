# Eng-Review — OCR 큐레이션 Phase A

- **리뷰 대상:** `docs/superpowers/plans/2026-06-30-ocr-curation-phase-a.md` (1819줄, 11 tasks + 최종 게이트)
- **설계 정본 대조:** spec `2026-06-30-ocr-curation-retraining-design.md`, ADR `0004`
- **불변식 기준:** `AGENTS.md`, `.claude/rules/api-conventions.md`, `.claude/ai-context/api-spec.json`
- **리뷰어 관점:** 엔지니어링 매니저 (범위·리스크·검증가능성·계약 불변식·TDD·좌표 정확성·SSOT)
- **방식:** 비대화형 subagent. 코드 표본 검증 포함.

## 종합 판정: **Go-with-fixes**

플랜은 구현 착수 가능 상태다. 좌표(라인 번호·시그니처·기존 테스트 패턴)는 **전부 실제 코드와 일치**했고, 외부 계약 불변식·3계층 경계·스키마 3곳 동기·백필 멱등성·트랜잭션 경계가 모두 정확하다. TDD 절차는 task별로 RED(정확한 실패 사유)→GREEN→검증→커밋이 완결됐다. **CRITICAL 없음. 하드 블로커 HIGH 없음.** 남은 것은 관측가능성/DRY 관련 MEDIUM 2건과 LOW 다수 — 어느 것도 머지를 막지 않으며 구현 중 흡수 가능하다. "fixes"는 착수 전제조건이 아니라 구현 시 반영 권고다.

---

## 코드 표본 검증 결과 (주장 vs 실제)

| 플랜 주장                                                                                   | 실제 코드                                                                                                                               | 판정                                    |
| ------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------- |
| `errors.py:52-55` = `register_error_handlers`가 AppError+Exception만 등록                   | `errors.py:52` def, 54-55 등록 2줄. `_error_body`(34)·`_unhandled_handler`(48) 존재                                                     | 일치                                    |
| `ocr_service.py:28-34` `__init__`, `57-88` confirm, `86` insert_correction                  | `__init__` 28-34, confirm 57-88, insert_correction 호출 86                                                                              | 일치                                    |
| confirm이 `with self._transaction():`로 tx 경계 (training_pairs insert가 합류)              | ocr_service.py:62 `with self._transaction():` 본문 전체 래핑                                                                            | 일치                                    |
| `ocr_correction.py:4-40` build_correction, lines[] 형태 25-34                               | build_correction 4-40, lines append 25-34. 키 `crop_ref/draft_label/final_label/label_changed/draft_supply/final_supply/supply_changed` | 일치                                    |
| `main.py` import 9-17, include_router 57-63, catch-all `_mount_static` 64                   | import 튜플 9-17, include 57-63, `_mount_static(application)` 64                                                                        | 일치                                    |
| `conftest.py:_ALL_TABLES` = 19-30행                                                         | `_ALL_TABLES` 리스트 19-30. (training_pairs 미포함 → 추가 필요 정확)                                                                    | 일치                                    |
| `schema_test.sql` DROP 블록 6-7행 (ocr_corrections/ocr_jobs)                                | line 6 `DROP ... ocr_corrections;`, line 7 `DROP ... ocr_jobs;`                                                                         | 일치                                    |
| `schema_test.sql` ocr_jobs CREATE의 `invoice_id INT,` 다음에 컬럼 추가                      | ocr_jobs CREATE 146-157, `invoice_id INT,` = line 151                                                                                   | 일치(블록 끝 157, 플랜 "158"은 ±1 무해) |
| ocr repo `insert_job/update_result/claim_job/link_invoice/insert_correction` 시그니처       | 전부 일치 (Task 4 테스트가 호출하는 형태 그대로)                                                                                        | 일치                                    |
| `envelope.list_response(data, pagination)` / `single(data)`                                 | envelope.py:10/18 동일 시그니처                                                                                                         | 일치                                    |
| `infer_job.py` `w` 생성 64행, crop 루프 76-81, crop_out_dir 캐스팅 58, cv2 import 51        | `w` = line 64, crop 루프 76-81, crop_out_dir mkdir 57-58, cv2 import = line **50**                                                      | 일치(cv2 50 vs 플랜 51, ±1 무해)        |
| `td.invoice_with_items()` 3 item 반환, items[0] override 패턴                               | test_data.py 존재, 3 item, 기존 test_ocr_service가 동일 override 사용                                                                   | 일치                                    |
| api-spec paths 21·schemas 28, `Pagination`·`ErrorEnvelope` 존재                             | paths 21, schemas 28, 둘 다 존재. ops 35 == overview 35                                                                                 | 일치 (+6 후에도 매칭 유지)              |
| ruff에 `id` path param 충돌 없음                                                            | ruff select = E/W/F/N/UP/B/SIM/I/D (flake8-builtins `A` 미포함). 기존 라우터 다수가 `def show(id: int)` 사용                            | 일치 (이슈 없음)                        |
| 기존 `test_confirm_rollback...`은 conflict가 insert_correction 이전 → training_pairs 미도달 | link_invoice=0 → conflict() = ocr_service.py:82-83, insert_correction(86) 이전. 회귀 영향 없음                                          | 일치                                    |

**외부 계약 불변식 검증(전 task):**

- 성공 envelope `{success,data,pagination?}` — list_jobs는 `list_response`, 나머지 JSON은 `single`. 일치.
- 에러 envelope `{success,error:{code,message,details?}}` — `not_found`→404 NOT_FOUND, Task1 핸들러→400 VALIDATION_ERROR. 일치.
- 검증 실패 **400** — Task1이 Pydantic 기본 422를 400으로 변환. 빈 body(Task7)·잘못된 enum(Task9) 둘 다 이 핸들러 경유. 일치.
- `details` = `{필드:메시지}` 맵 — 핸들러가 `loc[-1]`를 키로. 필드 누락/타입오류는 정확한 필드명 산출(검증함). 일치.
- 이미지 2종 FileResponse raw `image/png` 예외 — api-conventions.md(5번 항목)·api-spec(binary 표기) 양쪽 명문화. spec §4.2와 일치.

**설계 정본(spec/ADR) 대조:** training_pairs 컬럼·confirm 머티리얼라이즈·백필·게이트(`curation_reviewed`)·canonical_label 기본=final_label·6 엔드포인트·이미지 path 조립(crop_ref 불신뢰)·worker warped.png — **플랜이 spec §3~§5를 충실히 구현**. ADR의 "read-model 중복 정당화"·"canonical_label과 invoice_items.name 의도적 분기"도 보존.

---

## 심각도별 이슈

| #   | 이슈                                                                                   | 심각도 | 근거(코드 표본)                                                                                                                                                                      | 권고                                                                                                                                                                                                                                                   |
| --- | -------------------------------------------------------------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1   | 모델 레벨 validator(빈 body) 실패 시 `details` 키가 `"body"`가 됨 — 실제 필드명이 아님 | MEDIUM | Task1 핸들러 `field=str(loc[-1])`. Pydantic v2 `model_validator(mode="after")` 에러 `loc=("body",)`. Task7 `test_patch_pair_empty_body_is_400`은 code만 검사하고 details 키는 미검사 | 계약상 details는 `{필드:메시지}`. 전체-객체 위반은 `"body"`(또는 `"__root__"`)가 들어가 프론트가 필드 매핑 불가. 핸들러에서 `loc` 길이가 1이면 `"_"`/`"non_field"` 등 합의된 키로 정규화하거나, 최소한 contract 테스트에 details 키를 고정해 회귀 방지 |
| 2   | result_json JSON 파싱 로직 중복                                                        | MEDIUM | `CurationRepository.find_job_detail`(Task6)이 `json.loads if isinstance(raw,str)`를 인라인 재구현. 동일 로직이 `ocr_repository.py:10-16 _parse_job`에 이미 존재                      | repo 격리상 허용 범위지만 DRY 위반. `_parse_job` 류 헬퍼를 공유 모듈로 추출하거나 주석으로 의도적 중복임을 명시                                                                                                                                        |
| 3   | Task9 invalid-kind 400 테스트가 Task1 핸들러에 의존(문서 미기재)                       | LOW    | enum path param 실패도 `RequestValidationError`→Task1 핸들러 경유. 플랜은 Task1을 "body 검증 선결"로만 서술                                                                          | 순차 실행이라 동작엔 문제 없음. Task9 의존성 메모에 "Task1 핸들러가 path/query 검증 422→400도 처리"를 한 줄 추가                                                                                                                                       |
| 4   | `update_pair`의 `if not cols: return` 데드 분기 + `_data_dir` RuntimeError 분기 미커버 | LOW    | 라우터가 항상 검증된 status/canonical_label만 전달(model_validator가 둘 다 None 차단), 테스트는 SJMJ_DATA_DIR 항상 설정                                                              | 방어 코드로 무해. 집계 커버리지 80%에 영향 미미. 필요 시 단위테스트 1건씩 추가 권고(과하지 않게)                                                                                                                                                       |
| 5   | `build_training_pairs` row_index 파싱 취약                                             | LOW    | `int(ref.rsplit("/row-",1)[-1])` — crop_ref에 `/row-` 없으면 ValueError. 단 crop_ref는 항상 서버 생성(infer_job.py:26 `job-{id}/row-{i}`)                                            | 입력이 신뢰 경로라 실질 위험 낮음. 멀티자리 테스트는 이미 존재(Task3). 그대로 진행 가능                                                                                                                                                                |
| 6   | `status` DB enum/CHECK 미강제(VARCHAR(16))                                             | LOW    | spec §3.1도 VARCHAR. Pydantic `Literal`이 API 경계에서 강제, confirm/백필은 항상 'included'                                                                                          | 직접 SQL 우회만 위험. spec과 일치하므로 수용. 변경 불요                                                                                                                                                                                                |
| 7   | 라인 번호 ±1 드리프트 다수                                                             | LOW    | cv2 import 50(플랜 51), ocr_jobs CREATE 끝 157(플랜 158), ocr_corrections 160-170(플랜 171), errors 52-55(spec 54-55)                                                                | 전부 "다음 줄 삽입" 기준이라 무해. 구현 시 앵커 문자열로 위치 잡으면 영향 0                                                                                                                                                                            |

---

## 검증 가능성 / TDD / 리스크 평가 (블로킹 아님, 판정 근거)

- **TDD 완결성:** Task 1~9는 Step1(RED, 실패사유 명시)→Step3(최소 GREEN)→Step4(검증)→Step5(commit) 완비. RED 사유가 실제로 성립함을 확인 — 예: Task1은 핸들러 미등록 시 FastAPI 기본 422 반환→`assert 400` 실패(정확). Task5는 라우트 미등록→SPA fallback이 JSON 아님(정확). **Task 10(worker)은 RED 생략을 정당화** — cv2/실모델 글루는 ml/ 합성 단위테스트 도달 불가(ml/AGENTS.md 근거), 검증은 ruff+인스펙션+Phase C 라이브 e2e. 합당.
- **커버리지 80% 게이트:** 신규 백엔드 슬라이스(router/service/repository/schema)는 contract+integration 테스트로 커버. infer_job 변경은 `ml/` 스코프라 backend `--cov=app`에 미포함 → backend 커버리지 영향 없음(정확한 분리).
- **스키마 3곳 동기:** migration_008 + schema_test.sql(백필 제외) + conftest `_ALL_TABLES`(training_pairs를 FK 자식으로 첫 항목). `SET FOREIGN_KEY_CHECKS=0` 하 TRUNCATE는 기존 invoices(부모) 트렁케이트로 이미 입증된 패턴. 정합.
- **백필 멱등성:** `crop_ref UNIQUE` + `ON DUPLICATE KEY UPDATE id=id` no-op + `WHERE crop_ref IS NOT NULL AND job_id IS NOT NULL`. JSON_TABLE 경로(`$.lines[*]`)의 필드가 build_correction 산출 lines[]와 정확히 매칭. ALTER는 information_schema 가드(PREPARE/EXECUTE)로 재실행 안전. FK ON DELETE(CASCADE/SET NULL)가 migration·schema_test 양쪽 동일.
- **트랜잭션 경계:** confirm이 `self._transaction()` 안에서 `insert_correction`→`build_training_pairs`→`insert_training_pairs`(connection()로 바인딩 conn 합류) 호출. 한 tx 원자성 보장. 레이싱 롤백 테스트는 conflict가 그 이전에 발생해 영향 없음(검증).
- **Task 의존 순서:** Task1(핸들러)→Task7/9(검증), Task2(스키마)→Task3~9, Task3(순수함수)→Task4, Task4/5(repo+main 등록)→Task6~9 확장. 전부 단조 순차, 역방향 의존 없음.
- **범위:** 신규 slice 1개(router+service+repository+schema 4종) + 6 엔드포인트, ~16 파일. plan-eng의 "8파일/2서비스 초과 = 스멜" 트리거에 걸리지만, 기존 invoices/ocr 등과 **동형 패턴**이라 우발적 복잡도 아님. 과설계 징후 없음(YAGNI: 라벨 그룹 일괄병합 뷰는 의도적 후속 보류). 범위 적정.
- **SSOT/데이터 경계:** 경로·DB명 env(`SJMJ_DATA_DIR`/`DB_*`)에서만. 이미지 path는 정수 path param으로 서버 조립(path traversal 차단). api-spec SSoT 동기 task(11) + jq/개수 매칭 자가검증 게이트 포함.

---

## 리뷰 메서드 노트

- 코드 표본 검증은 추정이 아니라 실제 Read/Grep/python·jq 실행 결과에 근거함.
- 비대화형 subagent이므로 plan-eng-review의 AskUserQuestion 게이트는 생략하고 서면 리포트로 대체.
- 플랜 파일은 수정하지 않음(리뷰 전용). 수정은 후속 단계 담당.
