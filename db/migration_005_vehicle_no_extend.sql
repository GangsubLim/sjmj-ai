-- migration_005_vehicle_no_extend.sql
-- 여러 차량번호 입력을 지원하기 위해 vehicle_no 컬럼 길이를 20 → 255로 확장

ALTER TABLE invoices
    MODIFY COLUMN vehicle_no VARCHAR(255);
