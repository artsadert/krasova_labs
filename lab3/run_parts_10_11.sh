#!/usr/bin/env bash
# Части 10-11: автоматическое создание партиций, health check, alert, восстановление.
set -uo pipefail
BASE="$(cd "$(dirname "$0")" && pwd)"
OUT="$BASE/results/parts_10_11.txt"
: > "$OUT"
cd "$BASE/jobs"

export ALERT_CHANNEL="console,webhook"
export ALERT_WEBHOOK_URL="http://127.0.0.1:8099/"

step() { { echo; echo "########## $1"; echo; } >> "$OUT"; }
sh_run() { { echo "\$ $1"; eval "$1" 2>&1; echo "(код возврата: $?)"; echo; } >> "$OUT"; }
sql_run() {
  { echo "\$ psql -c \"$1\""; } >> "$OUT"
  printf 'SET search_path TO krasova_lab3;\n%s\n' "$1" \
    | docker exec -i -e PGPASSWORD=12341234 database_postgres \
        psql -U postgres -d postgres -X -q >> "$OUT" 2>&1
  echo >> "$OUT"
}

rm -f alert_state.json received_alerts.log

step "Шаг 0. Исходное состояние: какие партиции есть сейчас"
sql_run "SELECT c.relname AS partition FROM pg_class c
JOIN pg_inherits i ON i.inhrelid = c.oid
JOIN pg_class p ON p.oid = i.inhparent
WHERE p.relname = 'events' ORDER BY 1;"
sh_run "date '+сегодня: %Y-%m-%d'"

step "Шаг 1. Проверка ДО запуска job — партиций на горизонт нет, ожидаем CRITICAL и alert"
sh_run "python3 partition_health_check.py --table events --horizon 3"

step "Шаг 2. Повторная проверка — состояние не изменилось, alert НЕ должен уйти повторно"
sh_run "python3 partition_health_check.py --table events --horizon 3"

step "Шаг 3. CreatePartitionsJob создаёт недостающие партиции"
sh_run "python3 create_partitions_job.py --table events --horizon 3"

step "Шаг 4. Повторный запуск job — идемпотентность"
sh_run "python3 create_partitions_job.py --table events --horizon 3"

step "Шаг 5. Проверка после восстановления — ожидаем OK и recovery-уведомление"
sh_run "python3 partition_health_check.py --table events --horizon 3"

step "Шаг 6. Проверка ещё раз — состояние OK не изменилось, уведомлений нет"
sh_run "python3 partition_health_check.py --table events --horizon 3"

step "Шаг 7. Имитация сбоя ночной job: удаляем партицию последнего дня горизонта"
MISSING=$(date -d '+3 days' '+%Y_%m_%d')
sql_run "DROP TABLE events_${MISSING};"
sql_run "SELECT c.relname AS partition FROM pg_class c
JOIN pg_inherits i ON i.inhrelid = c.oid
JOIN pg_class p ON p.oid = i.inhparent
WHERE p.relname = 'events' ORDER BY 1;"

step "Шаг 8. PartitionHealthCheck обнаруживает проблему — CRITICAL и alert"
sh_run "python3 partition_health_check.py --table events --horizon 3"

step "Шаг 9. Ещё две проверки подряд — alert подавлен, спама нет"
sh_run "python3 partition_health_check.py --table events --horizon 3"
sh_run "python3 partition_health_check.py --table events --horizon 3"

step "Шаг 10. Восстановление: запускаем job"
sh_run "python3 create_partitions_job.py --table events --horizon 3"

step "Шаг 11. Контрольная проверка — OK и recovery-уведомление"
sh_run "python3 partition_health_check.py --table events --horizon 3"

step "Шаг 12. Что реально доставлено во внешний канал"
sh_run "cat received_alerts.log"

step "Шаг 13. Итоговое состояние партиций и файла состояния алертов"
sql_run "SELECT c.relname AS partition, pg_get_expr(c.relpartbound, c.oid) AS bounds
FROM pg_class c JOIN pg_inherits i ON i.inhrelid = c.oid
JOIN pg_class p ON p.oid = i.inhparent
WHERE p.relname = 'events' ORDER BY 1;"
sh_run "cat alert_state.json"

echo "done -> $OUT"
