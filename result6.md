# Результаты выполнения — лабораторная работа №6

Полный протокол: схема, код распределённого слоя и фактический вывод
экспериментов. Отчёт с выводами — в [`README6.md`](README6.md).

Сервис — **Triply-User** (микросервис профилей пассажиров и водителей
платформы Triply, Go + GORM + PostgreSQL). Запросы взяты из реального кода
`internal/infrastructure/postgresrepo/`.

Стенд: четыре независимых PostgreSQL 18.6 на портах 5440-5443, shard key —
`user_id`, маршрутизация — consistent hash ring из лабораторной №5.
Данные: 50 000 профилей (40 000 пассажиров, 10 000 водителей), 10 000 машин,
200 000 оценок.

---

## Схема на каждом шарде

```sql
-- Схема сервиса Triply-User на каждом шарде.
-- Взята из GORM-моделей internal/infrastructure/postgresrepo/models.go.
--
-- Shard key — user_id. По нему шардируются ВСЕ таблицы:
--   users.id, riders.user_id, drivers.user_id, vehicles.driver_id, ratings.ratee_id
-- то есть профиль водителя, его машина и полученные им оценки лежат вместе.

DROP TABLE IF EXISTS ratings, vehicles, drivers, riders, users CASCADE;

CREATE TABLE users (
    id         UUID        PRIMARY KEY,
    phone      TEXT        NOT NULL,
    email      TEXT,
    name       TEXT        NOT NULL,
    avatar_url TEXT,
    role       TEXT        NOT NULL CHECK (role IN ('rider','driver','admin')),
    rating     REAL        NOT NULL DEFAULT 0,
    trip_count INT         NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX idx_users_phone ON users (phone);

CREATE TABLE riders (
    user_id                   UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    default_payment_method_id UUID,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE drivers (
    user_id            UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    status             TEXT NOT NULL DEFAULT 'offline'
                            CHECK (status IN ('offline','available','on_trip')),
    licence_url        TEXT,
    background_checked BOOLEAN NOT NULL DEFAULT false,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Под запрос DriverRepository.List: WHERE status = ? ORDER BY created_at DESC
CREATE INDEX idx_drivers_status_created ON drivers (status, created_at DESC, user_id DESC);

CREATE TABLE vehicles (
    id           UUID PRIMARY KEY,
    driver_id    UUID NOT NULL REFERENCES drivers(user_id) ON DELETE CASCADE,
    make         TEXT NOT NULL,
    model        TEXT NOT NULL,
    year         INT,
    color        TEXT,
    plate_number TEXT NOT NULL,
    category     TEXT NOT NULL CHECK (category IN ('economy','comfort','xl')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX idx_vehicles_driver ON vehicles (driver_id);

CREATE TABLE ratings (
    id         UUID PRIMARY KEY,
    trip_id    UUID,
    rater_id   UUID NOT NULL,          -- кто оценил: может жить на ДРУГОМ шарде
    ratee_id   UUID NOT NULL,          -- кого оценили: это и есть shard key
    score      REAL NOT NULL,
    comment    TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Под RatingRepository.List: WHERE ratee_id = ? ORDER BY created_at DESC, id DESC
CREATE INDEX idx_ratings_ratee ON ratings (ratee_id, created_at DESC, id DESC);
CREATE INDEX idx_ratings_trip  ON ratings (trip_id);
```

---

## Распределённый слой запросов — `app/triply.py`

```python
"""Распределённый слой доступа к данным Triply-User поверх шардов.

Повторяет реальные запросы сервиса из
internal/infrastructure/postgresrepo/{driver,rider,rating}.go, но поверх
четырёх независимых PostgreSQL. Каждая операция помечена типом:

  SINGLE  — router вычисляет шард по shard key, запрос идёт в один PostgreSQL;
  SCATTER — shard key не задан, запрос идёт во все шарды, слияние в приложении.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "lab5", "app"))

import hashing   # noqa: E402
import shards    # noqa: E402

ALL_SHARDS = shards.INITIAL_SHARDS + [shards.NEW_SHARD]
RING = hashing.ConsistentHashRing(ALL_SHARDS)


class ShardDown(RuntimeError):
    def __init__(self, shard: str, detail: str):
        self.shard = shard
        super().__init__(f"{shard} недоступен: {detail}")


def shard_for(user_id: str) -> str:
    """Router: shard key -> имя шарда."""
    return RING.route(user_id)


def q(shard: str, sql: str, *, tuples_only: bool = True) -> str:
    try:
        return shards.query(shard, sql, tuples_only=tuples_only)
    except shards.ShardError as exc:
        raise ShardDown(shard, str(exc).splitlines()[0]) from exc


def scatter(sql: str, live: list[str] | None = None):
    """Разослать один запрос на все шарды и собрать ответы."""
    targets = live if live is not None else ALL_SHARDS
    out, failures = {}, []
    t0 = time.perf_counter()
    for s in targets:
        try:
            out[s] = q(s, sql)
        except ShardDown as exc:
            failures.append(exc)
    return out, (time.perf_counter() - t0) * 1000, failures


PG_ROOT = os.environ.get("LAB6_PGROOT", "")


def stop_shard(shard: str) -> str:
    """Остановить шард: контейнер Docker или локальный кластер."""
    import subprocess
    if PG_ROOT:
        d = os.path.join(PG_ROOT, shard.replace("shard", "shard"))
        subprocess.run(["pg_ctl", "-D", d, "-m", "fast", "stop"], capture_output=True)
        return f"pg_ctl stop {d}"
    container = shards.SHARDS[shard]["container"]
    subprocess.run(["docker", "stop", container], capture_output=True)
    return f"docker stop {container}"


def start_shard(shard: str) -> str:
    import subprocess
    if PG_ROOT:
        d = os.path.join(PG_ROOT, shard)
        port = shards.SHARDS[shard]["port"]
        subprocess.run(["pg_ctl", "-D", d,
                        "-o", f"-p {port} -c listen_addresses=127.0.0.1 "
                              f"-c unix_socket_directories='' -c shared_buffers=128MB",
                        "-l", os.path.join(d, "server.log"), "start"], capture_output=True)
        return f"pg_ctl start {d}"
    container = shards.SHARDS[shard]["container"]
    subprocess.run(["docker", "start", container], capture_output=True)
    return f"docker start {container}"


def timed(fn, *args, **kwargs):
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, (time.perf_counter() - t0) * 1000


# ---------------------------------------------------------------- SINGLE ---
def get_driver(user_id: str):
    """GET /v1/drivers/{user_id} — три запроса DriverRepository.GetByID.

    Все три идут на ОДИН шард: drivers, users и vehicles шардированы
    одним и тем же ключом.
    """
    shard = shard_for(user_id)
    t0 = time.perf_counter()
    driver = q(shard, f"SELECT user_id, status, background_checked FROM drivers WHERE user_id = '{user_id}';")
    user = q(shard, f"SELECT id, name, phone, rating, trip_count FROM users WHERE id = '{user_id}';")
    vehicle = q(shard, f"SELECT id, make, model, plate_number, category FROM vehicles WHERE driver_id = '{user_id}';")
    return shard, (driver, user, vehicle), (time.perf_counter() - t0) * 1000


def list_ratings_of(ratee_id: str, limit: int = 10):
    """GET /v1/ratings?ratee_id=... — RatingRepository.List, один шард."""
    shard = shard_for(ratee_id)
    t0 = time.perf_counter()
    total = q(shard, f"SELECT count(*) FROM ratings WHERE ratee_id = '{ratee_id}';")
    rows = q(shard, f"""SELECT id, score, created_at FROM ratings
                        WHERE ratee_id = '{ratee_id}'
                        ORDER BY created_at DESC, id DESC LIMIT {limit};""")
    return shard, int(total.splitlines()[-1]), len(rows.splitlines()), (time.perf_counter() - t0) * 1000


def driver_average_rating(ratee_id: str):
    """Агрегация внутри одного шарда — так работает RatingRepository.Create."""
    shard = shard_for(ratee_id)
    t0 = time.perf_counter()
    avg = q(shard, f"SELECT COALESCE(AVG(score), 0), count(*) FROM ratings WHERE ratee_id = '{ratee_id}';")
    a, n = avg.splitlines()[-1].split("|")
    return shard, float(a), int(n), (time.perf_counter() - t0) * 1000


# --------------------------------------------------------------- SCATTER ---
def count_drivers_by_status(live: list[str] | None = None):
    """GROUP BY по всему сервису: частичная агрегация + слияние."""
    parts, ms, failures = scatter(
        "SELECT status, count(*) FROM drivers GROUP BY status;", live)
    merged: dict[str, int] = {}
    for out in parts.values():
        for line in out.splitlines():
            if line.strip():
                status, cnt = line.split("|")
                merged[status] = merged.get(status, 0) + int(cnt)
    return merged, parts, ms, failures


def platform_average_rating(live: list[str] | None = None):
    """Средний рейтинг по платформе. Показывает ловушку среднего средних."""
    parts, ms, failures = scatter(
        "SELECT count(*), COALESCE(sum(score),0), COALESCE(avg(score),0) FROM ratings;", live)
    per_shard, n_total, sum_total, avgs = {}, 0, 0.0, []
    for shard, out in parts.items():
        cnt, s_sum, s_avg = out.splitlines()[-1].split("|")
        per_shard[shard] = (int(cnt), float(s_sum), float(s_avg))
        n_total += int(cnt)
        sum_total += float(s_sum)
        if int(cnt):
            avgs.append(float(s_avg))
    correct = sum_total / n_total if n_total else 0.0
    naive = sum(avgs) / len(avgs) if avgs else 0.0
    return per_shard, correct, naive, n_total, ms, failures


def list_available_drivers(limit: int = 20, live: list[str] | None = None):
    """GET /v1/drivers?status=available — DriverRepository.List.

    Shard key в запросе НЕ участвует, поэтому это scatter-gather:
    берём top-limit с каждого шарда и сливаем в приложении.
    """
    parts, ms, failures = scatter(f"""
        SELECT user_id, created_at FROM drivers
        WHERE status = 'available'
        ORDER BY created_at DESC, user_id DESC
        LIMIT {limit};""", live)
    rows = []
    for shard, out in parts.items():
        for line in out.splitlines():
            if line.strip():
                uid, created = line.split("|")
                rows.append((created, uid, shard))
    rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
    return rows[:limit], parts, ms, failures


def single_shard_top(limit: int = 20, shard: str | None = None):
    """Что было бы, если бы взяли top-N только с одного шарда."""
    target = shard or ALL_SHARDS[0]
    out = q(target, f"""
        SELECT user_id, created_at FROM drivers
        WHERE status = 'available'
        ORDER BY created_at DESC, user_id DESC LIMIT {limit};""")
    # (user_id, created_at, shard) — тот же порядок полей, что и у merged,
    # иначе сравнение множеств молча сравнивает id с датами.
    return [(l.split("|")[0], l.split("|")[1], target)
            for l in out.splitlines() if l.strip()]


# ------------------------------------------------------ CROSS-SHARD JOIN ---
def ratings_with_rater_profiles(ratee_id: str, limit: int = 10):
    """Оценки водителя вместе с именами тех, кто их поставил.

    ratings шардированы по ratee_id, а профиль автора оценки (rater_id)
    лежит на шарде, вычисленном уже по другому ключу. Обычным JOIN не
    обойтись: приложение делает второй проход по нужным шардам.
    """
    t0 = time.perf_counter()
    home = shard_for(ratee_id)
    rows = q(home, f"""SELECT id, rater_id, score FROM ratings
                       WHERE ratee_id = '{ratee_id}'
                       ORDER BY created_at DESC LIMIT {limit};""")
    raters = [l.split("|")[1] for l in rows.splitlines() if l.strip()]

    # Группируем авторов по их шардам — иначе получится N запросов вместо
    # одного на шард.
    by_shard: dict[str, list[str]] = {}
    for rid in raters:
        by_shard.setdefault(shard_for(rid), []).append(rid)

    names: dict[str, str] = {}
    for shard, ids in by_shard.items():
        quoted = ",".join(f"'{i}'" for i in ids)
        out = q(shard, f"SELECT id, name FROM users WHERE id IN ({quoted});")
        for line in out.splitlines():
            if line.strip():
                uid, name = line.split("|")
                names[uid] = name
    return {
        "home_shard": home,
        "ratings": len(raters),
        "rater_shards": sorted(by_shard),
        "extra_queries": len(by_shard),
        "resolved": len(names),
        "ms": (time.perf_counter() - t0) * 1000,
    }


def local_join_driver_vehicle(limit_per_shard: int = 5):
    """JOIN drivers + users + vehicles — все три шардированы одинаково,
    поэтому JOIN выполняется обычным SQL внутри каждого шарда."""
    parts, ms, failures = scatter(f"""
        SELECT d.user_id, u.name, v.plate_number
        FROM drivers d
        JOIN users u ON u.id = d.user_id
        LEFT JOIN vehicles v ON v.driver_id = d.user_id
        WHERE d.status = 'available'
        LIMIT {limit_per_shard};""")
    total = sum(len([l for l in o.splitlines() if l.strip()]) for o in parts.values())
    return total, parts, ms, failures
```

