#!/usr/bin/env bash
# Разворачивает планирование партиций внутри СУБД.
#
# Что нужно до запуска:
#   1) образ собран с postgresql-18-cron (Dockerfile проекта);
#   2) сервер поднят с shared_preload_libraries=pg_cron (docker-compose.yaml);
#   3) схемы krasova_lab3 / carrent_lab3 уже существуют — расписание ссылается
#      на их таблицы.
#
# Скрипт идемпотентен: повторный запуск обновляет функции и задания,
# но не плодит дубликаты.
set -euo pipefail
BASE="$(cd "$(dirname "$0")" && pwd)"

PSQL='docker exec -i -e PGPASSWORD=12341234 database_postgres psql -U postgres -d postgres -X -q -v ON_ERROR_STOP=1'

echo ">>> проверяем, что pg_cron загружен"
eval $PSQL -t -A -c "SHOW shared_preload_libraries;" | grep -q pg_cron \
  || { echo "pg_cron не загружен: добавьте shared_preload_libraries=pg_cron и перезапустите сервер"; exit 1; }

echo ">>> применяем функции и расписание"
eval $PSQL < "$BASE/sql/03_pg_cron.sql" > /dev/null

echo ">>> текущее расписание"
eval $PSQL -c "SELECT jobid, jobname, schedule, active FROM cron.job ORDER BY jobid;"
