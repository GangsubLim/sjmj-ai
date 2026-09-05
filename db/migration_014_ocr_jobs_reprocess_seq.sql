-- Migration 014: ocr_jobs.reprocess_seq — 재처리 논리 세대 카운터 (S2 단계 기하)
-- 적용 순서: 013 → 014.
-- 목적: 워커가 crop_dir/geometry.json에 찍는 generation 스탬프의 진실원. 재처리가 실패하면
--       rollback_to_done이 옛 crop 디렉터리를 그대로 두므로(worker/poll.py 잡 격리 except),
--       이 컬럼이 없으면 증가한 세대에 이전 세대 기하가 조용히 붙는다(ADR 0012 · spec §6-2).
-- 증가 지점은 하나다: backend CurationRepository.requeue_for_reprocess의 status='pending'
--       전이와 **같은 UPDATE**. 워커 내부 재시도(worker/db.py의 requeue_for_reprocess ·
--       requeue_pending · requeue_stale_running)는 같은 사진·같은 엔진의 멱등 재실행이라
--       세대를 올리지 않는다 — 그 세 경로는 이 컬럼을 건드리지 않는다.
-- 백필: 없음. DEFAULT 0이 기존 행 전량을 세대 0으로 착지시킨다. 과거 잡에는 기하 파일 자체가
--       없어(백필 경로 없음 — ADR 0012 Consequences) 대조할 상대가 없다.
-- 컬럼 위치: AFTER 절을 쓰지 않는다 — 위치는 표시상 문제이고, 운영과 테스트 하니스의 물리
--       컬럼 순서가 이미 갈려 있을 수 있어 앵커 컬럼을 지목하면 이식성만 잃는다.
-- 멱등: information_schema 컬럼 존재 가드로 이미 있으면 DO 0으로 빠진다. 원장
--       (schema_migrations)이 사라지는 복구 경로가 실재한다(db/README.md).
-- ROLLBACK:
--   ALTER TABLE ocr_jobs DROP COLUMN reprocess_seq;
--   DELETE FROM schema_migrations WHERE filename='migration_014_ocr_jobs_reprocess_seq.sql';
--   (원장 행도 지워야 한다 — scripts/migrate-db.sh는 schema_migrations.filename 존재 여부로
--    적용 완료를 판단해 건너뛰므로, 컬럼만 지우고 원장을 남기면 다음 배포에서 014가 스킵된다.)
--   (강등은 lossy — 누적된 세대 수는 되돌아오지 않고, 그 시점의 geometry.json은 전량
--    generation 불일치(409)로 닫힌다.)

SET @col_exists := (
  SELECT COUNT(1) FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'ocr_jobs'
    AND column_name = 'reprocess_seq'
);
SET @sql := IF(@col_exists = 0,
  'ALTER TABLE ocr_jobs ADD COLUMN reprocess_seq INT NOT NULL DEFAULT 0',
  'DO 0');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
