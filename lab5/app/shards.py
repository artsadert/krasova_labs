"""Реестр шардов и доступ к ним.

Шарды — независимые экземпляры PostgreSQL. Они ничего не знают друг о друге:
о том, где лежит запись, знает только router в сервисе.
"""
import os
import subprocess

DB_USER = os.environ.get("DB_USERNAME", "postgres")
DB_NAME = os.environ.get("DB_DATABASE", "postgres")
DB_PASS = os.environ.get("DB_PASSWORD", "12341234")

# 127.0.0.1, а не localhost: localhost резолвится в ::1, и проброс порта
# Docker по IPv6 в этой системе не отвечает.
SHARDS = {
    "shard0": {"host": "127.0.0.1", "port": "5440", "container": "lab5_shard0"},
    "shard1": {"host": "127.0.0.1", "port": "5441", "container": "lab5_shard1"},
    "shard2": {"host": "127.0.0.1", "port": "5442", "container": "lab5_shard2"},
    "shard3": {"host": "127.0.0.1", "port": "5443", "container": "lab5_shard3"},
}

INITIAL_SHARDS = ["shard0", "shard1", "shard2"]
NEW_SHARD = "shard3"


class ShardError(RuntimeError):
    pass


def _psql_args(shard: str, tuples_only: bool) -> list[str]:
    node = SHARDS[shard]
    args = ["psql", "-h", node["host"], "-p", node["port"], "-U", DB_USER,
            "-d", DB_NAME, "-X", "-q", "-v", "ON_ERROR_STOP=1"]
    if tuples_only:
        args += ["-t", "-A"]
    return args


def query(shard: str, sql: str, *, tuples_only: bool = True) -> str:
    env = dict(os.environ, PGPASSWORD=DB_PASS)
    proc = subprocess.run(_psql_args(shard, tuples_only), input=sql,
                          capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise ShardError(f"{shard}: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout.strip()


def copy_in(shard: str, table: str, rows: str) -> None:
    """Массовая загрузка через COPY ... FROM STDIN."""
    env = dict(os.environ, PGPASSWORD=DB_PASS)
    sql = f"COPY {table} FROM STDIN WITH (FORMAT csv)"
    args = _psql_args(shard, False) + ["-c", f"\\copy {table} FROM STDIN WITH (FORMAT csv)"]
    proc = subprocess.run(args, input=rows, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise ShardError(f"{shard}: {proc.stderr.strip()}")


def count(shard: str, table: str = "rentals") -> int:
    out = query(shard, f"SELECT count(*) FROM {table};")
    return int(out.splitlines()[-1])


def apply_schema(shard: str, ddl: str) -> None:
    query(shard, ddl)
