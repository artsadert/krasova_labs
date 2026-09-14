DROP SCHEMA IF EXISTS krasova_lab2 CASCADE;
CREATE SCHEMA krasova_lab2;
SET search_path TO krasova_lab2;

CREATE TABLE events (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT      NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    payload    JSONB,
    created_at TIMESTAMP   NOT NULL
);
