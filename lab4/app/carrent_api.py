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
