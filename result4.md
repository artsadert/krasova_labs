# Результаты выполнения — лабораторная работа №4

Полный протокол: конфигурация, SQL-запросы к обоим узлам и фактический вывод.
Отчёт с выводами — в [`README4.md`](README4.md).

Среда: PostgreSQL 18 в Docker. `lab4_primary` (порт 5432) принимает запись,
`lab4_replica` (порт 5433) обслуживает чтение. Схема сервиса — `carrent`.

Пароль репликации в выводе `primary_conninfo` замаскирован.

---

## Конфигурация: docker-compose.yaml

```yaml
# Лабораторная работа №4: масштабирование чтения. Primary + Replica.
#
#   postgres_primary : принимает запись, отдаёт поток WAL       (порт 5432)
#   postgres_replica : горячий резерв, только чтение            (порт 5433)
#
# Лабораторная №3 (партиционирование, pg_cron, alert-система) живёт
# в отдельном файле docker-compose3.yaml.

services:
  postgres_primary:
    container_name: lab4_primary
    image: postgres:18
    environment:
      POSTGRES_USER: ${DB_USERNAME?error}
      POSTGRES_PASSWORD: ${DB_PASSWORD?error}
      POSTGRES_DB: ${DB_DATABASE?error}
      REPLICATION_USER: ${REPLICATION_USER:-replicator}
      # Без значения по умолчанию: пароль берётся только из .env и не
      # попадает ни в compose-файл, ни в отчёт.
      REPLICATION_PASSWORD: ${REPLICATION_PASSWORD?error}
      # Пароли ролей хешируются scram-sha-256; репликационное подключение
      # проходит ту же проверку.
      POSTGRES_INITDB_ARGS: "--auth-host=scram-sha-256"

    # wal_level=replica — минимум, при котором в WAL попадает достаточно
    # информации, чтобы standby мог воспроизвести изменения.
    # Слоты репликации не дают Primary удалить WAL, который реплика ещё не забрала.
    command:
      - postgres
      - -c
      - wal_level=replica
      - -c
      - max_wal_senders=10
      - -c
      - max_replication_slots=10
      - -c
      - hot_standby=on
      - -c
      - wal_log_hints=on
      - -c
      - log_line_prefix=%m [%p] %q%u@%d

    volumes:
      - primary_data:/var/lib/postgresql
      - ./docker/primary-init:/docker-entrypoint-initdb.d:ro

    ports:
      - "5432:5432"

    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DB_USERNAME} -d ${DB_DATABASE}"]
      interval: 5s
      timeout: 5s
      retries: 12

    networks: [pg]
    restart: unless-stopped

  postgres_replica:
    container_name: lab4_replica
    image: postgres:18

    # Реплика не инициализирует свой кластер: она копирует его с Primary.
    # Поэтому POSTGRES_* здесь не нужны, а нужен доступ к репликации.
    environment:
      PRIMARY_HOST: postgres_primary
      REPLICATION_USER: ${REPLICATION_USER:-replicator}
      # Без значения по умолчанию: пароль берётся только из .env и не
      # попадает ни в compose-файл, ни в отчёт.
      REPLICATION_PASSWORD: ${REPLICATION_PASSWORD?error}
      REPLICATION_SLOT: ${REPLICATION_SLOT:-replica_slot}
      PGDATA: /var/lib/postgresql/18/docker

    # Свой entrypoint: pg_basebackup при первом старте, затем обычный сервер.
    # user: postgres — чтобы pg_basebackup писал от имени владельца кластера.
    user: postgres
    entrypoint: ["/usr/local/bin/replica-entrypoint.sh"]

    depends_on:
      postgres_primary:
        condition: service_healthy

    volumes:
      - replica_data:/var/lib/postgresql
      - ./docker/replica-entrypoint.sh:/usr/local/bin/replica-entrypoint.sh:ro

    ports:
      - "5433:5432"

    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DB_USERNAME} -d ${DB_DATABASE}"]
      interval: 5s
      timeout: 5s
      retries: 12

    networks: [pg]
    restart: unless-stopped

  pgadmin:
    container_name: lab4_pgadmin
    image: dpage/pgadmin4:9.0
    depends_on: [postgres_primary]
    environment:
      PGADMIN_DEFAULT_EMAIL: ${PGADMIN_DEFAULT_EMAIL}
      PGADMIN_DEFAULT_PASSWORD: ${PGADMIN_DEFAULT_PASSWORD}
    ports:
      - "${PGADMIN_PORT:-5050}:80"
    networks: [pg]
    restart: unless-stopped

networks:
  pg:
    driver: bridge

volumes:
  primary_data:
  replica_data:
```

