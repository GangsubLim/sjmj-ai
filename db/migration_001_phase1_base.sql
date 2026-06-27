-- migration_001_phase1_base.sql
-- Phase 1: 기본 구조 확장

-- invoices 테이블 확장
ALTER TABLE invoices
    ADD COLUMN memo TEXT AFTER vehicle_no,
    ADD COLUMN show_stamp BOOLEAN DEFAULT TRUE AFTER memo;

-- issuers 테이블 확장
ALTER TABLE issuers
    ADD COLUMN bank_account VARCHAR(100) AFTER fax,
    ADD COLUMN stamp_image_url VARCHAR(500) AFTER bank_account,
    ADD COLUMN tel_fax VARCHAR(50) AFTER stamp_image_url;

-- 기존 phone/fax 데이터로 tel_fax fallback 생성
-- CASE WHEN으로 NULL 조합 시 불완전한 '/' 또는 빈 문자열 방지
UPDATE issuers
SET tel_fax = CASE
    WHEN phone IS NOT NULL AND fax IS NOT NULL THEN CONCAT(phone, '/', fax)
    WHEN phone IS NOT NULL THEN phone
    WHEN fax IS NOT NULL THEN fax
    ELSE NULL
END
WHERE tel_fax IS NULL AND (phone IS NOT NULL OR fax IS NOT NULL);
