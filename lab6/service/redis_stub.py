#!/usr/bin/env python3
"""Минимальный сервер протокола RESP — замена Redis для запуска Triply-User.

Redis в системе не установлен, а Docker недоступен. Сервису он нужен только
под кеш доступности водителей (rediscache/availability.go): PING, SET с TTL,
GET. Этого достаточно, чтобы поднять настоящий сервис и дергать его REST API.

Запуск: python3 redis_stub.py [порт]
"""
import socket
import socketserver
import sys
import threading
import time

STORE: dict[bytes, tuple[bytes, float | None]] = {}
LOCK = threading.Lock()


def _read_line(f) -> bytes:
    line = f.readline()
    if not line:
        raise ConnectionError
    return line[:-2] if line.endswith(b"\r\n") else line.rstrip(b"\r\n")


def parse(f) -> list[bytes] | None:
    """Прочитать одну команду: массив bulk-строк либо inline-команду."""
    head = _read_line(f)
    if not head:
        return []
    if head[:1] != b"*":                      # inline-команда (redis-cli PING)
        return head.split()
    n = int(head[1:])
    args = []
    for _ in range(n):
        marker = _read_line(f)
        if marker[:1] != b"$":
            raise ValueError(f"ожидался bulk, получено {marker!r}")
        length = int(marker[1:])
        if length == -1:
            args.append(b"")
            continue
        data = f.read(length + 2)
        args.append(data[:length])
    return args


def now() -> float:
    return time.monotonic()


def do_set(args: list[bytes]) -> bytes:
    key, value = args[1], args[2]
    expire_at = None
    i = 3
    while i < len(args):
        opt = args[i].upper()
        if opt == b"EX":
            expire_at = now() + float(args[i + 1]); i += 2
        elif opt == b"PX":
            expire_at = now() + float(args[i + 1]) / 1000.0; i += 2
        elif opt in (b"NX", b"XX", b"KEEPTTL", b"GET"):
            i += 1
        else:
            i += 1
    with LOCK:
        STORE[key] = (value, expire_at)
    return b"+OK\r\n"


def do_get(args: list[bytes]) -> bytes:
    key = args[1]
    with LOCK:
        item = STORE.get(key)
        if item and item[1] is not None and item[1] <= now():
            del STORE[key]
            item = None
    if item is None:
        return b"$-1\r\n"                      # nil -> go-redis вернёт redis.Nil
    value = item[0]
    return b"$%d\r\n%s\r\n" % (len(value), value)


def handle(args: list[bytes]) -> bytes:
    if not args:
        return b"+OK\r\n"
    cmd = args[0].upper()
    if cmd == b"PING":
        return b"+PONG\r\n"
    if cmd == b"SET":
        return do_set(args)
    if cmd == b"GET":
        return do_get(args)
    if cmd == b"DEL":
        removed = 0
        with LOCK:
            for k in args[1:]:
                removed += 1 if STORE.pop(k, None) is not None else 0
        return b":%d\r\n" % removed
    if cmd == b"EXISTS":
        with LOCK:
            return b":%d\r\n" % sum(1 for k in args[1:] if k in STORE)
    if cmd == b"TTL":
        with LOCK:
            item = STORE.get(args[1])
        if not item:
            return b":-2\r\n"
        if item[1] is None:
            return b":-1\r\n"
        return b":%d\r\n" % max(0, int(item[1] - now()))
    if cmd in (b"CLIENT", b"SELECT", b"CONFIG", b"QUIT", b"AUTH"):
        return b"+OK\r\n"
    if cmd == b"INFO":
        payload = b"# Server\r\nredis_version:7.0.0-stub\r\n"
        return b"$%d\r\n%s\r\n" % (len(payload), payload)
    if cmd == b"HELLO":
        # Отказ от RESP3 — go-redis спокойно откатывается на RESP2.
        return b"-ERR unknown command 'HELLO'\r\n"
    if cmd == b"COMMAND":
        return b"*0\r\n"
    return b"+OK\r\n"


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            while True:
                args = parse(self.rfile)
                self.wfile.write(handle(args))
                self.wfile.flush()
        except (ConnectionError, ValueError, IndexError):
            return


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 6379
    print(f"redis-stub слушает 127.0.0.1:{port}", flush=True)
    Server(("127.0.0.1", port), Handler).serve_forever()
