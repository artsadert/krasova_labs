# Результаты выполнения — лабораторная работа №5

Полный протокол: конфигурация шардов, код router и обеих стратегий,
фактический вывод эксперимента. Отчёт с выводами — в [`README5.md`](README5.md).

Среда: четыре независимых экземпляра PostgreSQL 18 в Docker
(`lab5_shard0..3`, порты 5440-5443). Сервис — CarRent, сущность `rentals`,
shard key — `user_id`. Объём — 100 000 аренд.

Записи при ресшардинге переносятся **физически**: `COPY ... TO STDOUT` со
старого шарда, `COPY ... FROM STDIN` на новый, затем `DELETE`. После каждого
этапа router перепроверяет 2 000 случайных записей.

---

## Конфигурация: docker-compose.yaml

```yaml
# Лабораторная работа №5: шардирование сервиса CarRent.
#
# Четыре независимых экземпляра PostgreSQL. Первые три работают с самого
# начала, четвёртый нужен для эксперимента «3 шарда -> 4 шарда».
# Между собой они ничего не знают: всю маршрутизацию делает router в сервисе.
#
# Лабораторные 3 и 4 живут в docker-compose3.yaml и docker-compose4.yaml.

x-shard: &shard
  image: postgres:18
  environment:
    POSTGRES_USER: ${DB_USERNAME?error}
    POSTGRES_PASSWORD: ${DB_PASSWORD?error}
    POSTGRES_DB: ${DB_DATABASE?error}
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U ${DB_USERNAME} -d ${DB_DATABASE}"]
    interval: 5s
    timeout: 5s
    retries: 12
  networks: [shards]
  restart: unless-stopped

services:
  shard0:
    <<: *shard
    container_name: lab5_shard0
    ports: ["5440:5432"]
    volumes: [shard0_data:/var/lib/postgresql]

  shard1:
    <<: *shard
    container_name: lab5_shard1
    ports: ["5441:5432"]
    volumes: [shard1_data:/var/lib/postgresql]

  shard2:
    <<: *shard
    container_name: lab5_shard2
    ports: ["5442:5432"]
    volumes: [shard2_data:/var/lib/postgresql]

  # Четвёртый шард поднимается вместе с остальными, но в кольцо и в router
  # добавляется только на этапе ресширдинга (задания 5 и 7).
  shard3:
    <<: *shard
    container_name: lab5_shard3
    ports: ["5443:5432"]
    volumes: [shard3_data:/var/lib/postgresql]

networks:
  shards:
    driver: bridge

volumes:
  shard0_data:
  shard1_data:
  shard2_data:
  shard3_data:
```

---

## Схема на каждом шарде

Шарды симметричны: одна и та же таблица, никакой связи между экземплярами.

```sql
-- Одна и та же таблица на каждом шарде: шарды независимы и симметричны.
-- Сервис CarRent, сущность rentals (аренды), shard key — user_id.
DROP TABLE IF EXISTS rentals;

CREATE TABLE rentals (
    id          BIGINT       PRIMARY KEY,
    user_id     BIGINT       NOT NULL,
    car_id      BIGINT       NOT NULL,
    started_at  TIMESTAMPTZ  NOT NULL,
    finished_at TIMESTAMPTZ,
    minute_fee  NUMERIC(8,2) NOT NULL
);

-- Запросы сервиса идут по shard key, поэтому индекс по нему обязателен
-- внутри каждого шарда: шардирование убирает лишние шарды, индекс — лишние строки.
CREATE INDEX idx_rentals_user ON rentals (user_id, started_at DESC);
```

---

## Задание 3. Демонстрация router на живых шардах

