-- Migration 008: 큐레이션 게이트 + training_pairs read-model
-- 적용 순서: 007 → 008.
-- 목적: 라이브 교정(ocr_corrections)을 행-단위 학습 read-model(training_pairs)로 머티리얼라이즈,
--       ocr_jobs에 잡-단위 검수 게이트(curation_reviewed) 추가, 기존 교정 1회 백필.
-- ROLLBACK:
--   DROP TABLE IF EXISTS training_pairs;
--   ALTER TABLE ocr_jobs DROP COLUMN curation_reviewed;

-- 1) training_pairs (confirm된 행마다 1행, crop_ref 전역 유니크)
CREATE TABLE IF NOT EXISTS training_pairs (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    crop_ref VARCHAR(64) UNIQUE NOT NULL,            -- "job-42/row-0"
    job_id INT UNSIGNED NOT NULL,
    invoice_id INT,
    row_index INT NOT NULL,
    draft_label VARCHAR(200),                        -- 모델 top-1 (item_top5[0].label)
    final_label VARCHAR(200),                        -- confirm 시 사용자 입력명 (불변 스냅샷)
    canonical_label VARCHAR(200),                    -- 학습용 정규화 라벨 (기본 = final_label)
    supply INT,                                      -- 행 식별용 읽기전용 맥락
    status VARCHAR(16) NOT NULL DEFAULT 'included',  -- included | excluded
    reviewed_at TIMESTAMP NULL DEFAULT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_training_pairs_job (job_id),
    INDEX idx_training_pairs_canonical (canonical_label),
    INDEX idx_training_pairs_status (status),
    CONSTRAINT fk_training_pairs_job FOREIGN KEY (job_id)
        REFERENCES ocr_jobs(id) ON DELETE CASCADE,
    CONSTRAINT fk_training_pairs_invoice FOREIGN KEY (invoice_id)
        REFERENCES invoices(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 2) ocr_jobs 잡-단위 검수 게이트 (재실행 안전 가드)
SET @col_exists := (
  SELECT COUNT(1) FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'ocr_jobs'
    AND column_name = 'curation_reviewed'
);
SET @sql := IF(@col_exists = 0,
  'ALTER TABLE ocr_jobs ADD COLUMN curation_reviewed BOOLEAN NOT NULL DEFAULT FALSE',
  'DO 0');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 3) 기존 ocr_corrections.correction_json.lines[] → training_pairs 1회 백필
--    crop_ref UNIQUE + ON DUPLICATE KEY no-op 으로 재실행 멱등.
--    요구사항: JSON_TABLE 은 MySQL 8.0.4+ 에서만 동작. 적용 전 운영 MySQL 버전을 확인할 것.
--    (crop_ref 에 job_id 가 박혀 전역 유니크하고 confirm 은 1회뿐이라 중복 라인은 발생하지 않음 → dedup 불필요.)
INSERT INTO training_pairs
    (crop_ref, job_id, invoice_id, row_index, draft_label, final_label, canonical_label, supply, status)
SELECT
    jt.crop_ref,
    c.job_id,
    c.invoice_id,
    CAST(SUBSTRING_INDEX(jt.crop_ref, '/row-', -1) AS UNSIGNED),
    jt.draft_label,
    jt.final_label,
    jt.final_label,
    jt.final_supply,
    'included'
FROM ocr_corrections c
JOIN JSON_TABLE(
    c.correction_json, '$.lines[*]' COLUMNS (
        crop_ref VARCHAR(64) PATH '$.crop_ref',
        draft_label VARCHAR(200) PATH '$.draft_label',
        final_label VARCHAR(200) PATH '$.final_label',
        final_supply INT PATH '$.final_supply'
    )
) AS jt
WHERE jt.crop_ref IS NOT NULL
  AND c.job_id IS NOT NULL
ON DUPLICATE KEY UPDATE training_pairs.id = training_pairs.id;
