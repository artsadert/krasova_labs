"""Доступ к PostgreSQL через psql — без внешних зависимостей.

Два режима подключения выбираются автоматически:

  network — если задан PGHOST (так работает контейнер alert-системы):
            psql -h $PGHOST -p $PGPORT ...
  exec    — иначе: docker exec <контейнер> psql ...
            (удобно для локальных прогонов с хоста)
"""
import os
import subprocess

import env_file  # noqa: F401  (подхватывает .env)

CONTAINER = os.environ.get("PG_CONTAINER", "database_postgres")
DB_USER   = os.environ.get("DB_USERNAME", "postgres")
DB_NAME   = os.environ.get("DB_DATABASE", "postgres")
DB_PASS   = os.environ.get("DB_PASSWORD", "12341234")
SCHEMA    = os.environ.get("LAB_SCHEMA", "krasova_lab3")
PGHOST    = os.environ.get("PGHOST")
PGPORT    = os.environ.get("PGPORT", "5432")

MODE = "network" if PGHOST else "exec"


class DbError(RuntimeError):
    pass


def _command(tuples_only: bool) -> list[str]:
    psql = ["psql", "-U", DB_USER, "-d", DB_NAME, "-X", "-q", "-v", "ON_ERROR_STOP=1"]
    if tuples_only:
        psql += ["-t", "-A"]
    if MODE == "network":
        return ["psql", "-h", PGHOST, "-p", PGPORT] + psql[1:]
    return ["docker", "exec", "-i", "-e", f"PGPASSWORD={DB_PASS}", CONTAINER] + psql


def query(sql: str, *, tuples_only: bool = True) -> str:
    """Выполнить SQL и вернуть текстовый результат."""
    env = dict(os.environ, PGPASSWORD=DB_PASS)
    full = f"SET search_path TO {SCHEMA};\n{sql}\n"
    proc = subprocess.run(_command(tuples_only), input=full,
                          capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise DbError(proc.stderr.strip() or proc.stdout.strip())
    return proc.stdout.strip()


def rows(sql: str) -> list[list[str]]:
    out = query(sql)
    return [line.split("|") for line in out.splitlines() if line]