---

## Наполнение шардов — `app/load_data.py`

```python
#!/usr/bin/env python3
"""Наполнение шардов данными Triply-User.

Распределение намеренно неравномерное по активности: часть водителей
получает в разы больше оценок, чем остальные. Это нужно для задания 7
(hot shard): строк на шардах поровну, а запросов — нет.
"""
import datetime as dt
import io
import os
import random
import sys
import uuid

import triply

RIDERS = int(os.environ.get("RIDERS", "40000"))
DRIVERS = int(os.environ.get("DRIVERS", "10000"))
RATINGS = int(os.environ.get("RATINGS", "200000"))
SEED = 20260917

MAKES = ["Toyota", "Kia", "Hyundai", "Skoda", "VW", "Renault"]
MODELS = ["Camry", "Rio", "Solaris", "Octavia", "Polo", "Logan"]
CATEGORIES = ["economy", "comfort", "xl"]
STATUSES = ["offline", "available", "on_trip"]


def uid(rnd: random.Random) -> str:
    return str(uuid.UUID(int=rnd.getrandbits(128), version=4))


def build():
    rnd = random.Random(SEED)
    riders = [uid(rnd) for _ in range(RIDERS)]
    drivers = [uid(rnd) for _ in range(DRIVERS)]

    # Регистрации размазаны по году: иначе ORDER BY created_at DESC
    # вырождается и top-N целиком приходит с того шарда, который грузили
    # последним.
    base = dt.datetime(2025, 9, 17)

    def when() -> str:
        return (base + dt.timedelta(seconds=rnd.randint(0, 365 * 86400))).strftime(
            "%Y-%m-%d %H:%M:%S")

    users, rider_rows, driver_rows, vehicle_rows = [], [], [], []
    for i, u in enumerate(riders):
        users.append((u, f"+7900{i:07d}", f"rider{i}@triply.test", f"Rider {i}", "rider", 0, rnd.randint(0, 300), when()))
        rider_rows.append((u, str(uuid.UUID(int=rnd.getrandbits(128), version=4)), when()))
    for i, u in enumerate(drivers):
        users.append((u, f"+7911{i:07d}", f"driver{i}@triply.test", f"Driver {i}", "driver", 0, rnd.randint(0, 5000), when()))
        driver_rows.append((u, rnd.choice(STATUSES), rnd.random() < 0.8, when()))
        vehicle_rows.append((uid(rnd), u, rnd.choice(MAKES), rnd.choice(MODELS),
                             rnd.randint(2012, 2026), f"A{i:04d}BC", rnd.choice(CATEGORIES)))

    # Оценки: 20% водителей получают 80% оценок — «звёзды» платформы.
    hot = drivers[: max(1, len(drivers) // 5)]
    ratings = []
    for i in range(RATINGS):
        ratee = rnd.choice(hot) if i % 5 else rnd.choice(drivers)
        ratings.append((uid(rnd), uid(rnd), rnd.choice(riders), ratee,
                        round(rnd.uniform(3.0, 5.0), 1), i, when()))
    return users, rider_rows, driver_rows, vehicle_rows, ratings, riders, drivers, hot


def load():
    users, rider_rows, driver_rows, vehicle_rows, ratings, riders, drivers, hot = build()

    bufs = {s: {t: io.StringIO() for t in
                ("users", "riders", "drivers", "vehicles", "ratings")}
            for s in triply.ALL_SHARDS}

    for u, phone, email, name, role, rating, trips, created in users:
        bufs[triply.shard_for(u)]["users"].write(
            f"{u},{phone},{email},{name},{role},{rating},{trips},{created}\n")
    for u, pay, created in rider_rows:
        bufs[triply.shard_for(u)]["riders"].write(f"{u},{pay},{created}\n")
    for u, status, checked, created in driver_rows:
        bufs[triply.shard_for(u)]["drivers"].write(
            f"{u},{status},{str(checked).lower()},{created}\n")
    for vid, drv, make, model, year, plate, cat in vehicle_rows:
        bufs[triply.shard_for(drv)]["vehicles"].write(
            f"{vid},{drv},{make},{model},{year},{plate},{cat}\n")
    # ratings шардируются по ratee_id — вместе с профилем оценённого
    for rid, trip, rater, ratee, score, i, created in ratings:
        bufs[triply.shard_for(ratee)]["ratings"].write(
            f"{rid},{trip},{rater},{ratee},{score},comment {i},{created}\n")

    cols = {
        "users": "(id, phone, email, name, role, rating, trip_count, created_at)",
        "riders": "(user_id, default_payment_method_id, created_at)",
        "drivers": "(user_id, status, background_checked, created_at)",
        "vehicles": "(id, driver_id, make, model, year, plate_number, category)",
        "ratings": "(id, trip_id, rater_id, ratee_id, score, comment, created_at)",
    }
    for shard in triply.ALL_SHARDS:
        for table in ("users", "riders", "drivers", "vehicles", "ratings"):
            data = bufs[shard][table].getvalue()
            if data:
                import shards as sh
                sh.copy_in(shard, f"{table} {cols[table]}", data)
    return riders, drivers, hot


if __name__ == "__main__":
    import shards as sh
    ddl = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "sql", "01_schema.sql"), encoding="utf-8").read()
    print("применяем схему на всех шардах...")
    for s in triply.ALL_SHARDS:
        sh.query(s, ddl)
    print("загружаем данные...")
    riders, drivers, hot = load()
    with open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "results", "keys.txt"), "w") as fh:
        fh.write("\n".join(["RIDERS"] + riders[:50] + ["DRIVERS"] + drivers[:50] +
                           ["HOT"] + hot[:50]))
    print(f"готово: {len(riders):,} пассажиров, {len(drivers):,} водителей, "
          f"{RATINGS:,} оценок")
```

