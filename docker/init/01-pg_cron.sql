-- pg_cron живёт в одной базе кластера; она задаётся параметром
-- cron.database_name (см. docker-compose.yaml) и по умолчанию это postgres.
CREATE EXTENSION IF NOT EXISTS pg_cron;

-- Право планировать задания. Здесь владелец один и тот же, но в реальной
-- системе имеет смысл выдавать его отдельной служебной роли.
GRANT USAGE ON SCHEMA cron TO postgres;
