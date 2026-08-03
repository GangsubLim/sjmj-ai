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
-- POSIX 문자클래스 [[:space:]]는 백슬래시가 없어 .sql·Python·마크다운 어디서도 안전하다.
-- Python .strip()과의 일치 범위(2026-08-03 로컬 MySQL 9.6 실측, HEX 비교):
--   일치 — 0x09 0x0A 0x0B 0x0C 0x0D 0x20, 0x85(NEL), U+00A0(NBSP), U+1680,
--          U+2000..U+200A, U+2028, U+2029, U+202F, U+205F, U+3000.
--   불일치 — 0x1C..0x1F. Python .strip()은 지우지만 [[:space:]]는 남긴다
--          (ICU가 이들을 White_Space로 보지 않는다). 즉 '\x1c중고'를 이 문장은
--          '\x1c중고'로, 서비스는 '중고'로 넣는다.
--   그럼에도 이 불일치가 사전을 쪼개지는 않는다: 0x1C..0x1F는 두 collation 모두에서
--   무시 가능(ignorable)이라 유니크 인덱스가 '\x1c중고'와 '중고'를 같은 항목으로 보고,
--   '\x1c'만 있는 라벨은 위 <> '' 가드에서 ''과 같다고 판정돼 걸러진다.
--   0x1C..0x1F는 OCR 라벨에 실질적으로 나타나지 않으므로 정규식은 그대로 둔다 —
--   백슬래시 없는 리터럴 안전성이 이 잔여 오차보다 크다. 오차 자체는
--   tests/integration/test_migration_010_item_vocabulary.py가 고정한다.
-- (주의: '=' 비교로 검증하면 안 된다 — 0x1C..0x1F가 collation-ignorable이라 지워지지
--  않았는데도 같다고 나온다. HEX()로 결과 바이트를 직접 봐야 한다.)
--
-- COLLATE는 지우지 말 것 — 미래 대비가 아니라 이 문장의 동작을 결정한다.
-- 운영은 training_pairs(utf8mb4_unicode_ci)와 item_suggestions(utf8mb4_0900_ai_ci)가
-- 갈려 있고, SELECT DISTINCT가 곧 문자열 비교다. COLLATE가 없으면 DISTINCT는 소스 쪽
-- utf8mb4_unicode_ci로 중복을 제거하는데, 두 collation은 동치 판정이 달라 목적지
-- 유니크 인덱스가 보는 기준과 어긋난다. COLLATE를 붙여 DISTINCT의 비교 기준을
-- 목적지 유니크 인덱스와 일치시킨다.
-- 방향이 결과를 바꾼다(2026-08-03 실측): utf8mb4_unicode_ci는 UCA 4.0.0이라 보조평면
-- 문자에 가중치가 없어 '휠𠀀' = '휠𠀁'로 보지만, utf8mb4_0900_ai_ci는 둘을 구분한다.
-- 소스 기준(또는 COLLATE 누락)으로 DISTINCT하면 둘 중 하나가 조용히 사라지고,
-- 목적지 기준으로 DISTINCT하면 둘 다 등록된다 — 목적지 유니크 인덱스가 별개 항목으로
-- 보는 라벨이므로 후자가 맞다. 이 방향은
-- tests/integration/test_migration_010_item_vocabulary.py가 고정한다.
--
-- ROLLBACK 안내: 자동 롤백 불가(사람이 등록한 항목과 구분 표식을 두지 않는 것이 결정이다).
--   헤더의 실측 4건은 2026-07-31 스냅샷이라 실제 배포 시점의 삽입 집합과 다를 수 있다.
--   무엇이 들어갔는지는 아래 SQL로 사후 식별한다 — 이 문장이 넣은 행은 usage_count가
--   기본값 0이고 created_at이 원장 적용 시각 근처다:
--     SELECT id, item_name FROM item_suggestions
--     WHERE usage_count = 0
--       AND created_at BETWEEN
--             (SELECT applied_at FROM schema_migrations
--              WHERE filename = 'migration_010_sync_item_vocabulary.sql') - INTERVAL 1 MINUTE
--         AND (SELECT applied_at FROM schema_migrations
--              WHERE filename = 'migration_010_sync_item_vocabulary.sql') + INTERVAL 1 MINUTE;
--   읽는 컬럼의 출처: item_suggestions.created_at은 migration_002가 추가했고,
--   schema_migrations(filename PK, applied_at)는 scripts/migrate-db.sh가 만드는 원장이다.
--   창을 applied_at ± 1분으로 닫는 이유: 아래를 열어 두면 러너가 파일을 먼저 적용하고
--   원장 행을 나중에 넣어 created_at이 applied_at보다 근소하게 앞서는 경우를 놓친다.
--   위를 열어 두면(상한 없음) 적용 며칠 뒤에 판단할 때 그 사이 자동 등록됐거나 사람이
--   추가한 미사용 항목이 전부 목록에 들어와, 목록대로 지우면 무관한 사전 항목을 삭제한다.
--   원장 행이 없으면(미적용·원장 초기화) 서브쿼리가 NULL이라 조건 전체가 NULL이 되어
--   에러 없이 0행이 나온다 — 0행을 "지울 것이 없다"로 읽기 전에 원장 행 존재를 먼저 확인한다.
--   이 식별은 휴리스틱이다 — 같은 시각에 사람이 추가하고 아직 안 쓴 항목도 함께 잡힌다.
--   따라서 목록을 눈으로 확인한 뒤 품목 관리 화면에서 개별 삭제한다(또는 확인된 id로 DELETE).
--   원장 행은 지우지 말 것. scripts/migrate-db.sh는 원장에 행이 없는 파일을 다시 실행하므로
--   (같은 파일의 다른 행이 남아 있으면 "원장 비어 있음" 가드에도 걸리지 않는다), 항목 삭제와
--   원장 행 삭제를 함께 하면 다음 배포에서 이 문장이 재적용돼 방금 지운 품목이 되살아난다.
--   재실행 안전의 뜻은 "같은 행 집합으로 수렴"이지 "사람의 삭제를 존중"이 아니다.

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
