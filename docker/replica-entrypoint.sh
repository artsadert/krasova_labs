#!/usr/bin/env bash
# Entrypoint реплики.
#
# При первом запуске каталог данных пуст: снимаем базовую копию с Primary
# через pg_basebackup и переводим кластер в режим standby.
# При последующих запусках копия уже есть — просто стартуем сервер,
# он догонит Primary по WAL с того места, где остановился.
set -euo pipefail

PRIMARY_HOST="${PRIMARY_HOST:-postgres_primary}"
REPL_USER="${REPLICATION_USER:-replicator}"
REPL_SLOT="${REPLICATION_SLOT:-replica_slot}"
export PGPASSWORD="${REPLICATION_PASSWORD:?переменная REPLICATION_PASSWORD обязательна}"

if [ ! -s "$PGDATA/PG_VERSION" ]; then
    echo "[replica] каталог данных пуст — ждём Primary на ${PRIMARY_HOST}"
    until pg_isready -h "$PRIMARY_HOST" -U "$REPL_USER" -q; do
        sleep 1
    done

    echo "[replica] снимаем базовую копию через pg_basebackup"
    mkdir -p "$PGDATA"
    # -R создаёт standby.signal и postgresql.auto.conf с primary_conninfo,
    # -C -S заводит физический слот репликации на Primary,
    # -Xs тянет WAL потоком параллельно с копированием.
    pg_basebackup \
        --host="$PRIMARY_HOST" \
        --username="$REPL_USER" \
        --pgdata="$PGDATA" \
        --format=plain \
        --wal-method=stream \
        --write-recovery-conf \
        --create-slot --slot="$REPL_SLOT" \
        --checkpoint=fast --progress --verbose
    chmod 0700 "$PGDATA"
    echo "[replica] копия снята, кластер переведён в standby"
else
    echo "[replica] каталог данных уже существует — продолжаем с места остановки"
fi

exec postgres
