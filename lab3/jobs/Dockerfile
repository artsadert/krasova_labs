# Alert-система партиционирования: ночное создание партиций,
# контроль горизонта и уведомления в Telegram.
FROM python:3.13-slim

# psql — единственная зависимость: код ходит в БД через него,
# внешних Python-пакетов система не требует.
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client tzdata \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    TZ=Europe/Moscow \
    STATE_DIR=/state

WORKDIR /app
COPY *.py /app/

# Контейнер работает не под root.
# Внимание на порядок: каталог /state создаётся и передаётся пользователю ДО
# объявления VOLUME — всё, что сделано с каталогом после VOLUME, отбрасывается,
# и том остался бы принадлежать root, а непривилегированный процесс не смог бы
# в него писать.
RUN useradd --create-home --uid 10001 partitions \
 && mkdir -p /state \
 && chown -R partitions:partitions /app /state
USER partitions

# Состояние алертов переживает перезапуск контейнера — иначе после каждого
# рестарта система заново слала бы alert об уже известной проблеме.
VOLUME ["/state"]

# Проверка живости: health check по первой наблюдаемой таблице.
HEALTHCHECK --interval=60s --timeout=20s --start-period=15s --retries=3 \
  CMD python3 -c "import sys, db; db.query('SELECT 1'); sys.exit(0)" || exit 1

CMD ["python3", "scheduler.py"]
