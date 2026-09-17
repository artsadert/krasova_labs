-- Одна и та же таблица на каждом шарде: шарды независимы и симметричны.
-- Сервис CarRent, сущность rentals (аренды), shard key — user_id.
DROP TABLE IF EXISTS rentals;

CREATE TABLE rentals (
    id          BIGINT       PRIMARY KEY,
    user_id     BIGINT       NOT NULL,
    car_id      BIGINT       NOT NULL,
    started_at  TIMESTAMPTZ  NOT NULL,
    finished_at TIMESTAMPTZ,
    minute_fee  NUMERIC(8,2) NOT NULL
);

-- Запросы сервиса идут по shard key, поэтому индекс по нему обязателен
-- внутри каждого шарда: шардирование убирает лишние шарды, индекс — лишние строки.
CREATE INDEX idx_rentals_user ON rentals (user_id, started_at DESC);
