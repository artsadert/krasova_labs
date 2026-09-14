#!/usr/bin/env bash
# Часть 12: партиционирование rentals сервиса CarRent.
set -uo pipefail
BASE="$(cd "$(dirname "$0")" && pwd)"
OUT="$BASE/results/part_12.txt"
: > "$OUT"

PSQL='docker exec -i -e PGPASSWORD=12341234 database_postgres psql -U postgres -d postgres -X -q -v ON_ERROR_STOP=1'
exec_sql() { printf 'SET search_path TO carrent_lab3;\n%s\n' "$1" | eval $PSQL; }
run() { { echo "@@@ $1"; echo "--- SQL"; echo "$2"; echo "--- OUT"; } >> "$OUT"; exec_sql "$2" >> "$OUT" 2>&1; echo >> "$OUT"; }
explain() {
  local label="$1" q="$2"
  exec_sql "EXPLAIN (ANALYZE, BUFFERS) $q" > /dev/null 2>&1 || true
  { echo "@@@ $label"; echo "--- SQL"; echo "EXPLAIN (ANALYZE, BUFFERS)"; echo "$q"; echo "--- OUT"; } >> "$OUT"
  exec_sql "EXPLAIN (ANALYZE, BUFFERS) $q" >> "$OUT" 2>&1
  echo >> "$OUT"
}

run "s5.partitions" "SELECT c.relname AS partition, pg_get_expr(c.relpartbound, c.oid) AS bounds
FROM pg_class c JOIN pg_inherits i ON i.inhrelid = c.oid
JOIN pg_class p ON p.oid = i.inhparent
WHERE p.relname = 'rentals' ORDER BY 1;"

run "s5.fill" "INSERT INTO rentals (car_id, user_id, started_at, finished_at, created_at, updated_at, minute_fee)
SELECT (random() * 50000)::bigint + 1,
       (random() * 200000)::bigint + 1,
       ts, ts + ((random() * 120)::int || ' minutes')::interval,
       ts, ts, round((5 + random() * 20)::numeric, 2)
FROM (
  SELECT TIMESTAMP '2026-06-01' + ((random() * 121 * 86400)::int || ' seconds')::interval AS ts
  FROM generate_series(1, 2000000)
) g;
ANALYZE rentals;"

run "s5.distribution" "SELECT tableoid::regclass AS partition_name, COUNT(*),
       pg_size_pretty(pg_relation_size(tableoid)) AS size
FROM rentals GROUP BY tableoid ORDER BY partition_name;"

run "s6.indexes" "CREATE INDEX idx_rentals_user_created ON rentals (user_id, created_at DESC);
ANALYZE rentals;"

# --- Запрос 1: GET /rentals?from=2026-08-01&to=2026-09-01 -------------------
explain "s6.q1_range" "SELECT * FROM rentals
WHERE created_at >= '2026-08-01' AND created_at < '2026-09-01'
ORDER BY created_at DESC LIMIT 50;"

# --- Запрос 2: GET /rentals/{id} -------------------------------------------
explain "s6.q2_by_id" "SELECT * FROM rentals WHERE id = 1234567;"
explain "s6.q2_by_id_with_date" "SELECT * FROM rentals
WHERE id = 1234567 AND created_at >= '2026-08-01' AND created_at < '2026-09-01';"

# --- Запрос 3: GET /rentals/statistics?month=2026-08 -----------------------
explain "s6.q3_statistics" "SELECT count(*) AS rentals,
       round(avg(minute_fee), 2) AS avg_fee,
       round(sum(minute_fee), 2) AS total_fee
FROM rentals
WHERE created_at >= '2026-08-01' AND created_at < '2026-09-01';"

# --- Запрос 4: история пользователя ----------------------------------------
explain "s6.q4_user_history" "SELECT * FROM rentals WHERE user_id = 4242
ORDER BY created_at DESC LIMIT 50;"
explain "s6.q4_user_history_month" "SELECT * FROM rentals
WHERE user_id = 4242 AND created_at >= '2026-09-01' AND created_at < '2026-10-01'
ORDER BY created_at DESC LIMIT 50;"

# --- Для сравнения: та же статистика на непартиционированной таблице -------
run "s6.plain_copy" "CREATE TABLE rentals_plain (LIKE rentals INCLUDING DEFAULTS);
INSERT INTO rentals_plain SELECT * FROM rentals;
CREATE INDEX idx_plain_created ON rentals_plain (created_at);
ANALYZE rentals_plain;"
explain "s6.q3_statistics_plain" "SELECT count(*) AS rentals,
       round(avg(minute_fee), 2) AS avg_fee,
       round(sum(minute_fee), 2) AS total_fee
FROM rentals_plain
WHERE created_at >= '2026-08-01' AND created_at < '2026-09-01';"

# --- Шаг 7: удаление старых данных -----------------------------------------
run "s7.drop_old_partition" "SELECT count(*) AS rows_before FROM rentals;"
run "s7.detach" "ALTER TABLE rentals DETACH PARTITION rentals_2026_06;
DROP TABLE rentals_2026_06;
SELECT count(*) AS rows_after FROM rentals;"

echo "done -> $OUT"
