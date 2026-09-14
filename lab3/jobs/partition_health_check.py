#!/usr/bin/env python3
"""PartitionHealthCheck — проверяет наличие партиций на ближайшие N дней.

Состояния: OK / CRITICAL. Уведомление отправляется только при СМЕНЕ состояния,
поэтому один и тот же alert не уходит бесконечно, а возврат в норму порождает
recovery-уведомление.

Код возврата: 0 — OK, 2 — CRITICAL (как у nagios-совместимых проверок).
"""
import argparse
import datetime as dt
import json
import os
import sys

import notifier
import partitions

STATE_FILE = os.environ.get("PARTITION_STATE_FILE",
                            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "alert_state.json"))


def load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)


def critical_message(table, missing, horizon, now, gran=partitions.Day) -> str:
    names = "\n".join(partitions.partition_name(table, d, gran) for d in missing)
    return (f"🚨 Partition alert\n"
            f"Table: {table}\n"
            f"Missing partitions:\n{names}\n"
            f"Expected horizon: {horizon} {gran.name}s\n"
            f"Checked at:\n{now:%Y-%m-%d %H:%M:%S}")


def recovery_message(table: str, now: dt.datetime) -> str:
    return (f"🟢 Partition check OK\n"
            f"Table: {table}\n"
            f"All required partitions exist.\n"
            f"Checked at:\n{now:%Y-%m-%d %H:%M:%S}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default="events")
    ap.add_argument("--horizon", type=int, default=3)
    ap.add_argument("--today", default=None)
    ap.add_argument("--granularity", default="day", choices=["day", "month"])
    ap.add_argument("--repeat-after", type=int, default=0,
                    help="повторять alert не чаще, чем раз в N секунд (0 — не повторять)")
    args = ap.parse_args()

    now = dt.datetime.now()
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()

    gran = partitions.get_granularity(args.granularity)
    missing = partitions.missing_periods(args.table, today, args.horizon, gran)
    status = "CRITICAL" if missing else "OK"

    print(f"{now:%Y-%m-%d %H:%M:%S}  PartitionHealthCheck: table={args.table}, "
          f"today={today}, horizon={args.horizon} {gran.name}(s)")
    for day in partitions.required_periods(today, args.horizon, gran):
        mark = "MISSING" if day in missing else "ok"
        print(f"    {partitions.partition_name(args.table, day, gran)}  {mark}")
    print(f"    Result: {status}")

    state = load_state()
    state_key = f"{args.table}:{gran.name}"
    entry = state.get(state_key, {"status": "OK", "last_alert_at": None})
    previous = entry.get("status", "OK")
    last_alert_at = entry.get("last_alert_at")

    should_notify = False
    kind = None
    if status == "CRITICAL":
        if previous != "CRITICAL":
            should_notify, kind = True, "alert"
        elif args.repeat_after:
            age = (now - dt.datetime.fromisoformat(last_alert_at)).total_seconds() if last_alert_at else 1e9
            if age >= args.repeat_after:
                should_notify, kind = True, "repeat"
    elif previous == "CRITICAL":
        should_notify, kind = True, "recovery"

    if should_notify:
        text = (recovery_message(args.table, now) if kind == "recovery"
                else critical_message(args.table, missing, args.horizon, now, gran))
        print(f"    Уведомление ({kind}):")
        for line in notifier.send(text):
            print(f"      {line}")
        entry["last_alert_at"] = now.isoformat(timespec="seconds")
    else:
        reason = ("состояние не изменилось — alert подавлен"
                  if status == "CRITICAL" else "всё в порядке, уведомлять не о чем")
        print(f"    Уведомление не отправлено: {reason}")

    entry["status"] = status
    entry["checked_at"] = now.isoformat(timespec="seconds")
    state[state_key] = entry
    save_state(state)

    return 2 if status == "CRITICAL" else 0


if __name__ == "__main__":
    sys.exit(main())
