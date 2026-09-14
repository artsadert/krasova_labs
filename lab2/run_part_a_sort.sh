#!/usr/bin/env bash
# Задание 7, углублённо: когда Sort действительно исчезает.
set -euo pipefail
BASE="$(cd "$(dirname "$0")" && pwd)"
OUT="$BASE/results/part_a_sort.txt"
: > "$OUT"
PSQL='docker exec -i -e PGPASSWORD=12341234 database_postgres psql -U postgres -d postgres -X -q -v ON_ERROR_STOP=1'
exec_sql() { printf 'SET search_path TO krasova_lab2;\n%s\n' "$1" | eval $PSQL; }
run() { { echo "@@@ $1"; echo "--- SQL"; echo "$2"; echo "--- OUT"; } >> "$OUT"; exec_sql "$2" >> "$OUT" 2>&1; echo >> "$OUT"; }
explain() {
  local label="$1" pre="$2" q="$3"
  exec_sql "$pre EXPLAIN (ANALYZE, BUFFERS) $q" > /dev/null 2>&1 || true
  { echo "@@@ $label"; echo "--- SQL"; echo "$pre"; echo "EXPLAIN (ANALYZE, BUFFERS)"; echo "$q"; echo "--- OUT"; } >> "$OUT"
  exec_sql "$pre EXPLAIN (ANALYZE, BUFFERS) $q" >> "$OUT" 2>&1
  echo >> "$OUT"
}

# А. Тот же запрос, но bitmap-план запрещён — видно альтернативу, которую
#    планировщик счёл дороже.
explain "sort.forced_index_scan" "SET enable_bitmapscan = off;" \
  "SELECT * FROM events WHERE user_id = 30096 ORDER BY created_at DESC LIMIT 100;"

# Б. «Тяжёлый» пользователь: 200 000 событий у одного user_id — типичная
#    ситуация для реального потока событий.
run "sort.create_power_user" "INSERT INTO events (user_id, event_type, payload, created_at)
SELECT 9999999, 'MESSAGE', '{\"source\":\"web\"}'::jsonb,
       NOW() - (random() * INTERVAL '365 days')
FROM generate_series(1, 200000);
ANALYZE events;"
run "sort.power_user_count" "SELECT count(*) AS events_of_power_user FROM events WHERE user_id = 9999999;"

Q="SELECT * FROM events WHERE user_id = 9999999 ORDER BY created_at DESC LIMIT 100;"
run "sort.drop_composite" "DROP INDEX IF EXISTS idx_events_user_created;"
explain "sort.power_user_only_user_idx" "" "$Q"
run "sort.recreate_composite" "CREATE INDEX idx_events_user_created ON events(user_id, created_at DESC);"
explain "sort.power_user_composite" "" "$Q"

# В. Порядок колонок: обратный составной индекс на том же запросе
run "sort.create_reversed" "CREATE INDEX idx_events_created_user ON events(created_at DESC, user_id);"
run "sort.drop_correct" "DROP INDEX idx_events_user_created;"
explain "sort.power_user_reversed" "" "$Q"
run "sort.cleanup" "DROP INDEX idx_events_created_user;
CREATE INDEX idx_events_user_created ON events(user_id, created_at DESC);"
echo "done -> $OUT"
