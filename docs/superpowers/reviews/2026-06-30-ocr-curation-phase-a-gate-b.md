# 게이트 B (범용 리뷰) — OCR 큐레이션 Phase A (PR #12)

- 대상 PR: #12 `feat/ocr-curation-phase-a` → `devel` (28파일, +5157/-295)
- 베이스 비교: `origin/devel...HEAD`, head `0c73114`
- 리뷰 방식: `code-review:code-review` 5-에이전트 팬아웃(CLAUDE.md 준수 / 얕은 버그 / git 히스토리 / 이전 PR 코멘트 / 코드 주석 준수) + 이슈별 0~100 채점
- 모드: 자동수정(사용자 '수정 보류' 미명시)
- 판정 기준 SSOT: 루트 `AGENTS.md`(외부 계약 불변식·3계층·env 경로·Pydantic 규칙), `apps/invoice-ocr/backend/AGENTS.md`, `.claude/rules/api-conventions.md`, `.claude/rules/common/*`
- 판정 방법론: `superpowers:receiving-code-review`(맹목 수용 금지, 코드 근거로 ACCEPT/PARTIAL/REJECT)

## 결과 요약

- 전체 findings(≥50 밴드 보유): 3건 (≥80: **0건** / 50~79: 3건). 그 외 LOW 2건은 <50으로 폐기.
- ≥80 (PR 코멘트 대상): 없음 → PR 본문 코멘트 없음.
- 분류: ACCEPT 1 / PARTIAL 0 / REJECT 2 (+ 폐기 2)
- 적용 fix: 1건 (문서 동기) — 커밋 SHA는 본 문서 하단 참조
- verification: 통과 (아래)

## 검증 (fix 적용 후 / fix는 문서 전용이라 Python 무영향)

```
cd apps/invoice-ocr/backend
uv run ruff check .            # All checks passed!
uv run ruff format --check .   # 87 files already formatted
uv run pytest --cov=app --cov-fail-under=80 -q
#   310 passed, 4 warnings
#   TOTAL coverage 97.55% (≥80 게이트 통과)
#   curation_repository.py 98% · curation_service.py 97% · routers/curation.py 100% · ocr_correction.py 100%
```

ml/ 변경(`infer_job.py`)은 `warped.png` 1줄 저장 추가뿐이고 paddle 무의존 경로가 아니어서 backend 검증으로 충분(게이트 A에서 이미 정합 확인됨).

## Findings (구조화 전체 리스트)

### B-1 — 신규 슬라이스의 전용 unit/repository 테스트 파일 부재 — REJECT (score 55, 50~79밴드)

- path: `apps/invoice-ocr/backend/tests/{unit,integration}/`
- line: n/a (파일 부재)
- severity: HIGH(제보자 주장) → 실측 후 MEDIUM 강등
- existing*code: 다른 슬라이스는 `test*<slice>_service.py`(unit, repo mock) + `test_<slice>\_repository.py`(integration, 실 MySQL)를 모두 보유. curation은 `contract/test_curation_routes.py`(428줄, 풀스택 실 MySQL) + `integration/test_curation_schema.py`(DDL) + `unit/test_ocr_correction.py`(순수함수) + `unit/test_errors_validation.py`만 보유. plan 요약 51줄은 `test_curation_repository.py`(신규)를 나열했으나 실제 task로 구현되지 않음.
- content: sibling 패턴과 plan 요약상으로는 전용 repository 통합 테스트가 예고되어 있다.
- 판정 근거(REJECT): 기능·커버리지 요건이 **이미 충족**된다. `curation_repository.py` 98%, `curation_service.py` 97%, router 100% — 이 커버리지는 실 MySQL을 때리는 contract 스위트가 router→service→repository 풀스택을 관통하며 달성한 것이라, 이 프로젝트에서 contract 테스트는 사실상 통합 테스트 역할을 겸한다. 전용 `test_curation_repository.py`를 별도 추가하면 동일 SQL을 중복 검증(DRY/YAGNI 위반)하고 churn만 늘린다. 80% 커버리지 게이트(CI 미러)가 회귀를 방어한다. 게이트 A·eng-review·receiving이 이 테스트 구성으로 통과한 점도 정합. → 미수정. (엄격한 구조 일치를 원하면 후속에서 얇은 repository 테스트 추가 가능하나 본 PR 블로커 아님.)

### B-2 — 슬라이스 전환 현황 표에 curation(첫 Pydantic) 미기록 — ACCEPT, 수정함 (score 70, 50~79밴드)

