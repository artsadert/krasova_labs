#!/usr/bin/env python3
"""Планировщик alert-системы: крутится в контейнере вместо cron.

Что делает:
  * периодически запускает CreatePartitionsJob для каждой наблюдаемой таблицы;
  * чаще запускает PartitionHealthCheck, который шлёт alert в Telegram
    только при смене состояния.

Наблюдаемые таблицы описываются переменной WATCH в формате
    схема:таблица:гранулярность:горизонт[,...]
например
    WATCH=krasova_lab3:events:day:3,carrent_lab3:rentals:month:2

Интервалы: JOB_INTERVAL (сек, по умолчанию 3600), CHECK_INTERVAL (сек, 300).
Логи идут в stdout — их видно через `docker compose logs -f`.
"""
import datetime as dt
import os
import subprocess
import sys
import time

import env_file  # noqa: F401

HERE = os.path.dirname(os.path.abspath(__file__))
JOB_INTERVAL   = int(os.environ.get("JOB_INTERVAL", "3600"))
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "300"))
RELAY_INTERVAL = int(os.environ.get("RELAY_INTERVAL", "60"))
# Создание партиций может выполняться внутри СУБД через pg_cron. Тогда
# внешняя job не нужна — за ней остаётся только контроль и доставка алертов.
USE_PG_CRON    = os.environ.get("USE_PG_CRON", "false").lower() in ("1", "true", "yes")
WATCH = os.environ.get("WATCH", "krasova_lab3:events:day:3")


def log(msg: str) -> None:
    print(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  [scheduler] {msg}", flush=True)


def parse_watch(spec: str) -> list[dict]:
    targets = []
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        parts = item.split(":")
        if len(parts) != 4:
            log(f"пропущено некорректное описание: {item!r}")
            continue
        schema, table, gran, horizon = parts
        targets.append({"schema": schema, "table": table,
                        "granularity": gran, "horizon": horizon})
    return targets


def run_relay() -> None:
    """Доставить наружу уведомления, которые сложил в outbox pg_cron."""
    proc = subprocess.run([sys.executable, os.path.join(HERE, "alert_relay.py")],
                          env=dict(os.environ), capture_output=True, text=True)
    for line in (proc.stdout or "").splitlines():
        if "новых уведомлений нет" not in line:
            print(f"    {line}", flush=True)
    for line in (proc.stderr or "").splitlines():
        print(f"    !! {line}", flush=True)


def run(script: str, target: dict) -> int:
    env = dict(os.environ,
               LAB_SCHEMA=target["schema"],
               PARTITION_STATE_FILE=os.path.join(
                   os.environ.get("STATE_DIR", HERE),
                   f"alert_state_{target['schema']}_{target['table']}.json"))
    cmd = [sys.executable, os.path.join(HERE, script),
           "--table", target["table"],
           "--granularity", target["granularity"],
           "--horizon", target["horizon"]]
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    for line in (proc.stdout or "").splitlines():
        print(f"    {line}", flush=True)
    if proc.returncode not in (0, 2):        # 2 — штатный CRITICAL
        for line in (proc.stderr or "").splitlines():
            print(f"    !! {line}", flush=True)
    return proc.returncode


def main() -> int:
    targets = parse_watch(WATCH)
    if not targets:
        log("нечего наблюдать — проверьте переменную WATCH"); return 1

    log(f"старт. JOB_INTERVAL={JOB_INTERVAL}s, CHECK_INTERVAL={CHECK_INTERVAL}s")
    for t in targets:
        log(f"наблюдаем {t['schema']}.{t['table']} "
            f"({t['granularity']}, горизонт {t['horizon']})")
    log(f"канал уведомлений: {os.environ.get('ALERT_CHANNEL', 'console')}")
    log("создание партиций: " + ("pg_cron внутри СУБД" if USE_PG_CRON
                                 else "внешняя CreatePartitionsJob"))

    next_job = 0.0
    next_check = 0.0
    next_relay = 0.0
    while True:
        now = time.monotonic()
        if now >= next_job:
            if USE_PG_CRON:
                log("создание партиций делегировано pg_cron — job пропущена")
            else:
                for t in targets:
                    log(f"CreatePartitionsJob -> {t['schema']}.{t['table']}")
                    run("create_partitions_job.py", t)
            next_job = now + JOB_INTERVAL
        if now >= next_check:
            for t in targets:
                log(f"PartitionHealthCheck -> {t['schema']}.{t['table']}")
                code = run("partition_health_check.py", t)
                if code == 2:
                    log(f"состояние {t['table']}: CRITICAL")
            next_check = now + CHECK_INTERVAL
        if now >= next_relay:
            run_relay()
            next_relay = now + RELAY_INTERVAL
        time.sleep(min(5, CHECK_INTERVAL))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("остановлен")
