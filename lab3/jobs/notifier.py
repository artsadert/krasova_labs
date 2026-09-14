"""Отправка уведомлений. Канал выбирается переменной ALERT_CHANNEL.

Поддерживаются:
  console  — вывод в stdout (по умолчанию);
  file     — дозапись в файл ALERT_FILE;
  webhook  — HTTP POST на ALERT_WEBHOOK_URL (VK Callback, Mattermost, Slack,
             любой HTTP-приёмник);
  telegram — Bot API, нужны TELEGRAM_BOT_TOKEN (или TOKEN) и TELEGRAM_CHAT_ID;
  vk       — VK API messages.send, нужны VK_TOKEN и VK_PEER_ID;
  email    — SMTP, нужны SMTP_HOST/SMTP_USER/SMTP_PASSWORD/ALERT_EMAIL_TO.

Каналы можно перечислить через запятую: ALERT_CHANNEL=console,webhook
"""
import json
import os

import env_file  # noqa: F401  (подхватывает .env)
import smtplib
import urllib.parse
import urllib.request
from email.message import EmailMessage

TIMEOUT = 10


def _console(text: str) -> str:
    print(text)
    return "напечатано в stdout"


def _file(text: str) -> str:
    path = os.environ.get("ALERT_FILE", "alerts.log")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text + "\n" + "-" * 60 + "\n")
    return f"записано в {path}"


def _webhook(text: str) -> str:
    url = os.environ["ALERT_WEBHOOK_URL"]
    payload = json.dumps({"text": text}).encode()
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return f"HTTP {resp.status} от {url}"


def _telegram(text: str) -> str:
    # TOKEN — имя переменной, под которым токен лежит в .env проекта
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ["TOKEN"]
    chat  = os.environ["TELEGRAM_CHAT_ID"]
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=TIMEOUT) as r:
        return f"telegram HTTP {r.status}"


def _vk(text: str) -> str:
    data = urllib.parse.urlencode({
        "access_token": os.environ["VK_TOKEN"],
        "peer_id": os.environ["VK_PEER_ID"],
        "message": text,
        "random_id": 0,
        "v": "5.199",
    }).encode()
    url = "https://api.vk.com/method/messages.send"
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=TIMEOUT) as r:
        return f"vk HTTP {r.status}"


def _email(text: str) -> str:
    msg = EmailMessage()
    msg["Subject"] = text.splitlines()[0][:120]
    msg["From"] = os.environ.get("SMTP_USER", "partitions@localhost")
    msg["To"] = os.environ["ALERT_EMAIL_TO"]
    msg.set_content(text)
    host = os.environ.get("SMTP_HOST", "localhost")
    port = int(os.environ.get("SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=TIMEOUT) as smtp:
        if os.environ.get("SMTP_USER"):
            smtp.starttls()
            smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        smtp.send_message(msg)
    return f"email отправлен на {msg['To']}"


CHANNELS = {"console": _console, "file": _file, "webhook": _webhook,
            "telegram": _telegram, "vk": _vk, "email": _email}


def send(text: str) -> list[str]:
    """Отправить сообщение во все настроенные каналы. Возвращает отчёт."""
    names = [c.strip() for c in os.environ.get("ALERT_CHANNEL", "console").split(",") if c.strip()]
    report = []
    for name in names:
        handler = CHANNELS.get(name)
        if handler is None:
            report.append(f"{name}: неизвестный канал")
            continue
        try:
            report.append(f"{name}: {handler(text)}")
        except Exception as exc:                      # канал не должен ронять проверку
            report.append(f"{name}: ОШИБКА ОТПРАВКИ — {exc}")
    return report