---

## Подготовка Primary: роль репликации и pg_hba

Выполняется автоматически при инициализации пустого кластера
(`/docker-entrypoint-initdb.d/00-replication.sh`).

```bash
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
```

---

## Entrypoint Replica: pg_basebackup и переход в standby

```bash
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
```

---

## Схема сервиса CarRent (создаётся на Primary)

```sql
-- Схема сервиса CarRent на Primary. На Replica она появится сама, через WAL.
DROP SCHEMA IF EXISTS carrent CASCADE;
CREATE SCHEMA carrent;
SET search_path TO carrent;

CREATE TABLE users (
    id         BIGSERIAL PRIMARY KEY,
    name       TEXT        NOT NULL,
    email      TEXT        NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE cars (
    id            BIGSERIAL PRIMARY KEY,
    model         TEXT         NOT NULL,
    license_plate TEXT         NOT NULL UNIQUE,
    minute_fee    NUMERIC(8,2) NOT NULL
);

CREATE TABLE rentals (
    id          BIGSERIAL   PRIMARY KEY,
    user_id     BIGINT      NOT NULL REFERENCES users(id),
    car_id      BIGINT      NOT NULL REFERENCES cars(id),
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    minute_fee  NUMERIC(8,2) NOT NULL
);

CREATE INDEX idx_rentals_user_created ON rentals (user_id, created_at DESC);
CREATE INDEX idx_rentals_created      ON rentals (created_at);

INSERT INTO users (name, email)
SELECT 'User ' || i, 'user' || i || '@carrent.test' FROM generate_series(1, 5000) AS i;

INSERT INTO cars (model, license_plate, minute_fee)
SELECT (ARRAY['Kia Rio','Lada Vesta','VW Polo','Skoda Rapid','BMW 320i'])[1 + (i % 5)],
       'A' || lpad(i::text, 4, '0') || 'BC',
       round((5 + random() * 20)::numeric, 2)
FROM generate_series(1, 1000) AS i;

INSERT INTO rentals (user_id, car_id, started_at, finished_at, created_at, minute_fee)
SELECT 1 + (random() * 4999)::int,
       1 + (random() * 999)::int,
       ts, ts + ((random() * 120)::int || ' minutes')::interval, ts,
       round((5 + random() * 20)::numeric, 2)
FROM (SELECT now() - ((random() * 90 * 86400)::int || ' seconds')::interval AS ts
      FROM generate_series(1, 200000)) g;

ANALYZE users; ANALYZE cars; ANALYZE rentals;
```

---

## Протокол выполнения частей 1-6

