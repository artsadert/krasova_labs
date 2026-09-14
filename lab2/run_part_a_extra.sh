#!/usr/bin/env bash
# Дополнительные замеры на итоговом объёме 10 000 000 строк:
#  - задание 7 на пользователе с большим числом событий (когда Sort реально мешает);
#  - задание 8: сколько строк обрабатывает агрегация;
#  - задание 10: размеры индексов на 10M.
set -euo pipefail
BASE="$(cd "$(dirname "$0")" && pwd)"
OUT="$BASE/results/part_a_extra.txt"
: > "$OUT"
PSQL='docker exec -i -e PGPASSWORD=12341234 database_postgres psql -U postgres -d postgres -X -q -v ON_ERROR_STOP=1'
exec_sql() { printf 'SET search_path TO krasova_lab2;\n%s\n' "$1" | eval $PSQL; }
run() { { echo "@@@ $1"; echo "--- SQL"; echo "$2"; echo "--- OUT"; } >> "$OUT"; exec_sql "$2" >> "$OUT" 2>&1; echo >> "$OUT"; }
explain() {
  local label="$1" q="$2"
  exec_sql "EXPLAIN (ANALYZE, BUFFERS) $q" > /dev/null 2>&1 || true
  { echo "@@@ $label"; echo "--- SQL"; echo "EXPLAIN (ANALYZE, BUFFERS)"; echo "$q"; echo "--- OUT"; } >> "$OUT"
  exec_sql "EXPLAIN (ANALYZE, BUFFERS) $q" >> "$OUT" 2>&1
  echo >> "$OUT"
}

run "extra.scale" "SELECT count(*) AS rows,
       pg_size_pretty(pg_relation_size('events')) AS table_size FROM events;"

# Сколько строк реально обрабатывает агрегация задания 8
run "extra.agg_volume" "SELECT count(*) AS rows_last_30_days,
       round(100.0*count(*)/(SELECT count(*) FROM events),2) AS pct_of_table
FROM events WHERE created_at >= NOW() - INTERVAL '30 days';"

# Пользователь с наибольшим числом событий — для задания 7
run "extra.top_user" "SELECT user_id, count(*) AS events
FROM events GROUP BY user_id ORDER BY count(*) DESC LIMIT 3;"

TOP=$(exec_sql "SELECT user_id FROM events GROUP BY user_id ORDER BY count(*) DESC LIMIT 1;" | sed -n '3p' | xargs)
echo "top user = $TOP"

Q_SORT_TOP="SELECT * FROM events WHERE user_id = $TOP ORDER BY created_at DESC LIMIT 100;"

explain "extra.t7_top_noindex" "$Q_SORT_TOP"
run "extra.create_user_idx" "CREATE INDEX idx_events_user_id ON events(user_id);"
explain "extra.t7_top_user_idx" "$Q_SORT_TOP"
run "extra.create_composite" "CREATE INDEX idx_events_user_created ON events(user_id, created_at DESC);"
explain "extra.t7_top_composite" "$Q_SORT_TOP"

# Задание 10: размеры всех трёх индексов на 10M
run "extra.create_created_idx" "CREATE INDEX idx_events_created_at ON events(created_at);"
run "extra.index_sizes_10m" "SELECT indexrelname AS index_name,
       pg_size_pretty(pg_relation_size(indexrelid)) AS index_size,
       pg_relation_size(indexrelid) AS bytes
FROM pg_stat_user_indexes
WHERE schemaname='krasova_lab2' AND relname='events'
ORDER BY pg_relation_size(indexrelid) DESC;"
run "extra.total_size_10m" "SELECT pg_size_pretty(pg_relation_size('events')) AS table_size,
       pg_size_pretty(pg_indexes_size('events')) AS indexes_size,
       pg_size_pretty(pg_total_relation_size('events')) AS total_size;"

# Задание 11: агрегация по дням за год на 10M
explain "extra.t11_daily" "SELECT DATE(created_at), COUNT(*)
FROM events WHERE created_at >= NOW() - INTERVAL '365 days'
GROUP BY DATE(created_at);"

echo "done -> $OUT"
