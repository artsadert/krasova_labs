-- ==========================================================================
-- Лабораторная работа №3. Части 1-9: стратегии партиционирования.
-- ==========================================================================
DROP SCHEMA IF EXISTS krasova_lab3 CASCADE;
CREATE SCHEMA krasova_lab3;
SET search_path TO krasova_lab3;

-- --- Часть 1. RANGE по дате ------------------------------------------------
CREATE TABLE events (
    id         BIGINT      NOT NULL,
    user_id    BIGINT      NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    payload    TEXT,
    created_at TIMESTAMP   NOT NULL
) PARTITION BY RANGE (created_at);

CREATE TABLE events_2026_09_09 PARTITION OF events
    FOR VALUES FROM ('2026-09-09') TO ('2026-09-10');
CREATE TABLE events_2026_09_10 PARTITION OF events
    FOR VALUES FROM ('2026-09-10') TO ('2026-09-11');
CREATE TABLE events_2026_09_11 PARTITION OF events
    FOR VALUES FROM ('2026-09-11') TO ('2026-09-12');

-- --- Часть 3. RANGE по числовому значению ----------------------------------
CREATE TABLE products (
    id    BIGINT  NOT NULL,
    name  TEXT    NOT NULL,
    price NUMERIC NOT NULL
) PARTITION BY RANGE (price);

CREATE TABLE products_cheap     PARTITION OF products FOR VALUES FROM (0)    TO (100);
CREATE TABLE products_medium    PARTITION OF products FOR VALUES FROM (100)  TO (1000);
CREATE TABLE products_expensive PARTITION OF products FOR VALUES FROM (1000) TO (MAXVALUE);

-- --- Часть 4. LIST ---------------------------------------------------------
CREATE TABLE customers (
    id            BIGINT      NOT NULL,
    name          TEXT        NOT NULL,
    customer_type VARCHAR(30) NOT NULL
) PARTITION BY LIST (customer_type);

CREATE TABLE customers_b2c        PARTITION OF customers FOR VALUES IN ('B2C');
CREATE TABLE customers_b2b        PARTITION OF customers FOR VALUES IN ('B2B');
CREATE TABLE customers_enterprise PARTITION OF customers FOR VALUES IN ('Enterprise');

-- --- Часть 6. HASH ---------------------------------------------------------
CREATE TABLE user_events (
    id         BIGINT      NOT NULL,
    user_id    BIGINT      NOT NULL,
    event_type VARCHAR(50),
    created_at TIMESTAMP   NOT NULL
) PARTITION BY HASH (user_id);

CREATE TABLE user_events_0 PARTITION OF user_events FOR VALUES WITH (MODULUS 4, REMAINDER 0);
CREATE TABLE user_events_1 PARTITION OF user_events FOR VALUES WITH (MODULUS 4, REMAINDER 1);
CREATE TABLE user_events_2 PARTITION OF user_events FOR VALUES WITH (MODULUS 4, REMAINDER 2);
CREATE TABLE user_events_3 PARTITION OF user_events FOR VALUES WITH (MODULUS 4, REMAINDER 3);
