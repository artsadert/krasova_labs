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
