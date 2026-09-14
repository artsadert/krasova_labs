#!/usr/bin/env bash
# Лабораторная работа №2, часть A: как объём данных влияет на планы и время.
# Таблица events наращивается по контрольным точкам; на каждой снимаются
# замеры без индексов и с индексами.
set -euo pipefail

BASE="$(cd "$(dirname "$0")" && pwd)"
OUT="$BASE/results/part_a_raw.txt"
mkdir -p "$BASE/results"
: > "$OUT"

PSQL='docker exec -i -e PGPASSWORD=12341234 database_postgres psql -U postgres -d postgres -X -q -v ON_ERROR_STOP=1'
exec_sql() { printf 'SET search_path TO krasova_lab2;\n%s\n' "$1" | eval $PSQL; }

run() {
  { echo "@@@ $1"; echo "--- SQL"; echo "$2"; echo "--- OUT"; } >> "$OUT"
  exec_sql "$2" >> "$OUT" 2>&1
  echo >> "$OUT"
}

# explain <метка> <запрос> — прогрев, затем замер
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
           WHERE schemaname='krasova_lab2' AND tablename='events'
             AND indexname <> 'events_pkey'
  LOOP EXECUTE format('DROP INDEX krasova_lab2.%I', r.indexname); END LOOP;
END \$\$;" > /dev/null
}

# Запросы заданий 4-8
Q_USER="SELECT * FROM events WHERE user_id = 123;"
Q_DATE="SELECT * FROM events WHERE created_at >= NOW() - INTERVAL '1 day';"
Q_SORT="SELECT * FROM events WHERE user_id = 123 ORDER BY created_at DESC LIMIT 100;"
Q_AGG="SELECT event_type, COUNT(*) FROM events
WHERE created_at >= NOW() - INTERVAL '30 days'
GROUP BY event_type;"

CHECKPOINTS="10000 100000 1000000 5000000 10000000"
CURRENT=0

for TARGET in $CHECKPOINTS; do
  DELTA=$(( TARGET - CURRENT ))
  echo ">>> наращиваем до $TARGET (+$DELTA строк)"

  # --- Задание 2: генерация данных ---
  START=$(date +%s%N)
  exec_sql "INSERT INTO events (user_id, event_type, payload, created_at)
SELECT
    (random() * 100000)::bigint,
    CASE
        WHEN random() < 0.4 THEN 'MESSAGE'
        WHEN random() < 0.7 THEN 'LOGIN'
        WHEN random() < 0.9 THEN 'PURCHASE'
        ELSE 'OTHER'
    END,
    jsonb_build_object('source', (ARRAY['web','ios','android'])[1 + (i % 3)]),
    NOW() - (random() * INTERVAL '365 days')
FROM generate_series(1, $DELTA) AS i;" > /dev/null
  GEN_MS=$(( ($(date +%s%N) - START) / 1000000 ))
  exec_sql "ANALYZE events;" > /dev/null
  CURRENT=$TARGET

  { echo "@@@ v$TARGET.generate"; echo "--- SQL"; echo "-- вставлено $DELTA строк, итого $TARGET"; echo "--- OUT";
    echo "время генерации: ${GEN_MS} ms"; echo; } >> "$OUT"

  # --- Задание 3: размеры ---
  run "v$TARGET.size" "SELECT count(*) AS rows,
       pg_size_pretty(pg_relation_size('events'))       AS table_size,
       pg_size_pretty(pg_total_relation_size('events')) AS total_size,
       pg_size_pretty(pg_indexes_size('events'))        AS indexes_size
FROM events;"

  # --- Задания 4,6,7,8: базовые замеры без вторичных индексов ---
  drop_secondary_indexes
  explain "v$TARGET.t4_noindex"  "$Q_USER"
  explain "v$TARGET.t6_noindex"  "$Q_DATE"
  explain "v$TARGET.t7_noindex"  "$Q_SORT"
  explain "v$TARGET.t8_noindex"  "$Q_AGG"

  # --- Задание 5: индекс по user_id ---
  run "v$TARGET.create_user_idx" "CREATE INDEX idx_events_user_id ON events(user_id);"
  explain "v$TARGET.t5_index" "$Q_USER"

  # --- Задание 6: индекс по created_at ---
  run "v$TARGET.create_created_idx" "CREATE INDEX idx_events_created_at ON events(created_at);"
  explain "v$TARGET.t6_index" "$Q_DATE"
  explain "v$TARGET.t8_index" "$Q_AGG"

  # --- Задание 7: составной индекс ---
  run "v$TARGET.create_composite_idx" "CREATE INDEX idx_events_user_created ON events(user_id, created_at DESC);"
  explain "v$TARGET.t7_index" "$Q_SORT"

  # --- Задание 10: размеры индексов ---
  run "v$TARGET.index_sizes" "SELECT indexrelname AS index_name,
       pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
FROM pg_stat_user_indexes
WHERE schemaname='krasova_lab2' AND relname='events'
ORDER BY pg_relation_size(indexrelid) DESC;"

  drop_secondary_indexes
done

echo "done -> $OUT"
