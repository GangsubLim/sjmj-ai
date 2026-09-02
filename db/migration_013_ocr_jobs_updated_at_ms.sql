-- Migration 013: ocr_jobs.updated_at 밀리초 해상도 (Issue #95-1, #84 후속)
-- 적용 순서: 012 → 013.
-- 목적: 큐레이션 낙관적 잠금의 세대 토큰 해상도를 초에서 밀리초로 올린다. 토큰 표현식은
--       CAST(UNIX_TIMESTAMP(updated_at) AS CHAR) 하나이며(app/repositories/
--       curation_repository.py 의 JOB_TOKEN_SQL 단일 소스), 초 해상도에서는 같은 초 안의
--       두 번째 쓰기가 토큰을 튀게 하지 못해 재처리 직후 열려 있던 옛 화면의 stale write 가
--       대조를 통과할 수 있다.
-- 무해성: 토큰은 프론트에 불투명 문자열 계약이라 값 모양(정수 → 소수 셋째 자리)이 바뀌어도
--       클라이언트는 무변경이다. 배포 직후 열려 있던 검수 화면의 옛 초 단위 토큰은 1 회
--       409 를 받고 새로고침으로 해소된다.
-- 배포 창: deploy.yml 은 migrate(:51) → 백엔드 재시작·health(:83) → ml-worker 재시작(:88)
--       순서라 이 ALTER 는 ml-worker 가동 중에 걸린다. 컬럼 타입 변경이라 온라인 DDL 이
--       INPLACE 로 내려가지 않는다(로컬 실측 9.6.0: ERROR 1846 — Cannot change column type
--       INPLACE. Try ALGORITHM=COPY) — 테이블 재작성 동안 ocr_jobs 쓰기가 막힌다. 행 수가
--       수백 규모라 수 초 이내이며 그 창을 수용한다.
-- 멱등: datetime_precision 가드로 이미 3 이면 DO 0 으로 빠진다. MODIFY 는 테이블 재작성이라
--       무가드 재실행 비용이 크다 — 원장(schema_migrations)이 사라지는 복구 경로가 실재한다.
-- 가드 축: datetime_precision · is_nullable · extra(on update) 3 축을 함께 본다. 목표 DDL 은
--       4 속성이라 정밀도 한 축만 보면 TIMESTAMP(3) NULL 처럼 부분 드리프트한 컬럼을 정상으로
--       오인해 영구 스킵된다. extra 는 부분 문자열로만 본다 — 표기가 버전마다 흔들린다
--       (로컬 9.6.0 실측: 'DEFAULT_GENERATED on update CURRENT_TIMESTAMP(3)').
--       LIKE 의 % 는 테스트 러너에서도 안전하다(_migration_sql.apply 가 파라미터 없이 원문을 넘긴다).
-- ROLLBACK:
--   ALTER TABLE ocr_jobs MODIFY updated_at TIMESTAMP NOT NULL
--     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP;
--   DELETE FROM schema_migrations WHERE filename='migration_013_ocr_jobs_updated_at_ms.sql';
--   (원장 행도 지워야 한다 — scripts/migrate-db.sh 는 schema_migrations.filename 존재
--    여부로 적용 완료를 판단해 건너뛰므로, 컬럼만 되돌리고 원장을 남기면 다음 배포에서
--    013 이 스킵된다.)
--   (강등은 lossy — 소수초는 되돌아오지 않는다. 애플리케이션은 정밀도에 의존하지 않아 기능
--    영향은 없으나 값 자체의 정확한 복구는 배포 전 백업 복원뿐이다.)

SET @is_ms := (
  SELECT COUNT(1) FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'ocr_jobs'
    AND column_name = 'updated_at'
    AND datetime_precision = 3
    AND is_nullable = 'NO'
    AND extra LIKE '%on update%'
);
SET @sql := IF(@is_ms = 0,
  'ALTER TABLE ocr_jobs MODIFY updated_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)',
  'DO 0');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
