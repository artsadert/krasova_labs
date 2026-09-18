-- Схема сервиса Triply-User на каждом шарде.
-- Взята из GORM-моделей internal/infrastructure/postgresrepo/models.go.
--
-- Shard key — user_id. По нему шардируются ВСЕ таблицы:
--   users.id, riders.user_id, drivers.user_id, vehicles.driver_id, ratings.ratee_id
-- то есть профиль водителя, его машина и полученные им оценки лежат вместе.

DROP TABLE IF EXISTS ratings, vehicles, drivers, riders, users CASCADE;

CREATE TABLE users (
    id         UUID        PRIMARY KEY,
    phone      TEXT        NOT NULL,
    email      TEXT,
    name       TEXT        NOT NULL,
    avatar_url TEXT,
    role       TEXT        NOT NULL CHECK (role IN ('rider','driver','admin')),
    rating     REAL        NOT NULL DEFAULT 0,
    trip_count INT         NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX idx_users_phone ON users (phone);

CREATE TABLE riders (
    user_id                   UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    default_payment_method_id UUID,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE drivers (
    user_id            UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    status             TEXT NOT NULL DEFAULT 'offline'
                            CHECK (status IN ('offline','available','on_trip')),
    licence_url        TEXT,
    background_checked BOOLEAN NOT NULL DEFAULT false,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Под запрос DriverRepository.List: WHERE status = ? ORDER BY created_at DESC
CREATE INDEX idx_drivers_status_created ON drivers (status, created_at DESC, user_id DESC);

CREATE TABLE vehicles (
    id           UUID PRIMARY KEY,
    driver_id    UUID NOT NULL REFERENCES drivers(user_id) ON DELETE CASCADE,
    make         TEXT NOT NULL,
    model        TEXT NOT NULL,
    year         INT,
    color        TEXT,
    plate_number TEXT NOT NULL,
    category     TEXT NOT NULL CHECK (category IN ('economy','comfort','xl')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX idx_vehicles_driver ON vehicles (driver_id);

CREATE TABLE ratings (
    id         UUID PRIMARY KEY,
    trip_id    UUID,
    rater_id   UUID NOT NULL,          -- кто оценил: может жить на ДРУГОМ шарде
    ratee_id   UUID NOT NULL,          -- кого оценили: это и есть shard key
    score      REAL NOT NULL,
    comment    TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Под RatingRepository.List: WHERE ratee_id = ? ORDER BY created_at DESC, id DESC
CREATE INDEX idx_ratings_ratee ON ratings (ratee_id, created_at DESC, id DESC);
CREATE INDEX idx_ratings_trip  ON ratings (trip_id);
