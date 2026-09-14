#!/usr/bin/env bash
# Часть 12, шаги 7-9: автоматизация, контроль и alert для rentals (гранулярность — месяц).
set -uo pipefail
BASE="$(cd "$(dirname "$0")" && pwd)"
OUT="$BASE/results/part_12_ops.txt"
: > "$OUT"
cd "$BASE/jobs"

export LAB_SCHEMA="carrent_lab3"
export ALERT_CHANNEL="console,webhook"
export ALERT_WEBHOOK_URL="http://127.0.0.1:8099/"
export PARTITION_STATE_FILE="$BASE/jobs/alert_state_carrent.json"

step() { { echo; echo "########## $1"; echo; } >> "$OUT"; }
sh_run() { { echo "\$ $1"; eval "$1" 2>&1; echo "(код возврата: $?)"; echo; } >> "$OUT"; }

rm -f "$PARTITION_STATE_FILE"
: > received_alerts.log

step "Шаг 8. Контроль: есть ли партиции rentals на текущий и 2 следующих месяца"
sh_run "python3 partition_health_check.py --table rentals --granularity month --horizon 2"

step "Шаг 9. Alert ушёл. Повторная проверка — дубликат подавлен"
sh_run "python3 partition_health_check.py --table rentals --granularity month --horizon 2"

step "Шаг 7. Ночная job создаёт будущие месячные партиции"
sh_run "python3 create_partitions_job.py --table rentals --granularity month --horizon 2"

step "Шаг 7б. Повторный запуск — идемпотентность"
sh_run "python3 create_partitions_job.py --table rentals --granularity month --horizon 2"

step "Контрольная проверка — OK и recovery-уведомление"
sh_run "python3 partition_health_check.py --table rentals --granularity month --horizon 2"

step "Проверка перехода через год: горизонт 4 месяца от ноября"
sh_run "python3 create_partitions_job.py --table rentals --granularity month --horizon 4 --today 2026-11-15"
sh_run "python3 partition_health_check.py --table rentals --granularity month --horizon 4 --today 2026-11-15"

step "Итоговый список партиций rentals"
sh_run "printf 'SET search_path TO carrent_lab3;\nSELECT c.relname AS partition, pg_get_expr(c.relpartbound, c.oid) AS bounds FROM pg_class c JOIN pg_inherits i ON i.inhrelid = c.oid JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = %s ORDER BY 1;\n' \"'rentals'\" | docker exec -i -e PGPASSWORD=12341234 database_postgres psql -U postgres -d postgres -X -q"

step "Доставленные уведомления"
sh_run "cat received_alerts.log"

echo "done -> $OUT"