```text
########## Часть 1. Primary и Replica подняты

$ docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
NAME           STATUS                    PORTS
lab4_pgadmin   Up 12 minutes             443/tcp, 0.0.0.0:5050->80/tcp, [::]:5050->80/tcp
lab4_primary   Up 12 minutes (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
lab4_replica   Up 12 minutes (healthy)   0.0.0.0:5433->5432/tcp, [::]:5433->5432/tcp

[PRIMARY] кем себя считает узел
 addr | in_recovery |  role   |                                                      version                                                       
------+-------------+---------+--------------------------------------------------------------------------------------------------------------------
      | f           | PRIMARY | PostgreSQL 18.0 (Debian 18.0-1.pgdg13+3) on x86_64-pc-linux-gnu, compiled by gcc (Debian 14.2.0-19) 14.2.0, 64-bit
(1 row)


[REPLICA] кем себя считает узел
 addr | in_recovery |  role   
------+-------------+---------
      | t           | REPLICA
(1 row)



########## Часть 2. Состояние репликации

[PRIMARY] параметры WAL
         name          | setting 
-----------------------+---------
 hot_standby           | on
 max_replication_slots | 10
 max_wal_senders       | 10
 wal_level             | replica
(4 rows)


[PRIMARY] нагрузка для наполнения lag-колонок

[PRIMARY] pg_stat_replication
 client_addr |  usename   | application_name |   state   | sync_state |  sent_lsn  | write_lsn  | flush_lsn  | replay_lsn |    write_lag    |    flush_lag    |   replay_lag    
-------------+------------+------------------+-----------+------------+------------+------------+------------+------------+-----------------+-----------------+-----------------
 172.2.0.4   | replicator | walreceiver      | streaming | async      | 0/304269C8 | 0/304269C8 | 0/304269C8 | 0/304269C8 | 00:00:00.018061 | 00:00:00.019231 | 00:00:00.041135
(1 row)


[PRIMARY] слот репликации
  slot_name   | slot_type | active | restart_lsn | wal_status 
--------------+-----------+--------+-------------+------------
 replica_slot | physical  | t      | 0/304269C8  | reserved
(1 row)


[REPLICA] процесс получения WAL
  status   |   sender_host    | sender_port |  slot_name   | written_lsn | flushed_lsn | latest_end_lsn 
-----------+------------------+-------------+--------------+-------------+-------------+----------------
 streaming | postgres_primary |        5432 | replica_slot | 0/30426A28  | 0/30426A28  | 0/30426A28
(1 row)


[REPLICA] как реплика подключается к Primary
                                                                                                                                                 primary_conninfo                                                                                                                                                  
-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
 user=replicator password=*** channel_binding=prefer host=postgres_primary port=5432 sslmode=prefer sslnegotiation=postgres sslcompression=0 sslcertmode=allow sslsni=1 ssl_min_protocol_version=TLSv1.2 gssencmode=prefer krbsrvname=postgres gssdelegation=0 target_session_attrs=any load_balance_hosts=disable
(1 row)



########## Часть 3. Запись на Primary видна на Replica

[PRIMARY] INSERT нового пользователя
  id  |        name        |             email              |          created_at           
------+--------------------+--------------------------------+-------------------------------
 5001 | Реплика Проверкина | replication-proof@carrent.test | 2026-09-14 07:48:24.951134+00
(1 row)


[PRIMARY] UPDATE существующей записи
 id |   model    | license_plate | minute_fee 
----+------------+---------------+------------
  1 | Lada Vesta | A0001BC       |      99.99
(1 row)


[REPLICA] SELECT того же пользователя
  id  |        name        |             email              |          created_at           
------+--------------------+--------------------------------+-------------------------------
 5001 | Реплика Проверкина | replication-proof@carrent.test | 2026-09-14 07:48:24.951134+00
(1 row)


[REPLICA] SELECT обновлённой машины
 id |   model    | license_plate | minute_fee 
----+------------+---------------+------------
  1 | Lada Vesta | A0001BC       |      99.99
(1 row)


[PRIMARY] LSN на Primary
 current_wal_lsn 
-----------------
 0/30426CC8
(1 row)


[REPLICA] LSN на Replica
  received  |  replayed  |      last_replayed_xact       
------------+------------+-------------------------------
 0/30426CC8 | 0/30426CC8 | 2026-09-14 07:48:25.021879+00
(1 row)



########## Часть 4. Попытки записи на Replica

[REPLICA] INSERT на реплике
ERROR:  cannot execute INSERT in a read-only transaction

[REPLICA] UPDATE на реплике
ERROR:  cannot execute UPDATE in a read-only transaction

[REPLICA] DELETE на реплике
ERROR:  cannot execute DELETE in a read-only transaction

[REPLICA] CREATE TABLE на реплике
ERROR:  cannot execute CREATE TABLE in a read-only transaction

[REPLICA] транзакция по умолчанию
 default_transaction_read_only 
-------------------------------
 off
(1 row)

 transaction_read_only 
-----------------------
 on
(1 row)



########## Часть 6. Replication lag: наблюдаемое отставание

[PRIMARY] лаг в спокойном состоянии
 application_name |   state   | bytes_behind |    write_lag    |    flush_lag    |   replay_lag    
------------------+-----------+--------------+-----------------+-----------------+-----------------
 walreceiver      | streaming |            0 | 00:00:00.000383 | 00:00:00.000681 | 00:00:00.000728
(1 row)


--- гонка: запись на Primary и немедленное чтение с Replica ---
$ python3 /home/multivader/programs/databases/lab4/app/lag_probe.py --mode race --iterations 20
Гонка запись/чтение, 20 попыток
  #    задержка до появления на Replica   промахов
  1                             51.7 мс          0
  2                             51.9 мс          0
  3                             53.2 мс          0
  4                             58.7 мс          0
  5                             52.2 мс          0
  6                             50.1 мс          0
  7                             59.3 мс          0
  8                             55.6 мс          0
  9                             54.1 мс          0
 10                             55.6 мс          0
 11                             52.6 мс          0
 12                             52.9 мс          0
 13                             51.6 мс          0
 14                             51.8 мс          0
 15                             54.2 мс          0
 16                             55.3 мс          0
 17                             56.6 мс          0
 18                             53.9 мс          0
 19                             58.7 мс          0
 20                             53.1 мс          0

мин 50.1 мс, медиана 53.9 мс, макс 59.3 мс
случаев, когда Replica ещё не содержала запись: 0
Важно: сюда входит и время самого psql-подключения, поэтому это
верхняя оценка лага, а не чистая задержка репликации.

--- воспроизводимое отставание: замораживаем применение WAL на Replica ---
$ python3 /home/multivader/programs/databases/lab4/app/lag_probe.py --mode pause
Замораживаем применение WAL на Replica
  pg_wal_replay_pause() выполнен, состояние: paused

Пишем на Primary, пока реплика заморожена:

Primary уже видит запись:
   count = 1
Replica ещё НЕ видит:
   count = 0

Отставание в этот момент (в байтах WAL), по данным Primary:
application_name | bytes_behind |   replay_lag    
------------------+--------------+-----------------
 walreceiver      |          288 | 00:00:00.000576
(1 row)
Возобновляем применение WAL:
Replica догнала:
   count = 1

--- естественное отставание под нагрузкой ---
$ python3 /home/multivader/programs/databases/lab4/app/lag_probe.py --mode load --rows 400000
Заливаем 400000 строк одним INSERT и параллельно следим за Replica
  LSN на Primary до записи: 0/30428540
  запись заняла 4472 мс, замеров во время записи: 56
  пиковое отставание: 13507616 байт WAL (12.9 МБ), replay_lag до 305.287 мс
  через секунду после записи отставание: 0 байт
  строк в rentals — Primary: 620000, Replica: 620000


########## Часть 5. Сервис CarRent: чтение с Replica, запись на Primary

$ python3 /home/multivader/programs/databases/lab4/app/carrent_api.py
======================================================================
Куда фактически подключается сервис
======================================================================
  PRIMARY  127.0.0.1:5432  -> узел сообщает о себе: PRIMARY
  REPLICA  127.0.0.1:5433  -> узел сообщает о себе: REPLICA

======================================================================
Чтение — с Replica
======================================================================
GET /rentals?user_id=42&limit=5
  -> REPLICA (127.0.0.1:5433)
id   |  model   | minute_fee |          created_at           
--------+----------+------------+-------------------------------
 229698 | BMW 320i |       9.42 | 2026-09-14 07:48:29.226389+00
 236704 | VW Polo  |      10.57 | 2026-09-14 07:48:29.226389+00
 240147 | Kia Rio  |      15.44 | 2026-09-14 07:48:29.226389+00
 240198 | Kia Rio  |      23.66 | 2026-09-14 07:48:29.226389+00
 245067 | VW Polo  |      22.06 | 2026-09-14 07:48:29.226389+00
(5 rows)
GET /rentals/statistics
  -> REPLICA (127.0.0.1:5433)
rentals | users | avg_fee | total_fee  
---------+-------+---------+------------
  486606 |  5000 |   14.80 | 7203000.12
(1 row)
GET /cars/available?limit=5
  -> REPLICA (127.0.0.1:5433)
id | model | license_plate | minute_fee 
----+-------+---------------+------------
(0 rows)
======================================================================
Запись — на Primary
======================================================================
POST /rentals  user_id=42 car_id=7
  -> PRIMARY (127.0.0.1:5432)
  создана аренда id=620001

и сразу читаем её через Replica:
  -> REPLICA (127.0.0.1:5433)
id   | user_id | car_id | minute_fee 
--------+---------+--------+------------
 620001 |      42 |      7 |      12.50
(1 row)


########## Итоговое состояние репликации

[PRIMARY] pg_stat_replication
 application_name |   state   | sync_state | bytes_behind 
------------------+-----------+------------+--------------
 walreceiver      | streaming | async      |            0
(1 row)
```