```text
==============================================================================
Маршрутизация ключей: hash(key) % N
==============================================================================
  hash(101) % 3 -> shard1   (hash = 4085873138522744359)
  hash(102) % 3 -> shard2   (hash = 17044249250018982011)
  hash(103) % 3 -> shard1   (hash = 7598925360551452939)
  hash(12345) % 3 -> shard1   (hash = 9402613386967674988)

Тот же ключ в кольце consistent hashing (3 шарда):
  ring(101) -> shard1
  ring(102) -> shard0
  ring(103) -> shard0
  ring(12345) -> shard0

Детерминированность: тот же ключ в новом процессе даёт тот же шард
  stable_hash('101') = 4085873138522744359

==============================================================================
Сервис поверх текущего кластера (4 шарда, consistent hashing)
==============================================================================

Сколько записей на каждом шарде:
   shard0: 28,243
   shard1: 22,467
   shard2: 24,784
   shard3: 24,506
   всего: 100,000

GET /rentals?user_id=443  -> router выбрал shard1
id   | car_id | minute_fee |       started_at       
-------+--------+------------+------------------------
 53908 |   1344 |      16.19 | 2026-08-23 10:59:03+00
 34845 |   2799 |      10.79 | 2026-08-23 01:05:11+00
 98476 |    589 |      15.58 | 2026-08-21 23:39:36+00
 80456 |    430 |       6.69 | 2026-08-19 22:15:23+00
 72332 |    910 |      14.73 | 2026-08-19 15:58:02+00
(5 rows)
POST /rentals (user_id=443) -> записано в shard1
   тот же шард, что и для чтения: True
   запись найдена на shard1: 1
   (демонстрационная запись удалена)
```

---

## Стратегии шардирования — `app/hashing.py`

```python
"""Две стратегии шардирования: hash(key) % N и Consistent Hashing.

Про выбор хеш-функции. Встроенная hash() в Python для строк рандомизируется
при каждом запуске процесса (PYTHONHASHSEED), поэтому для шардирования она
непригодна: после перезапуска сервиса один и тот же ключ уехал бы на другой
шард. Нужна детерминированная функция — здесь md5, взятая как 64-битное целое.
Криптостойкость тут не нужна, нужна воспроизводимость и равномерность.
"""
import bisect
import hashlib


def stable_hash(value) -> int:
    """Детерминированный хеш ключа: одинаков во всех процессах и запусках."""
    digest = hashlib.md5(str(value).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


class ModuloRouter:
    """shard = hash(key) % N — простейшая стратегия."""

    name = "hash(key) % N"

    def __init__(self, shards: list[str]):
        self.shards = list(shards)

    def route(self, key) -> str:
        return self.shards[stable_hash(key) % len(self.shards)]

    def with_shard(self, shard: str) -> "ModuloRouter":
        """Новый router с добавленным шардом — для эксперимента 3 -> 4."""
        return ModuloRouter(self.shards + [shard])

    def without_shard(self, shard: str) -> "ModuloRouter":
        return ModuloRouter([s for s in self.shards if s != shard])


class ConsistentHashRing:
    """Кольцо consistent hashing с виртуальными узлами.

    Каждый шард представлен на кольце не одной точкой, а vnodes точками.
    Ключ отображается в точку кольца и обслуживается первым шардом по часовой
    стрелке. При добавлении шарда переезжают только ключи участков, которые
    забрал новичок, а не весь набор.
    """

    name = "Consistent Hashing"

    def __init__(self, shards: list[str], vnodes: int = 150):
        self.vnodes = vnodes
        self._points: list[int] = []      # отсортированные позиции на кольце
        self._owners: dict[int, str] = {}  # позиция -> шард
        self.shards: list[str] = []
        for shard in shards:
            self.add_shard(shard)

    def _vnode_points(self, shard: str):
        for i in range(self.vnodes):
            yield stable_hash(f"{shard}#{i}")

    def add_shard(self, shard: str) -> None:
        if shard in self.shards:
            return
        self.shards.append(shard)
        for point in self._vnode_points(shard):
            if point not in self._owners:
                self._owners[point] = shard
                bisect.insort(self._points, point)

    def remove_shard(self, shard: str) -> None:
        if shard not in self.shards:
            return
        self.shards.remove(shard)
        for point in self._vnode_points(shard):
            if self._owners.get(point) == shard:
                del self._owners[point]
                idx = bisect.bisect_left(self._points, point)
                if idx < len(self._points) and self._points[idx] == point:
                    self._points.pop(idx)

    def route(self, key) -> str:
        if not self._points:
            raise RuntimeError("кольцо пустое")
        h = stable_hash(key)
        idx = bisect.bisect_right(self._points, h)
        if idx == len(self._points):     # прошли конец кольца — возвращаемся в начало
            idx = 0
        return self._owners[self._points[idx]]

    def with_shard(self, shard: str) -> "ConsistentHashRing":
        ring = ConsistentHashRing(self.shards, self.vnodes)
        ring.add_shard(shard)
        return ring

    def without_shard(self, shard: str) -> "ConsistentHashRing":
        ring = ConsistentHashRing(self.shards, self.vnodes)
        ring.remove_shard(shard)
        return ring
```

