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
