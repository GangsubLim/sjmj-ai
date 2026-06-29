-- migration_002_company_item_extend.sql

-- company_suggestions 확장
ALTER TABLE company_suggestions
    ADD COLUMN contact_person VARCHAR(50) AFTER recipient2,
    ADD COLUMN phone VARCHAR(20) AFTER contact_person,
    ADD COLUMN address VARCHAR(255) AFTER phone,
    ADD COLUMN business_number VARCHAR(20) AFTER address,
    ADD COLUMN notes TEXT AFTER business_number,
    ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP AFTER notes;

-- item_suggestions 확장
ALTER TABLE item_suggestions
    ADD COLUMN default_unit_price INT DEFAULT 0 AFTER default_unit,
    ADD COLUMN category VARCHAR(50) AFTER default_unit_price,
    ADD COLUMN notes TEXT AFTER category,
    ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP AFTER notes;

-- 카테고리 인덱스
ALTER TABLE item_suggestions ADD INDEX idx_category (category);

-- last_used ON UPDATE CURRENT_TIMESTAMP 제거
-- PRD 의도: 거래명세서에서 사용될 때만 last_used 갱신 (PUT 수정 시 갱신 방지)
-- 참고: MySQL 5.7에서 MODIFY COLUMN은 기존 last_used 값을 보존함.
--       ON UPDATE 속성만 제거되고 데이터는 변경되지 않음.
ALTER TABLE company_suggestions
    MODIFY COLUMN last_used TIMESTAMP NULL DEFAULT NULL;
ALTER TABLE item_suggestions
    MODIFY COLUMN last_used TIMESTAMP NULL DEFAULT NULL;