---

## Router сервиса — `app/router.py`

```python
"""Router сервиса CarRent: определяет, на каком шарде живёт запись.

Сервис не обращается к шардам напрямую — он спрашивает router, а тот по
shard key (user_id) возвращает нужный экземпляр PostgreSQL. Стратегия
подменяется целиком, интерфейс не меняется.
"""
import shards


class ShardRouter:
    def __init__(self, strategy):
        self.strategy = strategy

    @property
    def shard_names(self) -> list[str]:
        return list(self.strategy.shards)

    def shard_for(self, user_id: int) -> str:
        """Главная функция router: ключ -> имя шарда."""
        return self.strategy.route(user_id)

    # --- операции сервиса -------------------------------------------------
    def create_rental(self, rental: dict) -> str:
        """POST /rentals — пишем в шард, определённый по user_id."""
        shard = self.shard_for(rental["user_id"])
        shards.query(shard, f"""
            INSERT INTO rentals (id, user_id, car_id, started_at, finished_at, minute_fee)
            VALUES ({rental['id']}, {rental['user_id']}, {rental['car_id']},
                    '{rental['started_at']}', NULL, {rental['minute_fee']});""")
        return shard

    def user_rentals(self, user_id: int, limit: int = 5) -> tuple[str, str]:
        """GET /rentals?user_id=... — запрос идёт ровно в один шард."""
        shard = self.shard_for(user_id)
        rows = shards.query(shard, f"""
            SELECT id, car_id, minute_fee, started_at
            FROM rentals WHERE user_id = {user_id}
            ORDER BY started_at DESC LIMIT {limit};""", tuples_only=False)
        return shard, rows

    def count_all(self) -> dict[str, int]:
        """Счётчики по шардам — этот запрос, наоборот, обходит все шарды."""
        return {s: shards.count(s) for s in self.shard_names}

    def total(self) -> int:
        return sum(self.count_all().values())
```

---

## Реестр шардов — `app/shards.py`

```python
"""Реестр шардов и доступ к ним.

Шарды — независимые экземпляры PostgreSQL. Они ничего не знают друг о друге:
о том, где лежит запись, знает только router в сервисе.
"""
import os
import subprocess

DB_USER = os.environ.get("DB_USERNAME", "postgres")
DB_NAME = os.environ.get("DB_DATABASE", "postgres")
DB_PASS = os.environ.get("DB_PASSWORD", "12341234")

# 127.0.0.1, а не localhost: localhost резолвится в ::1, и проброс порта
# Docker по IPv6 в этой системе не отвечает.
SHARDS = {
    "shard0": {"host": "127.0.0.1", "port": "5440", "container": "lab5_shard0"},
    "shard1": {"host": "127.0.0.1", "port": "5441", "container": "lab5_shard1"},
    "shard2": {"host": "127.0.0.1", "port": "5442", "container": "lab5_shard2"},
    "shard3": {"host": "127.0.0.1", "port": "5443", "container": "lab5_shard3"},
}

INITIAL_SHARDS = ["shard0", "shard1", "shard2"]
NEW_SHARD = "shard3"


class ShardError(RuntimeError):
    pass


def _psql_args(shard: str, tuples_only: bool) -> list[str]:
    node = SHARDS[shard]
    args = ["psql", "-h", node["host"], "-p", node["port"], "-U", DB_USER,
            "-d", DB_NAME, "-X", "-q", "-v", "ON_ERROR_STOP=1"]
    if tuples_only:
        args += ["-t", "-A"]
    return args


def query(shard: str, sql: str, *, tuples_only: bool = True) -> str:
    env = dict(os.environ, PGPASSWORD=DB_PASS)
    proc = subprocess.run(_psql_args(shard, tuples_only), input=sql,
                          capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise ShardError(f"{shard}: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout.strip()


def copy_in(shard: str, table: str, rows: str) -> None:
    """Массовая загрузка через COPY ... FROM STDIN."""
    env = dict(os.environ, PGPASSWORD=DB_PASS)
    sql = f"COPY {table} FROM STDIN WITH (FORMAT csv)"
    args = _psql_args(shard, False) + ["-c", f"\\copy {table} FROM STDIN WITH (FORMAT csv)"]
    proc = subprocess.run(args, input=rows, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise ShardError(f"{shard}: {proc.stderr.strip()}")


def count(shard: str, table: str = "rentals") -> int:
    out = query(shard, f"SELECT count(*) FROM {table};")
    return int(out.splitlines()[-1])


def apply_schema(shard: str, ddl: str) -> None:
    query(shard, ddl)
```

