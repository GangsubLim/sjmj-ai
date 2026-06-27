-- deduction 필드 추가를 위한 마이그레이션 스크립트
-- 실행일: 2025-08-24

-- invoice_items 테이블에 deduction 컬럼 추가
ALTER TABLE invoice_items ADD deduction BOOLEAN DEFAULT FALSE AFTER total;

-- 추가된 컬럼 확인
-- DESCRIBE invoice_items;