---

## Эксперименты — `app/run_lab6.py`

```python
#!/usr/bin/env python3
"""Лабораторная работа №6: поведение Triply-User после шардирования."""
import os
import random
import subprocess
import sys
import time

import load_data
import triply

RESULTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results")


def log(msg: str = "") -> None:
    print(msg, flush=True)


def rule(title: str) -> None:
    log(); log("=" * 78); log(title); log("=" * 78)


def load_keys():
    keys = {"RIDERS": [], "DRIVERS": [], "HOT": []}
    section = None
    for line in open(os.path.join(RESULTS, "keys.txt"), encoding="utf-8"):
        line = line.strip()
        if line in keys:
            section = line
        elif line and section:
            keys[section].append(line)
    return keys


def distribution():
    rule("Распределение данных по шардам")
    log(f"{'шард':<10}{'users':>10}{'drivers':>10}{'vehicles':>10}{'ratings':>10}")
    totals = {"users": 0, "drivers": 0, "vehicles": 0, "ratings": 0}
    for s in triply.ALL_SHARDS:
        row = triply.q(s, """SELECT (SELECT count(*) FROM users),
                                    (SELECT count(*) FROM drivers),
                                    (SELECT count(*) FROM vehicles),
                                    (SELECT count(*) FROM ratings);""")
        u, d, v, r = map(int, row.splitlines()[-1].split("|"))
        totals["users"] += u; totals["drivers"] += d
        totals["vehicles"] += v; totals["ratings"] += r
        log(f"{s:<10}{u:>10,}{d:>10,}{v:>10,}{r:>10,}")
    log(f"{'итого':<10}{totals['users']:>10,}{totals['drivers']:>10,}"
        f"{totals['vehicles']:>10,}{totals['ratings']:>10,}")
    return totals


def task2(keys):
    rule("Задание 2. Single-Shard Query")
    log("GET /v1/drivers/{user_id} — DriverRepository.GetByID\n")
    for drv in keys["DRIVERS"][:3]:
        shard, (d, u, v), ms = triply.get_driver(drv)
        log(f"  driver {drv[:8]}...  -> {shard}   {ms:6.1f} мс   "
            f"профиль: {'есть' if u.strip() else 'нет'}, "
            f"машина: {'есть' if v.strip() else 'нет'}")
    log("\nВсе три запроса (drivers, users, vehicles) ушли в ОДИН шард:")
    log("  router вычислил его по user_id, остальные шарды не опрашивались.")

    log("\nGET /v1/ratings?ratee_id=... — RatingRepository.List")
    for drv in keys["HOT"][:3]:
        shard, total, got, ms = triply.list_ratings_of(drv, limit=10)
        log(f"  ratee {drv[:8]}...  -> {shard}   всего оценок {total:>5}, "
            f"отдано {got:>2}   {ms:6.1f} мс")


def task3(keys):
    rule("Задание 3. Агрегирующий запрос")
    log("а) Агрегация ВНУТРИ одного шарда: средний рейтинг водителя")
    log("   (именно так делает RatingRepository.Create после вставки оценки)\n")
    for drv in keys["HOT"][:3]:
        shard, avg, n, ms = triply.driver_average_rating(drv)
        log(f"   ratee {drv[:8]}... -> {shard}  AVG={avg:.3f} по {n:,} оценкам  {ms:6.1f} мс")

    log("\nб) Агрегация ПО ВСЕМУ сервису: GROUP BY status (GET /v1/drivers)\n")
    merged, parts, ms, _ = triply.count_drivers_by_status()
    for shard in sorted(parts):
        per = ", ".join(l.replace("|", "=") for l in parts[shard].splitlines() if l.strip())
        log(f"   {shard}: {per}")
    log(f"   -> слияние в приложении: " +
        ", ".join(f"{k}={v:,}" for k, v in sorted(merged.items())))
    log(f"   scatter-gather занял {ms:.1f} мс")

    log("\nв) Ловушка AVG: среднее средних != среднее по всем данным\n")
    per_shard, correct, naive, n_total, ms2, _ = triply.platform_average_rating()
    for shard in sorted(per_shard):
        cnt, s_sum, s_avg = per_shard[shard]
        log(f"   {shard}: count={cnt:>7,}  sum={s_sum:>12,.1f}  avg={s_avg:.4f}")
    log(f"\n   неверно (среднее из четырёх AVG): {naive:.6f}")
    log(f"   верно   (sum(sum) / sum(count)):  {correct:.6f}")
    log(f"   расхождение: {abs(naive - correct):.6f}")
    log(f"   всего оценок: {n_total:,}, scatter занял {ms2:.1f} мс")

    log("\nг) Время scatter при разном числе шардов (один и тот же COUNT):\n")
    log(f"   {'шардов':>8}{'время, мс':>14}")
    for n in (1, 2, 3, 4):
        subset = triply.ALL_SHARDS[:n]
        best = min(triply.scatter("SELECT count(*) FROM ratings;", subset)[1]
                   for _ in range(3))
        log(f"   {n:>8}{best:>14.1f}")


def task4(keys):
    rule("Задание 4. JOIN между шардами")
    log("а) JOIN, который остался локальным: drivers + users + vehicles")
    log("   Все три таблицы шардированы по user_id, значит связанные строки")
    log("   всегда лежат вместе и обычный SQL JOIN работает внутри шарда.\n")
    total, parts, ms, _ = triply.local_join_driver_vehicle()
    for shard in sorted(parts):
        n = len([l for l in parts[shard].splitlines() if l.strip()])
        log(f"   {shard}: JOIN выполнен локально, строк {n}")
    log(f"   итого {total} строк за {ms:.1f} мс, ни одной передачи между шардами")

    log("\nб) JOIN, который стал распределённым: ratings + профиль автора оценки")
    log("   ratings шардированы по ratee_id, а rater_id — чужой ключ,")
    log("   поэтому авторы оценок лежат на других шардах.\n")
    for drv in keys["HOT"][:3]:
        info = triply.ratings_with_rater_profiles(drv, limit=10)
        log(f"   ratee {drv[:8]}... живёт на {info['home_shard']}")
        log(f"      оценок получено:        {info['ratings']}")
        log(f"      авторы разбросаны по:   {', '.join(info['rater_shards'])}")
        log(f"      дополнительных запросов: {info['extra_queries']} "
            f"(вместо одного JOIN)")
        log(f"      суммарно {info['ms']:.1f} мс\n")


def task5():
    rule("Задание 5. ORDER BY + LIMIT")
    log("GET /v1/drivers?status=available — DriverRepository.List")
    log("Запрос не содержит shard key, поэтому идёт на все шарды.\n")

    merged, parts, ms, _ = triply.list_available_drivers(limit=20)
    for shard in sorted(parts):
        n = len([l for l in parts[shard].splitlines() if l.strip()])
        log(f"   {shard}: вернул {n} кандидатов")
    log(f"\n   после слияния в приложении — top-20 за {ms:.1f} мс")
    log("   откуда пришли строки итогового top-20:")
    origin: dict[str, int] = {}
    for created, uid, shard in merged:
        origin[shard] = origin.get(shard, 0) + 1
    for shard in sorted(origin):
        log(f"      {shard}: {origin[shard]} из 20")

    log("\n   Что было бы, если взять top-20 только с одного шарда:")
    single = triply.single_shard_top(limit=20)
    merged_ids = {uid for _, uid, _ in merged}       # merged: (created, uid, shard)
    single_ids = {uid for uid, _, _ in single}       # single: (uid, created, shard)
    hit = len(merged_ids & single_ids)
    log(f"      шард, к которому обратились: {single[0][2] if single else '-'}")
    log(f"      совпало с правильным ответом: {hit} из 20")
    log(f"      пропущено водителей:          {len(merged_ids - single_ids)}")
    log("      то есть ответ был бы просто неверным: более свежие водители")
    log("      с других шардов не попали бы в выдачу вообще.")


def task6(keys):
    rule("Задание 6. Отказ одного шарда")
    victim = triply.ALL_SHARDS[2]
    log(f"Останавливаем {victim}: {triply.stop_shard(victim)}\n")
    time.sleep(2)

    log("1) Запросы к пользователям, живущим на упавшем шарде:")
    dead_keys = [k for k in keys["DRIVERS"] if triply.shard_for(k) == victim][:2]
    for k in dead_keys:
        try:
            triply.get_driver(k)
            log(f"   driver {k[:8]}...: неожиданно успех")
        except triply.ShardDown as exc:
            log(f"   driver {k[:8]}...: ОТКАЗ — {str(exc)[:70]}")

    log("\n2) Запросы к пользователям с живых шардов:")
    alive_keys = [k for k in keys["DRIVERS"] if triply.shard_for(k) != victim][:3]
    for k in alive_keys:
        shard, _, ms = triply.get_driver(k)
        log(f"   driver {k[:8]}...: OK, обслужен {shard} за {ms:.1f} мс")

    log("\n3) Scatter-запрос (GET /v1/drivers?status=available):")
    live = [s for s in triply.ALL_SHARDS if s != victim]
    merged, parts, ms, failures = triply.list_available_drivers(limit=20)
    log(f"   ответили шарды: {', '.join(sorted(parts))}")
    for f in failures:
        log(f"   отказ: {str(f)[:70]}")
    log(f"   выдача собрана из {len(parts)} шардов вместо 4 — ответ НЕПОЛНЫЙ,")
    log(f"   но сервис продолжает отвечать ({ms:.1f} мс)")

    log("\n4) Агрегация по всему сервису:")
    merged2, parts2, ms2, fails2 = triply.count_drivers_by_status()
    log(f"   посчитано по {len(parts2)} шардам: " +
        ", ".join(f"{k}={v:,}" for k, v in sorted(merged2.items())))
    log("   число занижено на долю упавшего шарда — такой ответ нельзя")
    log("   отдавать как точный")

    log(f"\nПоднимаем {victim} обратно: {triply.start_shard(victim)}")
    for _ in range(30):
        time.sleep(1)
        try:
            triply.q(victim, "SELECT 1;")
            break
        except triply.ShardDown:
            continue
    log("шард снова доступен, проверяем:")
    merged3, _, _, _ = triply.count_drivers_by_status()
    log("   " + ", ".join(f"{k}={v:,}" for k, v in sorted(merged3.items())))


def task7(keys):
    rule("Задание 7. Hot Shard")
    rows = {}
    for s in triply.ALL_SHARDS:
        rows[s] = int(triply.q(s, "SELECT count(*) FROM ratings;").splitlines()[-1])
    total_rows = sum(rows.values())
    ideal_rows = total_rows / len(rows)

    def simulate(pick, n=10000):
        rnd = random.Random(20260917)
        hits = {s: 0 for s in triply.ALL_SHARDS}
        for i in range(n):
            hits[triply.shard_for(pick(rnd, i))] += 1
        return hits

    def report(title, hits, n=10000):
        log(f"\n{title}")
        log(f"   {'шард':<10}{'строк':>10}{'доля строк':>13}"
            f"{'запросов':>11}{'доля запросов':>16}")
        for s in triply.ALL_SHARDS:
            log(f"   {s:<10}{rows[s]:>10,}{rows[s]/total_rows*100:>12.2f}%"
                f"{hits[s]:>11,}{hits[s]/n*100:>15.2f}%")
        spread = (max(hits.values()) - min(hits.values())) / (n / len(hits)) * 100
        log(f"   разброс по запросам: {spread:.2f}%"
            f"   (разброс по строкам: "
            f"{(max(rows.values()) - min(rows.values())) / ideal_rows * 100:.2f}%)")
        return spread

    log("Строк на шардах распределено хешем, то есть почти поровну.")
    log("Вопрос в том, распределяются ли так же ЗАПРОСЫ.")

    hot, ordinary = keys["HOT"], keys["DRIVERS"]

    # А. Много «популярных» водителей, разбросанных хешем по всем шардам.
    s_a = report("А. 20% активных водителей дают 80% запросов:",
                 simulate(lambda rnd, i: rnd.choice(hot) if i % 5 else rnd.choice(ordinary)))
    log("   Хеш раскидал активных водителей по всем шардам, поэтому")
    log("   нагрузка осталась ровной. Много горячих ключей — не проблема.")

    # Б. Один доминирующий ключ: корпоративный аккаунт или звезда платформы.
    star = hot[0]
    star_shard = triply.shard_for(star)
    s_b = report(f"Б. Один водитель ({star[:8]}...) собирает половину запросов:",
                 simulate(lambda rnd, i: star if i % 2 else rnd.choice(ordinary)))
    log(f"   Этот водитель живёт на {star_shard}, и весь его трафик идёт туда же.")
    log("   Один ключ неделим: хеш не может разложить его по шардам.")

    # В. Запросы без shard key вообще.
    log("\nВ. Запросы без shard key (GET /v1/drivers?status=available):")
    log(f"   {'шард':<10}{'доля запросов':>16}")
    for s in triply.ALL_SHARDS:
        log(f"   {s:<10}{100.0 / len(triply.ALL_SHARDS):>15.2f}%")
    log("   Такой запрос попадает в КАЖДЫЙ шард, то есть нагружает все сразу.")
    log("   Чем больше доля подобных запросов, тем меньше смысла в шардировании.")

    log(f"\nИтог: разброс нагрузки {s_a:.2f}% в сценарии А против "
        f"{s_b:.2f}% в сценарии Б")
    log("при одном и том же распределении строк.")


def main() -> int:
    if "--load" in sys.argv:
        import shards as sh
        ddl = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "sql", "01_schema.sql"), encoding="utf-8").read()
        log("применяем схему...")
        for s in triply.ALL_SHARDS:
            sh.query(s, ddl)
        log("загружаем данные Triply-User...")
        t0 = time.perf_counter()
        riders, drivers, hot = load_data.load()
        with open(os.path.join(RESULTS, "keys.txt"), "w") as fh:
            fh.write("\n".join(["RIDERS"] + riders[:50] + ["DRIVERS"] + drivers[:50] +
                               ["HOT"] + hot[:50]))
        log(f"загрузка заняла {time.perf_counter() - t0:.1f} с")

    keys = load_keys()
    distribution()
    task2(keys)
    task3(keys)
    task4(keys)
    task5()
    task6(keys)
    task7(keys)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

---

## Запуск самого сервиса

Redis в системе не установлен, а Docker недоступен, поэтому под кеш
доступности поднят минимальный сервер протокола RESP на Python.

```python
#!/usr/bin/env python3
"""Минимальный сервер протокола RESP — замена Redis для запуска Triply-User.

Redis в системе не установлен, а Docker недоступен. Сервису он нужен только
под кеш доступности водителей (rediscache/availability.go): PING, SET с TTL,
GET. Этого достаточно, чтобы поднять настоящий сервис и дергать его REST API.

Запуск: python3 redis_stub.py [порт]
"""
import socket
import socketserver
import sys
import threading
import time

