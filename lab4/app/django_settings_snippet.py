"""Часть 5: как то же разделение чтения и записи выглядит в реальном сервисе.

CarRent — проект на Django (~/programs/CarRent), поэтому маршрутизация
описывается не в коде эндпойнтов, а в настройках: Django сам выбирает
подключение по типу операции.

Ниже — фрагменты, которые нужно добавить в CarRent/settings.py.
Файл не подключается к проекту автоматически: это справочный образец,
рабочая демонстрация — в db.py и carrent_api.py.
"""

# --- CarRent/settings.py -----------------------------------------------------

DATABASES = {
    # Запись и всё, что требует актуальных данных.
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "postgres",
        "USER": "postgres",
        "PASSWORD": "...",              # из переменных окружения
        "HOST": "postgres_primary",
        "PORT": "5432",
    },
    # Только чтение. Отдельное подключение к Replica.
    "replica": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "postgres",
        "USER": "postgres",
        "PASSWORD": "...",
        "HOST": "postgres_replica",
        "PORT": "5432",
        "OPTIONS": {
            # Страховка на стороне клиента: даже ошибочный UPDATE не уйдёт
            # на реплику незамеченным, а упадёт сразу.
            "options": "-c default_transaction_read_only=on",
        },
        # Django не должен пытаться создавать тестовую БД из реплики.
        "TEST": {"MIRROR": "default"},
    },
}

DATABASE_ROUTERS = ["RentSystem.routers.ReadWriteRouter"]


# --- RentSystem/routers.py ---------------------------------------------------

class ReadWriteRouter:
    """Чтение уводим на Replica, запись оставляем на Primary."""

    def db_for_read(self, model, **hints):
        return "replica"

    def db_for_write(self, model, **hints):
        return "default"

    def allow_relation(self, obj1, obj2, **hints):
        # Обе базы содержат одни и те же данные, связи между объектами законны.
        return True

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        # Миграции применяются только на Primary; на Replica схема приедет
        # через WAL сама.
        return db == "default"


# --- Использование в коде сервиса -------------------------------------------
#
#   # GET /rentals?user_id=...  — уйдёт на replica по правилу роутера
#   Rental.objects.filter(user_id=uid).order_by("-created_at")[:50]
#
#   # POST /rentals — уйдёт на default (Primary)
#   Rental.objects.create(user_id=uid, car_id=cid, minute_fee=fee)
#
#   # Явное указание, когда нужно прочитать гарантированно свежие данные
#   # сразу после записи (обходим replication lag):
#   Rental.objects.using("default").get(pk=rental_id)
