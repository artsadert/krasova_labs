#!/usr/bin/env python3
"""Определяет TELEGRAM_CHAT_ID и дописывает его в .env.

Перед запуском напишите боту любое сообщение (например, /start) —
Telegram отдаёт chat_id только после того, как пользователь начал диалог.
"""
import json
import os
import re
import sys
import urllib.request

import env_file

ENV_PATH = env_file.DEFAULT_PATH
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TOKEN")


def api(method: str):
    with urllib.request.urlopen(f"https://api.telegram.org/bot{TOKEN}/{method}", timeout=10) as r:
        return json.load(r)


def main() -> int:
    if not TOKEN:
        print("В .env нет TOKEN / TELEGRAM_BOT_TOKEN"); return 1

    me = api("getMe")
    if not me.get("ok"):
        print("Токен недействителен:", me.get("description")); return 1
    print(f"Бот: @{me['result']['username']}")

    updates = api("getUpdates").get("result", [])
    chats = {}
    for u in updates:
        msg = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
        ch = msg.get("chat")
        if ch:
            chats[ch["id"]] = ch.get("title") or ch.get("username") or ch.get("first_name") or ch.get("type")

    if not chats:
        print(f"Ни одного чата. Напишите боту @{me['result']['username']} любое "
              f"сообщение (/start) и запустите скрипт снова.")
        return 2

    for cid, name in chats.items():
        print(f"  chat_id={cid}  {name}")
    chat_id = str(list(chats)[-1])          # последний писавший

    text = open(ENV_PATH, encoding="utf-8").read()
    if re.search(r"^TELEGRAM_CHAT_ID=.*$", text, re.M):
        text = re.sub(r"^TELEGRAM_CHAT_ID=.*$", f"TELEGRAM_CHAT_ID={chat_id}", text, flags=re.M)
    else:
        text = text.replace("# TELEGRAM_CHAT_ID=", f"TELEGRAM_CHAT_ID={chat_id}")
        if f"TELEGRAM_CHAT_ID={chat_id}" not in text:
            text += f"\nTELEGRAM_CHAT_ID={chat_id}\n"
    text = re.sub(r"^ALERT_CHANNEL=.*$", "ALERT_CHANNEL=console,telegram", text, flags=re.M)
    open(ENV_PATH, "w", encoding="utf-8").write(text)
    print(f"Записано в .env: TELEGRAM_CHAT_ID={chat_id}, ALERT_CHANNEL=console,telegram")
    return 0


if __name__ == "__main__":
    sys.exit(main())
