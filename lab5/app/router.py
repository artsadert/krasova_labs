"""Router сервиса CarRent: определяет, на каком шарде живёт запись.

Сервис не обращается к шардам напрямую — он спрашивает router, а тот по
shard key (user_id) возвращает нужный экземпляр PostgreSQL. Стратегия
подменяется целиком, интерфейс не меняется.
"""
import shards


class ShardRouter:
    def __init__(self, strategy):
        self.strategy = strategy

    @property
    def shard_names(self) -> list[str]:
        return list(self.strategy.shards)

    def shard_for(self, user_id: int) -> str:
        """Главная функция router: ключ -> имя шарда."""
        return self.strategy.route(user_id)

    # --- операции сервиса -------------------------------------------------
    def create_rental(self, rental: dict) -> str:
        """POST /rentals — пишем в шард, определённый по user_id."""
        shard = self.shard_for(rental["user_id"])
        shards.query(shard, f"""
            INSERT INTO rentals (id, user_id, car_id, started_at, finished_at, minute_fee)
            VALUES ({rental['id']}, {rental['user_id']}, {rental['car_id']},
                    '{rental['started_at']}', NULL, {rental['minute_fee']});""")
        return shard

    def user_rentals(self, user_id: int, limit: int = 5) -> tuple[str, str]:
        """GET /rentals?user_id=... — запрос идёт ровно в один шард."""
        shard = self.shard_for(user_id)
        rows = shards.query(shard, f"""
            SELECT id, car_id, minute_fee, started_at
            FROM rentals WHERE user_id = {user_id}
            ORDER BY started_at DESC LIMIT {limit};""", tuples_only=False)
        return shard, rows

    def count_all(self) -> dict[str, int]:
        """Счётчики по шардам — этот запрос, наоборот, обходит все шарды."""
        return {s: shards.count(s) for s in self.shard_names}

    def total(self) -> int:
        return sum(self.count_all().values())
