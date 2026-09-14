"""Работа с партициями по времени: что есть, что должно быть, создание.

Поддерживаются две гранулярности:
  day   — партиция на сутки   (events_2026_09_12)
  month — партиция на месяц   (rentals_2026_09)
"""
import datetime as dt
import re

import db


def _first_of_month(d: dt.date) -> dt.date:
    return d.replace(day=1)


def _add_months(d: dt.date, n: int) -> dt.date:
    total = (d.year * 12 + d.month - 1) + n
    return dt.date(total // 12, total % 12 + 1, 1)


class Day:
    name = "day"
    suffix = "%Y_%m_%d"
    pattern = re.compile(r"^(?P<base>.+)_(?P<s>\d{4}_\d{2}_\d{2})$")

    @staticmethod
    def normalize(d: dt.date) -> dt.date:
        return d

    @staticmethod
    def shift(d: dt.date, n: int) -> dt.date:
        return d + dt.timedelta(days=n)

    @staticmethod
    def parse(suffix: str) -> dt.date:
        y, m, dd = suffix.split("_")
        return dt.date(int(y), int(m), int(dd))


class Month:
    name = "month"
    suffix = "%Y_%m"
    pattern = re.compile(r"^(?P<base>.+)_(?P<s>\d{4}_\d{2})$")

    @staticmethod
    def normalize(d: dt.date) -> dt.date:
        return _first_of_month(d)

    @staticmethod
    def shift(d: dt.date, n: int) -> dt.date:
        return _add_months(_first_of_month(d), n)

    @staticmethod
    def parse(suffix: str) -> dt.date:
        y, m = suffix.split("_")
        return dt.date(int(y), int(m), 1)


GRANULARITIES = {"day": Day, "month": Month}


def get_granularity(name: str):
    try:
        return GRANULARITIES[name]
    except KeyError:
        raise ValueError(f"неизвестная гранулярность: {name}") from None


def partition_name(table: str, period: dt.date, gran=Day) -> str:
    return f"{table}_{period.strftime(gran.suffix)}"


def existing_periods(table: str, gran=Day) -> set[dt.date]:
    """Периоды, для которых партиции реально существуют."""
    sql = f"""
SELECT c.relname
FROM pg_class c
JOIN pg_inherits i ON i.inhrelid = c.oid
JOIN pg_class p    ON p.oid = i.inhparent
JOIN pg_namespace n ON n.oid = p.relnamespace
WHERE p.relname = '{table}' AND n.nspname = '{db.SCHEMA}';"""
    found = set()
    for line in db.query(sql).splitlines():
        m = gran.pattern.match(line.strip())
        if m and m.group("base") == table:
            try:
                found.add(gran.parse(m.group("s")))
            except ValueError:
                continue
    return found


def required_periods(today: dt.date, horizon: int, gran=Day) -> list[dt.date]:
    """Текущий период плюс horizon периодов вперёд."""
    start = gran.normalize(today)
    return [gran.shift(start, i) for i in range(horizon + 1)]


def create_partition(table: str, period: dt.date, gran=Day) -> str:
    name = partition_name(table, period, gran)
    nxt = gran.shift(period, 1)
    db.query(f"""
CREATE TABLE IF NOT EXISTS {name}
PARTITION OF {table}
FOR VALUES FROM ('{period:%Y-%m-%d}') TO ('{nxt:%Y-%m-%d}');""")
    return name


def missing_periods(table: str, today: dt.date, horizon: int, gran=Day) -> list[dt.date]:
    have = existing_periods(table, gran)
    return [p for p in required_periods(today, horizon, gran) if p not in have]


# --- обратная совместимость с частями 10-11 (гранулярность «день») -----------
existing_days = existing_periods
required_days = required_periods
missing_days = missing_periods
