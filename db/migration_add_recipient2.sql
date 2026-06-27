-- recipient2 필드 추가를 위한 마이그레이션 스크립트
-- 실행일: 2025-08-24

-- invoices 테이블에 recipient2 컬럼 추가
ALTER TABLE invoices ADD COLUMN recipient2 VARCHAR(100) AFTER recipient;

-- company_suggestions 테이블에 recipient2 컬럼 추가  
ALTER TABLE company_suggestions ADD COLUMN recipient2 VARCHAR(100) AFTER company_name;

-- 추가된 컬럼 확인
-- DESCRIBE invoices;
-- DESCRIBE company_suggestions;