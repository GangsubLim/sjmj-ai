-- Migration 011: 큐레이션 게이트 해제 이력 (Issue #52, ADR 0004 보강)
-- 적용 순서: 010 → 011.
-- 목적: 잡의 "첫 검수 시각"을 잡 단위로 보존해 '미검수'와 '재검수 필요'를 가른다.
--       쌍 수정 시 training_pairs.reviewed_at 은 NULL 로 되돌리므로(사람 경로가
--       ml/tools/blank_crop_report.py 의 기계 경로와 같은 관례를 쓴다), 쌍만으로는
--       "한 번도 검수 안 한 잡"과 "검수됐다가 해제된 잡"을 구별할 수 없다.
--       curation_reviewed_at 은 해제 시에 지우지 않는다 — 3-state 판별식:
--         미검수      : curation_reviewed = 0 AND curation_reviewed_at IS NULL
--         재검수 필요 : curation_reviewed = 0 AND curation_reviewed_at IS NOT NULL
--         검수됨      : curation_reviewed = 1
--       신규 컬럼은 DATETIME이고 백필 소스 training_pairs.reviewed_at은 TIMESTAMP다 —
--       같은 서버·같은 tz 전제로 값을 그대로 옮기며, 화면에는 노출하지 않고 3-state
--       판별의 NULL 여부로만 쓰므로 tz 변환 불일치는 관측 가능한 영향이 없다.
-- ROLLBACK:
--   ALTER TABLE ocr_jobs DROP COLUMN curation_reviewed_at;
--   DELETE FROM schema_migrations WHERE filename='migration_011_curation_reviewed_at.sql';
--   (원장 행도 지워야 한다 — scripts/migrate-db.sh 는 schema_migrations.filename 존재
--    여부로 적용 완료를 판단해 건너뛰므로, 컬럼만 지우고 원장을 남기면 다음 배포에서
--    011 이 스킵돼 컬럼 없는 스키마 위로 후속 코드가 "migrate ok" 로 올라간다.)

SET @col_exists := (
  SELECT COUNT(1) FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'ocr_jobs'
    AND column_name = 'curation_reviewed_at'
);
SET @sql := IF(@col_exists = 0,
  'ALTER TABLE ocr_jobs ADD COLUMN curation_reviewed_at DATETIME NULL AFTER curation_reviewed',
  'DO 0');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 기존 데이터 1회 백필. 백필이 없으면 기존 검수완료 잡이 나중에 해제될 때 "미검수"로 오표시된다.
-- 대상을 curation_reviewed = 1 로 좁히지 않는다 — 기계 경로가 이미 게이트를 해제해 둔 잡
-- (curation_reviewed = 0 인데 일부 쌍에 reviewed_at 이 남아 있음)도 "재검수 필요"로 보여야 한다.
-- MIN(reviewed_at) 은 대상 쌍이 없으면 NULL 을 내므로 한 번도 검수되지 않은 잡은 스스로 걸러진다.
-- WHERE 가 이미 채워진 값을 건너뛰므로 재실행 멱등이다.
-- 백필은 updated_at을 보존한다 — j.updated_at = j.updated_at 자기대입으로 MySQL의
-- ON UPDATE CURRENT_TIMESTAMP(migration_007) 자동 갱신을 억제한다.
UPDATE ocr_jobs j SET
  curation_reviewed_at = (SELECT MIN(tp.reviewed_at) FROM training_pairs tp WHERE tp.job_id = j.id),
  j.updated_at = j.updated_at
 WHERE j.curation_reviewed_at IS NULL;
