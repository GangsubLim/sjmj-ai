-- SJMJ 테스트 데이터베이스 통합 스키마
-- schema.sql + 모든 migration 파일을 하나로 통합

SET FOREIGN_KEY_CHECKS = 0;

DROP TABLE IF EXISTS training_pairs;
DROP TABLE IF EXISTS ocr_corrections;
DROP TABLE IF EXISTS ocr_jobs;
DROP TABLE IF EXISTS app_settings;
DROP TABLE IF EXISTS invoice_items;
DROP TABLE IF EXISTS invoices;
DROP TABLE IF EXISTS issuers;
DROP TABLE IF EXISTS company_suggestions;
DROP TABLE IF EXISTS item_suggestions;
DROP TABLE IF EXISTS sales_records;
DROP TABLE IF EXISTS salespeople;

SET FOREIGN_KEY_CHECKS = 1;

-- 발급자 정보 테이블 (schema.sql + migration_001)
CREATE TABLE issuers (
    id INT PRIMARY KEY AUTO_INCREMENT,
    company_name VARCHAR(100) NOT NULL,
    representative VARCHAR(50),
    business_number VARCHAR(20),
    address VARCHAR(255),
    business_type VARCHAR(100),
    business_item VARCHAR(100),
    phone VARCHAR(20),
    fax VARCHAR(20),
    bank_account VARCHAR(100),
    stamp_image_url VARCHAR(500),
    tel_fax VARCHAR(50),
    show_sjdojang BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 거래명세서 메인 테이블 (schema.sql + migration_001 + migration_add_recipient2)
CREATE TABLE invoices (
    id INT PRIMARY KEY AUTO_INCREMENT,
    document_title VARCHAR(100) DEFAULT '거 래 명 세 서',
    issue_date DATE NOT NULL,
    recipient VARCHAR(100) NOT NULL,
    recipient2 VARCHAR(100),
    vehicle_no VARCHAR(255),
    memo TEXT,
    show_stamp BOOLEAN DEFAULT TRUE,
    issuer_id INT,
    total_supply INT DEFAULT 0,
    total_vat INT DEFAULT 0,
    grand_total INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (issuer_id) REFERENCES issuers(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 거래명세서 품목 테이블 (schema.sql + migration_add_deduction)
CREATE TABLE invoice_items (
    id INT PRIMARY KEY AUTO_INCREMENT,
    invoice_id INT NOT NULL,
    item_order INT NOT NULL,
    name VARCHAR(200) NOT NULL,
    quantity INT DEFAULT 0,
    unit VARCHAR(20),
    unit_price INT DEFAULT 0,
    supply INT DEFAULT 0,
    vat INT DEFAULT 0,
    total INT DEFAULT 0,
    deduction BOOLEAN DEFAULT FALSE,
    FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE,
    INDEX idx_invoice_items_order (invoice_id, item_order)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 회사 자동완성 목록 테이블 (schema.sql + migration_add_recipient2 + migration_002)
CREATE TABLE company_suggestions (
    id INT PRIMARY KEY AUTO_INCREMENT,
    company_name VARCHAR(100) UNIQUE NOT NULL,
    recipient2 VARCHAR(100),
    phone VARCHAR(20),
    fax VARCHAR(20),
    sms_number_type VARCHAR(10) NOT NULL DEFAULT 'phone',
    address VARCHAR(255),
    business_number VARCHAR(20),
    notes TEXT,
    usage_count INT DEFAULT 0,
    last_used TIMESTAMP NULL DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 품목 자동완성 목록 테이블 (schema.sql + migration_002)
CREATE TABLE item_suggestions (
    id INT PRIMARY KEY AUTO_INCREMENT,
    item_name VARCHAR(200) UNIQUE NOT NULL,
    default_unit VARCHAR(20),
    default_unit_price INT DEFAULT 0,
    category VARCHAR(50),
    notes TEXT,
    usage_count INT DEFAULT 0,
    last_used TIMESTAMP NULL DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_category (category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 앱 설정 테이블 (migration_003)
CREATE TABLE app_settings (
    id INT PRIMARY KEY AUTO_INCREMENT,
    setting_key VARCHAR(100) UNIQUE NOT NULL,
    setting_value TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 기본 앱 설정 삽입
INSERT INTO app_settings (setting_key, setting_value) VALUES
    ('default_vat_rate', '0.1'),
    ('default_document_title', '거 래 명 세 서'),
    ('default_unit', 'EA'),
    ('pdf_filename_pattern', '거래명세서_{recipient}_{issue_date}');

-- 영업사원 테이블 (migration_006)
CREATE TABLE IF NOT EXISTS salespeople (
  id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  sort_order INT NOT NULL DEFAULT 0,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_active_sort (is_active, sort_order)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 영업 실적 테이블 (migration_006)
CREATE TABLE IF NOT EXISTS sales_records (
  id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  salesperson_id INT UNSIGNED NOT NULL,
  work_date DATE NOT NULL,
  quantity INT NOT NULL DEFAULT 0,
  snapshot_name VARCHAR(100) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_salesperson_date (salesperson_id, work_date),
  INDEX idx_work_date (work_date),
  CONSTRAINT fk_sales_records_salesperson FOREIGN KEY (salesperson_id)
    REFERENCES salespeople(id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 추론 잡 테이블 (migration_007_ml_seam)
CREATE TABLE ocr_jobs (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    image_path VARCHAR(512),
    result_json JSON,
    invoice_id INT,
    curation_reviewed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_ocr_jobs_status (status),
    CONSTRAINT fk_ocr_jobs_invoice FOREIGN KEY (invoice_id)
        REFERENCES invoices(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 교정 피드백 테이블 (migration_007_ml_seam)
CREATE TABLE ocr_corrections (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    job_id INT UNSIGNED,
    invoice_id INT,
    correction_json JSON,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_ocr_corrections_job FOREIGN KEY (job_id)
        REFERENCES ocr_jobs(id) ON DELETE SET NULL,
    CONSTRAINT fk_ocr_corrections_invoice FOREIGN KEY (invoice_id)
        REFERENCES invoices(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 학습 read-model 테이블 (migration_008)
CREATE TABLE training_pairs (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    crop_ref VARCHAR(64) UNIQUE NOT NULL,
    job_id INT UNSIGNED NOT NULL,
    invoice_id INT,
    row_index INT NOT NULL,
    draft_label VARCHAR(200),
    final_label VARCHAR(200),
    canonical_label VARCHAR(200),
    supply INT,
    status VARCHAR(16) NOT NULL DEFAULT 'included',
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