STORE: dict[bytes, tuple[bytes, float | None]] = {}
LOCK = threading.Lock()


def _read_line(f) -> bytes:
    line = f.readline()
    if not line:
        raise ConnectionError
    return line[:-2] if line.endswith(b"\r\n") else line.rstrip(b"\r\n")


def parse(f) -> list[bytes] | None:
    """Прочитать одну команду: массив bulk-строк либо inline-команду."""
    head = _read_line(f)
    if not head:
        return []
    if head[:1] != b"*":                      # inline-команда (redis-cli PING)
        return head.split()
    n = int(head[1:])
    args = []
    for _ in range(n):
        marker = _read_line(f)
        if marker[:1] != b"$":
            raise ValueError(f"ожидался bulk, получено {marker!r}")
        length = int(marker[1:])
        if length == -1:
            args.append(b"")
            continue
        data = f.read(length + 2)
        args.append(data[:length])
    return args


def now() -> float:
    return time.monotonic()


def do_set(args: list[bytes]) -> bytes:
    key, value = args[1], args[2]
    expire_at = None
    i = 3
    while i < len(args):
        opt = args[i].upper()
        if opt == b"EX":
            expire_at = now() + float(args[i + 1]); i += 2
        elif opt == b"PX":
            expire_at = now() + float(args[i + 1]) / 1000.0; i += 2
        elif opt in (b"NX", b"XX", b"KEEPTTL", b"GET"):
            i += 1
        else:
            i += 1
    with LOCK:
        STORE[key] = (value, expire_at)
    return b"+OK\r\n"


