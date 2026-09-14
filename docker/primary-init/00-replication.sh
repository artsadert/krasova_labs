#!/usr/bin/env bash
# Выполняется один раз, при инициализации пустого кластера Primary.
# Готовит всё, что нужно для streaming replication.
set -euo pipefail

REPL_USER="${REPLICATION_USER:-replicator}"
REPL_PASS="${REPLICATION_PASSWORD:?переменная REPLICATION_PASSWORD обязательна}"

# Отдельная роль только для репликации: атрибут REPLICATION даёт право
# читать поток WAL, но не даёт доступа к данным.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE ${REPL_USER} WITH REPLICATION LOGIN PASSWORD '${REPL_PASS}';
SQL

# Обычные строки pg_hba, которые добавляет образ, разрешают подключение к базам,
# но НЕ к псевдобазе replication — для неё нужна отдельная запись.
cat >> "$PGDATA/pg_hba.conf" <<HBA

# --- streaming replication (лабораторная №4) ---
host    replication     ${REPL_USER}    all     scram-sha-256
HBA

echo "Primary подготовлен: роль ${REPL_USER} создана, pg_hba разрешает replication"
