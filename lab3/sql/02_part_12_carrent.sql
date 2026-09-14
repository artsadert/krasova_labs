-- ==========================================================================
-- Часть 12. Партиционирование собственной базы: CarRent, таблица rentals.
-- RANGE по created_at с помесячными партициями.
-- ==========================================================================
DROP SCHEMA IF EXISTS carrent_lab3 CASCADE;
CREATE SCHEMA carrent_lab3;
SET search_path TO carrent_lab3;

CREATE TABLE rentals (
    id          BIGSERIAL     NOT NULL,
    car_id      BIGINT        NOT NULL,
    user_id     BIGINT        NOT NULL,
    started_at  TIMESTAMP     NOT NULL,
    finished_at TIMESTAMP,
    created_at  TIMESTAMP     NOT NULL,
    updated_at  TIMESTAMP     NOT NULL,
    minute_fee  NUMERIC(8,2)  NOT NULL,
    -- В партиционированной таблице первичный ключ обязан включать ключ
    -- партиционирования, поэтому (id) превращается в (id, created_at).
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

CREATE TABLE rentals_2026_06 PARTITION OF rentals
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE rentals_2026_07 PARTITION OF rentals
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE rentals_2026_08 PARTITION OF rentals
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE rentals_2026_09 PARTITION OF rentals
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