def do_get(args: list[bytes]) -> bytes:
    key = args[1]
    with LOCK:
        item = STORE.get(key)
        if item and item[1] is not None and item[1] <= now():
            del STORE[key]
            item = None
    if item is None:
        return b"$-1\r\n"                      # nil -> go-redis вернёт redis.Nil
    value = item[0]
    return b"$%d\r\n%s\r\n" % (len(value), value)


def handle(args: list[bytes]) -> bytes:
    if not args:
        return b"+OK\r\n"
    cmd = args[0].upper()
    if cmd == b"PING":
        return b"+PONG\r\n"
    if cmd == b"SET":
        return do_set(args)
    if cmd == b"GET":
        return do_get(args)
    if cmd == b"DEL":
        removed = 0
        with LOCK:
            for k in args[1:]:
                removed += 1 if STORE.pop(k, None) is not None else 0
        return b":%d\r\n" % removed
    if cmd == b"EXISTS":
        with LOCK:
            return b":%d\r\n" % sum(1 for k in args[1:] if k in STORE)
    if cmd == b"TTL":
        with LOCK:
            item = STORE.get(args[1])
        if not item:
            return b":-2\r\n"
        if item[1] is None:
            return b":-1\r\n"
        return b":%d\r\n" % max(0, int(item[1] - now()))
    if cmd in (b"CLIENT", b"SELECT", b"CONFIG", b"QUIT", b"AUTH"):
        return b"+OK\r\n"
    if cmd == b"INFO":
        payload = b"# Server\r\nredis_version:7.0.0-stub\r\n"
        return b"$%d\r\n%s\r\n" % (len(payload), payload)
    if cmd == b"HELLO":
        # Отказ от RESP3 — go-redis спокойно откатывается на RESP2.
        return b"-ERR unknown command 'HELLO'\r\n"
    if cmd == b"COMMAND":
        return b"*0\r\n"
    return b"+OK\r\n"


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            while True:
                args = parse(self.rfile)
                self.wfile.write(handle(args))
                self.wfile.flush()
        except (ConnectionError, ValueError, IndexError):
            return


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 6379
    print(f"redis-stub слушает 127.0.0.1:{port}", flush=True)
    Server(("127.0.0.1", port), Handler).serve_forever()
```

### Router поверх четырёх экземпляров сервиса — `app/service_router.py`

```python
"""Router поверх РЕАЛЬНОГО сервиса Triply-User.

Развёрнуто четыре экземпляра сервиса, каждый работает со своим шардом:

    shard0 -> http://127.0.0.1:8090 -> PostgreSQL :5440
    shard1 -> http://127.0.0.1:8091 -> PostgreSQL :5441
    shard2 -> http://127.0.0.1:8092 -> PostgreSQL :5442
    shard3 -> http://127.0.0.1:8093 -> PostgreSQL :5443

Router выбирает экземпляр по shard key (user_id) тем же кольцом consistent
hashing, что и в лабораторной №5. Ниже — только HTTP-вызовы настоящего REST
API, никакого прямого SQL.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "lab5", "app"))
import hashing  # noqa: E402

SHARDS = ["shard0", "shard1", "shard2", "shard3"]
BASE_URL = {s: f"http://127.0.0.1:{8090 + i}" for i, s in enumerate(SHARDS)}
PG_PORT = {s: 5440 + i for i, s in enumerate(SHARDS)}
RING = hashing.ConsistentHashRing(SHARDS)
TIMEOUT = 10


class ServiceDown(RuntimeError):
    def __init__(self, shard, detail):
        self.shard = shard
        super().__init__(f"{shard} ({BASE_URL[shard]}) недоступен: {detail}")


def shard_for(user_id: str) -> str:
    """Router: shard key -> шард (и, значит, экземпляр сервиса)."""
    return RING.route(user_id)


def call(shard: str, path: str, method: str = "GET", body: dict | None = None):
    url = BASE_URL[shard] + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        return {"_http_error": exc.code, "body": exc.read().decode()[:200]}
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise ServiceDown(shard, str(getattr(exc, "reason", exc))[:60]) from exc


def timed_call(shard: str, path: str, **kw):
    t0 = time.perf_counter()
    result = call(shard, path, **kw)
    return result, (time.perf_counter() - t0) * 1000


# --- SINGLE: router знает шард ---------------------------------------------
def get_driver(user_id: str):
    """GET /v1/drivers/{user_id} — ровно один экземпляр сервиса."""
    shard = shard_for(user_id)
    data, ms = timed_call(shard, f"/v1/drivers/{user_id}")
    return shard, data, ms


def set_availability(user_id: str, status: str):
    shard = shard_for(user_id)
    data, ms = timed_call(shard, f"/v1/drivers/{user_id}/availability",
                          method="PUT", body={"status": status})
    return shard, data, ms


def get_availability(user_id: str):
    shard = shard_for(user_id)
    data, ms = timed_call(shard, f"/v1/drivers/{user_id}/availability")
    return shard, data, ms


def create_rating(ratee_id: str, rater_id: str, score: float):
    """POST /v1/ratings — сервис внутри одной транзакции вставит оценку,
    пересчитает AVG и обновит профиль. Всё на шарде оценённого."""
    shard = shard_for(ratee_id)
    data, ms = timed_call(shard, "/v1/ratings", method="POST",
                          body={"ratee_id": ratee_id, "rater_id": rater_id,
                                "score": score, "comment": "lab6"})
    return shard, data, ms


# --- SCATTER: shard key неизвестен -----------------------------------------
def scatter_list(path: str, live: list[str] | None = None):
    """Один и тот же GET во все экземпляры сервиса."""
    targets = live if live is not None else SHARDS
    out, failures = {}, []
    t0 = time.perf_counter()
    for s in targets:
        try:
            out[s] = call(s, path)
        except ServiceDown as exc:
            failures.append(exc)
    return out, (time.perf_counter() - t0) * 1000, failures


def total_by_status(status: str, live: list[str] | None = None):
    """Сколько всего водителей в статусе — сумма page.total по экземплярам."""
    parts, ms, failures = scatter_list(f"/v1/drivers?status={status}&page_size=1", live)
    per_shard = {s: d.get("page", {}).get("total", 0) for s, d in parts.items()}
    return per_shard, sum(per_shard.values()), ms, failures


def merged_top(status: str, limit: int, live: list[str] | None = None):
    """ORDER BY created_at DESC LIMIT n через сервис: top-n с каждого + слияние."""
    parts, ms, failures = scatter_list(
        f"/v1/drivers?status={status}&page_size={limit}", live)
    rows = []
    for shard, data in parts.items():
        for item in data.get("items", []):
            rows.append((item["created_at"], item["user_id"], shard))
    rows.sort(reverse=True)
    return rows[:limit], parts, ms, failures
```

### Сценарии лабораторной через REST API — `app/run_lab6_service.py`

```python
#!/usr/bin/env python3
"""Лабораторная работа №6 поверх РАБОТАЮЩЕГО сервиса Triply-User.

