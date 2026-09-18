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
