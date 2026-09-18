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
