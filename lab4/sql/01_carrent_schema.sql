-- Схема сервиса CarRent на Primary. На Replica она появится сама, через WAL.
DROP SCHEMA IF EXISTS carrent CASCADE;
CREATE SCHEMA carrent;
SET search_path TO carrent;

CREATE TABLE users (
    id         BIGSERIAL PRIMARY KEY,
    name       TEXT        NOT NULL,
    email      TEXT        NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE cars (
    id            BIGSERIAL PRIMARY KEY,
    model         TEXT         NOT NULL,
    license_plate TEXT         NOT NULL UNIQUE,
    minute_fee    NUMERIC(8,2) NOT NULL
);

CREATE TABLE rentals (
    id          BIGSERIAL   PRIMARY KEY,
    user_id     BIGINT      NOT NULL REFERENCES users(id),
    car_id      BIGINT      NOT NULL REFERENCES cars(id),
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    minute_fee  NUMERIC(8,2) NOT NULL
);

CREATE INDEX idx_rentals_user_created ON rentals (user_id, created_at DESC);
CREATE INDEX idx_rentals_created      ON rentals (created_at);

INSERT INTO users (name, email)
SELECT 'User ' || i, 'user' || i || '@carrent.test' FROM generate_series(1, 5000) AS i;

INSERT INTO cars (model, license_plate, minute_fee)
SELECT (ARRAY['Kia Rio','Lada Vesta','VW Polo','Skoda Rapid','BMW 320i'])[1 + (i % 5)],
       'A' || lpad(i::text, 4, '0') || 'BC',
       round((5 + random() * 20)::numeric, 2)
FROM generate_series(1, 1000) AS i;

INSERT INTO rentals (user_id, car_id, started_at, finished_at, created_at, minute_fee)
SELECT 1 + (random() * 4999)::int,
       1 + (random() * 999)::int,
       ts, ts + ((random() * 120)::int || ' minutes')::interval, ts,
       round((5 + random() * 20)::numeric, 2)
FROM (SELECT now() - ((random() * 90 * 86400)::int || ' seconds')::interval AS ts
      FROM generate_series(1, 200000)) g;

ANALYZE users; ANALYZE cars; ANALYZE rentals;