---

## Демонстрация router — `app/demo_router.py`

```python
#!/usr/bin/env python3
"""Демонстрация router на живых шардах (задание 3).

Показывает саму маршрутизацию и работу эндпойнтов сервиса поверх текущего
состояния кластера (4 шарда, consistent hashing — итог задания 7).
"""
import hashing
import router as router_mod
import shards

print("=" * 78)
print("Маршрутизация ключей: hash(key) % N")
print("=" * 78)
mod = hashing.ModuloRouter(shards.INITIAL_SHARDS)
for key in (101, 102, 103, 12345):
    print(f"  hash({key}) % 3 -> {mod.route(key)}   "
          f"(hash = {hashing.stable_hash(key)})")

print()
print("Тот же ключ в кольце consistent hashing (3 шарда):")
ring3 = hashing.ConsistentHashRing(shards.INITIAL_SHARDS)
for key in (101, 102, 103, 12345):
    print(f"  ring({key}) -> {ring3.route(key)}")

print()
print("Детерминированность: тот же ключ в новом процессе даёт тот же шард")
print(f"  stable_hash('101') = {hashing.stable_hash(101)}")

print()
print("=" * 78)
print("Сервис поверх текущего кластера (4 шарда, consistent hashing)")
print("=" * 78)
ring = hashing.ConsistentHashRing(shards.INITIAL_SHARDS + [shards.NEW_SHARD])
svc = router_mod.ShardRouter(ring)

print("\nСколько записей на каждом шарде:")
counts = svc.count_all()
for s in sorted(counts):
    print(f"   {s}: {counts[s]:,}")
print(f"   всего: {svc.total():,}")

user_id = 443          # самый активный пользователь набора
shard, rows = svc.user_rentals(user_id, limit=5)
print(f"\nGET /rentals?user_id={user_id}  -> router выбрал {shard}")
print(rows)

new_rental = {"id": 999_000_001, "user_id": user_id, "car_id": 77,
              "started_at": "2026-09-15 10:00:00", "minute_fee": 14.50}
target = svc.create_rental(new_rental)
print(f"POST /rentals (user_id={user_id}) -> записано в {target}")
print("   тот же шард, что и для чтения:", target == shard)

found = shards.query(target, f"SELECT count(*) FROM rentals WHERE id = {new_rental['id']};")
print(f"   запись найдена на {target}: {found.splitlines()[-1]}")
shards.query(target, f"DELETE FROM rentals WHERE id = {new_rental['id']};")
print("   (демонстрационная запись удалена)")
```

---

## Эксперимент — `app/run_lab5.py`

