#!/usr/bin/env bash
# Лабораторная работа №1: индексы и планы выполнения в PostgreSQL.
# Скрипт идемпотентен: сбрасывает все индексы (кроме PK) и прогоняет задания 1-15.
set -euo pipefail

BASE="$(cd "$(dirname "$0")" && pwd)"
OUT="$BASE/results/raw_output.txt"
mkdir -p "$BASE/results"
: > "$OUT"

PSQL='docker exec -i -e PGPASSWORD=12341234 database_postgres psql -U postgres -d postgres -X -q -v ON_ERROR_STOP=1'

exec_sql() { printf 'SET search_path TO krasova_lab1;\n%s\n' "$1" | eval $PSQL; }

# run <метка> <sql> — выполнить и записать запрос вместе с ответом
run() {
  { echo "@@@ $1"; echo "--- SQL"; echo "$2"; echo "--- OUT"; } >> "$OUT"
  exec_sql "$2" >> "$OUT" 2>&1
  echo >> "$OUT"
}

# explain <метка> <опции> <запрос> — прогрев кэша, затем замер
explain() {
  local label="$1" opts="$2" q="$3"
  exec_sql "EXPLAIN ($opts) $q" > /dev/null 2>&1 || true
  { echo "@@@ $label"; echo "--- SQL"; echo "EXPLAIN ($opts)"; echo "$q"; echo "--- OUT"; } >> "$OUT"
  exec_sql "EXPLAIN ($opts) $q" >> "$OUT" 2>&1
  echo >> "$OUT"
}

# сброс всех индексов, кроме первичного ключа
reset_indexes() {
  exec_sql "DO \$\$
DECLARE r record;
BEGIN
  FOR r IN SELECT indexname FROM pg_indexes
           WHERE schemaname='krasova_lab1' AND indexname <> 'orders_pkey'
  LOOP EXECUTE format('DROP INDEX krasova_lab1.%I', r.indexname); END LOOP;
END \$\$;" > /dev/null
}

AB="ANALYZE, BUFFERS"
reset_indexes

### Задание 1. Таблица и её индексы после создания
run "1.structure" "SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema='krasova_lab1' AND table_name='orders'
ORDER BY ordinal_position;"
run "1.indexes" "SELECT indexname, indexdef FROM pg_indexes
WHERE schemaname='krasova_lab1' ORDER BY indexname;"

### Задание 2. Проверка сгенерированных данных
run "2.stats" "SELECT count(*) AS rows, count(DISTINCT user_id) AS users,
       min(created_at)::date AS first_day, max(created_at)::date AS last_day,
       pg_size_pretty(pg_total_relation_size('orders')) AS total_size FROM orders;"
run "2.per_user" "SELECT min(cnt) AS min_orders_per_user, max(cnt) AS max_orders_per_user
FROM (SELECT count(*) AS cnt FROM orders GROUP BY user_id) s;"
run "2.amount_range" "SELECT min(amount) AS min_amount, max(amount) AS max_amount FROM orders;"
run "2.status_dist" "SELECT status, count(*) AS rows,
       round(100.0*count(*)/sum(count(*)) OVER (),2) AS pct
FROM orders GROUP BY status ORDER BY status;"

### Задание 3. EXPLAIN без индекса
explain "3.explain" "COSTS" "SELECT * FROM orders WHERE user_id = 123;"

### Задание 4. EXPLAIN ANALYZE без индекса
explain "4.explain_analyze" "$AB" "SELECT * FROM orders WHERE user_id = 123;"

### Задание 5. Sequential Scan
explain "5.full_scan" "$AB" "SELECT * FROM orders;"
explain "5.amount"    "$AB" "SELECT * FROM orders WHERE amount > 0;"

### Задание 6. B-tree индекс по user_id
run "6.create" "CREATE INDEX idx_orders_user_id ON orders(user_id);"
run "6.size" "SELECT pg_size_pretty(pg_relation_size('idx_orders_user_id')) AS index_size;"
explain "6.after" "$AB" "SELECT * FROM orders WHERE user_id = 123;"

