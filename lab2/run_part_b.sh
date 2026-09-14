#!/usr/bin/env bash
# Лабораторная работа №2, часть B: сущность rentals сервиса CarRent.
set -euo pipefail

BASE="$(cd "$(dirname "$0")" && pwd)"
OUT="$BASE/results/part_b_raw.txt"
mkdir -p "$BASE/results"
: > "$OUT"

PSQL='docker exec -i -e PGPASSWORD=12341234 database_postgres psql -U postgres -d postgres -X -q -v ON_ERROR_STOP=1'
exec_sql() { printf 'SET search_path TO carrent_lab2;\n%s\n' "$1" | eval $PSQL; }

run() {
  { echo "@@@ $1"; echo "--- SQL"; echo "$2"; echo "--- OUT"; } >> "$OUT"
  exec_sql "$2" >> "$OUT" 2>&1
  echo >> "$OUT"
}

explain() {
  local label="$1" q="$2"
  exec_sql "EXPLAIN (ANALYZE, BUFFERS) $q" > /dev/null 2>&1 || true
  { echo "@@@ $label"; echo "--- SQL"; echo "EXPLAIN (ANALYZE, BUFFERS)"; echo "$q"; echo "--- OUT"; } >> "$OUT"
  exec_sql "EXPLAIN (ANALYZE, BUFFERS) $q" >> "$OUT" 2>&1
  echo >> "$OUT"
}

drop_secondary_indexes() {
  exec_sql "DO \$\$
DECLARE r record;
BEGIN
  FOR r IN SELECT indexname FROM pg_indexes
           WHERE schemaname='carrent_lab2' AND tablename='rentals'
             AND indexname <> 'rentals_pkey'
  LOOP EXECUTE format('DROP INDEX carrent_lab2.%I', r.indexname); END LOOP;
END \$\$;" > /dev/null
}

# Задание 13: три реальных сценария сервиса
Q1="SELECT * FROM rentals WHERE user_id = 123;"
Q2="SELECT * FROM rentals
WHERE created_at >= NOW() - INTERVAL '7 days' AND created_at < NOW();"
Q3="SELECT * FROM rentals WHERE user_id = 123 ORDER BY created_at DESC LIMIT 50;"

CHECKPOINTS="100000 1000000 5000000"
CURRENT=0

for TARGET in $CHECKPOINTS; do
  DELTA=$(( TARGET - CURRENT ))
  echo ">>> rentals: наращиваем до $TARGET (+$DELTA)"

  exec_sql "INSERT INTO rentals (car_id, user_id, started_at, finished_at, created_at, updated_at, minute_fee)
SELECT
    (random() * 50000)::bigint + 1,
    (random() * 200000)::bigint + 1,
    NOW() - (random() * INTERVAL '365 days'),
    CASE WHEN random() < 0.95
         THEN NOW() - (random() * INTERVAL '365 days') + (random() * INTERVAL '120 minutes')
         ELSE NULL END,
    NOW() - (random() * INTERVAL '365 days'),
    NOW(),
    round((5 + random() * 20)::numeric, 2)
FROM generate_series(1, $DELTA) AS i;" > /dev/null
  exec_sql "ANALYZE rentals;" > /dev/null
  CURRENT=$TARGET

  run "b$TARGET.size" "SELECT count(*) AS rows,
       pg_size_pretty(pg_relation_size('rentals'))       AS table_size,
       pg_size_pretty(pg_total_relation_size('rentals')) AS total_size
FROM rentals;"

  # Задание 14/16: как есть в проекте — только первичный ключ
  drop_secondary_indexes
  explain "b$TARGET.q1_baseline" "$Q1"
  explain "b$TARGET.q2_baseline" "$Q2"
  explain "b$TARGET.q3_baseline" "$Q3"

  # Задание 18: оптимизация
  run "b$TARGET.create_indexes" "CREATE INDEX idx_rentals_user_created ON rentals(user_id, created_at DESC);
CREATE INDEX idx_rentals_created_at ON rentals(created_at);"
  explain "b$TARGET.q1_indexed" "$Q1"
  explain "b$TARGET.q2_indexed" "$Q2"
  explain "b$TARGET.q3_indexed" "$Q3"

  run "b$TARGET.index_sizes" "SELECT indexrelname AS index_name,
       pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
FROM pg_stat_user_indexes
WHERE schemaname='carrent_lab2' AND relname='rentals'
ORDER BY pg_relation_size(indexrelid) DESC;"

  drop_secondary_indexes
done

echo "done -> $OUT"
