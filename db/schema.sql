-- SJMJ 거래명세서 데이터베이스 스키마

-- 발급자 정보 테이블
CREATE TABLE IF NOT EXISTS issuers (
    id INT PRIMARY KEY AUTO_INCREMENT,
    company_name VARCHAR(100) NOT NULL,
    representative VARCHAR(50),
    business_number VARCHAR(20),
    address VARCHAR(255),
    business_type VARCHAR(100),
    business_item VARCHAR(100),
    phone VARCHAR(20),
    fax VARCHAR(20),
    show_sjdojang BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- 거래명세서 메인 테이블
CREATE TABLE IF NOT EXISTS invoices (
    id INT PRIMARY KEY AUTO_INCREMENT,
    document_title VARCHAR(100) DEFAULT '거 래 명 세 서',
    issue_date DATE NOT NULL,
    recipient VARCHAR(100) NOT NULL,
    recipient2 VARCHAR(100),
    vehicle_no VARCHAR(255),
    issuer_id INT,
    total_supply INT DEFAULT 0,
    total_vat INT DEFAULT 0,
    grand_total INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (issuer_id) REFERENCES issuers(id) ON DELETE SET NULL
);

-- 거래명세서 품목 테이블
CREATE TABLE IF NOT EXISTS invoice_items (
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
    FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE,
    INDEX idx_invoice_items_order (invoice_id, item_order)
);

-- 회사 자동완성 목록 테이블
CREATE TABLE IF NOT EXISTS company_suggestions (
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
    last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 품목 자동완성 목록 테이블
CREATE TABLE IF NOT EXISTS item_suggestions (
    id INT PRIMARY KEY AUTO_INCREMENT,
    item_name VARCHAR(200) UNIQUE NOT NULL,
    default_unit VARCHAR(20),
    usage_count INT DEFAULT 0,
    last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- 기본 회사 데이터 삽입 (iOS 앱과 동일)
INSERT INTO company_suggestions (company_name) VALUES 
('한국강화')
ON DUPLICATE KEY UPDATE company_name = VALUES(company_name);

-- 기본 품목 데이터 삽입 (iOS 앱과 동일)
INSERT INTO item_suggestions (item_name, default_unit) VALUES 
('엔진오일', 'EA'),
('파워오일', 'EA'),
('파워오일휠터', 'EA'),
('브레이크오일', 'EA'),
('브레이크호수', 'EA'),
('드라이', 'EA'),
('드라이휠터', 'EA'),
('라이닝', 'EA'),
('라이트', 'EA'),
('에어', 'EA'),
('타이어', 'EA'),
('중고타이어', 'EA'),
('차압센서', 'EA'),
('항균필터', 'EA'),
('브러쉬', 'EA'),
('부동액', 'EA'),
('번호등', 'EA'),
('삼.디.스.센', 'EA'),
('얼라이먼트', 'EA'),
('볼트', 'EA'),
('너트', 'EA'),
('핸들조인트', 'EA'),
('탑부싱', 'EA'),
('벨트교환', 'EA'),
('쇼바', 'EA'),
('공임', 'EA'),
('링크대', 'EA'),
('엔도대', 'EA'),
('킹핀', 'EA'),
('베어링', 'EA'),
('도리까이', 'EA'),
('앗세이', 'EA'),
('구리스', 'EA'),
('구리스리데나', 'EA'),
('원터치', 'EA'),
('깔깔이', 'EA'),
('하부', 'EA')
ON DUPLICATE KEY UPDATE item_name = VALUES(item_name);

-- 기존 테이블에 recipient2 컬럼 추가 (이미 테이블이 존재하는 경우)
-- ALTER TABLE invoices ADD COLUMN IF NOT EXISTS recipient2 VARCHAR(100) AFTER recipient;
-- ALTER TABLE company_suggestions ADD COLUMN IF NOT EXISTS recipient2 VARCHAR(100) AFTER company_name;