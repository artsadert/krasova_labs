"""Маршрутизация подключений сервиса CarRent: запись на Primary, чтение на Replica.

Это ядро Read Scaling: приложение держит два независимых подключения и само
решает, куда отправить запрос. Replica принимает только чтение — попытка
записи туда завершится ошибкой (см. часть 4 отчёта).
"""
import os
import subprocess

# Именно 127.0.0.1, а не localhost: localhost резолвится в ::1, а проброс
# порта Docker по IPv6 в этой системе не отвечает — соединение просто виснет.
PRIMARY = {
    "role": "PRIMARY",
    "host": os.environ.get("PRIMARY_HOST", "127.0.0.1"),
    "port": os.environ.get("PRIMARY_PORT", "5432"),
}
REPLICA = {
    "role": "REPLICA",
    "host": os.environ.get("REPLICA_HOST", "127.0.0.1"),
    "port": os.environ.get("REPLICA_PORT", "5433"),
}

DB_USER = os.environ.get("DB_USERNAME", "postgres")
DB_NAME = os.environ.get("DB_DATABASE", "postgres")
DB_PASS = os.environ.get("DB_PASSWORD", "12341234")

VERBOSE = os.environ.get("DB_VERBOSE", "1") == "1"


class DbError(RuntimeError):
    pass


def _run(node: dict, sql: str, tuples_only: bool) -> str:
    args = ["psql", "-h", node["host"], "-p", node["port"], "-U", DB_USER,
            "-d", DB_NAME, "-X", "-q", "-v", "ON_ERROR_STOP=1",
            "-v", "connect_timeout=5"]
    if tuples_only:
        args += ["-t", "-A"]
    env = dict(os.environ, PGPASSWORD=DB_PASS)
    proc = subprocess.run(args, input=f"SET search_path TO carrent;\n{sql}\n",
                          capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise DbError(proc.stderr.strip() or proc.stdout.strip())
    return proc.stdout.strip()


def write(sql: str, *, tuples_only: bool = True) -> str:
    """Любая модификация данных — только на Primary."""
    if VERBOSE:
        print(f"  -> {PRIMARY['role']} ({PRIMARY['host']}:{PRIMARY['port']})")
    return _run(PRIMARY, sql, tuples_only)


def read(sql: str, *, tuples_only: bool = True) -> str:
    """Чтение обслуживает Replica, разгружая Primary."""
    if VERBOSE:
        print(f"  -> {REPLICA['role']} ({REPLICA['host']}:{REPLICA['port']})")
    return _run(REPLICA, sql, tuples_only)


def node_role(node: dict) -> str:
    """Кем узел считает себя сам: recovery == реплика."""
    return "REPLICA" if _run(node, "SELECT pg_is_in_recovery();", True) == "t" else "PRIMARY"