---

## Часть 5. Код сервиса

### Маршрутизация подключений — `app/db.py`

```python
"""Маршрутизация подключений сервиса CarRent: запись на Primary, чтение на Replica.

Это ядро Read Scaling: приложение держит два независимых подключения и само
решает, куда отправить запрос. Replica принимает только чтение — попытка
записи туда завершится ошибкой (см. часть 4 отчёта).
"""
import os
import subprocess

# Именно 127.0.0.1, а не localhost: localhost резолвится в ::1, а проброс
# порта Docker по IPv6 в этой системе не отвечает — соединение просто виснет.
PRIMARY = {
    "role": "PRIMARY",
    "host": os.environ.get("PRIMARY_HOST", "127.0.0.1"),
    "port": os.environ.get("PRIMARY_PORT", "5432"),
}
REPLICA = {
    "role": "REPLICA",
    "host": os.environ.get("REPLICA_HOST", "127.0.0.1"),
    "port": os.environ.get("REPLICA_PORT", "5433"),
}

DB_USER = os.environ.get("DB_USERNAME", "postgres")
DB_NAME = os.environ.get("DB_DATABASE", "postgres")
DB_PASS = os.environ.get("DB_PASSWORD", "12341234")

VERBOSE = os.environ.get("DB_VERBOSE", "1") == "1"


class DbError(RuntimeError):
    pass


def _run(node: dict, sql: str, tuples_only: bool) -> str:
    args = ["psql", "-h", node["host"], "-p", node["port"], "-U", DB_USER,
            "-d", DB_NAME, "-X", "-q", "-v", "ON_ERROR_STOP=1",
            "-v", "connect_timeout=5"]
    if tuples_only:
        args += ["-t", "-A"]
    env = dict(os.environ, PGPASSWORD=DB_PASS)
    proc = subprocess.run(args, input=f"SET search_path TO carrent;\n{sql}\n",
                          capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise DbError(proc.stderr.strip() or proc.stdout.strip())
    return proc.stdout.strip()


def write(sql: str, *, tuples_only: bool = True) -> str:
    """Любая модификация данных — только на Primary."""
    if VERBOSE:
        print(f"  -> {PRIMARY['role']} ({PRIMARY['host']}:{PRIMARY['port']})")
    return _run(PRIMARY, sql, tuples_only)


def read(sql: str, *, tuples_only: bool = True) -> str:
    """Чтение обслуживает Replica, разгружая Primary."""
    if VERBOSE:
        print(f"  -> {REPLICA['role']} ({REPLICA['host']}:{REPLICA['port']})")
    return _run(REPLICA, sql, tuples_only)


def node_role(node: dict) -> str:
    """Кем узел считает себя сам: recovery == реплика."""
    return "REPLICA" if _run(node, "SELECT pg_is_in_recovery();", True) == "t" else "PRIMARY"
```

