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
