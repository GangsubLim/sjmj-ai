-- Migration 010: 기존 발산분 일괄 등록 — 정식 라벨 → 자동완성 사전 (ADR 0008, Issue #40)
-- 적용 순서: 009 → 010.
-- 목적: 검수완료 잡의 included 정식 라벨 중 자동완성 사전에 없는 것을 등록한다.
--       대상 조건은 CurationService.mark_reviewed의 등록 조건과 같다(런북 진단 SQL도 동일 조건).
--       실측(2026-07-31 운영 DB) 기준 신규 등록은 4건: 휠 / 중고 / 라이닝1조 / 배선수리.
-- 재실행 안전: ON DUPLICATE KEY UPDATE 자기대입이라 2회 실행해도 행 집합이 같다.
--
-- ON DUPLICATE KEY UPDATE의 id는 반드시 테이블명으로 한정한다 — INSERT … SELECT에서는
-- 소스 테이블(training_pairs·ocr_jobs)에도 id가 있어 한정 없는 `id = id`는
-- ERROR 1052 (Column 'id' in field list is ambiguous)로 실패한다. migration_008도 같은 형태다.
--
-- 정규화는 TRIM()이 아니라 REGEXP_REPLACE(…, '^[[:space:]]+|[[:space:]]+$', '')를 쓴다.
-- MySQL TRIM()은 ASCII 스페이스(0x20)만 지우고, 서비스(CurationService._register_label)의
-- Python .strip()은 탭·개행·U+3000·NBSP까지 지운다. TRIM()을 쓰면 (a) 라벨 "\t중고"가
-- 사전에 "\t중고"로 들어가 서비스가 넣는 "중고"와 두 항목으로 갈리고, (b) 탭만 있는 라벨이
-- TRIM 후에도 빈 문자열이 아니어서 공백뿐인 항목이 사전에 등록된다.
-- (로컬 sjmj_test 재현, 2026-08-03: 원안은 ['\t', '\t중고', '　휠', '라이닝1조']를 등록했다.)
--
-- 정규식에 '\s'를 쓰지 않는다. MySQL 문자열 리터럴이 백슬래시를 먼저 소비해
-- '^\s+|\s+$'는 '^s+|s+$'가 되고 라벨 앞뒤의 알파벳 s를 지운다(재현 확인).
-- POSIX 문자클래스 [[:space:]]는 백슬래시가 없어 .sql·Python·마크다운 어디서도 안전하고,
-- 탭·개행·U+3000·NBSP·0x0B·0x0C·0x1C에서 Python .strip()과 동일 결과를 낸다(실측).
--
-- COLLATE는 지우지 말 것 — 미래 대비가 아니라 이 문장의 동작을 결정한다.
-- 운영은 training_pairs(utf8mb4_unicode_ci)와 item_suggestions(utf8mb4_0900_ai_ci)가
-- 갈려 있고, SELECT DISTINCT가 곧 문자열 비교다. COLLATE가 없으면 DISTINCT는 소스 쪽
-- utf8mb4_unicode_ci로 중복을 제거하는데, 두 collation은 동치 판정이 달라 목적지
-- 유니크 인덱스가 보는 기준과 어긋난다. COLLATE를 붙여 DISTINCT의 비교 기준을
-- 목적지 유니크 인덱스와 일치시킨다.
--
-- ROLLBACK 안내: 자동 롤백 불가(사람이 등록한 항목과 구분 표식을 두지 않는 것이 결정이다).
--   헤더의 실측 4건은 2026-07-31 스냅샷이라 실제 배포 시점의 삽입 집합과 다를 수 있다.
--   무엇이 들어갔는지는 아래 SQL로 사후 식별한다 — 이 문장이 넣은 행은 usage_count가
--   기본값 0이고 created_at이 원장 적용 시각 근처다:
--     SELECT id, item_name FROM item_suggestions
--     WHERE usage_count = 0
--       AND created_at >= (SELECT applied_at FROM schema_migrations
--                          WHERE filename = 'migration_010_sync_item_vocabulary.sql') - INTERVAL 1 MINUTE;
--   읽는 컬럼의 출처: item_suggestions.created_at은 migration_002가 추가했고,
--   schema_migrations(filename PK, applied_at)는 scripts/migrate-db.sh가 만드는 원장이다.
--   INTERVAL 1 MINUTE를 빼는 이유: 러너가 파일을 먼저 적용하고 원장 행을 나중에 넣어
--   created_at이 applied_at보다 근소하게 앞설 수 있다.
--   이 식별은 휴리스틱이다 — 같은 시각에 사람이 추가하고 아직 안 쓴 항목도 함께 잡힌다.
--   따라서 목록을 눈으로 확인한 뒤 품목 관리 화면에서 개별 삭제한다(또는 확인된 id로 DELETE).
--   원장 행 제거가 필요하면 schema_migrations에서 filename이 이 파일명인 행을 지운다.

INSERT INTO item_suggestions (item_name, default_unit)
SELECT DISTINCT
       REGEXP_REPLACE(tp.canonical_label, '^[[:space:]]+|[[:space:]]+$', '')
         COLLATE utf8mb4_0900_ai_ci,
       'EA'
FROM training_pairs tp
JOIN ocr_jobs j ON j.id = tp.job_id AND j.curation_reviewed = 1
WHERE tp.status = 'included'
  AND REGEXP_REPLACE(COALESCE(tp.canonical_label, ''),
                     '^[[:space:]]+|[[:space:]]+$', '') <> ''
ON DUPLICATE KEY UPDATE item_suggestions.id = item_suggestions.id;