### Эндпойнты сервиса — `app/carrent_api.py`

```python
#!/usr/bin/env python3
"""Эндпойнты сервиса CarRent с разделением чтения и записи.

  POST /rentals                 -> Primary  (запись)
  GET  /rentals?user_id=...     -> Replica  (чтение)
  GET  /rentals/statistics      -> Replica  (чтение)
  GET  /cars/available          -> Replica  (чтение)

Запуск демонстрации: python3 carrent_api.py
"""
import sys

import db


def create_rental(user_id: int, car_id: int, minute_fee: float) -> int:
    """POST /rentals — запись идёт на Primary."""
    print(f"POST /rentals  user_id={user_id} car_id={car_id}")
    out = db.write(f"""
        INSERT INTO rentals (user_id, car_id, minute_fee)
        VALUES ({user_id}, {car_id}, {minute_fee})
        RETURNING id;""")
    rental_id = int(out.splitlines()[-1])
    print(f"  создана аренда id={rental_id}")
    return rental_id


def list_user_rentals(user_id: int, limit: int = 5) -> str:
    """GET /rentals?user_id=... — чтение с Replica."""
    print(f"GET /rentals?user_id={user_id}&limit={limit}")
    return db.read(f"""
        SELECT r.id, c.model, r.minute_fee, r.created_at
        FROM rentals r JOIN cars c ON c.id = r.car_id
        WHERE r.user_id = {user_id}
        ORDER BY r.created_at DESC
        LIMIT {limit};""", tuples_only=False)


def rentals_statistics() -> str:
    """GET /rentals/statistics — тяжёлая аналитика уходит с Primary на Replica."""
    print("GET /rentals/statistics")
    return db.read("""
        SELECT count(*) AS rentals,
               count(DISTINCT user_id) AS users,
               round(avg(minute_fee), 2) AS avg_fee,
               round(sum(minute_fee), 2) AS total_fee
        FROM rentals
        WHERE created_at >= now() - interval '30 days';""", tuples_only=False)


def available_cars(limit: int = 5) -> str:
    """GET /cars/available — каталог тоже читаем с Replica."""
    print(f"GET /cars/available?limit={limit}")
    return db.read(f"""
        SELECT c.id, c.model, c.license_plate, c.minute_fee
        FROM cars c
        WHERE NOT EXISTS (
            SELECT 1 FROM rentals r
            WHERE r.car_id = c.id AND r.finished_at IS NULL)
        ORDER BY c.minute_fee
        LIMIT {limit};""", tuples_only=False)


def main() -> int:
    print("=" * 70)
    print("Куда фактически подключается сервис")
    print("=" * 70)
    for node in (db.PRIMARY, db.REPLICA):
        print(f"  {node['role']:<8} {node['host']}:{node['port']}  "
              f"-> узел сообщает о себе: {db.node_role(node)}")

    print("\n" + "=" * 70)
    print("Чтение — с Replica")
    print("=" * 70)
    print(list_user_rentals(42))
    print(rentals_statistics())
    print(available_cars())

    print("=" * 70)
    print("Запись — на Primary")
    print("=" * 70)
    rental_id = create_rental(42, 7, 12.50)

    print("\nи сразу читаем её через Replica:")
    print(db.read(f"SELECT id, user_id, car_id, minute_fee FROM rentals "
                  f"WHERE id = {rental_id};", tuples_only=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

### То же самое в реальном Django-проекте — `app/django_settings_snippet.py`

```python
"""Часть 5: как то же разделение чтения и записи выглядит в реальном сервисе.

CarRent — проект на Django (~/programs/CarRent), поэтому маршрутизация
описывается не в коде эндпойнтов, а в настройках: Django сам выбирает
подключение по типу операции.

Ниже — фрагменты, которые нужно добавить в CarRent/settings.py.
Файл не подключается к проекту автоматически: это справочный образец,
рабочая демонстрация — в db.py и carrent_api.py.
"""

