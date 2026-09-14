#!/usr/bin/env bash
# Лабораторная работа №4: Primary + Replica, streaming replication.
set -uo pipefail
BASE="$(cd "$(dirname "$0")" && pwd)"
OUT="$BASE/results/lab4_raw.txt"
mkdir -p "$BASE/results"; : > "$OUT"

P='docker exec -i -e PGPASSWORD=12341234 lab4_primary psql -U postgres -d postgres -X -q'
R='docker exec -i -e PGPASSWORD=12341234 lab4_replica psql -U postgres -d postgres -X -q'

hdr()  { { echo; echo "########## $1"; echo; } >> "$OUT"; }
prim() { { echo "[PRIMARY] $1"; } >> "$OUT"; printf 'SET search_path TO carrent;\n%s\n' "$2" | eval $P >> "$OUT" 2>&1; echo >> "$OUT"; }
repl() { { echo "[REPLICA] $1"; } >> "$OUT"; printf 'SET search_path TO carrent;\n%s\n' "$2" | eval $R >> "$OUT" 2>&1; echo >> "$OUT"; }
sh_run(){ { echo "\$ $1"; eval "$1" 2>&1; echo; } >> "$OUT"; }

############ Сброс: пересоздаём схему сервиса на Primary ############
# Без этого повторный прогон падает на UNIQUE-конституции users.email,
# а количество аренд растёт от запуска к запуску.
echo ">>> пересоздаём схему carrent на Primary"
docker exec -i -e PGPASSWORD=12341234 lab4_primary psql -U postgres -d postgres -q \
  -v ON_ERROR_STOP=1 < "$BASE/sql/01_carrent_schema.sql" > /dev/null
sleep 2

############ Часть 1. Два экземпляра ############
hdr "Часть 1. Primary и Replica подняты"
sh_run "docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'"
prim "кем себя считает узел" "SELECT inet_server_addr() AS addr, pg_is_in_recovery() AS in_recovery,
       CASE WHEN pg_is_in_recovery() THEN 'REPLICA' ELSE 'PRIMARY' END AS role, version();"
repl "кем себя считает узел" "SELECT inet_server_addr() AS addr, pg_is_in_recovery() AS in_recovery,
       CASE WHEN pg_is_in_recovery() THEN 'REPLICA' ELSE 'PRIMARY' END AS role;"

############ Часть 2. Streaming replication ############
hdr "Часть 2. Состояние репликации"
prim "параметры WAL" "SELECT name, setting FROM pg_settings
WHERE name IN ('wal_level','max_wal_senders','max_replication_slots','hot_standby') ORDER BY name;"
# lag-колонки заполняются только когда по каналу реально идут данные,
# поэтому непосредственно перед замером делаем небольшую запись.
prim "нагрузка для наполнения lag-колонок" "INSERT INTO rentals (user_id, car_id, minute_fee)
SELECT 1 + (random()*4999)::int, 1 + (random()*999)::int, 10.00
FROM generate_series(1, 20000);"
prim "pg_stat_replication" "SELECT client_addr, usename, application_name, state, sync_state,
       sent_lsn, write_lsn, flush_lsn, replay_lsn,
       write_lag, flush_lag, replay_lag FROM pg_stat_replication;"
prim "слот репликации" "SELECT slot_name, slot_type, active, restart_lsn, wal_status
FROM pg_replication_slots;"
repl "процесс получения WAL" "SELECT status, sender_host, sender_port, slot_name,
       written_lsn, flushed_lsn, latest_end_lsn FROM pg_stat_wal_receiver;"
# Пароль репликации из primary_conninfo вырезаем: строка попадает в отчёт.
repl "как реплика подключается к Primary" "SELECT regexp_replace(setting, 'password=[^ ]*', 'password=***')
AS primary_conninfo FROM pg_settings WHERE name = 'primary_conninfo';"

############ Часть 3. Доказательство репликации ############
hdr "Часть 3. Запись на Primary видна на Replica"
prim "INSERT нового пользователя" "INSERT INTO users (name, email)
VALUES ('Реплика Проверкина', 'replication-proof@carrent.test')
RETURNING id, name, email, created_at;"
prim "UPDATE существующей записи" "UPDATE cars SET minute_fee = 99.99 WHERE id = 1
RETURNING id, model, license_plate, minute_fee;"
sleep 1
repl "SELECT того же пользователя" "SELECT id, name, email, created_at FROM users
WHERE email = 'replication-proof@carrent.test';"
repl "SELECT обновлённой машины" "SELECT id, model, license_plate, minute_fee FROM cars WHERE id = 1;"
prim "LSN на Primary" "SELECT pg_current_wal_lsn() AS current_wal_lsn;"
repl "LSN на Replica" "SELECT pg_last_wal_receive_lsn() AS received, pg_last_wal_replay_lsn() AS replayed,
       pg_last_xact_replay_timestamp() AS last_replayed_xact;"

############ Часть 4. Replica только для чтения ############
hdr "Часть 4. Попытки записи на Replica"
repl "INSERT на реплике" "INSERT INTO users (name, email) VALUES ('Нельзя', 'nope@carrent.test');"
repl "UPDATE на реплике" "UPDATE cars SET minute_fee = 1 WHERE id = 2;"
repl "DELETE на реплике" "DELETE FROM rentals WHERE id = 1;"
repl "CREATE TABLE на реплике" "CREATE TABLE should_fail (id int);"
repl "транзакция по умолчанию" "SHOW default_transaction_read_only; SHOW transaction_read_only;"

############ Часть 6. Replication lag ############
hdr "Часть 6. Replication lag: наблюдаемое отставание"
prim "лаг в спокойном состоянии" "SELECT application_name, state,
       pg_wal_lsn_diff(sent_lsn, replay_lsn) AS bytes_behind,
       write_lag, flush_lag, replay_lag FROM pg_stat_replication;"

{ echo "--- гонка: запись на Primary и немедленное чтение с Replica ---"; } >> "$OUT"
sh_run "python3 $BASE/app/lag_probe.py --mode race --iterations 20"

{ echo "--- воспроизводимое отставание: замораживаем применение WAL на Replica ---"; } >> "$OUT"
sh_run "python3 $BASE/app/lag_probe.py --mode pause"

{ echo "--- естественное отставание под нагрузкой ---"; } >> "$OUT"
sh_run "python3 $BASE/app/lag_probe.py --mode load --rows 400000"

############ Часть 5. Чтение сервиса через Replica ############
hdr "Часть 5. Сервис CarRent: чтение с Replica, запись на Primary"
sh_run "python3 $BASE/app/carrent_api.py"

hdr "Итоговое состояние репликации"
prim "pg_stat_replication" "SELECT application_name, state, sync_state,
       pg_wal_lsn_diff(sent_lsn, replay_lsn) AS bytes_behind FROM pg_stat_replication;"

echo "done -> $OUT"
