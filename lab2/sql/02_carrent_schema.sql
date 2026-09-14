-- Часть B. Сущность rentals из сервиса CarRent.
-- DDL воспроизводит storage/init.sql проекта: те же колонки и типы,
-- индексов, кроме первичного ключа, в исходной схеме нет.
DROP SCHEMA IF EXISTS carrent_lab2 CASCADE;
CREATE SCHEMA carrent_lab2;
SET search_path TO carrent_lab2;

CREATE TABLE rentals (
    id          BIGSERIAL PRIMARY KEY,
    car_id      BIGINT        NOT NULL,
    user_id     BIGINT        NOT NULL,
    started_at  TIMESTAMPTZ   NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ   NOT NULL DEFAULT now(),
    minute_fee  NUMERIC(8,2)  NOT NULL
);
