-- ============================================================
-- POC(kslim) → 정식 버전(SJMJ-Web) 데이터 마이그레이션
-- ============================================================
--
-- 사용법:
--   Step 1: 정식 DB 스키마 생성
--     mysql -u root -e "CREATE DATABASE sjmj CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
--     mysql -u root sjmj < database/schema.sql
--     mysql -u root sjmj < database/migration_001_phase1_base.sql
--     mysql -u root sjmj < database/migration_002_company_item_extend.sql
--     mysql -u root sjmj < database/migration_003_app_settings.sql
--
--   Step 2: POC 덤프를 임시 DB에 로드
--     mysql -u root -e "CREATE DATABASE kslim_tmp CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
--     mysql -u root kslim_tmp < database/kslim-2.sql
--
--   Step 3: 마이그레이션 실행
--     mysql -u root sjmj < database/migration_poc_to_production.sql
--
--   Step 4: 임시 DB 삭제
--     mysql -u root -e "DROP DATABASE kslim_tmp;"
--
-- 변환 내역:
--   - invoice_items.deduction 컬럼 제외 (정식 버전에서 미사용)
--   - invoices에 없는 invoice_id를 참조하는 고아 invoice_items 제외
--   - issuers: POC에서 비어있으므로 스킵
--   - invoices.memo → NULL, invoices.show_stamp → TRUE (기본값)
--   - company_suggestions 확장 컬럼 → NULL (기본값)
--   - item_suggestions 확장 컬럼 → NULL/0 (기본값)
--   - app_settings: migration_003에서 이미 생성됨
-- ============================================================

SET NAMES utf8mb4;
START TRANSACTION;
SET FOREIGN_KEY_CHECKS = 0;

-- --------------------------------------------------------
-- 1. 기존 시드 데이터 정리
--    schema.sql이 삽입한 기본 데이터를 제거하고 POC 실데이터로 교체
-- --------------------------------------------------------
DELETE FROM invoice_items;
DELETE FROM invoices;
DELETE FROM company_suggestions;
DELETE FROM item_suggestions;

-- --------------------------------------------------------
-- 2. company_suggestions (22건)
--    POC 컬럼: id, company_name, recipient2, usage_count, last_used
--    정식 추가 컬럼: contact_person, phone, address, business_number, notes, created_at → NULL
-- --------------------------------------------------------
INSERT INTO company_suggestions (id, company_name, recipient2, usage_count, last_used)
SELECT id, company_name, recipient2, usage_count, last_used
FROM kslim_tmp.company_suggestions;

-- --------------------------------------------------------
-- 3. item_suggestions (59건)
--    POC 컬럼: id, item_name, default_unit, usage_count, last_used
--    정식 추가 컬럼: default_unit_price, category, notes, created_at → NULL/0
-- --------------------------------------------------------
INSERT INTO item_suggestions (id, item_name, default_unit, usage_count, last_used)
SELECT id, item_name, default_unit, usage_count, last_used
FROM kslim_tmp.item_suggestions;

-- --------------------------------------------------------
-- 4. invoices (~258건)
--    POC 컬럼: 기본 12개 컬럼 (issuer_id는 전부 NULL)
--    정식 추가 컬럼: memo → NULL, show_stamp → TRUE (기본값)
-- --------------------------------------------------------
INSERT INTO invoices (id, document_title, issue_date, recipient, recipient2, vehicle_no,
                      issuer_id, total_supply, total_vat, grand_total, created_at, updated_at)
SELECT id, document_title, issue_date, recipient, recipient2, vehicle_no,
       issuer_id, total_supply, total_vat, grand_total, created_at, updated_at
FROM kslim_tmp.invoices;

-- --------------------------------------------------------
-- 5. invoice_items (고아 레코드 제외, deduction 컬럼 제외)
--    POC에서 invoice_id 1~10 등 invoices에 존재하지 않는 참조 → 제외
--    deduction 컬럼 → SELECT 목록에서 제외하여 자연 드랍
-- --------------------------------------------------------
INSERT INTO invoice_items (id, invoice_id, item_order, name, quantity, unit,
                           unit_price, supply, vat, total)
SELECT t.id, t.invoice_id, t.item_order, t.name, t.quantity, t.unit,
       t.unit_price, t.supply, t.vat, t.total
FROM kslim_tmp.invoice_items t
WHERE EXISTS (SELECT 1 FROM invoices i WHERE i.id = t.invoice_id);

SET FOREIGN_KEY_CHECKS = 1;
COMMIT;

-- ============================================================
-- 검증 쿼리
-- ============================================================

-- 테이블별 건수 비교
SELECT '=== 마이그레이션 결과 ===' AS '';

SELECT 'company_suggestions' AS 테이블,
       (SELECT COUNT(*) FROM kslim_tmp.company_suggestions) AS 'POC 원본',
       COUNT(*) AS '정식 이관'
FROM company_suggestions
UNION ALL
SELECT 'item_suggestions',
       (SELECT COUNT(*) FROM kslim_tmp.item_suggestions),
       COUNT(*)
FROM item_suggestions
UNION ALL
SELECT 'invoices',
       (SELECT COUNT(*) FROM kslim_tmp.invoices),
       COUNT(*)
FROM invoices
UNION ALL
SELECT 'invoice_items',
       (SELECT COUNT(*) FROM kslim_tmp.invoice_items),
       COUNT(*)
FROM invoice_items;

-- 제외된 고아 레코드 상세
SELECT '=== 제외된 고아 invoice_items ===' AS '';

SELECT t.id, t.invoice_id, t.name
FROM kslim_tmp.invoice_items t
WHERE NOT EXISTS (SELECT 1 FROM kslim_tmp.invoices i WHERE i.id = t.invoice_id)
ORDER BY t.invoice_id, t.item_order;

-- FK 무결성 검증
SELECT '=== FK 무결성 검증 ===' AS '';

SELECT 'invoice_items → invoices' AS '관계',
       COUNT(*) AS '무결성 위반 건수'
FROM invoice_items ii
LEFT JOIN invoices i ON ii.invoice_id = i.id
WHERE i.id IS NULL;

-- issuers 상태 확인
SELECT '=== issuers 상태 ===' AS '';

SELECT COUNT(*) AS 'issuers 등록 건수 (설정 화면에서 입력 필요)' FROM issuers;

SELECT COUNT(*) AS 'issuer_id NULL인 invoices (발행자 미연결)'
FROM invoices WHERE issuer_id IS NULL;
