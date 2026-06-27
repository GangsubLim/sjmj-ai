-- Migration 007: ML 이음새 선반영 (Phase 1A — 자리만, 코드 없음)
-- 적용 순서: 006 → 007. 재실행 안전 (IF NOT EXISTS + 인덱스 존재 가드).
-- 목적: Phase 2(ML 연결)가 채울 빈 자리를 미리 둔다.
--   1) invoices.total_supply 매칭키 인덱스 (사진↔DB GT 매칭 조회 성능. grand_total 아님)
--   2) ocr_jobs        — 추론 잡 상태 (Phase 2 2B worker가 채움)
--   3) ocr_corrections — 사람 교정 피드백 (Phase 2 2C/2D 피드백 루프가 채움)
-- ROLLBACK:
--   DROP TABLE IF EXISTS ocr_corrections;
--   DROP TABLE IF EXISTS ocr_jobs;
--   ALTER TABLE invoices DROP INDEX idx_invoices_total_supply;

-- 1) total_supply 매칭키 인덱스 (현재 invoices 인덱스는 FK issuer_id뿐) — 재실행 안전 가드
SET @idx_exists := (
  SELECT COUNT(1) FROM information_schema.statistics
  WHERE table_schema = DATABASE()
    AND table_name = 'invoices'
    AND index_name = 'idx_invoices_total_supply'
);
SET @sql := IF(@idx_exists = 0,
  'ALTER TABLE invoices ADD INDEX idx_invoices_total_supply (total_supply)',
  'DO 0');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 2) 추론 잡 상태 (빈 자리)
CREATE TABLE IF NOT EXISTS ocr_jobs (
  id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  status VARCHAR(20) NOT NULL DEFAULT 'pending', -- pending/running/done/failed
  image_path VARCHAR(512),
  result_json JSON,                              -- 초안 JSON(품목 top-5 + 금액)
  invoice_id INT,                                -- 확정 시 연결되는 운영 invoice (nullable)
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_ocr_jobs_status (status),
  CONSTRAINT fk_ocr_jobs_invoice FOREIGN KEY (invoice_id)
    REFERENCES invoices(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3) 교정 피드백 (빈 자리)
CREATE TABLE IF NOT EXISTS ocr_corrections (
  id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  job_id INT UNSIGNED,             -- 어느 추론 잡의 교정인지
  invoice_id INT,                  -- 확정된 운영 invoice
  correction_json JSON,            -- 사람 교정 내용(행타입·라벨 교정)
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_ocr_corrections_job FOREIGN KEY (job_id)
    REFERENCES ocr_jobs(id) ON DELETE SET NULL,
  CONSTRAINT fk_ocr_corrections_invoice FOREIGN KEY (invoice_id)
    REFERENCES invoices(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
