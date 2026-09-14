#!/usr/bin/env bash
# Части 1-9: заполнение данными, partition pruning, индексы.
set -euo pipefail
BASE="$(cd "$(dirname "$0")" && pwd)"
OUT="$BASE/results/parts_1_9.txt"
mkdir -p "$BASE/results"; : > "$OUT"

PSQL='docker exec -i -e PGPASSWORD=12341234 database_postgres psql -U postgres -d postgres -X -q -v ON_ERROR_STOP=1'
PSQL_SOFT='docker exec -i -e PGPASSWORD=12341234 database_postgres psql -U postgres -d postgres -X -q'
exec_sql()      { printf 'SET search_path TO krasova_lab3;\n%s\n' "$1" | eval $PSQL; }
exec_sql_soft() { printf 'SET search_path TO krasova_lab3;\n%s\n' "$1" | eval $PSQL_SOFT; }

run()  { { echo "@@@ $1"; echo "--- SQL"; echo "$2"; echo "--- OUT"; } >> "$OUT"; exec_sql "$2" >> "$OUT" 2>&1; echo >> "$OUT"; }
# soft — ожидаем ошибку и записываем её текст
soft() { { echo "@@@ $1"; echo "--- SQL"; echo "$2"; echo "--- OUT"; } >> "$OUT"; exec_sql_soft "$2" >> "$OUT" 2>&1; echo >> "$OUT"; }
explain() {
  local label="$1" q="$2"
  exec_sql "EXPLAIN (ANALYZE, BUFFERS) $q" > /dev/null 2>&1 || true
  { echo "@@@ $label"; echo "--- SQL"; echo "EXPLAIN (ANALYZE, BUFFERS)"; echo "$q"; echo "--- OUT"; } >> "$OUT"
  exec_sql "EXPLAIN (ANALYZE, BUFFERS) $q" >> "$OUT" 2>&1
  echo >> "$OUT"
}

############ Часть 1. RANGE по дате ############
run "p1.structure" "\d+ events"

run "p1.fill" "INSERT INTO events (id, user_id, event_type, payload, created_at)
SELECT i,
       (random() * 100000)::bigint,
       (ARRAY['click','view','login','purchase','logout'])[1 + (i % 5)],
       'payload-' || i,
       TIMESTAMP '2026-09-09' + ((i % 3) || ' days')::interval
                              + ((random() * 86399)::int || ' seconds')::interval
FROM generate_series(1, 3000000) AS i;
-- гарантируем наличие пользователя 12345 в каждой партиции
INSERT INTO events (id, user_id, event_type, payload, created_at)
SELECT 9000000 + i, 12345, 'click', 'vip', 
       TIMESTAMP '2026-09-09' + ((i % 3) || ' days')::interval + ((i * 37 % 86399) || ' seconds')::interval
FROM generate_series(1, 30) AS i;
ANALYZE events;"

run "p1.distribution" "SELECT tableoid::regclass AS partition_name, COUNT(*)
FROM events GROUP BY tableoid ORDER BY partition_name;"

run "p1.q1_boundary" "SELECT tableoid::regclass AS partition_name, created_at
FROM events WHERE created_at = TIMESTAMP '2026-09-10 12:00:00' LIMIT 1;"

run "p1.q1_probe" "INSERT INTO events VALUES (99000001, 1, 'probe', 'p', '2026-09-10 12:00:00');
INSERT INTO events VALUES (99000002, 1, 'probe', 'p', '2026-09-11 00:00:00');
SELECT id, tableoid::regclass AS partition_name, created_at
FROM events WHERE id IN (99000001, 99000002) ORDER BY id;"

soft "p1.q3_out_of_range" "INSERT INTO events VALUES (99000003, 1, 'probe', 'p', '2026-09-12 10:00:00');"

run "p1.boundaries" "SELECT c.relname AS partition, pg_get_expr(c.relpartbound, c.oid) AS bounds
FROM pg_class c
JOIN pg_inherits i ON i.inhrelid = c.oid
JOIN pg_class p ON p.oid = i.inhparent
WHERE p.relname = 'events' ORDER BY c.relname;"

############ Часть 2. Partition pruning ############
explain "p2.pruning_by_date" "SELECT COUNT(*) FROM events
WHERE created_at >= '2026-09-10' AND created_at < '2026-09-11';"
explain "p2.no_pruning" "SELECT COUNT(*) FROM events WHERE event_type = 'click';"

