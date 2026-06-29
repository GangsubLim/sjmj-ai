-- migration_003_app_settings.sql

CREATE TABLE IF NOT EXISTS app_settings (
    id INT PRIMARY KEY AUTO_INCREMENT,
    setting_key VARCHAR(100) UNIQUE NOT NULL,
    setting_value TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- 기본 설정값 삽입
INSERT INTO app_settings (setting_key, setting_value) VALUES
    ('default_vat_rate', '0.1'),
    ('default_document_title', '거 래 명 세 서'),
    ('default_unit', 'EA'),
    ('pdf_filename_pattern', '거래명세서_{recipient}_{issue_date}')
ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value);
