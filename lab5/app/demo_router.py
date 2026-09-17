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