############ Часть 3. RANGE по числу ############
run "p3.fill" "INSERT INTO products (id, name, price) VALUES
 (1,'Ручка', 25.00), (2,'Тетрадь', 99.99), (3,'Наушники', 100.00),
 (4,'Клавиатура', 450.00), (5,'Монитор', 999.99), (6,'Ноутбук', 1000.00),
 (7,'Сервер', 25000.00);
ANALYZE products;"
run "p3.distribution" "SELECT tableoid::regclass AS partition_name, count(*), min(price), max(price)
FROM products GROUP BY tableoid ORDER BY partition_name;"
explain "p3.pruning" "SELECT * FROM products WHERE price >= 100 AND price < 500;"

############ Часть 4. LIST ############
run "p4.fill" "INSERT INTO customers (id, name, customer_type)
SELECT i, 'Client ' || i,
       (ARRAY['B2C','B2B','Enterprise'])[1 + (i % 3)]
FROM generate_series(1, 30000) AS i;
ANALYZE customers;"
run "p4.distribution" "SELECT tableoid::regclass AS partition_name, COUNT(*)
FROM customers GROUP BY tableoid ORDER BY partition_name;"
explain "p4.pruning" "SELECT * FROM customers WHERE customer_type = 'B2B';"

############ Часть 5. DEFAULT partition ############
soft "p5.insert_vip_fail" "INSERT INTO customers VALUES (100, 'Test User', 'VIP');"
run  "p5.create_default" "CREATE TABLE customers_default PARTITION OF customers DEFAULT;"
run  "p5.insert_vip_ok" "INSERT INTO customers VALUES (100, 'Test User', 'VIP');
SELECT id, name, customer_type, tableoid::regclass AS partition_name
FROM customers WHERE id = 100;"
soft "p5.default_blocks_new" "CREATE TABLE customers_vip PARTITION OF customers FOR VALUES IN ('VIP');"

############ Часть 6. HASH ############
run "p6.fill" "INSERT INTO user_events (id, user_id, event_type, created_at)
SELECT i, (random() * 1000000)::bigint, 'evt', NOW()
FROM generate_series(1, 1000000) AS i;
ANALYZE user_events;"
run "p6.distribution" "SELECT tableoid::regclass AS partition_name, COUNT(*),
       round(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct
FROM user_events GROUP BY tableoid ORDER BY partition_name;"
explain "p6.pruning_by_user" "SELECT * FROM user_events WHERE user_id = 555;"
explain "p6.no_pruning_by_date" "SELECT COUNT(*) FROM user_events WHERE created_at >= NOW() - INTERVAL '1 day';"

############ Часть 8. Партиционирование и индексы ############
run "p8.create_index" "CREATE INDEX idx_events_user_id ON events (user_id);"
run "p8.index_hierarchy" "SELECT c.relname AS index_name, c.relispartition AS is_partition_index,
       t.relname AS on_table
FROM pg_class c
JOIN pg_index ix ON ix.indexrelid = c.oid
JOIN pg_class t ON t.oid = ix.indrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'krasova_lab3' AND c.relname LIKE '%user_id%'
ORDER BY c.relname;"
explain "p8.pruning_plus_index" "SELECT * FROM events
WHERE created_at >= '2026-09-10' AND created_at < '2026-09-11' AND user_id = 12345;"
explain "p8.index_without_pruning" "SELECT * FROM events WHERE user_id = 12345;"

############ Часть 9. Когда партиционирование не помогает ############
explain "p9.before_index" "SELECT COUNT(*) FROM events WHERE event_type = 'click';"
run "p9.create_index" "CREATE INDEX idx_events_event_type ON events (event_type);"
explain "p9.after_index" "SELECT COUNT(*) FROM events WHERE event_type = 'click';"
explain "p9.type_plus_date" "SELECT COUNT(*) FROM events
WHERE event_type = 'click' AND created_at >= '2026-09-10' AND created_at < '2026-09-11';"

run "p9.counts" "SELECT count(*) AS total_rows,
       count(*) FILTER (WHERE event_type = 'click') AS click_rows,
       round(100.0 * count(*) FILTER (WHERE event_type = 'click') / count(*), 2) AS click_pct
FROM events;"

run "p9.sizes" "SELECT c.relname AS partition,
       pg_size_pretty(pg_relation_size(c.oid)) AS table_size,
       pg_size_pretty(pg_indexes_size(c.oid)) AS indexes_size
FROM pg_class c JOIN pg_inherits i ON i.inhrelid = c.oid
JOIN pg_class p ON p.oid = i.inhparent
WHERE p.relname = 'events' ORDER BY c.relname;"

echo "done -> $OUT"