```python
#!/usr/bin/env python3
"""Лабораторная работа №5: шардирование CarRent, router и consistent hashing.

Эксперимент проводится дважды на одних и тех же данных:
  A) router на hash(key) % N,  3 шарда -> 4;
  B) router на consistent hash ring, 3 шарда -> 4.
Записи переносятся физически, а не только пересчитываются.
"""
import argparse
import datetime as dt
import io
import os
import random
import sys
import time

import hashing
import router as router_mod
import shards

BASE = os.path.dirname(os.path.abspath(__file__))
DDL = open(os.path.join(BASE, "..", "sql", "01_shard_schema.sql"), encoding="utf-8").read()

TOTAL_ROWS = int(os.environ.get("TOTAL_ROWS", "100000"))
USERS = int(os.environ.get("USERS", "20000"))
SEED = 20260915


def log(msg: str = "") -> None:
    print(msg, flush=True)


def rule(title: str) -> None:
    log()
    log("=" * 78)
    log(title)
    log("=" * 78)


# --------------------------------------------------------------------------
def generate_rentals(n: int) -> list[dict]:
    """Данные сервиса: аренды CarRent. user_id — будущий shard key."""
    rnd = random.Random(SEED)
    base = dt.datetime(2026, 6, 1)
    rentals = []
    for i in range(1, n + 1):
        rentals.append({
            "id": i,
            # Пользователи не равновероятны: у активных аренд больше.
            # Так ближе к реальности, чем ровное распределение.
            "user_id": rnd.randint(1, USERS) if i % 4 else rnd.randint(1, USERS // 20),
            "car_id": rnd.randint(1, 5000),
            "started_at": (base + dt.timedelta(seconds=rnd.randint(0, 90 * 86400))
                           ).strftime("%Y-%m-%d %H:%M:%S"),
            "minute_fee": round(rnd.uniform(5, 25), 2),
        })
    return rentals


def reset_shards(shard_names: list[str]) -> None:
    for s in shard_names:
        shards.apply_schema(s, DDL)


def bulk_load(strategy, rentals: list[dict]) -> dict[str, int]:
    """Разложить записи по шардам согласно стратегии и залить через COPY."""
    buckets: dict[str, io.StringIO] = {s: io.StringIO() for s in strategy.shards}
    counts = {s: 0 for s in strategy.shards}
    for r in rentals:
        shard = strategy.route(r["user_id"])
        buckets[shard].write(
            f"{r['id']},{r['user_id']},{r['car_id']},{r['started_at']},,{r['minute_fee']}\n")
        counts[shard] += 1
    for shard, buf in buckets.items():
        shards.copy_in(shard, "rentals", buf.getvalue())
    return counts


def distribution_table(counts: dict[str, int], total: int) -> None:
    log(f"{'шард':<10}{'записей':>12}{'доля':>10}{'отклонение от идеала':>24}")
    ideal = total / len(counts)
    for shard in sorted(counts):
        n = counts[shard]
        log(f"{shard:<10}{n:>12,}{n / total * 100:>9.2f}%"
            f"{(n - ideal) / ideal * 100:>+23.2f}%")
    log(f"{'итого':<10}{total:>12,}")
    spread = (max(counts.values()) - min(counts.values())) / ideal * 100
    log(f"разброс между самым большим и самым маленьким шардом: {spread:.2f}% от идеала")


# --------------------------------------------------------------------------
def migrate(old_strategy, new_strategy, rentals: list[dict], label: str) -> dict:
    """Физически перенести записи, которым новая стратегия назначила другой шард."""
    log(f"\nПересчитываем расположение {len(rentals):,} записей...")
    moves: dict[tuple[str, str], list[int]] = {}
    stayed = 0
    for r in rentals:
        old = old_strategy.route(r["user_id"])
        new = new_strategy.route(r["user_id"])
        if old == new:
            stayed += 1
        else:
            moves.setdefault((old, new), []).append(r["id"])

    moved = sum(len(v) for v in moves.values())
    log(f"остаются на месте: {stayed:,}")
    log(f"меняют шард:       {moved:,}  ({moved / len(rentals) * 100:.2f}%)")

    log("\nпотоки переноса:")
    for (old, new), ids in sorted(moves.items()):
        log(f"   {old} -> {new}: {len(ids):,}")

    log("\nпереносим физически...")
    t0 = time.perf_counter()
    for (old, new), ids in sorted(moves.items()):
        for chunk_start in range(0, len(ids), 5000):
            chunk = ids[chunk_start:chunk_start + 5000]
            id_list = ",".join(map(str, chunk))
            rows = shards.query(old, f"""
                COPY (SELECT id, user_id, car_id, started_at, finished_at, minute_fee
                      FROM rentals WHERE id IN ({id_list})) TO STDOUT WITH (FORMAT csv);""")
            if rows:
                shards.copy_in(new, "rentals", rows + "\n")
            shards.query(old, f"DELETE FROM rentals WHERE id IN ({id_list});")
    elapsed = time.perf_counter() - t0
    log(f"перенос занял {elapsed:.1f} с")

    return {"label": label, "moved": moved, "stayed": stayed,
            "total": len(rentals), "pct": moved / len(rentals) * 100,
            "seconds": elapsed}


def verify(strategy, rentals: list[dict], sample: int = 2000) -> None:
    """Проверить, что router действительно находит записи там, где ожидает."""
    rnd = random.Random(SEED + 1)
    probes = rnd.sample(rentals, min(sample, len(rentals)))
    bad = 0
    for r in probes:
        shard = strategy.route(r["user_id"])
        found = shards.query(shard, f"SELECT count(*) FROM rentals WHERE id = {r['id']};")
        if found.splitlines()[-1] != "1":
            bad += 1
    log(f"проверено {len(probes)} случайных записей: "
        f"{'все найдены на предсказанном шарде' if bad == 0 else f'НЕ НАЙДЕНО: {bad}'}")


# --------------------------------------------------------------------------
def experiment(strategy_factory, name: str, rentals: list[dict]) -> dict:
    rule(f"Стратегия: {name}")

    strategy = strategy_factory(shards.INITIAL_SHARDS)
    all_shards = shards.INITIAL_SHARDS + [shards.NEW_SHARD]
    reset_shards(all_shards)

    log(f"\nЗагружаем {len(rentals):,} аренд на {len(strategy.shards)} шарда")
    t0 = time.perf_counter()
    counts = bulk_load(strategy, rentals)
    log(f"загрузка заняла {time.perf_counter() - t0:.1f} с\n")
    distribution_table(counts, len(rentals))

    log("\nФактические счётчики, прочитанные из самих PostgreSQL:")
    live = {s: shards.count(s) for s in strategy.shards}
    for s in sorted(live):
        log(f"   {s}: {live[s]:,}")
    assert live == counts, "расхождение между ожиданием router и содержимым шардов"
    log("совпадает с расчётом router")

    verify(strategy, rentals)

    rule(f"{name}: добавляем 4-й шард")
    new_strategy = strategy.with_shard(shards.NEW_SHARD)
    stats = migrate(strategy, new_strategy, rentals, name)

    log("\nРаспределение после добавления шарда:")
    live_after = {s: shards.count(s) for s in all_shards}
    distribution_table(live_after, sum(live_after.values()))
    assert sum(live_after.values()) == len(rentals), "записи потерялись при переносе"
    log("суммарное число записей не изменилось — ничего не потеряно")

    verify(new_strategy, rentals)
    stats["after"] = live_after
    stats["before"] = counts
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=TOTAL_ROWS)
    args = ap.parse_args()

    rule("Данные сервиса CarRent")
    rentals = generate_rentals(args.rows)
    uniq_users = len({r["user_id"] for r in rentals})
    log(f"аренд: {len(rentals):,}")
    log(f"различных user_id (shard key): {uniq_users:,}")
    top = {}
    for r in rentals:
        top[r["user_id"]] = top.get(r["user_id"], 0) + 1
    hottest = sorted(top.items(), key=lambda kv: -kv[1])[:3]
    log("самые активные пользователи: " +
        ", ".join(f"user {u} — {c} аренд" for u, c in hottest))

    results = []
    results.append(experiment(hashing.ModuloRouter, "hash(key) % N", rentals))
    results.append(experiment(hashing.ConsistentHashRing, "Consistent Hashing", rentals))

    rule("Что будет дальше: ещё шарды и удаление шарда")
    log("Расчёт на тех же ключах, без физического переноса.\n")
    keys = [r["user_id"] for r in rentals]

    log(f"{'переход':<22}{'hash(key) % N':>18}{'Consistent Hashing':>22}{'теор. минимум':>16}")
    for n in (3, 4, 5, 6, 7):
        names = [f"shard{i}" for i in range(n)]
        added = f"shard{n}"
        mod_before = hashing.ModuloRouter(names)
        mod_after = mod_before.with_shard(added)
        ring_before = hashing.ConsistentHashRing(names)
        ring_after = ring_before.with_shard(added)
        mod_moved = sum(1 for k in keys if mod_before.route(k) != mod_after.route(k))
        ring_moved = sum(1 for k in keys if ring_before.route(k) != ring_after.route(k))
        log(f"{f'{n} -> {n + 1} шардов':<22}{mod_moved / len(keys) * 100:>17.2f}%"
            f"{ring_moved / len(keys) * 100:>21.2f}%{1 / (n + 1) * 100:>15.2f}%")

    log("\nУдаление шарда (3 шарда, выводим shard2):")
    names = [f"shard{i}" for i in range(3)]
    mod_b, ring_b = hashing.ModuloRouter(names), hashing.ConsistentHashRing(names)
    mod_a, ring_a = mod_b.without_shard("shard2"), ring_b.without_shard("shard2")
    mod_moved = sum(1 for k in keys if mod_b.route(k) != mod_a.route(k))
    ring_moved = sum(1 for k in keys if ring_b.route(k) != ring_a.route(k))
    on_removed = sum(1 for k in keys if mod_b.route(k) == "shard2")
    log(f"   записей жило на shard2:      {on_removed:,} ({on_removed / len(keys) * 100:.2f}%)")
    log(f"   hash(key) % N  переезжает:   {mod_moved:,} ({mod_moved / len(keys) * 100:.2f}%)")
    log(f"   Consistent Hashing:          {ring_moved:,} ({ring_moved / len(keys) * 100:.2f}%)")
    log("   у кольца переезжают только записи удалённого шарда, остальные не трогаются")

    log("\nВлияние числа виртуальных узлов на равномерность (3 шарда):")
    log(f"{'vnodes':>8}{'разброс между шардами':>26}")
    for v in (1, 5, 20, 50, 150, 500):
        ring = hashing.ConsistentHashRing([f"shard{i}" for i in range(3)], vnodes=v)
        c = {}
        for k in keys:
            sh = ring.route(k)
            c[sh] = c.get(sh, 0) + 1
        ideal = len(keys) / 3
        spread = (max(c.values()) - min(c.values())) / ideal * 100
        log(f"{v:>8}{spread:>25.2f}%")

    rule("Сравнение стратегий: 3 шарда -> 4 шарда")
    log(f"{'стратегия':<24}{'перемещено':>14}{'доля':>10}{'время переноса':>18}")
    for r in results:
        log(f"{r['label']:<24}{r['moved']:>14,}{r['pct']:>9.2f}%{r['seconds']:>17.1f} с")
    best, worst = min(results, key=lambda r: r["pct"]), max(results, key=lambda r: r["pct"])
    log(f"\n{best['label']} перемещает в {worst['pct'] / best['pct']:.1f} раза меньше данных")
    log(f"теоретический минимум при 3 -> 4 равен 1/4 = 25.00%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

---

## Полный вывод эксперимента

```text
==============================================================================
Данные сервиса CarRent
==============================================================================
аренд: 100,000
различных user_id (shard key): 19,556
самые активные пользователи: user 443 — 47 аренд, user 98 — 47 аренд, user 666 — 47 аренд

