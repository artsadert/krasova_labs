# PostgreSQL с расширениями, нужными проекту:
#   postgis — геоданные сервиса CarRent;
#   pg_cron — планировщик внутри СУБД: создаёт будущие партиции и ведёт
#             журнал проверок, из которого alert-система забирает инциденты.
FROM postgres:18

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      postgresql-18-postgis \
      postgresql-18-cron \
 && rm -rf /var/lib/apt/lists/*

# Скрипты инициализации выполняются один раз, при создании пустого кластера.
COPY docker/init/ /docker-entrypoint-initdb.d/

EXPOSE 5432