### Задание 7. Индекс при низкой селективности
run "7.create" "CREATE INDEX idx_orders_status ON orders(status);"
explain "7.new"       "$AB" "SELECT * FROM orders WHERE status = 'NEW';"
explain "7.paid"      "$AB" "SELECT * FROM orders WHERE status = 'PAID';"
explain "7.delivered" "$AB" "SELECT * FROM orders WHERE status = 'DELIVERED';"
explain "7.cancelled" "$AB" "SELECT * FROM orders WHERE status = 'CANCELLED';"

### Задание 8. Селективность
run "8.selectivity" "SELECT status, count(*) AS rows,
       round(100.0*count(*)/(SELECT count(*) FROM orders),2) AS pct_of_table
FROM orders GROUP BY status ORDER BY status;"
explain "8.high_selectivity" "$AB" "SELECT * FROM orders WHERE user_id = 777;"

### Задание 9. Диапазонный запрос
explain "9.before" "$AB" "SELECT * FROM orders WHERE created_at >= NOW() - INTERVAL '7 days';"
run "9.create" "CREATE INDEX idx_orders_created_at ON orders(created_at);"
explain "9.day"   "$AB" "SELECT * FROM orders WHERE created_at >= NOW() - INTERVAL '1 day';"
explain "9.week"  "$AB" "SELECT * FROM orders WHERE created_at >= NOW() - INTERVAL '7 days';"
explain "9.month" "$AB" "SELECT * FROM orders WHERE created_at >= NOW() - INTERVAL '1 month';"
explain "9.year"  "$AB" "SELECT * FROM orders WHERE created_at >= NOW() - INTERVAL '1 year';"

### Задание 10. Bitmap Scan
explain "10.bitmap" "$AB" "SELECT * FROM orders WHERE status = 'NEW';"

### Задание 11. Два отдельных индекса
run "11.indexes" "SELECT indexname FROM pg_indexes
WHERE schemaname='krasova_lab1' ORDER BY indexname;"
explain "11.two_indexes" "$AB" "SELECT * FROM orders WHERE user_id = 123 AND status = 'PAID';"

### Задание 12. Составной индекс
run "12.create" "CREATE INDEX idx_orders_user_status ON orders(user_id, status);"
explain "12.with_single" "$AB" "SELECT * FROM orders WHERE user_id = 123 AND status = 'PAID';"
run "12.drop_single" "DROP INDEX idx_orders_user_id;"
explain "12.composite_only" "$AB" "SELECT * FROM orders WHERE user_id = 123 AND status = 'PAID';"
run "12.restore" "CREATE INDEX idx_orders_user_id ON orders(user_id);"

### Задание 13. Порядок колонок в составном индексе
run "13.reset" "DROP INDEX idx_orders_user_id, idx_orders_status,
  idx_orders_created_at, idx_orders_user_status;
CREATE INDEX idx_orders_user_created ON orders(user_id, created_at);"
run "13.indexes_a" "SELECT indexname FROM pg_indexes
WHERE schemaname='krasova_lab1' ORDER BY indexname;"
explain "13a.user_only"  "$AB" "SELECT * FROM orders WHERE user_id = 123;"
explain "13a.user_range" "$AB" "SELECT * FROM orders WHERE user_id = 123
  AND created_at >= NOW() - INTERVAL '1 year';"
explain "13a.range_only" "$AB" "SELECT * FROM orders WHERE created_at >= NOW() - INTERVAL '1 day';"
run "13.swap" "DROP INDEX idx_orders_user_created;
CREATE INDEX idx_orders_created_user ON orders(created_at, user_id);"
explain "13b.user_only"  "$AB" "SELECT * FROM orders WHERE user_id = 123;"
explain "13b.user_range" "$AB" "SELECT * FROM orders WHERE user_id = 123
  AND created_at >= NOW() - INTERVAL '1 year';"
