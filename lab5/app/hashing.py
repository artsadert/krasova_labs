"""Две стратегии шардирования: hash(key) % N и Consistent Hashing.

Про выбор хеш-функции. Встроенная hash() в Python для строк рандомизируется
при каждом запуске процесса (PYTHONHASHSEED), поэтому для шардирования она
непригодна: после перезапуска сервиса один и тот же ключ уехал бы на другой
шард. Нужна детерминированная функция — здесь md5, взятая как 64-битное целое.
Криптостойкость тут не нужна, нужна воспроизводимость и равномерность.
"""
import bisect
import hashlib


def stable_hash(value) -> int:
    """Детерминированный хеш ключа: одинаков во всех процессах и запусках."""
    digest = hashlib.md5(str(value).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


class ModuloRouter:
    """shard = hash(key) % N — простейшая стратегия."""

    name = "hash(key) % N"

    def __init__(self, shards: list[str]):
        self.shards = list(shards)

    def route(self, key) -> str:
        return self.shards[stable_hash(key) % len(self.shards)]

    def with_shard(self, shard: str) -> "ModuloRouter":
        """Новый router с добавленным шардом — для эксперимента 3 -> 4."""
        return ModuloRouter(self.shards + [shard])

    def without_shard(self, shard: str) -> "ModuloRouter":
        return ModuloRouter([s for s in self.shards if s != shard])


class ConsistentHashRing:
    """Кольцо consistent hashing с виртуальными узлами.

    Каждый шард представлен на кольце не одной точкой, а vnodes точками.
    Ключ отображается в точку кольца и обслуживается первым шардом по часовой
    стрелке. При добавлении шарда переезжают только ключи участков, которые
    забрал новичок, а не весь набор.
    """

    name = "Consistent Hashing"

    def __init__(self, shards: list[str], vnodes: int = 150):
        self.vnodes = vnodes
        self._points: list[int] = []      # отсортированные позиции на кольце
        self._owners: dict[int, str] = {}  # позиция -> шард
        self.shards: list[str] = []
        for shard in shards:
            self.add_shard(shard)

    def _vnode_points(self, shard: str):
        for i in range(self.vnodes):
            yield stable_hash(f"{shard}#{i}")

    def add_shard(self, shard: str) -> None:
        if shard in self.shards:
            return
        self.shards.append(shard)
        for point in self._vnode_points(shard):
            if point not in self._owners:
                self._owners[point] = shard
                bisect.insort(self._points, point)

    def remove_shard(self, shard: str) -> None:
        if shard not in self.shards:
            return
        self.shards.remove(shard)
        for point in self._vnode_points(shard):
            if self._owners.get(point) == shard:
                del self._owners[point]
                idx = bisect.bisect_left(self._points, point)
                if idx < len(self._points) and self._points[idx] == point:
                    self._points.pop(idx)

    def route(self, key) -> str:
        if not self._points:
            raise RuntimeError("кольцо пустое")
        h = stable_hash(key)
        idx = bisect.bisect_right(self._points, h)
        if idx == len(self._points):     # прошли конец кольца — возвращаемся в начало
            idx = 0
        return self._owners[self._points[idx]]

    def with_shard(self, shard: str) -> "ConsistentHashRing":
        ring = ConsistentHashRing(self.shards, self.vnodes)
        ring.add_shard(shard)
        return ring

    def without_shard(self, shard: str) -> "ConsistentHashRing":
        ring = ConsistentHashRing(self.shards, self.vnodes)
        ring.remove_shard(shard)
        return ring
