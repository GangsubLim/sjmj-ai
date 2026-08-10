-- Migration 012: training_pairs.draft_supply — 확정 시점 초안 금액 스냅샷 (Issue #106)
-- 적용 순서: 011 → 012.
-- 목적: 재처리 승계 ②단계(draft 회수)의 앵커를 휘발성 ocr_jobs.result_json ⨝ row_index 조인에서
--       확정 시점 스냅샷 컬럼으로 옮긴다. 미결 전환(worker/db.commit_job ①)은 crop_ref만
--       orphan- 으로 옮기고 row_index 는 정렬 축이라 그대로 두는데, result_json 은 매 재처리마다
--       덮이므로 2회차부터 그 낡은 인덱스가 가리키는 것은 다른 행이다. 그래서 fetch_pairs 가
--       미결 쌍의 draft 를 통째로 버렸고(영구 봉인), 사람이 금액을 교정한 행은 ①로도 회수되지
--       않아 정상 엔진으로 재처리를 반복해도 미결이 줄지 않았다.
-- 백필 범위: 전량(정합 가드 통과분). 미결분만 채우면 오늘 ② 앵커가 정상 동작 중인 row- 형식 쌍이
--       NULL 로 남아 다음 재처리에서 ②가 통째로 죽는 순회귀가 된다.
-- 요구사항: JSON_TABLE 은 MySQL 8.0.4+ (migration_008 과 같은 전제). 다중 테이블 UPDATE 의
--       파생 테이블 JOIN 하한은 문헌 근거를 찾지 못해 명시하지 않는다 — 실측(2026-08-10)으로
--       운영 9.7.1 / 로컬 테스트 9.6.0 / CI mysql:8 임을 확인했고 셋 다 sql_mode 에
--       STRICT_TRANS_TABLES 를 포함한다.
-- 배포 후 창 B(마이그레이션 ~ 백엔드 재시작 사이): 그 수 분 동안 구 백엔드가 계속 서빙하고
--   그 INSERT 에는 draft_supply 가 없어 영구 NULL 이 된다. health check 통과(= 신버전 서빙)
--   확인 후 이 파일을 **통째로 1 회 더** 먹이면 닫힌다 — ALTER 는 가드로 DO 0 이 되고 백필은
--   WHERE draft_supply IS NULL 이라 앱이 쓴 값을 덮지 않는다(멱등). scripts/migrate-db.sh 는
--   schema_migrations 원장 때문에 012 를 건너뛰므로 러너가 아니라 mysql 로 직접 먹인다.
--   절차와 전후 대조 쿼리는 docs/runbooks/ocr-job-reprocessing.md 참조.
-- ROLLBACK:
--   ALTER TABLE training_pairs DROP COLUMN draft_supply;
--   DELETE FROM schema_migrations WHERE filename='migration_012_training_pairs_draft_supply.sql';
--   (원장 행도 지워야 한다 — scripts/migrate-db.sh 는 schema_migrations.filename 존재 여부로
--    적용 완료를 판단해 건너뛰므로, 컬럼만 지우고 원장을 남기면 다음 배포에서 012 가 스킵된다.)

SET @col_exists := (
  SELECT COUNT(1) FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'training_pairs'
    AND column_name = 'draft_supply'
);
SET @sql := IF(@col_exists = 0,
  'ALTER TABLE training_pairs ADD COLUMN draft_supply INT NULL AFTER draft_label',
  'DO 0');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 전량 백필 — 역산키 CONCAT('job-', job_id, '/row-', row_index) 로 확정 시점 line 을 찾고,
-- final_label · final_supply 가 쌍의 final_label · supply 와 일치할 때만(정합 가드) 싣는다.
-- 정합 가드는 유일키가 아니다 — 같은 명세서에 (final_label, final_supply) 가 동일한 행이 둘 이상인데
--   승계로 row_index 가 뒤바뀌면 가드를 통과하면서 다른 행의 초안이 실릴 수 있다. 라벨이 같아야
--   통과하므로 ②가 실제로 뒤바꿔도 전파되는 라벨은 동일하고, 갈릴 수 있는 것은 큐레이션에서
--   따로 고친 canonical_label 뿐이다(잔여 위험으로 수용).
-- job 당 ocr_corrections 1 행이 전제다 — DB 제약은 없고 OcrService.confirm 의 claim 3중
--   (claim_job → invoice_id 확정 검사 → link_invoice 0행 검사) 이 잡당 확정 1 회를 강제한다.
--   운영 실측(2026-08-10): 34 행 / 34 job, 중복 0. 2 행이 되면 어느 값이 실릴지는 미정의다
--   ("Each matching row is updated once, even if it matches the conditions multiple times").
--   아래 l.job_id = tp.job_id 는 그 비결정성을 없애지 못한다 — crop_ref 문자열만으로 잡 경계를
--   넘는 조합을 막는 명시적 방어이고, tp.job_id 의 인덱스도 함께 살린다.
-- collation: JSON_TABLE 출력은 utf8mb4_0900_ai_ci, training_pairs 는 utf8mb4_unicode_ci
--   (migration_008 이 명시) — 변환 없이 비교하면 ERROR 1267 로 죽는다.
-- 멱등: WHERE tp.draft_supply IS NULL 이 원장 밖 재실행에서도 앱이 쓴 값을 덮지 않게 한다.
--   DDL 은 auto-commit 이라 파일 단위 트랜잭션이 없으므로 멱등성이 원장의 보완 안전망이다.
-- 타입 가드 JSON_TYPE(draw) = 'INTEGER' 가 백엔드 _anchorable_supply 와 의미론을 맞춘다.
--   DECIMAL PATH 추출은 JSON 문자열·실수·bool 을 조용히 정수로 강제 변환한다
--   (실측 "120000"→120000, 1.5→2, true→1). 원시 타입을 JSON 컬럼으로 함께 뽑아 걸러야 백필과
--   신규 적재가 같은 값 집합만 싣는다. 한 번 들어간 거짓 앵커는 위 멱등 조건 때문에 재실행으로도
--   교정되지 않으므로 이 가드는 사후 복구가 아니라 사전 차단이어야 한다.
--   UNSIGNED INTEGER(>2^63) 는 타입 가드가 없어도 BETWEEN 이 거른다(실측: DECIMAL 추출이 음수).
-- 추출 타입이 DECIMAL(65,0) 인 이유: 초안 금액은 상한이 없어(handwriting/amount_read.parse_amount)
--   BIGINT PATH 로 뽑으면 추출 단계에서 범위를 넘길 수 있다. 넉넉히 뽑은 뒤 거르는 순서여야
--   가드가 실제로 동작한다(70 자리 값은 JSON 파서가 DOUBLE 로 읽어 타입 가드에서 먼저 걸린다).
UPDATE training_pairs tp
JOIN (
    SELECT c.job_id AS job_id,
           CONVERT(jt.cref USING utf8mb4) COLLATE utf8mb4_unicode_ci AS cref,
           CONVERT(jt.flab USING utf8mb4) COLLATE utf8mb4_unicode_ci AS flab,
           jt.fsup AS fsup,
           jt.dsup AS dsup,
           JSON_TYPE(jt.draw) AS dtype
    FROM ocr_corrections c,
         JSON_TABLE(c.correction_json, '$.lines[*]' COLUMNS (
             cref VARCHAR(64)   PATH '$.crop_ref',
             flab VARCHAR(200)  PATH '$.final_label',
             fsup DECIMAL(65,0) PATH '$.final_supply',
             dsup DECIMAL(65,0) PATH '$.draft_supply',
             draw JSON          PATH '$.draft_supply'
         )) jt
) l
  ON  l.job_id = tp.job_id
  AND l.cref = CONCAT('job-', tp.job_id, '/row-', tp.row_index)
  AND l.flab <=> tp.final_label
  AND l.fsup <=> tp.supply
SET tp.draft_supply = l.dsup
WHERE tp.draft_supply IS NULL
  AND l.dtype = 'INTEGER'
  AND l.dsup BETWEEN 0 AND 2147483647;
