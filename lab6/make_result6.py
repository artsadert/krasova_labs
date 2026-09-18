import io, os
BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
o = io.StringIO(); w = o.write
w("""# Результаты выполнения — лабораторная работа №6

Полный протокол: схема, код распределённого слоя и фактический вывод
экспериментов. Отчёт с выводами — в [`README6.md`](README6.md).

Сервис — **Triply-User** (микросервис профилей пассажиров и водителей
платформы Triply, Go + GORM + PostgreSQL). Запросы взяты из реального кода
`internal/infrastructure/postgresrepo/`.

Стенд: четыре независимых PostgreSQL 18.6 на портах 5440-5443, shard key —
`user_id`, маршрутизация — consistent hash ring из лабораторной №5.
Данные: 50 000 профилей (40 000 пассажиров, 10 000 водителей), 10 000 машин,
200 000 оценок.

---

## Схема на каждом шарде

""")
w("```sql\n" + open(f"{BASE}/sql/01_schema.sql", encoding="utf-8").read().strip() + "\n```\n\n")
for title, path in [
    ("Распределённый слой запросов — `app/triply.py`", "app/triply.py"),
    ("Наполнение шардов — `app/load_data.py`", "app/load_data.py"),
    ("Эксперименты — `app/run_lab6.py`", "app/run_lab6.py"),
]:
    w(f"---\n\n## {title}\n\n```python\n"
      + open(f"{BASE}/{path}", encoding="utf-8").read().strip() + "\n```\n\n")
w("""---

## Запуск самого сервиса

Redis в системе не установлен, а Docker недоступен, поэтому под кеш
доступности поднят минимальный сервер протокола RESP на Python.

""")
w("```python\n" + open(f"{BASE}/service/redis_stub.py", encoding="utf-8").read().strip() + "\n```\n\n")

w("""### Router поверх четырёх экземпляров сервиса — `app/service_router.py`

""")
w("```python\n" + open(f"{BASE}/app/service_router.py", encoding="utf-8").read().strip() + "\n```\n\n")
w("""### Сценарии лабораторной через REST API — `app/run_lab6_service.py`

""")
w("```python\n" + open(f"{BASE}/app/run_lab6_service.py", encoding="utf-8").read().strip() + "\n```\n\n")

w("---\n\n## Вывод экспериментов через работающий сервис\n\n```text\n")
w(open(f"{BASE}/results/lab6_service.txt", encoding="utf-8").read().strip())
w("\n```\n\n")

w("""---

## Вывод экспериментов на уровне SQL

Тот же набор сценариев, выполненный напрямую в шарды. Нужен там, где REST API
не показывает внутренностей: слияние агрегатов, распределённый JOIN по
`rater_id`, ловушка `AVG`.

```text
""")
w(open(f"{BASE}/results/lab6_raw.txt", encoding="utf-8").read().strip())
w("\n```\n")
DEST = os.path.join(ROOT, "result6.md")
open(DEST, "w", encoding="utf-8").write(o.getvalue())
print("result6.md:", os.path.getsize(DEST), "bytes")