# --- CarRent/settings.py -----------------------------------------------------

DATABASES = {
    # Запись и всё, что требует актуальных данных.
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "postgres",
        "USER": "postgres",
        "PASSWORD": "...",              # из переменных окружения
        "HOST": "postgres_primary",
        "PORT": "5432",
    },
    # Только чтение. Отдельное подключение к Replica.
    "replica": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "postgres",
        "USER": "postgres",
        "PASSWORD": "...",
        "HOST": "postgres_replica",
        "PORT": "5432",
        "OPTIONS": {
            # Страховка на стороне клиента: даже ошибочный UPDATE не уйдёт
            # на реплику незамеченным, а упадёт сразу.
            "options": "-c default_transaction_read_only=on",
        },
        # Django не должен пытаться создавать тестовую БД из реплики.
        "TEST": {"MIRROR": "default"},
    },
}

DATABASE_ROUTERS = ["RentSystem.routers.ReadWriteRouter"]


# --- RentSystem/routers.py ---------------------------------------------------

class ReadWriteRouter:
    """Чтение уводим на Replica, запись оставляем на Primary."""

    def db_for_read(self, model, **hints):
        return "replica"

    def db_for_write(self, model, **hints):
        return "default"

    def allow_relation(self, obj1, obj2, **hints):
        # Обе базы содержат одни и те же данные, связи между объектами законны.
        return True

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        # Миграции применяются только на Primary; на Replica схема приедет
        # через WAL сама.
        return db == "default"


