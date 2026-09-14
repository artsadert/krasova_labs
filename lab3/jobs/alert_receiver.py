#!/usr/bin/env python3
"""Локальный HTTP-приёмник уведомлений: печатает и пишет в файл всё, что пришло.

Играет роль внешнего канала (VK Callback API, Mattermost, Slack incoming webhook
устроены так же). Запуск: python3 alert_receiver.py 8099 received_alerts.log
"""
import datetime as dt
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
LOGFILE = sys.argv[2] if len(sys.argv) > 2 else "received_alerts.log"


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            text = json.loads(body).get("text", body.decode())
        except json.JSONDecodeError:
            text = body.decode()
        stamp = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}"
        with open(LOGFILE, "a", encoding="utf-8") as fh:
            fh.write(f"=== доставлено {stamp} ===\n{text}\n\n")
        print(f"[{stamp}] получено уведомление ({len(body)} байт)", flush=True)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):          # не засорять stdout логом сервера
        pass


if __name__ == "__main__":
    print(f"приёмник уведомлений слушает порт {PORT}, пишет в {LOGFILE}", flush=True)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
