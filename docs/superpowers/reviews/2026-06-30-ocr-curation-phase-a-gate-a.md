# 게이트 A — 룰셋 리뷰 (OCR 큐레이션 Phase A)

- **대상 브랜치:** `feat/ocr-curation-phase-a` (base `devel`, `devel..HEAD` 12 커밋)
- **리뷰 도구:** `/ocr-code-review:review --from devel --to HEAD` (파일별 read-only 리뷰어 팬아웃)
- **모드:** 자동수정 (ACCEPT/PARTIAL 직접 수정)
- **방법론:** `superpowers:receiving-code-review` — 각 finding을 ACCEPT/PARTIAL/REJECT로 판정
- **SSOT 기준:** 루트 `AGENTS.md`(외부 계약 불변식·3계층·스키마 동기·env 경로·Pydantic 전환), `.claude/rules/api-conventions.md`, `.claude/rules/common/*`
- **일자:** 2026-06-30

## 요약

- **리뷰 대상 파일:** 19건 (구현 11 + 테스트 6 + 스키마/마이그레이션/spec)
- **ocr findings:** 9건 (모두 medium — high 없음, low는 도구가 자체 폐기)
- **판정:** ACCEPT 4 / PARTIAL 1 / REJECT 4
- **verification:** ruff check 통과 · ruff format 통과 · pytest 310 passed · 커버리지 97.55% (게이트 80%)

## Finding 판정

| #   | 위치                                             | 내용                                                                                                                    | 판정        | 근거                                                                                                                                                                                          |
| --- | ------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------- | ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| F1  | `app/core/errors.py:55-58`                       | `loc[-1]`만 키로 쓰면 중첩/배열 body에서 필드 충돌·에러 유실                                                            | **ACCEPT**  | 공유 인프라(첫 Pydantic 슬라이스가 모든 후속 슬라이스에 제공). `"body"` 제외 loc 경로를 `.`로 join. details 문자열맵 계약 보존, 기존 테스트 3종 무파괴. 배열 loc 단위테스트 추가              |
| F2  | `app/repositories/curation_repository.py:24-27`  | 라인별 INSERT 루프 → executemany 권장                                                                                   | **REJECT**  | 5단계에서 라인별 INSERT로 확정(plan 명시). 내부 단일사용자·잡당 행수 소량 → 조기 최적화(KISS/YAGNI)                                                                                           |
| F3  | `app/schemas/curation.py:12`                     | `min_length=1`이 공백-only(`" "`) 라벨 미차단 → 학습데이터 오염                                                         | **ACCEPT**  | 시스템 경계 입력검증 규칙. `StringConstraints(strip_whitespace=True, min_length=1, max_length=200)`로 트림 후 검증. 공백-only→400 contract 테스트 추가. 기존 라벨 테스트값은 공백 없어 무영향 |
| F4  | `app/services/ocr_service.py:89-90`              | training_pairs 적재가 confirm 트랜잭션 안 → 보조기능 실패가 확정 전이                                                   | **REJECT**  | spec §3.2가 "같은 트랜잭션 머티리얼라이즈"로 의도 확정. 실패 트리거(crop_ref 파싱)는 신뢰입력으로 5단계 REJECT 기결. confirm은 단발(invoice_id claim+link 가드)이라 노출면 최소               |
| F5  | `db/migration_008...sql:48-71`                   | 백필 dedup 없음 → 재교정 시 latest-wins 비결정적                                                                        | **REJECT**  | confirm 단발(재확정 시 CONFLICT)이라 job당 ocr_corrections 1행, crop_ref에 job_id 박혀 전역 유니크 → 중복 라인 자체가 발생 불가. 전제(append 중복)가 코드와 불일치                            |
| F6  | `db/migration_008...sql:61`                      | `JSON_TABLE`은 MySQL 8.0.4+ 필요                                                                                        | **PARTIAL** | 백필은 운영 1회 수동 SQL(CI/테스트 미실행 — schema_test.sql엔 DDL만). 검증환경 MySQL 9.6 확인. 운영 적용 전 버전 확인 주석 추가(중복 불필요 사실도 함께 명시)                                 |
| F7  | `.claude/ai-context/api-spec.json:3418`          | `CurationPair`가 `job_id`+`top5` 모두 보유하나 어느 응답도 동시 반환 안 함(GET 상세=top5, PATCH=job_id) → spec 드리프트 | **ACCEPT**  | api-conventions 드리프트 차단 규칙. 구현 확인(service get_detail vs patch_pair). Invoice.items 선례대로 두 필드에 조건부 description 명시                                                     |
| F8  | `tests/contract/test_curation_routes.py:254`     | 멱등 단언이 TIMESTAMP 1초 해상도로 공허(가드 없어도 통과)                                                               | **ACCEPT**  | `WHERE reviewed_at IS NULL` 가드 실증 불가. row 0에 과거 sentinel(`2020-01-01`) 심고 review 후 불변 단언 → 가드 직접 입증                                                                     |
| F9  | `tests/contract/test_curation_routes.py:405,416` | path traversal 테스트의 `SECRET... not in res.content` 단언이 비-load-bearing                                           | **REJECT**  | 400 JSON envelope는 구조상 파일 바이트 미포함 → 무해한 belt-and-suspenders. 실제 방어(int/enum 강제→400+VALIDATION_ERROR)는 앞 두 단언이 입증. positive control 추가는 범위 확대              |

## 적용한 수정 (ACCEPT 4 + PARTIAL 1)

- `app/core/errors.py` — `_validation_error_handler` loc 경로 보존
- `app/schemas/curation.py` — `CanonicalLabel = Annotated[str, StringConstraints(strip_whitespace=...)]`, `Field` import 제거(고아)
- `.claude/ai-context/api-spec.json` — `CurationPair.job_id`/`top5` 조건부 description
- `db/migration_008_curation_training_pairs.sql` — JSON_TABLE 버전요건·dedup 불필요 주석
- `tests/unit/test_errors_validation.py` — 배열 loc 경로 보존 단위테스트 추가
- `tests/contract/test_curation_routes.py` — 멱등 테스트 sentinel 강화, 공백-only canonical_label 400 테스트 추가

## Verification (수정 후 재실행)

```
uv run ruff check .          → All checks passed!
uv run ruff format --check . → 87 files already formatted
uv run pytest --cov=app --cov-fail-under=80 -q
  → 310 passed, coverage 97.55%
jq empty api-spec.json       → JSON valid
```