# --- Использование в коде сервиса -------------------------------------------
#
#   # GET /rentals?user_id=...  — уйдёт на replica по правилу роутера
#   Rental.objects.filter(user_id=uid).order_by("-created_at")[:50]
#
#   # POST /rentals — уйдёт на default (Primary)
#   Rental.objects.create(user_id=uid, car_id=cid, minute_fee=fee)
#
#   # Явное указание, когда нужно прочитать гарантированно свежие данные
#   # сразу после записи (обходим replication lag):
#   Rental.objects.using("default").get(pk=rental_id)
```

### Измерение отставания — `app/lag_probe.py`

```python
#!/usr/bin/env python3
"""Измерение replication lag двумя способами.

race  — записать на Primary и тут же читать с Replica, засекая, через сколько
        изменение станет видно. Показывает лаг таким, каким его видит
        приложение.
pause — остановить применение WAL на Replica (pg_wal_replay_pause), сделать
        запись и убедиться, что Primary уже содержит новое значение, а Replica
        ещё нет. Воспроизводимая демонстрация того же эффекта.
"""
import argparse
import time

import db


def now_ms() -> float:
    return time.perf_counter() * 1000


def mode_race(iterations: int) -> None:
    print(f"Гонка запись/чтение, {iterations} попыток")
    print(f"{'#':>3}  {'задержка до появления на Replica':>34}  {'промахов':>9}")
    delays, misses_total = [], 0

    for i in range(1, iterations + 1):
        marker = f"race-{int(time.time()*1e6)}-{i}"
        t0 = now_ms()
        db.write(f"INSERT INTO users (name, email) VALUES ('race', '{marker}');")
        misses = 0
        while True:
            seen = db.read(f"SELECT count(*) FROM users WHERE email = '{marker}';")
            if seen.strip().endswith("1"):
                break
            misses += 1
            if now_ms() - t0 > 5000:
                break
        delay = now_ms() - t0
        delays.append(delay)
        misses_total += misses
        print(f"{i:>3}  {delay:>31.1f} мс  {misses:>9}")

    print(f"\nмин {min(delays):.1f} мс, медиана {sorted(delays)[len(delays)//2]:.1f} мс, "
          f"макс {max(delays):.1f} мс")
    print(f"случаев, когда Replica ещё не содержала запись: {misses_total}")
    print("Важно: сюда входит и время самого psql-подключения, поэтому это"
          "\nверхняя оценка лага, а не чистая задержка репликации.")


