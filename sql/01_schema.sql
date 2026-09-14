DROP SCHEMA IF EXISTS krasova_lab1 CASCADE;
CREATE SCHEMA krasova_lab1;
SET search_path TO krasova_lab1;

CREATE TABLE orders (
    id          BIGSERIAL PRIMARY KEY,
    user_id     INTEGER        NOT NULL,
    product_id  INTEGER        NOT NULL,
    status      VARCHAR(20)    NOT NULL,
    amount      NUMERIC(10, 2) NOT NULL,
    created_at  TIMESTAMP      NOT NULL,
    updated_at  TIMESTAMP      NOT NULL
);
