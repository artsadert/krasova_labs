import re, io, os

BASE = os.path.dirname(os.path.abspath(__file__))

def parse(path):
    if not os.path.exists(path): return []
    raw = open(path, encoding="utf-8").read()
    out = []
    for m in re.finditer(r"^@@@ (.+?)\n--- SQL\n(.*?)\n--- OUT\n(.*?)(?=\n@@@ |\Z)", raw, re.S|re.M):
        out.append((m.group(1).strip(), m.group(2).strip(), m.group(3).rstrip()))
    return out

def block(sql, out, title=None):
    s = ""
    if title: s += f"**{title}**\n\n"
    s += "```sql\n" + sql + "\n```\n\n"
    s += "```text\n" + (out if out.strip() else "(команда выполнена, вывода нет)") + "\n```\n\n"
    return s

TITLES = {
    "generate":            "Задание 2. Генерация данных",
    "size":                "Задание 3. Размер таблицы",
    "t4_noindex":          "Задание 4. `WHERE user_id = 123` без индекса",
    "create_user_idx":     "Задание 5. Создание индекса по `user_id`",
    "t5_index":            "Задание 5. Тот же запрос с индексом",
    "t6_noindex":          "Задание 6. Диапазон дат без индекса",
    "create_created_idx":  "Задание 6. Создание индекса по `created_at`",
    "t6_index":            "Задание 6. Диапазон дат с индексом",
    "t7_noindex":          "Задание 7. Фильтрация и сортировка без составного индекса",
    "create_composite_idx":"Задание 7. Создание составного индекса",
    "t7_index":            "Задание 7. Фильтрация и сортировка с составным индексом",
    "t8_noindex":          "Задание 8. Агрегация без индекса",
    "t8_index":            "Задание 8. Агрегация с индексом по `created_at`",
    "index_sizes":         "Задание 10. Размеры индексов",
}
VOL = {"10000":"10 000","100000":"100 000","1000000":"1 000 000",
       "5000000":"5 000 000","10000000":"10 000 000"}

o = io.StringIO(); w = o.write
w("""# Результаты измерений — лабораторная работа №2

Полный протокол: каждый выполненный SQL-запрос и фактический ответ `psql`.
Отчёт с выводами — в [`README2.md`](README2.md).

Среда: PostgreSQL 18.0 в контейнере `database_postgres`, `shared_buffers` = 128 MB
(значение по умолчанию). Начиная с 1 000 000 строк таблица не помещается в
`shared_buffers` целиком, поэтому в планах появляются строки `read=` наряду
с `hit=` — это реальные чтения, а не попадания в кэш.

Каждый замеряемый запрос выполнялся дважды, записан второй прогон.

---

# Часть A. Экспериментальная таблица `events`

## Задание 1. Создание таблицы

""")
w("```sql\n" + open(f"{BASE}/sql/01_schema.sql", encoding="utf-8").read().strip() + "\n```\n\n")

blocks = parse(f"{BASE}/results/part_a_raw.txt")
cur_vol = None
for label, sql, out in blocks:
    m = re.match(r"v(\d+)\.(.+)", label)
    if not m: continue
    vol, kind = m.group(1), m.group(2)
    if vol != cur_vol:
        cur_vol = vol
        w(f"\n---\n\n## Контрольная точка: {VOL[vol]} строк\n\n")
    w(block(sql, out, TITLES.get(kind, kind)))

w("\n---\n\n## Задание 9. Стоимость индексов на запись\n\n")
for label, sql, out in parse(f"{BASE}/results/part_a_write.txt"):
    w(block(sql, out, "Вставка 500 000 строк при разном числе индексов"
            if label == "t9.write_cost" else "Размеры индексов тестовой таблицы"))

