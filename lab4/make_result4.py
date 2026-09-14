import io, os

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)

o = io.StringIO(); w = o.write
w("""# Результаты выполнения — лабораторная работа №4

Полный протокол: конфигурация, SQL-запросы к обоим узлам и фактический вывод.
Отчёт с выводами — в [`README4.md`](README4.md).

Среда: PostgreSQL 18 в Docker. `lab4_primary` (порт 5432) принимает запись,
`lab4_replica` (порт 5433) обслуживает чтение. Схема сервиса — `carrent`.

Пароль репликации в выводе `primary_conninfo` замаскирован.

---

## Конфигурация: docker-compose.yaml

""")
w("```yaml\n" + open(f"{ROOT}/docker-compose.yaml", encoding="utf-8").read().strip() + "\n```\n\n")

w("""---

## Подготовка Primary: роль репликации и pg_hba

Выполняется автоматически при инициализации пустого кластера
(`/docker-entrypoint-initdb.d/00-replication.sh`).

""")
w("```bash\n" + open(f"{ROOT}/docker/primary-init/00-replication.sh", encoding="utf-8").read().strip() + "\n```\n\n")

w("""---

## Entrypoint Replica: pg_basebackup и переход в standby

""")
w("```bash\n" + open(f"{ROOT}/docker/replica-entrypoint.sh", encoding="utf-8").read().strip() + "\n```\n\n")

w("""---

## Схема сервиса CarRent (создаётся на Primary)

""")
w("```sql\n" + open(f"{BASE}/sql/01_carrent_schema.sql", encoding="utf-8").read().strip() + "\n```\n\n")

w("""---

## Протокол выполнения частей 1-6

```text
""")
w(open(f"{BASE}/results/lab4_raw.txt", encoding="utf-8").read().strip())
w("\n```\n\n")

w("""---

## Часть 5. Код сервиса

### Маршрутизация подключений — `app/db.py`

""")
w("```python\n" + open(f"{BASE}/app/db.py", encoding="utf-8").read().strip() + "\n```\n\n")
w("### Эндпойнты сервиса — `app/carrent_api.py`\n\n")
w("```python\n" + open(f"{BASE}/app/carrent_api.py", encoding="utf-8").read().strip() + "\n```\n\n")
w("### То же самое в реальном Django-проекте — `app/django_settings_snippet.py`\n\n")
w("```python\n" + open(f"{BASE}/app/django_settings_snippet.py", encoding="utf-8").read().strip() + "\n```\n\n")
w("### Измерение отставания — `app/lag_probe.py`\n\n")
w("```python\n" + open(f"{BASE}/app/lag_probe.py", encoding="utf-8").read().strip() + "\n```\n")

DEST = os.path.join(ROOT, "result4.md")
open(DEST, "w", encoding="utf-8").write(o.getvalue())
print("result4.md:", os.path.getsize(DEST), "bytes")
