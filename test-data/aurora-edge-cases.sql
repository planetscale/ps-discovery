-- Aurora edge-case schema for testing PS import readiness detection
-- Each table/object targets a specific import blocker or warning condition

CREATE DATABASE IF NOT EXISTS edge_cases;
USE edge_cases;

-- =============================================================
-- TABLES THAT SHOULD PASS IMPORT CHECKS
-- =============================================================

-- Clean InnoDB table with PK, utf8mb4
CREATE TABLE clean_orders (
    id BIGINT NOT NULL AUTO_INCREMENT,
    customer_id BIGINT NOT NULL,
    total DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    status ENUM('pending','shipped','delivered','cancelled') NOT NULL DEFAULT 'pending',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX idx_customer (customer_id),
    INDEX idx_status_created (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Clean table with composite PK
CREATE TABLE order_items (
    order_id BIGINT NOT NULL,
    item_seq INT NOT NULL,
    product_id BIGINT NOT NULL,
    quantity INT NOT NULL DEFAULT 1,
    unit_price DECIMAL(10,2) NOT NULL,
    PRIMARY KEY (order_id, item_seq),
    INDEX idx_product (product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Table with unique key but no explicit PK (InnoDB will use it as clustered key)
CREATE TABLE products (
    sku VARCHAR(50) NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    price DECIMAL(10,2),
    UNIQUE KEY uk_sku (sku)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- =============================================================
-- TABLES THAT SHOULD TRIGGER IMPORT WARNINGS/BLOCKERS
-- =============================================================

-- BLOCKER: Table without any unique or primary key
CREATE TABLE event_log (
    event_type VARCHAR(100),
    payload JSON,
    logged_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_type (event_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- BLOCKER: MyISAM storage engine (not supported by PS import)
CREATE TABLE legacy_sessions (
    session_id VARCHAR(128) NOT NULL,
    data MEDIUMBLOB,
    expires INT UNSIGNED NOT NULL,
    PRIMARY KEY (session_id)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4;

-- BLOCKER: MEMORY engine
CREATE TABLE cache_hot (
    cache_key VARCHAR(255) NOT NULL,
    cache_val VARCHAR(4000),
    PRIMARY KEY (cache_key)
) ENGINE=MEMORY DEFAULT CHARSET=utf8mb4;

-- WARNING: Unsupported charset (latin2)
CREATE TABLE legacy_addresses (
    id INT NOT NULL AUTO_INCREMENT,
    street VARCHAR(255),
    city VARCHAR(100),
    postal_code VARCHAR(20),
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=latin2;

-- WARNING: Foreign key constraints (import will be slower, no table subset, no resume)
CREATE TABLE customers (
    id BIGINT NOT NULL AUTO_INCREMENT,
    email VARCHAR(255) NOT NULL,
    name VARCHAR(255),
    PRIMARY KEY (id),
    UNIQUE KEY uk_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE customer_orders (
    id BIGINT NOT NULL AUTO_INCREMENT,
    customer_id BIGINT NOT NULL,
    amount DECIMAL(10,2),
    PRIMARY KEY (id),
    CONSTRAINT fk_customer FOREIGN KEY (customer_id)
        REFERENCES customers(id) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- =============================================================
-- OBJECTS THAT NEED SPECIAL HANDLING
-- =============================================================

-- Views (not imported, must be recreated manually)
CREATE VIEW v_active_orders AS
    SELECT o.id, o.customer_id, o.total, o.status, o.created_at
    FROM clean_orders o
    WHERE o.status IN ('pending', 'shipped');

CREATE VIEW v_order_summary AS
    SELECT customer_id, COUNT(*) AS order_count, SUM(total) AS total_spent
    FROM clean_orders
    GROUP BY customer_id;

-- Stored procedure
DELIMITER //
CREATE PROCEDURE sp_purge_old_events(IN days_old INT)
BEGIN
    DELETE FROM event_log WHERE logged_at < DATE_SUB(NOW(), INTERVAL days_old DAY);
END //
DELIMITER ;

-- Trigger
CREATE TRIGGER trg_orders_updated
    BEFORE UPDATE ON clean_orders
    FOR EACH ROW
    SET NEW.updated_at = CURRENT_TIMESTAMP;

-- =============================================================
-- FEATURE DETECTION TARGETS
-- =============================================================

-- Full-text index
CREATE TABLE articles (
    id INT NOT NULL AUTO_INCREMENT,
    title VARCHAR(255) NOT NULL,
    body LONGTEXT,
    PRIMARY KEY (id),
    FULLTEXT INDEX ft_content (title, body)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Geospatial column
CREATE TABLE locations (
    id INT NOT NULL AUTO_INCREMENT,
    name VARCHAR(255),
    coords POINT NOT NULL SRID 4326,
    PRIMARY KEY (id),
    SPATIAL INDEX idx_coords (coords)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Partitioned table
CREATE TABLE metrics (
    id BIGINT NOT NULL AUTO_INCREMENT,
    metric_name VARCHAR(100) NOT NULL,
    value DOUBLE NOT NULL,
    recorded_at DATE NOT NULL,
    PRIMARY KEY (id, recorded_at),
    INDEX idx_name_date (metric_name, recorded_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
PARTITION BY RANGE (YEAR(recorded_at)) (
    PARTITION p2024 VALUES LESS THAN (2025),
    PARTITION p2025 VALUES LESS THAN (2026),
    PARTITION p2026 VALUES LESS THAN (2027),
    PARTITION pmax VALUES LESS THAN MAXVALUE
);

-- Generated/virtual column
CREATE TABLE invoices (
    id BIGINT NOT NULL AUTO_INCREMENT,
    subtotal DECIMAL(10,2) NOT NULL,
    tax_rate DECIMAL(5,4) NOT NULL DEFAULT 0.0800,
    tax_amount DECIMAL(10,2) AS (subtotal * tax_rate) STORED,
    total DECIMAL(10,2) AS (subtotal + (subtotal * tax_rate)) VIRTUAL,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- JSON column with generated index
CREATE TABLE user_profiles (
    id BIGINT NOT NULL AUTO_INCREMENT,
    profile JSON NOT NULL,
    email VARCHAR(255) AS (JSON_UNQUOTE(JSON_EXTRACT(profile, '$.email'))) STORED,
    PRIMARY KEY (id),
    INDEX idx_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- =============================================================
-- SEED DATA
-- =============================================================

-- Customers (needed for FK tests)
INSERT INTO customers (email, name) VALUES
    ('alice@example.com', 'Alice Smith'),
    ('bob@example.com', 'Bob Jones'),
    ('carol@example.com', 'Carol Williams');

-- Orders with FK
INSERT INTO customer_orders (customer_id, amount) VALUES
    (1, 99.99), (1, 149.50), (2, 200.00), (3, 50.00);

-- Clean orders
INSERT INTO clean_orders (customer_id, total, status) VALUES
    (1, 99.99, 'shipped'), (1, 149.50, 'delivered'),
    (2, 200.00, 'pending'), (3, 50.00, 'cancelled');

-- Order items
INSERT INTO order_items (order_id, item_seq, product_id, quantity, unit_price) VALUES
    (1, 1, 100, 2, 49.99), (1, 2, 101, 1, 0.01),
    (2, 1, 102, 1, 149.50), (3, 1, 100, 4, 50.00);

-- Products
INSERT INTO products (sku, name, description, price) VALUES
    ('SKU-100', 'Widget', 'A standard widget', 49.99),
    ('SKU-101', 'Gasket', 'A small gasket', 0.01),
    ('SKU-102', 'Gizmo', 'A premium gizmo', 149.50);

-- Event log (no PK table)
INSERT INTO event_log (event_type, payload) VALUES
    ('page_view', '{"page": "/home"}'),
    ('click', '{"button": "signup"}'),
    ('page_view', '{"page": "/pricing"}');

-- Articles (fulltext)
INSERT INTO articles (title, body) VALUES
    ('Getting Started with PlanetScale', 'PlanetScale is a MySQL-compatible serverless database...'),
    ('Vitess Sharding Explained', 'Vitess uses vindexes to determine shard placement...');

-- Locations (spatial)
INSERT INTO locations (name, coords) VALUES
    ('San Francisco', ST_GeomFromText('POINT(37.7749 -122.4194)', 4326)),
    ('New York', ST_GeomFromText('POINT(40.7128 -74.0060)', 4326));

-- Metrics (partitioned)
INSERT INTO metrics (metric_name, value, recorded_at) VALUES
    ('cpu_usage', 45.2, '2024-06-15'),
    ('cpu_usage', 78.9, '2025-01-20'),
    ('memory_mb', 8192, '2026-03-01');

-- Invoices (generated columns)
INSERT INTO invoices (subtotal, tax_rate) VALUES
    (100.00, 0.0800), (250.00, 0.1000), (50.00, 0.0000);

-- User profiles (JSON + generated)
INSERT INTO user_profiles (profile) VALUES
    ('{"email": "alice@example.com", "plan": "pro", "features": ["sso", "audit"]}'),
    ('{"email": "bob@example.com", "plan": "free"}');
