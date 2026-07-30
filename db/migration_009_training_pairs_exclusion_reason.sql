-- Migration 009: training_pairs 배제 사유 축 (ADR 0006, Issue #38)
-- 적용 순서: 008 → 009.
-- 목적: 자동 배제(빈 크롭)와 사람 배제를 갈라 세기 위한 사유 컬럼 추가.
--       값 집합은 현재 'blank_crop' 하나뿐이며 기계만 채운다.
--       NULL 은 "사람 판정, 사유 미분류"를 뜻한다 — 비어 있음 자체가 사람 소유 표식이다.
-- ROLLBACK:
--   ALTER TABLE training_pairs DROP COLUMN exclusion_reason;
--   DELETE FROM schema_migrations WHERE filename='migration_009_training_pairs_exclusion_reason.sql';
--   (원장 행도 지워야 한다 — scripts/migrate-db.sh는 schema_migrations.filename 존재 여부로
--    적용 완료를 판단해 건너뛰므로, 컬럼만 지우고 원장을 남기면 다음 배포에서 009가 스킵돼
--    컬럼 없는 스키마 위로 후속 코드가 "migrate ok"로 올라간다.)

SET @col_exists := (
  SELECT COUNT(1) FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'training_pairs'
    AND column_name = 'exclusion_reason'
);
SET @sql := IF(@col_exists = 0,
  'ALTER TABLE training_pairs ADD COLUMN exclusion_reason VARCHAR(32) NULL DEFAULT NULL AFTER status',
  'DO 0');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
