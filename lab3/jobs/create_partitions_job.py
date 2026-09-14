#!/usr/bin/env python3
"""CreatePartitionsJob — создаёт недостающие партиции на горизонт вперёд.

Идемпотентна: повторный запуск не создаёт ничего лишнего.
Использование: python3 create_partitions_job.py [--table events] [--horizon 3]
"""
import argparse
import datetime as dt
import sys

import partitions


def log(msg: str) -> None:
    print(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default="events")
    ap.add_argument("--horizon", type=int, default=3, help="на сколько дней вперёд")
    ap.add_argument("--today", default=None, help="переопределить дату (YYYY-MM-DD)")
    ap.add_argument("--granularity", default="day", choices=["day", "month"])
    args = ap.parse_args()

    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    gran = partitions.get_granularity(args.granularity)

    log("Partition job started.")
    log(f"Table: {args.table}, today: {today}, horizon: {args.horizon} {gran.name}(s)")

    existing = partitions.existing_periods(args.table, gran)
    required = partitions.required_periods(today, args.horizon, gran)
    missing = [d for d in required if d not in existing]

    log(f"Existing partitions: {len(existing)}")
    log(f"Required partitions: {len(required)}")
    log(f"Missing partitions: {len(missing)}")

    if not missing:
        log("Nothing to create — all required partitions already exist.")
    for day in missing:
        log(f"Creating: {partitions.partition_name(args.table, day, gran)}")
        try:
            name = partitions.create_partition(args.table, day, gran)
            log(f"Partition {name} created successfully.")
        except Exception as exc:
            log(f"FAILED to create partition for {day}: {exc}")
            log("Partition job finished with errors.")
            return 1

    log("Partition job finished.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