- path: `apps/invoice-ocr/backend/AGENTS.md`
- line: 54~66 (슬라이스 전환 현황 표)
- severity: MEDIUM
- existing_code: 표가 7개 기존 슬라이스를 모두 `Validator | 미전환`으로 나열하고, 바로 아래 `> 슬라이스를 Pydantic으로 전환하면 이 표의 "검증 방식"을 Pydantic으로 갱신한다.`라고 규정. curation은 레포 최초 Pydantic-네이티브 슬라이스(`CurationPairPatch` + `model_validator`, `RequestValidationError` 핸들러 도입)인데 표에 행이 없음.
- content: 표는 "어느 슬라이스가 어떤 검증 방식인지" 한눈에 보는 레지스트리다. 첫 Pydantic 슬라이스가 누락되면 다음 작업자가 Validator/Pydantic 혼용 여부를 오인한다. backend/AGENTS.md가 명시적으로 갱신을 요구.
- 판정 근거(ACCEPT): 저비용·무위험·정본 규칙이 명시적으로 요구하는 문서 동기. `| curation | Pydantic | 최초 전환 |` 행 추가로 해소. Python 무영향이라 검증 그대로 통과.

### B-3 — `build_training_pairs`의 `int(ref.rsplit("/row-",1)[-1])`가 형식 위반 crop_ref에서 ValueError — REJECT (score 50, 50~79밴드)

- path: `apps/invoice-ocr/backend/app/services/ocr_correction.py`
- line: 68
- severity: MEDIUM (Agent #2·#3 독립 2표)
- existing_code: `"row_index": int(ref.rsplit("/row-", 1)[-1]),` — `/row-` 구분자 없는 crop_ref면 `int()`가 ValueError → confirm `with self._transaction()` 롤백(500). migration_008 백필 SQL은 `CAST(SUBSTRING_INDEX(...,'/row-',-1) AS UNSIGNED)`로 0을 반환(관대)해 두 경로의 실패 모드가 다름.
- content: crop_ref는 ML 워커가 `job-{id}/row-{n}` 형식으로 생성하고, 이 함수는 `draft_by_ref`에 매칭된 ref(= 동일 result_json 출처)만 처리한다. 입력은 사용자 제어 밖.
- 판정 근거(REJECT): **게이트 A/receiving에서 이미 "row_index는 신뢰 입력"으로 확정(REJECT)된 사항.** crop_ref는 ML 워커 생성·내부 불변식이며 매칭된 ref만 통과하므로 형식 위반은 운영상 발생 불가. Agent #3이 제안한 "build_correction의 lines[]에 row_index 정수 직통 전달" 리팩터는 `correction_json` shape + 백필 SQL 소비자까지 동시 변경하는 **Phase A 범위 밖 확장**이고 receiving이 이미 거부한 영역이라 채택하지 않음. → 미수정(후속 정리 후보로만 기록).

### 폐기(<50, 밴드 미달 — 단순 docstring 흠집)

- `ocr_correction.py:7` `rows_added` docstring이 "crop_ref 없는 item"만 기술하나 코드의 else는 "ref는 있으나 미매칭"도 포함 — score 30, 정상 플로우 영향 없음. 폐기.
- `curation_service.py:105` `original_image` docstring "절대경로" 단언, 코드는 DB `image_path` 위임(실무상 항상 절대경로) — score 35. 폐기.

### 부정 결과(신호 없음)

- 보안: path traversal 방어 정상(이미지 경로는 job_id·row int + kind enum만으로 서버 조립, crop_ref 문자열 미신뢰). SQL 전부 파라미터 바인딩.
- 트랜잭션 원자성: confirm의 `insert_training_pairs`가 기존 `with self._transaction()` 블록에 합류 — invoice/link/correction/training_pairs 원자 커밋. 정상.
- envelope/에러 계약: 성공 envelope·에러 코드 체계·400 검증 status·`{필드:메시지}` details 보존. `RequestValidationError` 422→400 변환 핸들러 + 고정 테스트 도입(Pydantic 선결 인프라 충족).
- conftest `_ALL_TABLES`·schema_test.sql FK 순서: `SET FOREIGN_KEY_CHECKS=0`로 truncate 감싸 순서 무관 + CREATE는 부모 뒤 자식. 정상.
- api-spec.json 동기: curation 엔드포인트가 `paths` + `x-api-overview.endpoints` 양쪽 반영.
- 이전 PR 코멘트: 이 레포에 인라인 리뷰 코멘트 이력 자체가 없음 → 적용 가능한 선행 지적 없음.

## 게이트 A/5단계 기결정 사항 재확인(뒤집지 않음)

training_pairs FK CASCADE(spec §3.2) · 백필 운영전용 무테스트 · result_json 파싱 의도적 중복(receiving PARTIAL) · build_training_pairs row_index 신뢰입력(REJECT, 위 B-3) · 라인별 INSERT(plan 명시) · `_data_dir` DRY/config 우회(기존 ocr_service 패턴 답습, 이번 미수정) · errors.py loc 경로 보존(게이트 A 반영) · original=업로드포맷 media_type(게이트 A/Task11) — 모두 유지.