explain "13b.range_only" "$AB" "SELECT * FROM orders WHERE created_at >= NOW() - INTERVAL '1 day';"

### Задание 14. WHERE + ORDER BY
run "14.reset" "DROP INDEX idx_orders_created_user;
CREATE INDEX idx_orders_user_id ON orders(user_id);"
explain "14.before" "$AB" "SELECT * FROM orders WHERE user_id = 123 ORDER BY created_at DESC;"
run "14.create" "DROP INDEX idx_orders_user_id;
CREATE INDEX idx_orders_user_created_desc ON orders(user_id, created_at DESC);"
explain "14.after" "$AB" "SELECT * FROM orders WHERE user_id = 123 ORDER BY created_at DESC;"

### Задание 15. Pagination Query
explain "15.before" "$AB" "SELECT * FROM orders WHERE user_id = 123
  ORDER BY created_at DESC LIMIT 20;"
run "15.create" "DROP INDEX idx_orders_user_created_desc;
CREATE INDEX idx_orders_pagination ON orders(user_id, created_at DESC)
  INCLUDE (id, product_id, status, amount, updated_at);
VACUUM (ANALYZE) orders;"
explain "15.after" "$AB" "SELECT * FROM orders WHERE user_id = 123
  ORDER BY created_at DESC LIMIT 20;"
# Замер задания 15 устойчив к шуму: время порядка 0.1 мс, поэтому один прогон
# ничего не доказывает. Берём 15 повторов и сравниваем min/median/max.
measure_pagination() {
  local q="SELECT * FROM orders WHERE user_id = 123 ORDER BY created_at DESC LIMIT 20;"
  for _ in $(seq 1 15); do
    printf 'SET search_path TO krasova_lab1;\nEXPLAIN (ANALYZE) %s\n' "$q" \
      | eval $PSQL -t -A | grep 'Execution Time' | grep -oE '[0-9.]+'
  done | sort -n \
       | awk '{a[NR]=$1} END{printf "  min=%.3f  median=%.3f  max=%.3f ms\n", a[1], a[int((NR+1)/2)], a[NR]}'
}

{
  echo "@@@ 15.repeat"
  echo "--- SQL"
  echo "-- 15 повторов EXPLAIN (ANALYZE) для каждого из двух вариантов индекса"
  echo "SELECT * FROM orders WHERE user_id = 123 ORDER BY created_at DESC LIMIT 20;"
  echo "--- OUT"
  echo "Покрывающий индекс idx_orders_pagination (Index Only Scan, 4 буфера):"
  measure_pagination
} >> "$OUT"

exec_sql "DROP INDEX idx_orders_pagination;
CREATE INDEX idx_orders_user_created_desc ON orders(user_id, created_at DESC);" > /dev/null
{
  echo "Обычный индекс (user_id, created_at DESC) (Index Scan, 11 буферов):"
  measure_pagination
  echo
} >> "$OUT"

exec_sql "DROP INDEX idx_orders_user_created_desc;
CREATE INDEX idx_orders_pagination ON orders(user_id, created_at DESC)
  INCLUDE (id, product_id, status, amount, updated_at);
VACUUM (ANALYZE) orders;" > /dev/null

run "15.sizes" "SELECT indexrelname AS index_name,
       pg_size_pretty(pg_relation_size(indexrelid)) AS size
FROM pg_stat_user_indexes WHERE schemaname='krasova_lab1'
ORDER BY pg_relation_size(indexrelid) DESC;"

python3 "$BASE/make_result.py"
run "15.index_sizes_compare" "CREATE INDEX tmp_plain_idx ON orders(user_id, created_at DESC);
SELECT pg_size_pretty(pg_relation_size('orders'))                AS heap,
       pg_size_pretty(pg_relation_size('tmp_plain_idx'))         AS plain_index,
       pg_size_pretty(pg_relation_size('idx_orders_pagination')) AS covering_index;
DROP INDEX tmp_plain_idx;"

echo "done -> $OUT, $BASE/result.md"
