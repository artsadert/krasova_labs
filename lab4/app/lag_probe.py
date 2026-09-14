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