==============================================================================
Стратегия: hash(key) % N
==============================================================================

Загружаем 100,000 аренд на 3 шарда
загрузка заняла 0.8 с

шард           записей      доля    отклонение от идеала
shard0          32,896    32.90%                  -1.31%
shard1          33,368    33.37%                  +0.10%
shard2          33,736    33.74%                  +1.21%
итого          100,000
разброс между самым большим и самым маленьким шардом: 2.52% от идеала

Фактические счётчики, прочитанные из самих PostgreSQL:
   shard0: 32,896
   shard1: 33,368
   shard2: 33,736
совпадает с расчётом router
проверено 2000 случайных записей: все найдены на предсказанном шарде

==============================================================================
hash(key) % N: добавляем 4-й шард
==============================================================================

Пересчитываем расположение 100,000 записей...
остаются на месте: 24,752
меняют шард:       75,248  (75.25%)

потоки переноса:
   shard0 -> shard1: 8,109
   shard0 -> shard2: 8,079
   shard0 -> shard3: 8,301
   shard1 -> shard0: 8,303
   shard1 -> shard2: 8,431
   shard1 -> shard3: 8,305
   shard2 -> shard0: 8,373
   shard2 -> shard1: 8,640
   shard2 -> shard3: 8,707

переносим физически...
перенос занял 4.7 с