def mode_pause() -> None:
    print("Замораживаем применение WAL на Replica")
    db.read("SELECT pg_wal_replay_pause();")
    print("  pg_wal_replay_pause() выполнен, состояние:",
          db.read("SELECT pg_get_wal_replay_pause_state();").splitlines()[-1])

    marker = f"paused-{int(time.time())}"
    print("\nПишем на Primary, пока реплика заморожена:")
    db.write(f"INSERT INTO users (name, email) VALUES ('paused test', '{marker}');")

    print("\nPrimary уже видит запись:")
    print("   count =", db.write(
        f"SELECT count(*) FROM users WHERE email = '{marker}';").splitlines()[-1])
    print("Replica ещё НЕ видит:")
    print("   count =", db.read(
        f"SELECT count(*) FROM users WHERE email = '{marker}';").splitlines()[-1])

    print("\nОтставание в этот момент (в байтах WAL), по данным Primary:")
    print(db.write("""SELECT application_name,
        pg_wal_lsn_diff(sent_lsn, replay_lsn) AS bytes_behind,
        replay_lag FROM pg_stat_replication;""", tuples_only=False))

    print("Возобновляем применение WAL:")
    db.read("SELECT pg_wal_replay_resume();")
    time.sleep(1)
    print("Replica догнала:")
    print("   count =", db.read(
        f"SELECT count(*) FROM users WHERE email = '{marker}';").splitlines()[-1])


def mode_load(rows: int) -> None:
    """Естественное отставание под нагрузкой.

    Замерять после завершения INSERT бесполезно: за время записи реплика
    успевает догнать, и наблюдаемое отставание оказывается нулевым.
    Поэтому запись идёт в отдельном потоке, а отставание снимается
    параллельно, пока транзакция ещё выполняется.
    """
    import threading

    print(f"Заливаем {rows} строк одним INSERT и параллельно следим за Replica")
    before = db.write("SELECT pg_current_wal_lsn();").splitlines()[-1]
    print(f"  LSN на Primary до записи: {before}")

    done = threading.Event()
    timing = {}

    def writer():
        t0 = now_ms()
        db.write(f"""
            INSERT INTO rentals (user_id, car_id, minute_fee, created_at, started_at)
            SELECT 1 + (random() * 4999)::int, 1 + (random() * 999)::int,
                   round((5 + random() * 20)::numeric, 2), now(), now()
            FROM generate_series(1, {rows});""")
        timing["ms"] = now_ms() - t0
        done.set()

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()

    samples = []
    while not done.is_set():
        row = db.write("""SELECT COALESCE(pg_wal_lsn_diff(sent_lsn, replay_lsn), 0)::bigint,
               COALESCE(EXTRACT(epoch FROM replay_lag) * 1000, 0)::numeric(10,3)
               FROM pg_stat_replication;""").splitlines()[-1]
        parts = row.split("|")
        if len(parts) == 2:
            samples.append((int(parts[0]), float(parts[1])))
        time.sleep(0.05)
    thread.join()

    print(f"  запись заняла {timing.get('ms', 0):.0f} мс, "
          f"замеров во время записи: {len(samples)}")
    if samples:
        peak_bytes = max(s[0] for s in samples)
        peak_ms = max(s[1] for s in samples)
        print(f"  пиковое отставание: {peak_bytes} байт WAL "
              f"({peak_bytes / 1024 / 1024:.1f} МБ), replay_lag до {peak_ms:.3f} мс")

    time.sleep(1)
    after = db.write("""SELECT COALESCE(pg_wal_lsn_diff(sent_lsn, replay_lsn), 0)
                        FROM pg_stat_replication;""").splitlines()[-1]
    print(f"  через секунду после записи отставание: {after} байт")
    print(f"  строк в rentals — Primary: "
          f"{db.write('SELECT count(*) FROM rentals;').splitlines()[-1]}, "
          f"Replica: {db.read('SELECT count(*) FROM rentals;').splitlines()[-1]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["race", "pause", "load"], default="race")
    ap.add_argument("--rows", type=int, default=300000)
    ap.add_argument("--iterations", type=int, default=20)
    args = ap.parse_args()

    db.VERBOSE = False
    if args.mode == "race":
        mode_race(args.iterations)
    elif args.mode == "load":
        mode_load(args.rows)
    else:
        mode_pause()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```