w("\n---\n\n## Дополнительные замеры на 10 000 000 строк\n\n")
EXTRA = {
 "extra.scale":"Текущий объём",
 "extra.agg_volume":"Задание 8. Сколько строк обрабатывает агрегация за 30 дней",
 "extra.top_user":"Пользователи с наибольшим числом событий",
 "extra.t7_top_noindex":"Задание 7. Сортировка для активного пользователя без индексов",
 "extra.create_user_idx":"Создание индекса по `user_id`",
 "extra.t7_top_user_idx":"Задание 7. То же с индексом по `user_id`",
 "extra.create_composite":"Создание составного индекса",
 "extra.t7_top_composite":"Задание 7. То же с составным индексом",
 "extra.create_created_idx":"Создание индекса по `created_at`",
 "extra.index_sizes_10m":"Задание 10. Размеры индексов на 10 000 000 строк",
 "extra.total_size_10m":"Задание 3. Итоговые размеры таблицы и индексов",
 "extra.t11_daily":"Задание 11. Агрегация по дням за 365 дней",
}
for label, sql, out in parse(f"{BASE}/results/part_a_extra.txt"):
    w(block(sql, out, EXTRA.get(label, label)))

w("\n---\n\n## Задание 7, углублённо: когда `Sort` действительно исчезает\n\n")
SORT = {
 "sort.forced_index_scan":"Тот же запрос при запрещённом bitmap-плане",
 "sort.create_power_user":"Добавление активного пользователя: 200 000 событий",
 "sort.power_user_count":"Проверка",
 "sort.drop_composite":"Удаление составного индекса",
 "sort.power_user_only_user_idx":"Активный пользователь, составного индекса нет",
 "sort.recreate_composite":"Создание составного индекса",
 "sort.power_user_composite":"Активный пользователь, составной индекс `(user_id, created_at DESC)`",
 "sort.create_reversed":"Создание индекса с обратным порядком колонок",
 "sort.drop_correct":"Удаление правильного индекса",
 "sort.power_user_reversed":"Активный пользователь, индекс `(created_at DESC, user_id)`",
 "sort.cleanup":"Возврат исходного набора индексов",
}
for label, sql, out in parse(f"{BASE}/results/part_a_sort.txt"):
    w(block(sql, out, SORT.get(label, label)))

w("""
---

# Часть B. Сущность `rentals` сервиса CarRent

## Задание 12. Схема

""")
w("```sql\n" + open(f"{BASE}/sql/02_carrent_schema.sql", encoding="utf-8").read().strip() + "\n```\n\n")

BT = {
 "size":"Задание 15. Объём и размер таблицы",
 "q1_baseline":"Задание 14. Запрос 1 (поиск по внешнему ключу) — как в проекте",
 "q2_baseline":"Задание 14. Запрос 2 (диапазон дат) — как в проекте",
 "q3_baseline":"Задание 14. Запрос 3 (фильтр + сортировка) — как в проекте",
 "create_indexes":"Задание 18. Создание индексов",
 "q1_indexed":"Задание 18. Запрос 1 после оптимизации",
 "q2_indexed":"Задание 18. Запрос 2 после оптимизации",
 "q3_indexed":"Задание 18. Запрос 3 после оптимизации",
 "index_sizes":"Размеры индексов",
}
cur_vol = None
for label, sql, out in parse(f"{BASE}/results/part_b_raw.txt"):
    m = re.match(r"b(\d+)\.(.+)", label)
    if not m: continue
    vol, kind = m.group(1), m.group(2)
    if vol != cur_vol:
        cur_vol = vol
        w(f"\n---\n\n## Контрольная точка: {VOL[vol]} аренд\n\n")
    w(block(sql, out, BT.get(kind, kind)))

w("""
---

## Задание 18. Попытки улучшить Запрос 2 (5 000 000 аренд)

""")
OPT = {
 "opt.setup":"Исходный индекс по `created_at`",
 "opt.v0_original":"Вариант 0. Исходный запрос сервиса",
 "opt.v1_pagination":"Вариант 1. Постраничная выдача",
 "opt.v2_index":"Создание покрывающего индекса",
 "opt.v2_covering":"Вариант 2. Только нужные колонки + покрывающий индекс",
 "opt.v3_aggregate":"Вариант 3. Агрегат вместо выгрузки строк",
 "opt.sizes":"Размеры индексов после оптимизации",
}
for label, sql, out in parse(f"{BASE}/results/part_b_opt.txt"):
    w(block(sql, out, OPT.get(label, label)))

DEST = os.path.join(os.path.dirname(BASE), "result2.md")
open(DEST,"w",encoding="utf-8").write(o.getvalue())
print("result2.md:", os.path.getsize(DEST), "bytes")
