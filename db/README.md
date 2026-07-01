# sjmj-ai DB 정본 (Phase 1A)

원본 SJMJ-Web `database/`의 스키마·마이그레이션을 정본화. 마이그레이션 도구는 기존
순번 `.sql` 관습 유지(Alembic 미도입 — KISS). 운영 데이터 덤프(실고객 PII)는 **레포에
커밋하지 않고** `SJMJ_DB_BACKUP` env 경로로 참조한다.

## 빈 DB 빌드 순서 (스키마 마이그레이션)

```
schema.sql
migration_001_phase1_base.sql
migration_002_company_item_extend.sql
migration_003_app_settings.sql
migration_004_company_fax_sms_target.sql
migration_005_vehicle_no_extend.sql
migration_006_sales_performance.sql
migration_add_deduction.sql
migration_add_recipient2.sql
migration_007_ml_seam.sql      ← Phase 1A 신규(ML 이음새: total_supply 인덱스 + ocr_jobs·ocr_corrections)
migration_008_curation_training_pairs.sql  ← 큐레이션 게이트(ocr_jobs.curation_reviewed) + training_pairs 학습 read-model; 백필 SQL은 JSON_TABLE(MySQL 8.0.4+ 필요)
```

## 일회성 데이터 마이그레이션 (빌드 순서 아님)

- `migration_poc_to_production.sql` — POC(kslim) 덤프를 정식 DB로 옮긴 **과거 1회성 데이터
  이전** 스크립트. `kslim_tmp` 임시 DB 적재를 전제하므로 빈 DB 빌드 순서에 포함하지 않는다.
  정본 보존 목적으로만 둔다(상단 주석에 당시 사용법 기록).

## ML 이음새 (Phase 1A가 남기고 Phase 2가 채움)

`migration_007_ml_seam.sql`이 자리만 만든다:

- `invoices.total_supply` 인덱스 — 사진↔DB GT 매칭키(공급가합, `grand_total` 아님) 조회 성능.
- `ocr_jobs` — 추론 잡 상태(빈 테이블). Phase 2 2B worker가 채움.
- `ocr_corrections` — 사람 교정 피드백(빈 테이블). Phase 2 2C/2D 피드백 루프가 채움.

## 검증

운영 덤프 적재 + 마이그레이션의 무결성(row count 일치 · FK 무결성 · 이음새 존재)은
`scripts/db-verify.sh`가 자동 검증한다.

```
SJMJ_DB_BACKUP=/path/to/db-2026-06-24-backup.sql scripts/db-verify.sh
```
