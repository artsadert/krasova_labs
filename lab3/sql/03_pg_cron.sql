-- ==========================================================================
-- Создание партиций внутри СУБД: pg_cron + PL/pgSQL.
--
-- Зачем в базе, а не во внешнем планировщике: расписание переживает
-- перезапуск приложения, лежит в том же бэкапе, что и данные, и создание
-- партиции больше не зависит от того, жив ли контейнер с job.
--
-- Уведомления остаются снаружи: у PostgreSQL нет HTTP-клиента, поэтому
-- задание пишет инциденты в таблицу-outbox, а relay забирает их и шлёт
-- в Telegram.
-- ==========================================================================
CREATE SCHEMA IF NOT EXISTS partition_ops;

-- Журнал: что планировщик делал и когда.
CREATE TABLE IF NOT EXISTS partition_ops.run_log (
    id          BIGSERIAL PRIMARY KEY,
    ran_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    target      TEXT        NOT NULL,
    created     INTEGER     NOT NULL DEFAULT 0,
    missing     INTEGER     NOT NULL DEFAULT 0,
    details     TEXT
);

-- Очередь исходящих уведомлений: база не умеет в HTTP, поэтому складывает
-- сообщения сюда, а внешний relay их отправляет и помечает отправленными.
CREATE TABLE IF NOT EXISTS partition_ops.alert_outbox (
    id          BIGSERIAL PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    severity    TEXT        NOT NULL CHECK (severity IN ('CRITICAL', 'OK')),
    target      TEXT        NOT NULL,
    message     TEXT        NOT NULL,
    sent_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_alert_outbox_unsent
    ON partition_ops.alert_outbox (id) WHERE sent_at IS NULL;

-- Начало периода, которому принадлежит дата.
CREATE OR REPLACE FUNCTION partition_ops.period_start(p_day DATE, p_granularity TEXT)
RETURNS DATE LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE p_granularity
             WHEN 'day'   THEN p_day
             WHEN 'month' THEN date_trunc('month', p_day)::date
           END;
$$;

-- Сдвиг на n периодов вперёд.
CREATE OR REPLACE FUNCTION partition_ops.period_shift(p_start DATE, p_granularity TEXT, n INT)
RETURNS DATE LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE p_granularity
             WHEN 'day'   THEN p_start + (n || ' days')::interval
             WHEN 'month' THEN p_start + (n || ' months')::interval
           END::date;
$$;

-- Имя партиции для периода: events_2026_09_12 или rentals_2026_09.
CREATE OR REPLACE FUNCTION partition_ops.partition_name(p_table TEXT, p_start DATE, p_granularity TEXT)
RETURNS TEXT LANGUAGE sql IMMUTABLE AS $$
    SELECT p_table || '_' || to_char(p_start,
           CASE p_granularity WHEN 'day' THEN 'YYYY_MM_DD' ELSE 'YYYY_MM' END);
$$;

-- Каких партиций не хватает на горизонт.
CREATE OR REPLACE FUNCTION partition_ops.missing_partitions(
    p_schema TEXT, p_table TEXT, p_granularity TEXT, p_horizon INT,
    p_today DATE DEFAULT CURRENT_DATE)
RETURNS TABLE (period_start DATE, partition_name TEXT)
LANGUAGE sql STABLE AS $$
    SELECT s.period_start,
           partition_ops.partition_name(p_table, s.period_start, p_granularity)
    FROM (
        SELECT partition_ops.period_shift(
                   partition_ops.period_start(p_today, p_granularity),
                   p_granularity, i) AS period_start
        FROM generate_series(0, p_horizon) AS i
    ) s
    WHERE NOT EXISTS (
        SELECT 1 FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = p_schema
          AND c.relname = partition_ops.partition_name(p_table, s.period_start, p_granularity)
    )
    ORDER BY s.period_start;
$$;

-- Главная процедура: досоздать недостающие партиции и записать результат.
CREATE OR REPLACE FUNCTION partition_ops.ensure_partitions(
    p_schema TEXT, p_table TEXT, p_granularity TEXT, p_horizon INT)
RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE
    r            RECORD;
    v_created    INT := 0;
    v_names      TEXT[] := '{}';
    v_target     TEXT := p_schema || '.' || p_table;
    v_next       DATE;
BEGIN
    FOR r IN SELECT * FROM partition_ops.missing_partitions(
                            p_schema, p_table, p_granularity, p_horizon)
    LOOP
        v_next := partition_ops.period_shift(r.period_start, p_granularity, 1);
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS %I.%I PARTITION OF %I.%I
             FOR VALUES FROM (%L) TO (%L)',
            p_schema, r.partition_name, p_schema, p_table, r.period_start, v_next);
        v_created := v_created + 1;
        v_names := v_names || r.partition_name;
    END LOOP;

    IF v_created > 0 THEN
        INSERT INTO partition_ops.run_log (target, created, missing, details)
        VALUES (v_target, v_created, 0, array_to_string(v_names, ', '));

        -- Создание партиции постфактум означает, что горизонт уже проседал,
        -- поэтому о самовосстановлении сообщаем наружу.
        INSERT INTO partition_ops.alert_outbox (severity, target, message)
        VALUES ('OK', v_target,
                format('🛠 pg_cron восстановил партиции' || chr(10) ||
                       'Table: %s' || chr(10) ||
                       'Created:' || chr(10) || '%s' || chr(10) ||
                       'Checked at:' || chr(10) || '%s',
                       v_target, array_to_string(v_names, chr(10)),
                       to_char(now(), 'YYYY-MM-DD HH24:MI:SS')));
    ELSE
        INSERT INTO partition_ops.run_log (target, created, missing, details)
        VALUES (v_target, 0, 0, 'всё на месте');
    END IF;

    RETURN v_created;
END;
$$;

-- ==========================================================================
-- Расписание.
--
-- Хранится здесь, а не только в таблице cron.job: иначе развёртывание с нуля
-- даёт расширение и функции, но не даёт самого планирования, и партиции
-- перестают создаваться молча.
--
-- cron.schedule идемпотентна по имени задания: повторный запуск файла не
-- плодит дубликаты, а обновляет существующее задание.
-- ==========================================================================
SELECT cron.schedule(
    'partitions-events', '* * * * *',
    $job$ SELECT partition_ops.ensure_partitions('krasova_lab3', 'events', 'day', 3) $job$);

SELECT cron.schedule(
    'partitions-rentals', '* * * * *',
    $job$ SELECT partition_ops.ensure_partitions('carrent_lab3', 'rentals', 'month', 2) $job$);

-- Чистка журнала pg_cron: без неё cron.job_run_details растёт бесконечно
-- (два задания в минуту — это около миллиона строк в год).
SELECT cron.schedule(
    'purge-cron-history', '17 3 * * *',
    $job$ DELETE FROM cron.job_run_details WHERE end_time < now() - interval '7 days' $job$);

-- Снять задание: SELECT cron.unschedule('partitions-events');
-- Посмотреть расписание:  SELECT jobid, jobname, schedule, active FROM cron.job;
-- Посмотреть историю:     SELECT * FROM cron.job_run_details ORDER BY runid DESC;