Распределение после добавления шарда:
шард           записей      доля    отклонение от идеала
shard0          25,083    25.08%                  +0.33%
shard1          25,078    25.08%                  +0.31%
shard2          24,526    24.53%                  -1.90%
shard3          25,313    25.31%                  +1.25%
итого          100,000
разброс между самым большим и самым маленьким шардом: 3.15% от идеала
суммарное число записей не изменилось — ничего не потеряно
проверено 2000 случайных записей: все найдены на предсказанном шарде

==============================================================================
Стратегия: Consistent Hashing
==============================================================================

Загружаем 100,000 аренд на 3 шарда
загрузка заняла 0.8 с

шард           записей      доля    отклонение от идеала
shard0          34,783    34.78%                  +4.35%
shard1          32,023    32.02%                  -3.93%
shard2          33,194    33.19%                  -0.42%
итого          100,000
разброс между самым большим и самым маленьким шардом: 8.28% от идеала

Фактические счётчики, прочитанные из самих PostgreSQL:
   shard0: 34,783
   shard1: 32,023
   shard2: 33,194
совпадает с расчётом router
проверено 2000 случайных записей: все найдены на предсказанном шарде

==============================================================================
Consistent Hashing: добавляем 4-й шард
==============================================================================

Пересчитываем расположение 100,000 записей...
остаются на месте: 75,494
меняют шард:       24,506  (24.51%)

