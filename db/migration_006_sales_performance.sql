-- Migration 006: 월별 영업사원 실적
-- 적용 순서: 005 → 006. 재실행 안전 (IF NOT EXISTS).
-- ROLLBACK:
--   DROP TABLE IF EXISTS sales_records;
--   DROP TABLE IF EXISTS salespeople;

CREATE TABLE IF NOT EXISTS salespeople (
  id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  sort_order INT NOT NULL DEFAULT 0,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_active_sort (is_active, sort_order)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

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
