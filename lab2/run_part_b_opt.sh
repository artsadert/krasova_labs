#!/usr/bin/env bash
# Задание 18: попытки улучшить самый зависимый от объёма запрос (Запрос 2)
# на итоговых 5 000 000 аренд.
set -euo pipefail
BASE="$(cd "$(dirname "$0")" && pwd)"
OUT="$BASE/results/part_b_opt.txt"
: > "$OUT"
PSQL='docker exec -i -e PGPASSWORD=12341234 database_postgres psql -U postgres -d postgres -X -q -v ON_ERROR_STOP=1'
exec_sql() { printf 'SET search_path TO carrent_lab2;\n%s\n' "$1" | eval $PSQL; }
run() { { echo "@@@ $1"; echo "--- SQL"; echo "$2"; echo "--- OUT"; } >> "$OUT"; exec_sql "$2" >> "$OUT" 2>&1; echo >> "$OUT"; }
explain() {
  local label="$1" q="$2"
  exec_sql "EXPLAIN (ANALYZE, BUFFERS) $q" > /dev/null 2>&1 || true
  { echo "@@@ $label"; echo "--- SQL"; echo "EXPLAIN (ANALYZE, BUFFERS)"; echo "$q"; echo "--- OUT"; } >> "$OUT"
  exec_sql "EXPLAIN (ANALYZE, BUFFERS) $q" >> "$OUT" 2>&1
  echo >> "$OUT"
}

run "opt.setup" "CREATE INDEX IF NOT EXISTS idx_rentals_created_at ON rentals(created_at);"

# Вариант 0: исходный запрос сервиса
explain "opt.v0_original" "SELECT * FROM rentals
WHERE created_at >= NOW() - INTERVAL '7 days' AND created_at < NOW();"

# Вариант 1: та же выборка, но постранично (реальный API отдаёт страницу)
explain "opt.v1_pagination" "SELECT * FROM rentals
WHERE created_at >= NOW() - INTERVAL '7 days' AND created_at < NOW()
ORDER BY created_at DESC LIMIT 50;"

# Вариант 2: только нужные колонки + покрывающий индекс
run "opt.v2_index" "CREATE INDEX idx_rentals_created_cover ON rentals(created_at)
  INCLUDE (id, user_id, car_id, minute_fee);
VACUUM (ANALYZE) rentals;"
explain "opt.v2_covering" "SELECT id, user_id, car_id, minute_fee, created_at FROM rentals
WHERE created_at >= NOW() - INTERVAL '7 days' AND created_at < NOW();"

# Вариант 3: агрегат вместо выгрузки строк
explain "opt.v3_aggregate" "SELECT count(*), sum(minute_fee) FROM rentals
WHERE created_at >= NOW() - INTERVAL '7 days' AND created_at < NOW();"

run "opt.sizes" "SELECT indexrelname AS index_name,
       pg_size_pretty(pg_relation_size(indexrelid)) AS size
FROM pg_stat_user_indexes WHERE schemaname='carrent_lab2' AND relname='rentals'
ORDER BY pg_relation_size(indexrelid) DESC;"
echo "done -> $OUT"
