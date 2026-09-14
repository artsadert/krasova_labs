import io, os, re

BASE = os.path.dirname(os.path.abspath(__file__))

def parse(path):
    if not os.path.exists(path): return []
    raw = open(path, encoding="utf-8").read()
    return [(m.group(1).strip(), m.group(2).strip(), m.group(3).rstrip())
            for m in re.finditer(r"^@@@ (.+?)\n--- SQL\n(.*?)\n--- OUT\n(.*?)(?=\n@@@ |\Z)", raw, re.S|re.M)]

def block(sql, out, title=None):
    s = f"**{title}**\n\n" if title else ""
    s += "```sql\n" + sql + "\n```\n\n"
    s += "```text\n" + (out if out.strip() else "(команда выполнена, вывода нет)") + "\n```\n\n"
    return s

o = io.StringIO(); w = o.write
w("""# Результаты выполнения — лабораторная работа №3

Полный протокол: SQL-запросы, вывод `psql`, запуски job и health check.
Отчёт с выводами — в [`README3.md`](README3.md).

Среда: PostgreSQL 18.0, контейнер `database_postgres`, схемы `krasova_lab3`
(части 1-11) и `carrent_lab3` (часть 12). Дата выполнения — 2026-09-12.

---

# Части 1-9. Стратегии партиционирования

## Создание партиционированных таблиц

""")
w("```sql\n" + open(f"{BASE}/sql/01_parts_1_9.sql", encoding="utf-8").read().strip() + "\n```\n\n")

TITLES = {
 "p1.structure":"Часть 1. Структура партиционированной таблицы",
 "p1.fill":"Часть 1. Заполнение тестовыми данными",
 "p1.distribution":"Часть 1. Распределение по партициям",
 "p1.q1_boundary":"Часть 1. Запись с created_at = 2026-09-10 12:00:00",
 "p1.q1_probe":"Часть 1. Куда попадают граничные значения",
 "p1.q3_out_of_range":"Часть 1. Вставка за 2026-09-12 (партиции нет)",
 "p1.boundaries":"Часть 1. Фактические границы партиций",
 "p2.pruning_by_date":"Часть 2. Запрос по диапазону дат — partition pruning",
 "p2.no_pruning":"Часть 2. Запрос по event_type — pruning невозможен",
 "p3.fill":"Часть 3. Товары с разными ценами",
 "p3.distribution":"Часть 3. Распределение по ценовым партициям",
 "p3.pruning":"Часть 3. Запрос price >= 100 AND price < 500",
 "p4.fill":"Часть 4. Заполнение customers",
 "p4.distribution":"Часть 4. Распределение по типам клиентов",
 "p4.pruning":"Часть 4. Запрос customer_type = 'B2B'",
 "p5.insert_vip_fail":"Часть 5. Вставка неизвестного значения 'VIP'",
 "p5.create_default":"Часть 5. Создание DEFAULT-партиции",
 "p5.insert_vip_ok":"Часть 5. Повторная вставка после DEFAULT",
 "p5.default_blocks_new":"Часть 5. Попытка создать партицию для 'VIP' позже",
 "p6.fill":"Часть 6. Загрузка 1 000 000 строк в HASH-таблицу",
 "p6.distribution":"Часть 6. Равномерность распределения",
 "p6.pruning_by_user":"Часть 6. Запрос по user_id — pruning работает",
 "p6.no_pruning_by_date":"Часть 6. Запрос по дате — pruning не работает",
 "p8.create_index":"Часть 8. Создание индекса на партиционированной таблице",
 "p8.index_hierarchy":"Часть 8. Где физически существует индекс",
 "p8.pruning_plus_index":"Часть 8. Partition pruning вместе с индексом",
 "p8.index_without_pruning":"Часть 8. Индекс без pruning",
 "p9.before_index":"Часть 9. COUNT по event_type до индекса",
 "p9.create_index":"Часть 9. Создание индекса по event_type",
 "p9.after_index":"Часть 9. COUNT по event_type после индекса",
 "p9.type_plus_date":"Часть 9. Тот же запрос вместе с условием по дате",
 "p9.counts":"Часть 9. Доля строк с event_type = 'click'",
 "p9.sizes":"Размеры партиций и их индексов",
}
for label, sql, out in parse(f"{BASE}/results/parts_1_9.txt"):
    w(block(sql, out, TITLES.get(label, label)))

w("""
---

# Части 10-11. Автоматизация, контроль и alerting

Ниже — полный прогон production-сценария: проверка, создание партиций,
имитация сбоя ночной job, alert, подавление дубликатов, восстановление и
recovery-уведомление. Уведомления реально доставлялись по HTTP на локальный
приёмник (`jobs/alert_receiver.py`), который играет роль внешнего канала.

```text
""")
w(open(f"{BASE}/results/parts_10_11.txt", encoding="utf-8").read().strip())
w("""
```

---

# Часть 12. Партиционирование собственной базы (CarRent)

## Схема

""")
w("```sql\n" + open(f"{BASE}/sql/02_part_12_carrent.sql", encoding="utf-8").read().strip() + "\n```\n\n")

P12 = {
 "s5.partitions":"Шаг 5. Созданные партиции и их границы",
 "s5.fill":"Шаг 5. Заполнение 2 000 000 аренд",
 "s5.distribution":"Шаг 5. Распределение по месяцам",
 "s6.indexes":"Шаг 6. Индекс поверх партиционированной таблицы",
 "s6.q1_range":"Шаг 6. Запрос 1 — GET /rentals?from=&to=",
 "s6.q2_by_id":"Шаг 6. Запрос 2 — GET /rentals/{id}",
 "s6.q2_by_id_with_date":"Шаг 6. Запрос 2 с уточнением месяца",
 "s6.q3_statistics":"Шаг 6. Запрос 3 — GET /rentals/statistics",
 "s6.q4_user_history":"Шаг 6. Запрос 4 — история пользователя",
 "s6.q4_user_history_month":"Шаг 6. Запрос 4 с уточнением месяца",
 "s6.plain_copy":"Непартиционированная копия для сравнения",
 "s6.q3_statistics_plain":"Та же статистика без партиционирования",
 "s7.drop_old_partition":"Шаг 7. Строк до удаления старого месяца",
 "s7.detach":"Шаг 7. DETACH + DROP партиции за июнь",
}
for label, sql, out in parse(f"{BASE}/results/part_12.txt"):
    w(block(sql, out, P12.get(label, label)))

w("""
---

## Часть 12, шаги 7-9. Автоматизация и alerting для `rentals`

```text
""")
w(open(f"{BASE}/results/part_12_ops.txt", encoding="utf-8").read().strip())
w("\n```\n")

w("""
---

## Alert-система в Docker

Тот же код, упакованный в контейнер: планировщик вместо cron, уведомления
уходят в Telegram. Ниже — реальный лог контейнера, включая имитацию сбоя.

```text
""")
w(open(f"{BASE}/results/part_11_docker.txt", encoding="utf-8").read().strip())
w("\n```\n")

w("""
---

## pg_cron: создание партиций внутри СУБД

""")
w("```sql\n" + open(f"{BASE}/sql/03_pg_cron.sql", encoding="utf-8").read().strip() + "\n```\n\n")
w("""```text
""")
w(open(f"{BASE}/results/part_11_pgcron.txt", encoding="utf-8").read().strip())
w("\n```\n")

DEST = os.path.join(os.path.dirname(BASE), "result3.md")
open(DEST, "w", encoding="utf-8").write(o.getvalue())
print("result3.md:", os.path.getsize(DEST), "bytes")
