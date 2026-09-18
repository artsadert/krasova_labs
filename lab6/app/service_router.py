"""Router поверх РЕАЛЬНОГО сервиса Triply-User.

Развёрнуто четыре экземпляра сервиса, каждый работает со своим шардом:

    shard0 -> http://127.0.0.1:8090 -> PostgreSQL :5440
    shard1 -> http://127.0.0.1:8091 -> PostgreSQL :5441
    shard2 -> http://127.0.0.1:8092 -> PostgreSQL :5442
    shard3 -> http://127.0.0.1:8093 -> PostgreSQL :5443

Router выбирает экземпляр по shard key (user_id) тем же кольцом consistent
hashing, что и в лабораторной №5. Ниже — только HTTP-вызовы настоящего REST
API, никакого прямого SQL.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "lab5", "app"))
import hashing  # noqa: E402

SHARDS = ["shard0", "shard1", "shard2", "shard3"]
BASE_URL = {s: f"http://127.0.0.1:{8090 + i}" for i, s in enumerate(SHARDS)}
PG_PORT = {s: 5440 + i for i, s in enumerate(SHARDS)}
RING = hashing.ConsistentHashRing(SHARDS)
TIMEOUT = 10


class ServiceDown(RuntimeError):
    def __init__(self, shard, detail):
        self.shard = shard
        super().__init__(f"{shard} ({BASE_URL[shard]}) недоступен: {detail}")


def shard_for(user_id: str) -> str:
    """Router: shard key -> шард (и, значит, экземпляр сервиса)."""
    return RING.route(user_id)


def call(shard: str, path: str, method: str = "GET", body: dict | None = None):
    url = BASE_URL[shard] + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        return {"_http_error": exc.code, "body": exc.read().decode()[:200]}
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise ServiceDown(shard, str(getattr(exc, "reason", exc))[:60]) from exc


def timed_call(shard: str, path: str, **kw):
    t0 = time.perf_counter()
    result = call(shard, path, **kw)
    return result, (time.perf_counter() - t0) * 1000


# --- SINGLE: router знает шард ---------------------------------------------
def get_driver(user_id: str):
    """GET /v1/drivers/{user_id} — ровно один экземпляр сервиса."""
    shard = shard_for(user_id)
    data, ms = timed_call(shard, f"/v1/drivers/{user_id}")
    return shard, data, ms


def set_availability(user_id: str, status: str):
    shard = shard_for(user_id)
    data, ms = timed_call(shard, f"/v1/drivers/{user_id}/availability",
                          method="PUT", body={"status": status})
    return shard, data, ms


def get_availability(user_id: str):
    shard = shard_for(user_id)
    data, ms = timed_call(shard, f"/v1/drivers/{user_id}/availability")
    return shard, data, ms


def create_rating(ratee_id: str, rater_id: str, score: float):
    """POST /v1/ratings — сервис внутри одной транзакции вставит оценку,
    пересчитает AVG и обновит профиль. Всё на шарде оценённого."""
    shard = shard_for(ratee_id)
    data, ms = timed_call(shard, "/v1/ratings", method="POST",
                          body={"ratee_id": ratee_id, "rater_id": rater_id,
                                "score": score, "comment": "lab6"})
    return shard, data, ms


# --- SCATTER: shard key неизвестен -----------------------------------------
def scatter_list(path: str, live: list[str] | None = None):
    """Один и тот же GET во все экземпляры сервиса."""
    targets = live if live is not None else SHARDS
    out, failures = {}, []
    t0 = time.perf_counter()
    for s in targets:
        try:
            out[s] = call(s, path)
        except ServiceDown as exc:
            failures.append(exc)
    return out, (time.perf_counter() - t0) * 1000, failures


def total_by_status(status: str, live: list[str] | None = None):
    """Сколько всего водителей в статусе — сумма page.total по экземплярам."""
    parts, ms, failures = scatter_list(f"/v1/drivers?status={status}&page_size=1", live)
    per_shard = {s: d.get("page", {}).get("total", 0) for s, d in parts.items()}
    return per_shard, sum(per_shard.values()), ms, failures


def merged_top(status: str, limit: int, live: list[str] | None = None):
    """ORDER BY created_at DESC LIMIT n через сервис: top-n с каждого + слияние."""
    parts, ms, failures = scatter_list(
        f"/v1/drivers?status={status}&page_size={limit}", live)
    rows = []
    for shard, data in parts.items():
        for item in data.get("items", []):
            rows.append((item["created_at"], item["user_id"], shard))
    rows.sort(reverse=True)
    return rows[:limit], parts, ms, failures
