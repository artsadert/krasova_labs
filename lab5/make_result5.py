import io, os

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)

o = io.StringIO(); w = o.write
w("""# Результаты выполнения — лабораторная работа №5

Полный протокол: конфигурация шардов, код router и обеих стратегий,
фактический вывод эксперимента. Отчёт с выводами — в [`README5.md`](README5.md).

Среда: четыре независимых экземпляра PostgreSQL 18 в Docker
(`lab5_shard0..3`, порты 5440-5443). Сервис — CarRent, сущность `rentals`,
shard key — `user_id`. Объём — 100 000 аренд.

Записи при ресшардинге переносятся **физически**: `COPY ... TO STDOUT` со
старого шарда, `COPY ... FROM STDIN` на новый, затем `DELETE`. После каждого
этапа router перепроверяет 2 000 случайных записей.

---

## Конфигурация: docker-compose.yaml

""")
w("```yaml\n" + open(f"{ROOT}/docker-compose.yaml", encoding="utf-8").read().strip() + "\n```\n\n")

w("""---

## Схема на каждом шарде

Шарды симметричны: одна и та же таблица, никакой связи между экземплярами.

""")
w("```sql\n" + open(f"{BASE}/sql/01_shard_schema.sql", encoding="utf-8").read().strip() + "\n```\n\n")

w("""---

## Задание 3. Демонстрация router на живых шардах

```text
""")
w(open(f"{BASE}/results/demo_router.txt", encoding="utf-8").read().strip())
w("\n```\n\n")

for title, path, lang in [
    ("Стратегии шардирования — `app/hashing.py`", "app/hashing.py", "python"),
    ("Router сервиса — `app/router.py`", "app/router.py", "python"),
    ("Реестр шардов — `app/shards.py`", "app/shards.py", "python"),
    ("Демонстрация router — `app/demo_router.py`", "app/demo_router.py", "python"),
    ("Эксперимент — `app/run_lab5.py`", "app/run_lab5.py", "python"),
]:
    w(f"---\n\n## {title}\n\n")
    w(f"```{lang}\n" + open(f"{BASE}/{path}", encoding="utf-8").read().strip() + "\n```\n\n")

w("""---

## Полный вывод эксперимента

```text
""")
w(open(f"{BASE}/results/lab5_raw.txt", encoding="utf-8").read().strip())
w("\n```\n")

DEST = os.path.join(ROOT, "result5.md")
open(DEST, "w", encoding="utf-8").write(o.getvalue())
print("result5.md:", os.path.getsize(DEST), "bytes")
