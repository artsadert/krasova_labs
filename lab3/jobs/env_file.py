"""Подхватывает переменные из .env проекта, если они не заданы в окружении.

Секреты (VK_TOKEN, TELEGRAM_BOT_TOKEN, SMTP_PASSWORD) держим в .env, а не в
коде и не в crontab: crontab читается любым процессом пользователя, а
переданные в командной строке значения видны в `ps`.

Реальное окружение имеет приоритет над файлом, поэтому разовый запуск
`VK_TOKEN=... python3 ...` по-прежнему работает.
"""
import os

DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env")


def load(path: str = DEFAULT_PATH) -> int:
    """Загрузить .env. Возвращает число установленных переменных."""
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except FileNotFoundError:
        return 0

    loaded = 0
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if value[:1] == value[-1:] and value[:1] in ("'", '"'):
            value = value[1:-1]
        if key and key not in os.environ:       # окружение важнее файла
            os.environ[key] = value
            loaded += 1
    return loaded


load()