Четыре экземпляра сервиса, каждый со своим шардом. Все обращения — по HTTP
к настоящему REST API, прямого SQL в сценариях нет (кроме одного места, где
мы намеренно читаем лог PostgreSQL, чтобы показать, какие запросы сервис
отправляет на самом деле).
"""
import os
import random
import subprocess
import sys
import time

import service_router as sr

PGROOT = os.environ.get("LAB6_PGROOT", "")
RESULTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results")


def log(msg: str = "") -> None:
    print(msg, flush=True)


def rule(title: str) -> None:
    log(); log("=" * 78); log(title); log("=" * 78)


def psql(port: int, sql: str) -> str:
    env = dict(os.environ, PGPASSWORD="postgres")
    p = subprocess.run(["psql", "-h", "127.0.0.1", "-p", str(port), "-U", "postgres",
                        "-d", "postgres", "-X", "-q", "-t", "-A", "-c", sql],
                       capture_output=True, text=True, env=env)
    return p.stdout.strip()


def sample_drivers(per_shard: int = 3) -> dict[str, list[str]]:
    """Собрать реальные user_id через сам сервис."""
    out = {}
    for s in sr.SHARDS:
        data = sr.call(s, f"/v1/drivers?page_size={per_shard}")
        out[s] = [i["user_id"] for i in data.get("items", [])]
    return out


def task_intro():
    rule("Стенд: четыре экземпляра Triply-User, по одному на шард")
    log(f"{'шард':<9}{'экземпляр сервиса':<28}{'PostgreSQL':<14}"
        f"{'drivers':>9}{'available':>11}")
    for s in sr.SHARDS:
        total = sr.call(s, "/v1/drivers?page_size=1")["page"]["total"]
        avail = sr.call(s, "/v1/drivers?status=available&page_size=1")["page"]["total"]
        log(f"{s:<9}{sr.BASE_URL[s]:<28}{':' + str(sr.PG_PORT[s]):<14}"
            f"{total:>9,}{avail:>11,}")


def task2(samples):
    rule("Задание 2. Single-Shard Query — через реальный сервис")
    log("GET /v1/drivers/{user_id}\n")
    for shard, ids in samples.items():
        if not ids:
            continue
        uid = ids[0]
        got_shard, data, ms = sr.get_driver(uid)
        name = data.get("name", "?")
        log(f"   {uid[:8]}...  router -> {got_shard:<8} {ms:6.1f} мс   "
            f"{name}, машина {data.get('vehicle', {}).get('plate_number', '-')}")
        assert got_shard == shard, "router указал не на тот шард"
    log("\n   router обратился ровно к одному экземпляру сервиса;")
    log("   остальные три даже не знали о запросе.")

    log("\nКакой SQL сервис отправляет на самом деле:")
    victim = sr.SHARDS[1]
    port, uid = sr.PG_PORT[victim], samples[victim][0]
    logfile = os.path.join(PGROOT, victim, "server.log") if PGROOT else None

    # ALTER SYSTEM нельзя выполнять в одном -c с другой командой: psql
    # оборачивает их в неявную транзакцию, а ALTER SYSTEM в ней запрещён.
    psql(port, "ALTER SYSTEM SET log_statement = 'all';")
    psql(port, "SELECT pg_reload_conf();")
    time.sleep(1)

    mark = sum(1 for _ in open(logfile, encoding="utf-8", errors="replace")) if logfile else 0
    sr.get_driver(uid)
    time.sleep(1)
    psql(port, "ALTER SYSTEM RESET log_statement;")
    psql(port, "SELECT pg_reload_conf();")

    if logfile and os.path.exists(logfile):
        tail = open(logfile, encoding="utf-8", errors="replace").read().splitlines()[mark:]
        stmts = []
        for line in tail:
            # GORM использует подготовленные выражения, поэтому в логе
            # "execute stmtcache_<hash>: SELECT ...", а не "statement:".
            for key in ("statement: ", "execute "):
                if key in line:
                    text = line.split(key, 1)[1]
                    if ": " in text and key == "execute ":
                        text = text.split(": ", 1)[1]
                    if text.startswith("SELECT") and any(
                            t in text for t in ('"drivers"', '"users"', '"vehicles"')):
                        stmts.append(text)
                    break
        for st in stmts:
            log(f"   {st[:100]}")
        log(f"\n   {len(stmts)} запроса, все ушли в {victim}: GetByID читает")
        log("   drivers, users и vehicles — и каждый по одному и тому же ключу.")


def task3(samples):
    rule("Задание 3. Агрегация через сервис")
    log("а) Агрегация внутри шарда: POST /v1/ratings пересчитывает AVG\n")
    ratee = samples[sr.SHARDS[0]][0]
    rater = samples[sr.SHARDS[1]][0]
    shard, data, ms = sr.create_rating(ratee, rater, 4.5)
    log(f"   оценка водителю {ratee[:8]}... -> {shard}  {ms:6.1f} мс")
    log(f"   сервис вернул пересчитанный средний балл: {data.get('average_rating', data)}")
    log("   INSERT + AVG + UPDATE прошли одной транзакцией на одном шарде")

    log("\nб) Агрегация по всему сервису: сколько всего available-водителей\n")
    per_shard, total, ms, _ = sr.total_by_status("available")
    for s in sr.SHARDS:
        log(f"   {s}: {per_shard[s]:>6,}")
    log(f"   -> сумма по всем экземплярам: {total:,}   ({ms:.1f} мс)")
    log("   ни один экземпляр сервиса не знает этого числа сам")

    log("\nв) Время scatter при разном числе шардов:\n")
    log(f"   {'шардов':>8}{'время, мс':>14}")
    for n in (1, 2, 3, 4):
        best = min(sr.total_by_status("available", sr.SHARDS[:n])[2] for _ in range(3))
        log(f"   {n:>8}{best:>14.1f}")


def task5():
    rule("Задание 5. ORDER BY + LIMIT через сервис")
    log("GET /v1/drivers?status=available&page_size=20\n")
    merged, parts, ms, _ = sr.merged_top("available", 20)
    for s in sr.SHARDS:
        log(f"   {s}: вернул {len(parts[s].get('items', []))} кандидатов")
    origin = {}
    for _, _, shard in merged:
        origin[shard] = origin.get(shard, 0) + 1
    log(f"\n   правильный top-20 собран за {ms:.1f} мс из:")
    for s in sorted(origin):
        log(f"      {s}: {origin[s]} из 20")

    single = sr.call(sr.SHARDS[0], "/v1/drivers?status=available&page_size=20")
    single_ids = {i["user_id"] for i in single.get("items", [])}
    merged_ids = {uid for _, uid, _ in merged}
    log(f"\n   если бы спросили только {sr.SHARDS[0]}:")
    log(f"      совпало: {len(merged_ids & single_ids)} из 20, "
        f"пропущено: {len(merged_ids - single_ids)}")


def task6(samples):
    rule("Задание 6. Отказ шарда при живом сервисе")
    victim = sr.SHARDS[2]
    log(f"Останавливаем PostgreSQL шарда {victim} (:{sr.PG_PORT[victim]}).")
    log(f"Экземпляр сервиса {sr.BASE_URL[victim]} при этом продолжает работать.\n")
    subprocess.run(["pg_ctl", "-D", os.path.join(PGROOT, victim), "-m", "fast", "stop"],
                   capture_output=True)
    time.sleep(2)

    log("1) Запрос к пользователю с упавшего шарда:")
    uid = samples[victim][0]
    shard, data, ms = sr.get_driver(uid)
    log(f"   GET /v1/drivers/{uid[:8]}... -> {shard}: "
        f"HTTP {data.get('_http_error', 'OK')} {data.get('body', '')[:60]}")

    log("\n2) Запросы к пользователям с живых шардов:")
    for s in sr.SHARDS:
        if s == victim or not samples[s]:
            continue
        got, d, ms = sr.get_driver(samples[s][0])
        log(f"   {samples[s][0][:8]}... -> {got}: OK, {d.get('name','?')}  {ms:.1f} мс")

    log("\n3) Scatter-запрос по всем экземплярам:")
    per_shard, total, ms, failures = sr.total_by_status("available")
    log(f"   ответили: {', '.join(f'{k}={v:,}' for k, v in sorted(per_shard.items()))}")
    log(f"   сумма = {total:,} — это НЕПОЛНОЕ число, но сервис его отдал")
    log(f"   ошибок транспорта: {len(failures)} "
        f"(экземпляр жив, падает только его запрос к БД)")

    log(f"\nПоднимаем PostgreSQL {victim} обратно...")
    subprocess.run(["pg_ctl", "-D", os.path.join(PGROOT, victim),
                    "-o", f"-p {sr.PG_PORT[victim]} -c listen_addresses=127.0.0.1 "
                          f"-c unix_socket_directories='' -c shared_buffers=128MB",
                    "-l", os.path.join(PGROOT, victim, "server.log"), "start"],
                   capture_output=True)
    for _ in range(30):
        time.sleep(1)
        try:
            if sr.call(victim, "/v1/drivers?status=available&page_size=1").get("page"):
                break
        except sr.ServiceDown:
            continue
    per_shard2, total2, _, _ = sr.total_by_status("available")
    log(f"   после восстановления сумма = {total2:,}")


def task7(samples):
    rule("Задание 7. Hot Shard на реальных вызовах сервиса")
    all_ids = [i for ids in samples.values() for i in ids]
    star = all_ids[0]
    star_shard = sr.shard_for(star)

    rnd = random.Random(20260917)
    hits = {s: 0 for s in sr.SHARDS}
    N = 400
    t0 = time.perf_counter()
    for i in range(N):
        uid = star if i % 2 else rnd.choice(all_ids)
        shard, _, _ = sr.get_driver(uid)
        hits[shard] += 1
    ms = (time.perf_counter() - t0) * 1000

    log(f"{N} реальных HTTP-запросов GET /v1/drivers/{{id}},")
    log(f"половина из них — к одному водителю {star[:8]}... ({star_shard})\n")
    log(f"   {'шард':<9}{'запросов':>10}{'доля':>10}")
    for s in sr.SHARDS:
        log(f"   {s:<9}{hits[s]:>10,}{hits[s]/N*100:>9.1f}%")
    spread = (max(hits.values()) - min(hits.values())) / (N / 4) * 100
    log(f"\n   разброс нагрузки: {spread:.1f}%   (суммарно {ms:.0f} мс)")
    log(f"   экземпляр {star_shard} обслужил больше всех, хотя строк у него")
    log("   столько же, сколько у соседей")


def main() -> int:
    samples = sample_drivers(3)
    task_intro()
    task2(samples)
    task3(samples)
    task5()
    task6(samples)
    task7(samples)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

---

## Вывод экспериментов через работающий сервис

```text
==============================================================================
Стенд: четыре экземпляра Triply-User, по одному на шард
==============================================================================
шард     экземпляр сервиса           PostgreSQL      drivers  available
shard0   http://127.0.0.1:8090       :5440             2,807        896
shard1   http://127.0.0.1:8091       :5441             2,204        740
shard2   http://127.0.0.1:8092       :5442             2,544        838
shard3   http://127.0.0.1:8093       :5443             2,445        832

==============================================================================
Задание 2. Single-Shard Query — через реальный сервис
==============================================================================
GET /v1/drivers/{user_id}

   b4dc35bc...  router -> shard0     38.1 мс   Driver 1618, машина A1618BC
   73b2ea7a...  router -> shard1     30.8 мс   Driver 3611, машина A3611BC
   bcf09afa...  router -> shard2     19.1 мс   Driver 4503, машина A4503BC
   20570d50...  router -> shard3     19.6 мс   Driver 6407, машина A6407BC

   router обратился ровно к одному экземпляру сервиса;
   остальные три даже не знали о запросе.

Какой SQL сервис отправляет на самом деле:
   SELECT * FROM "drivers" WHERE user_id = $1 ORDER BY "drivers"."user_id" LIMIT $2
   SELECT * FROM "users" WHERE id = $1 ORDER BY "users"."id" LIMIT $2
   SELECT * FROM "vehicles" WHERE driver_id = $1 ORDER BY "vehicles"."id" LIMIT $2

   3 запроса, все ушли в shard1: GetByID читает
   drivers, users и vehicles — и каждый по одному и тому же ключу.

==============================================================================
Задание 3. Агрегация через сервис
==============================================================================
а) Агрегация внутри шарда: POST /v1/ratings пересчитывает AVG

   оценка водителю b4dc35bc... -> shard0    23.4 мс
   сервис вернул пересчитанный средний балл: {'new_average': 4.08866}
   INSERT + AVG + UPDATE прошли одной транзакцией на одном шарде

б) Агрегация по всему сервису: сколько всего available-водителей

   shard0:    896
   shard1:    740
   shard2:    838
   shard3:    832
   -> сумма по всем экземплярам: 3,306   (87.2 мс)
   ни один экземпляр сервиса не знает этого числа сам

в) Время scatter при разном числе шардов:

     шардов     время, мс
          1          22.4
          2          41.8
          3          65.0
          4          81.7

==============================================================================
Задание 5. ORDER BY + LIMIT через сервис
==============================================================================
GET /v1/drivers?status=available&page_size=20

   shard0: вернул 20 кандидатов
   shard1: вернул 20 кандидатов
   shard2: вернул 20 кандидатов
   shard3: вернул 20 кандидатов

   правильный top-20 собран за 93.8 мс из:
      shard0: 7 из 20
      shard1: 5 из 20
      shard2: 2 из 20
      shard3: 6 из 20

   если бы спросили только shard0:
      совпало: 7 из 20, пропущено: 13

==============================================================================
Задание 6. Отказ шарда при живом сервисе
==============================================================================
Останавливаем PostgreSQL шарда shard2 (:5442).
Экземпляр сервиса http://127.0.0.1:8092 при этом продолжает работать.

1) Запрос к пользователю с упавшего шарда:
   GET /v1/drivers/bcf09afa... -> shard2: HTTP 500 {"error":"internal","message":"failed to connect to `user=po

2) Запросы к пользователям с живых шардов:
   b4dc35bc... -> shard0: OK, Driver 1618  22.1 мс
   73b2ea7a... -> shard1: OK, Driver 3611  28.5 мс
   20570d50... -> shard3: OK, Driver 6407  22.0 мс

3) Scatter-запрос по всем экземплярам:
   ответили: shard0=896, shard1=740, shard2=0, shard3=832
   сумма = 2,468 — это НЕПОЛНОЕ число, но сервис его отдал
   ошибок транспорта: 0 (экземпляр жив, падает только его запрос к БД)

Поднимаем PostgreSQL shard2 обратно...
   после восстановления сумма = 3,306

==============================================================================
Задание 7. Hot Shard на реальных вызовах сервиса
==============================================================================
400 реальных HTTP-запросов GET /v1/drivers/{id},
половина из них — к одному водителю b4dc35bc... (shard0)

   шард       запросов      доля
   shard0          256     64.0%
   shard1           47     11.8%
   shard2           54     13.5%
   shard3           43     10.8%

   разброс нагрузки: 213.0%   (суммарно 9470 мс)
   экземпляр shard0 обслужил больше всех, хотя строк у него
   столько же, сколько у соседей
```

---

## Вывод экспериментов на уровне SQL

Тот же набор сценариев, выполненный напрямую в шарды. Нужен там, где REST API
не показывает внутренностей: слияние агрегатов, распределённый JOIN по
`rater_id`, ловушка `AVG`.

```text
==============================================================================
Распределение данных по шардам
==============================================================================
шард           users   drivers  vehicles   ratings
shard0        13,966     2,807     2,807    56,774
shard1        11,126     2,204     2,204    43,187
shard2        12,650     2,544     2,544    51,352
shard3        12,258     2,445     2,445    48,687
итого         50,000    10,000    10,000   200,000

==============================================================================
Задание 2. Single-Shard Query
==============================================================================
GET /v1/drivers/{user_id} — DriverRepository.GetByID

  driver 9ab5e338...  -> shard1    110.6 мс   профиль: есть, машина: есть
  driver bc7585c4...  -> shard3    102.0 мс   профиль: есть, машина: есть
  driver 6d2b6c49...  -> shard1    101.0 мс   профиль: есть, машина: есть

Все три запроса (drivers, users, vehicles) ушли в ОДИН шард:
  router вычислил его по user_id, остальные шарды не опрашивались.

GET /v1/ratings?ratee_id=... — RatingRepository.List
  ratee 9ab5e338...  -> shard1   всего оценок    86, отдано 10     66.9 мс
  ratee bc7585c4...  -> shard3   всего оценок    83, отдано 10     62.3 мс
  ratee 6d2b6c49...  -> shard1   всего оценок    93, отдано 10     61.7 мс

==============================================================================
Задание 3. Агрегирующий запрос
==============================================================================
а) Агрегация ВНУТРИ одного шарда: средний рейтинг водителя
   (именно так делает RatingRepository.Create после вставки оценки)

   ratee 9ab5e338... -> shard1  AVG=4.073 по 86 оценкам    36.0 мс
   ratee bc7585c4... -> shard3  AVG=3.901 по 83 оценкам    33.4 мс
   ratee 6d2b6c49... -> shard1  AVG=3.912 по 93 оценкам    34.0 мс

б) Агрегация ПО ВСЕМУ сервису: GROUP BY status (GET /v1/drivers)

   shard0: offline=951, on_trip=960, available=896
   shard1: offline=758, on_trip=706, available=740
   shard2: on_trip=865, offline=841, available=838
   shard3: offline=835, on_trip=778, available=832
   -> слияние в приложении: available=3,306, offline=3,385, on_trip=3,309
   scatter-gather занял 126.7 мс

в) Ловушка AVG: среднее средних != среднее по всем данным

   shard0: count= 56,774  sum=   226,971.0  avg=3.9978
   shard1: count= 43,187  sum=   172,680.1  avg=3.9984
   shard2: count= 51,352  sum=   205,504.8  avg=4.0019
   shard3: count= 48,687  sum=   194,814.0  avg=4.0013

   неверно (среднее из четырёх AVG): 3.999848
   верно   (sum(sum) / sum(count)):  3.999849
   расхождение: 0.000001
   всего оценок: 200,000, scatter занял 151.7 мс

г) Время scatter при разном числе шардов (один и тот же COUNT):

     шардов     время, мс
          1          33.2
          2          64.4
          3          98.8
          4         128.4

==============================================================================
Задание 4. JOIN между шардами
==============================================================================
а) JOIN, который остался локальным: drivers + users + vehicles
   Все три таблицы шардированы по user_id, значит связанные строки
   всегда лежат вместе и обычный SQL JOIN работает внутри шарда.

   shard0: JOIN выполнен локально, строк 5
   shard1: JOIN выполнен локально, строк 5
   shard2: JOIN выполнен локально, строк 5
   shard3: JOIN выполнен локально, строк 5
   итого 20 строк за 121.8 мс, ни одной передачи между шардами

б) JOIN, который стал распределённым: ratings + профиль автора оценки
   ratings шардированы по ratee_id, а rater_id — чужой ключ,
   поэтому авторы оценок лежат на других шардах.

   ratee 9ab5e338... живёт на shard1
      оценок получено:        10
      авторы разбросаны по:   shard0, shard1, shard2
      дополнительных запросов: 3 (вместо одного JOIN)
      суммарно 123.4 мс

   ratee bc7585c4... живёт на shard3
      оценок получено:        10
      авторы разбросаны по:   shard0, shard1, shard3
      дополнительных запросов: 3 (вместо одного JOIN)
      суммарно 126.3 мс

   ratee 6d2b6c49... живёт на shard1
      оценок получено:        10
      авторы разбросаны по:   shard0, shard1, shard2, shard3
      дополнительных запросов: 4 (вместо одного JOIN)
      суммарно 170.5 мс


==============================================================================
Задание 5. ORDER BY + LIMIT
==============================================================================
GET /v1/drivers?status=available — DriverRepository.List
Запрос не содержит shard key, поэтому идёт на все шарды.

   shard0: вернул 20 кандидатов
   shard1: вернул 20 кандидатов
   shard2: вернул 20 кандидатов
   shard3: вернул 20 кандидатов

   после слияния в приложении — top-20 за 125.0 мс
   откуда пришли строки итогового top-20:
      shard0: 7 из 20
      shard1: 5 из 20
      shard2: 2 из 20
      shard3: 6 из 20

   Что было бы, если взять top-20 только с одного шарда:
      шард, к которому обратились: shard0
      совпало с правильным ответом: 7 из 20
      пропущено водителей:          13
      то есть ответ был бы просто неверным: более свежие водители
      с других шардов не попали бы в выдачу вообще.

==============================================================================
Задание 6. Отказ одного шарда
==============================================================================
Останавливаем shard2: pg_ctl stop /tmp/claude-1000/-home-multivader-programs-databases/bc0cc781-5bd3-4a44-9e2c-abe7b57ac50b/scratchpad/lab6_pg/shard2

1) Запросы к пользователям, живущим на упавшем шарде:
   driver 0060dca1...: ОТКАЗ — shard2 недоступен: shard2: psql: error: connection to server at "127.0
   driver 9fc5d466...: ОТКАЗ — shard2 недоступен: shard2: psql: error: connection to server at "127.0

2) Запросы к пользователям с живых шардов:
   driver 9ab5e338...: OK, обслужен shard1 за 99.9 мс
   driver bc7585c4...: OK, обслужен shard3 за 105.2 мс
   driver 6d2b6c49...: OK, обслужен shard1 за 107.7 мс

3) Scatter-запрос (GET /v1/drivers?status=available):
   ответили шарды: shard0, shard1, shard3
   отказ: shard2 недоступен: shard2: psql: error: connection to server at "127.0
   выдача собрана из 3 шардов вместо 4 — ответ НЕПОЛНЫЙ,
   но сервис продолжает отвечать (131.7 мс)

4) Агрегация по всему сервису:
   посчитано по 3 шардам: available=2,468, offline=2,544, on_trip=2,444
   число занижено на долю упавшего шарда — такой ответ нельзя
   отдавать как точный

Поднимаем shard2 обратно: pg_ctl start /tmp/claude-1000/-home-multivader-programs-databases/bc0cc781-5bd3-4a44-9e2c-abe7b57ac50b/scratchpad/lab6_pg/shard2
шард снова доступен, проверяем:
   available=3,306, offline=3,385, on_trip=3,309

==============================================================================
Задание 7. Hot Shard
==============================================================================
Строк на шардах распределено хешем, то есть почти поровну.
Вопрос в том, распределяются ли так же ЗАПРОСЫ.

А. 20% активных водителей дают 80% запросов:
   шард           строк   доля строк   запросов   доля запросов
   shard0        56,774       28.39%      2,647          26.47%
   shard1        43,187       21.59%      2,026          20.26%
   shard2        51,352       25.68%      2,743          27.43%
   shard3        48,687       24.34%      2,584          25.84%
   разброс по запросам: 28.68%   (разброс по строкам: 27.17%)
   Хеш раскидал активных водителей по всем шардам, поэтому
   нагрузка осталась ровной. Много горячих ключей — не проблема.

Б. Один водитель (9ab5e338...) собирает половину запросов:
   шард           строк   доля строк   запросов   доля запросов
   shard0        56,774       28.39%      1,299          12.99%
   shard1        43,187       21.59%      5,997          59.97%
   shard2        51,352       25.68%      1,407          14.07%
   shard3        48,687       24.34%      1,297          12.97%
   разброс по запросам: 188.00%   (разброс по строкам: 27.17%)
   Этот водитель живёт на shard1, и весь его трафик идёт туда же.
   Один ключ неделим: хеш не может разложить его по шардам.

В. Запросы без shard key (GET /v1/drivers?status=available):
   шард         доля запросов
   shard0              25.00%
   shard1              25.00%
   shard2              25.00%
   shard3              25.00%
   Такой запрос попадает в КАЖДЫЙ шард, то есть нагружает все сразу.
   Чем больше доля подобных запросов, тем меньше смысла в шардировании.

Итог: разброс нагрузки 28.68% в сценарии А против 188.00% в сценарии Б
при одном и том же распределении строк.
```