потоки переноса:
   shard0 -> shard3: 6,540
   shard1 -> shard3: 9,556
   shard2 -> shard3: 8,410

переносим физически...
перенос занял 1.4 с

Распределение после добавления шарда:
шард           записей      доля    отклонение от идеала
shard0          28,243    28.24%                 +12.97%
shard1          22,467    22.47%                 -10.13%
shard2          24,784    24.78%                  -0.86%
shard3          24,506    24.51%                  -1.98%
итого          100,000
разброс между самым большим и самым маленьким шардом: 23.10% от идеала
суммарное число записей не изменилось — ничего не потеряно
проверено 2000 случайных записей: все найдены на предсказанном шарде

==============================================================================
Что будет дальше: ещё шарды и удаление шарда
==============================================================================
Расчёт на тех же ключах, без физического переноса.

переход                    hash(key) % N    Consistent Hashing   теор. минимум
3 -> 4 шардов                     75.25%                24.51%          25.00%
4 -> 5 шардов                     80.00%                17.27%          20.00%
5 -> 6 шардов                     83.50%                15.43%          16.67%
6 -> 7 шардов                     85.92%                14.16%          14.29%
7 -> 8 шардов                     87.73%                11.79%          12.50%

Удаление шарда (3 шарда, выводим shard2):
   записей жило на shard2:      33,736 (33.74%)
   hash(key) % N  переезжает:   66,880 (66.88%)
   Consistent Hashing:          33,194 (33.19%)
   у кольца переезжают только записи удалённого шарда, остальные не трогаются

Влияние числа виртуальных узлов на равномерность (3 шарда):
  vnodes     разброс между шардами
       1                   242.72%
       5                    67.25%
      20                    63.53%
      50                    21.35%
     150                     8.28%
     500                     4.42%

==============================================================================
Сравнение стратегий: 3 шарда -> 4 шарда
==============================================================================
стратегия                   перемещено      доля    время переноса
hash(key) % N                   75,248    75.25%              4.7 с
Consistent Hashing              24,506    24.51%              1.4 с

Consistent Hashing перемещает в 3.1 раза меньше данных
теоретический минимум при 3 -> 4 равен 1/4 = 25.00%
```
