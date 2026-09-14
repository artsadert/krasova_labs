#!/usr/bin/env python3
"""Relay: забирает уведомления из partition_ops.alert_outbox и шлёт наружу.

Зачем он нужен: pg_cron умеет выполнять SQL, но у PostgreSQL нет HTTP-клиента,
поэтому задание не может само постучаться в Telegram. Задание складывает
сообщение в таблицу-outbox, а этот relay доставляет его и проставляет sent_at.

Схема гарантирует, что сообщение не потеряется при недоступности канала:
пока доставка не удалась, строка остаётся неотправленной и будет повторена.
"""
import argparse
import datetime as dt
import sys

import db
import notifier

FETCH = """
SELECT id, severity, target, replace(message, chr(10), '\\n')
FROM partition_ops.alert_outbox
WHERE sent_at IS NULL
ORDER BY id
LIMIT %d;"""


def log(msg: str) -> None:
    print(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  [relay] {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    rows = db.rows(FETCH % args.limit)
    if not rows:
        log("новых уведомлений нет")
        return 0

    sent, failed = 0, 0
    for row in rows:
        if len(row) < 4:
            continue
        msg_id, severity, target, message = row[0], row[1], row[2], "|".join(row[3:])
        text = message.replace("\\n", "\n")
        report = notifier.send(text)
        ok = not any("ОШИБКА" in line for line in report)
        log(f"#{msg_id} {severity} {target}: " + "; ".join(report))
        if ok:
            db.query(f"UPDATE partition_ops.alert_outbox "
                     f"SET sent_at = now() WHERE id = {int(msg_id)};")
            sent += 1
        else:
            failed += 1          # останется в очереди до следующей попытки

    log(f"отправлено: {sent}, осталось в очереди: {failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
