#!/usr/bin/env bash
# Задание 9: стоимость индексов на запись.
# Одна и та же вставка 500 000 строк в таблицу с разным числом индексов.
set -euo pipefail

BASE="$(cd "$(dirname "$0")" && pwd)"
OUT="$BASE/results/part_a_write.txt"
: > "$OUT"

PSQL='docker exec -i -e PGPASSWORD=12341234 database_postgres psql -U postgres -d postgres -X -q -v ON_ERROR_STOP=1'
exec_sql() { printf 'SET search_path TO krasova_lab2;\n%s\n' "$1" | eval $PSQL; }

BATCH=500000

INSERT_SQL="INSERT INTO events_write_test (user_id, event_type, payload, created_at)
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
FROM generate_series(1, $BATCH) AS i;"

exec_sql "DROP TABLE IF EXISTS events_write_test;
CREATE TABLE events_write_test (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT      NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    payload    JSONB,
    created_at TIMESTAMP   NOT NULL
);" > /dev/null

{ echo "@@@ t9.write_cost"; echo "--- SQL"; echo "-- вставка $BATCH строк при разном числе индексов"; echo "$INSERT_SQL"; echo "--- OUT"; } >> "$OUT"
printf '%-56s %12s %12s\n' "набор индексов" "время, мс" "размер табл." >> "$OUT"

measure() {
  local label="$1"
  exec_sql "TRUNCATE events_write_test RESTART IDENTITY;" > /dev/null
  local start=$(date +%s%N)
  exec_sql "$INSERT_SQL" > /dev/null
  local ms=$(( ($(date +%s%N) - start) / 1000000 ))
  local size=$(exec_sql "SELECT pg_size_pretty(pg_total_relation_size('events_write_test'));" | sed -n '3p' | xargs)
  printf '%-56s %12s %12s\n' "$label" "$ms" "$size" >> "$OUT"
}

measure "только PK (0 вторичных индексов)"
exec_sql "CREATE INDEX idx_wt_user_id ON events_write_test(user_id);" > /dev/null
measure "PK + idx_events_user_id"
exec_sql "CREATE INDEX idx_wt_created_at ON events_write_test(created_at);" > /dev/null
measure "PK + user_id + created_at"
exec_sql "CREATE INDEX idx_wt_user_created ON events_write_test(user_id, created_at DESC);" > /dev/null
measure "PK + user_id + created_at + (user_id, created_at DESC)"

echo >> "$OUT"
{ echo "@@@ t9.index_sizes"; echo "--- SQL"; echo "SELECT indexrelname, pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
FROM pg_stat_user_indexes WHERE relname = 'events_write_test'
ORDER BY pg_relation_size(indexrelid) DESC;"; echo "--- OUT"; } >> "$OUT"
exec_sql "SELECT indexrelname, pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
FROM pg_stat_user_indexes WHERE relname = 'events_write_test'
ORDER BY pg_relation_size(indexrelid) DESC;" >> "$OUT" 2>&1

exec_sql "DROP TABLE events_write_test;" > /dev/null
echo "done -> $OUT"
