# Результаты выполнения — лабораторная работа №1

Файл содержит все SQL-запросы задания и **фактический вывод** `psql`, полученный
на запущенной в Docker базе. Порядок разделов совпадает с порядком заданий.

Каждый замеряемый запрос выполнялся дважды: первый прогон прогревает
`shared_buffers` и его результат отбрасывается, в файл записан второй прогон.
Поэтому во всех планах видно `Buffers: shared hit=...` без `read=...` —
измеряется работа планировщика и исполнителя, а не скорость диска.

Воспроизведение целиком: `./run_lab.sh` (перезаписывает `results/raw_output.txt`).

Среда: PostgreSQL 18.0 в контейнере `database_postgres`, схема `krasova_lab1`,
таблица `orders`, 1 000 000 строк.

---

## Подготовка. Схема и таблица

```sql
DROP SCHEMA IF EXISTS krasova_lab1 CASCADE;
CREATE SCHEMA krasova_lab1;
SET search_path TO krasova_lab1;

CREATE TABLE orders (
    id          BIGSERIAL PRIMARY KEY,
    user_id     INTEGER        NOT NULL,
    product_id  INTEGER        NOT NULL,
    status      VARCHAR(20)    NOT NULL,
    amount      NUMERIC(10, 2) NOT NULL,
    created_at  TIMESTAMP      NOT NULL,
    updated_at  TIMESTAMP      NOT NULL
);
```

**Структура таблицы**

```sql
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema='krasova_lab1' AND table_name='orders'
ORDER BY ordinal_position;
```

```text
 column_name |          data_type          | is_nullable 
-------------+-----------------------------+-------------
 id          | bigint                      | NO
 user_id     | integer                     | NO
 product_id  | integer                     | NO
 status      | character varying           | NO
 amount      | numeric                     | NO
 created_at  | timestamp without time zone | NO
 updated_at  | timestamp without time zone | NO
(7 rows)
```

**Индексы сразу после `CREATE TABLE`**

```sql
SELECT indexname, indexdef FROM pg_indexes
WHERE schemaname='krasova_lab1' ORDER BY indexname;
```

```text
  indexname  |                                indexdef                                 
-------------+-------------------------------------------------------------------------
 orders_pkey | CREATE UNIQUE INDEX orders_pkey ON krasova_lab1.orders USING btree (id)
(1 row)
```

---

## Подготовка. Генерация 1 000 000 строк

```sql
SET search_path TO krasova_lab1;

TRUNCATE orders RESTART IDENTITY;
SELECT setseed(0.42);

INSERT INTO orders (user_id, product_id, status, amount, created_at, updated_at)
SELECT user_id, product_id, status, amount, created_at,
       created_at + (random() * 72)::int * INTERVAL '1 hour' AS updated_at
FROM (
    SELECT
        ((i - 1) / 10) + 1                                         AS user_id,
        1 + (random() * 999)::int                                  AS product_id,
        (ARRAY['NEW','PAID','DELIVERED','CANCELLED'])[1 + (i % 4)] AS status,
        round((random() * 9990 + 10)::numeric, 2)                  AS amount,
        NOW()::timestamp - INTERVAL '2 years'
            + (random() * 730 * 86400)::int * INTERVAL '1 second'  AS created_at
    FROM generate_series(1, 1000000) AS i
) src;

ANALYZE orders;
```

```text
INSERT 0 1000000
ANALYZE
```

**Объём и границы данных**

```sql
SELECT count(*) AS rows, count(DISTINCT user_id) AS users,
       min(created_at)::date AS first_day, max(created_at)::date AS last_day,
       pg_size_pretty(pg_total_relation_size('orders')) AS total_size FROM orders;
```

```text
  rows   | users  | first_day  |  last_day  | total_size 
---------+--------+------------+------------+------------
 1000000 | 100000 | 2024-09-10 | 2026-09-10 | 98 MB
(1 row)
```

**Число заказов на пользователя**

```sql
SELECT min(cnt) AS min_orders_per_user, max(cnt) AS max_orders_per_user
FROM (SELECT count(*) AS cnt FROM orders GROUP BY user_id) s;
```

```text
 min_orders_per_user | max_orders_per_user 
---------------------+---------------------
                  10 |                  10
(1 row)
```

**Распределение статусов**

```sql
SELECT status, count(*) AS rows,
       round(100.0*count(*)/sum(count(*)) OVER (),2) AS pct
FROM orders GROUP BY status ORDER BY status;
```

```text
  status   |  rows  |  pct  
-----------+--------+-------
 CANCELLED | 250000 | 25.00
 DELIVERED | 250000 | 25.00
 NEW       | 250000 | 25.00
 PAID      | 250000 | 25.00
(4 rows)
```

---

## Задание 3. `EXPLAIN` до создания индекса

```sql
EXPLAIN (COSTS)
SELECT * FROM orders WHERE user_id = 123;
```

```text
                                QUERY PLAN                                
--------------------------------------------------------------------------
 Gather  (cost=1000.00..16013.33 rows=10 width=45)
   Workers Planned: 2
   ->  Parallel Seq Scan on orders  (cost=0.00..15012.33 rows=4 width=45)
         Filter: (user_id = 123)
(4 rows)
```

---

## Задание 4. `EXPLAIN ANALYZE` до создания индекса

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE user_id = 123;
```

```text
                                                       QUERY PLAN                                                        
-------------------------------------------------------------------------------------------------------------------------
 Gather  (cost=1000.00..16013.33 rows=10 width=45) (actual time=0.372..30.515 rows=10.00 loops=1)
   Workers Planned: 2
   Workers Launched: 2
   Buffers: shared hit=9804
   ->  Parallel Seq Scan on orders  (cost=0.00..15012.33 rows=4 width=45) (actual time=14.800..23.717 rows=3.33 loops=3)
         Filter: (user_id = 123)
         Rows Removed by Filter: 333330
         Buffers: shared hit=9804
 Planning:
   Buffers: shared hit=75
 Planning Time: 0.329 ms
 Execution Time: 30.587 ms
(12 rows)
```

---

## Задание 5. Sequential Scan

**Запрос без `WHERE`**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders;
```

```text
                                                     QUERY PLAN                                                      
---------------------------------------------------------------------------------------------------------------------
 Seq Scan on orders  (cost=0.00..19804.00 rows=1000000 width=45) (actual time=0.034..59.072 rows=1000000.00 loops=1)
   Buffers: shared hit=9804
 Planning:
   Buffers: shared hit=69
 Planning Time: 0.616 ms
 Execution Time: 99.081 ms
(6 rows)
```

**Запрос с условием `amount > 0`**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE amount > 0;
```

```text
                                                     QUERY PLAN                                                      
---------------------------------------------------------------------------------------------------------------------
 Seq Scan on orders  (cost=0.00..22304.00 rows=999900 width=45) (actual time=0.010..130.309 rows=1000000.00 loops=1)
   Filter: (amount > '0'::numeric)
   Buffers: shared hit=9804
 Planning:
   Buffers: shared hit=77
 Planning Time: 0.314 ms
 Execution Time: 169.722 ms
(7 rows)
```

---

## Задание 6. B-tree индекс по `user_id`

**Создание индекса**

```sql
CREATE INDEX idx_orders_user_id ON orders(user_id);
```

```text
(команда выполнена, вывода нет)
```

**Размер индекса**

```sql
SELECT pg_size_pretty(pg_relation_size('idx_orders_user_id')) AS index_size;
```

```text
 index_size 
------------
 9256 kB
(1 row)
```

**Тот же запрос, что в задании 4, но уже с индексом**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE user_id = 123;
```

```text
                                                           QUERY PLAN                                                            
---------------------------------------------------------------------------------------------------------------------------------
 Index Scan using idx_orders_user_id on orders  (cost=0.42..8.60 rows=10 width=45) (actual time=0.020..0.025 rows=10.00 loops=1)
   Index Cond: (user_id = 123)
   Index Searches: 1
   Buffers: shared hit=5
 Planning:
   Buffers: shared hit=97
 Planning Time: 0.551 ms
 Execution Time: 0.127 ms
(8 rows)
```

---

## Задание 7. Индекс при низкой селективности

**Создание индекса по `status`**

```sql
CREATE INDEX idx_orders_status ON orders(status);
```

```text
(команда выполнена, вывода нет)
```

**status = 'NEW'**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE status = 'NEW';
```

```text
                                                                QUERY PLAN                                                                
------------------------------------------------------------------------------------------------------------------------------------------
 Bitmap Heap Scan on orders  (cost=2775.46..15681.12 rows=248133 width=45) (actual time=11.541..46.695 rows=250000.00 loops=1)
   Recheck Cond: ((status)::text = 'NEW'::text)
   Heap Blocks: exact=9804
   Buffers: shared hit=10017
   ->  Bitmap Index Scan on idx_orders_status  (cost=0.00..2713.42 rows=248133 width=0) (actual time=9.199..9.200 rows=250000.00 loops=1)
         Index Cond: ((status)::text = 'NEW'::text)
         Index Searches: 1
         Buffers: shared hit=213
 Planning:
   Buffers: shared hit=117
 Planning Time: 0.640 ms
 Execution Time: 57.139 ms
(12 rows)
```

**status = 'PAID'**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE status = 'PAID';
```

```text
                                                                QUERY PLAN                                                                
------------------------------------------------------------------------------------------------------------------------------------------
 Bitmap Heap Scan on orders  (cost=2831.24..15801.08 rows=253267 width=45) (actual time=8.166..36.365 rows=250000.00 loops=1)
   Recheck Cond: ((status)::text = 'PAID'::text)
   Heap Blocks: exact=9804
   Buffers: shared hit=10017
   ->  Bitmap Index Scan on idx_orders_status  (cost=0.00..2767.93 rows=253267 width=0) (actual time=6.392..6.392 rows=250000.00 loops=1)
         Index Cond: ((status)::text = 'PAID'::text)
         Index Searches: 1
         Buffers: shared hit=213
 Planning:
   Buffers: shared hit=117
 Planning Time: 0.352 ms
 Execution Time: 45.851 ms
(12 rows)
```

**status = 'DELIVERED'**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE status = 'DELIVERED';
```

```text
                                                                QUERY PLAN                                                                
------------------------------------------------------------------------------------------------------------------------------------------
 Bitmap Heap Scan on orders  (cost=2785.66..15701.32 rows=248933 width=45) (actual time=11.948..49.274 rows=250000.00 loops=1)
   Recheck Cond: ((status)::text = 'DELIVERED'::text)
   Heap Blocks: exact=9804
   Buffers: shared hit=10021
   ->  Bitmap Index Scan on idx_orders_status  (cost=0.00..2723.42 rows=248933 width=0) (actual time=8.995..8.995 rows=250000.00 loops=1)
         Index Cond: ((status)::text = 'DELIVERED'::text)
         Index Searches: 1
         Buffers: shared hit=217
 Planning:
   Buffers: shared hit=117
 Planning Time: 0.679 ms
 Execution Time: 60.297 ms
(12 rows)
```

**status = 'CANCELLED'**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE status = 'CANCELLED';
```

```text
                                                                QUERY PLAN                                                                
------------------------------------------------------------------------------------------------------------------------------------------
 Bitmap Heap Scan on orders  (cost=2791.34..15716.18 rows=249667 width=45) (actual time=10.436..41.726 rows=250000.00 loops=1)
   Recheck Cond: ((status)::text = 'CANCELLED'::text)
   Heap Blocks: exact=9804
   Buffers: shared hit=10020
   ->  Bitmap Index Scan on idx_orders_status  (cost=0.00..2728.93 rows=249667 width=0) (actual time=8.161..8.162 rows=250000.00 loops=1)
         Index Cond: ((status)::text = 'CANCELLED'::text)
         Index Searches: 1
         Buffers: shared hit=216
 Planning:
   Buffers: shared hit=117
 Planning Time: 0.430 ms
 Execution Time: 52.250 ms
(12 rows)
```

---

## Задание 8. Исследование селективности

**Доля каждого статуса в таблице**

```sql
SELECT status, count(*) AS rows,
       round(100.0*count(*)/(SELECT count(*) FROM orders),2) AS pct_of_table
FROM orders GROUP BY status ORDER BY status;
```

```text
  status   |  rows  | pct_of_table 
-----------+--------+--------------
 CANCELLED | 250000 |        25.00
 DELIVERED | 250000 |        25.00
 NEW       | 250000 |        25.00
 PAID      | 250000 |        25.00
(4 rows)
```

**Для сравнения — высокоселективное условие (0.001% таблицы)**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE user_id = 777;
```

```text
                                                           QUERY PLAN                                                            
---------------------------------------------------------------------------------------------------------------------------------
 Index Scan using idx_orders_user_id on orders  (cost=0.42..8.60 rows=10 width=45) (actual time=0.023..0.027 rows=10.00 loops=1)
   Index Cond: (user_id = 777)
   Index Searches: 1
   Buffers: shared hit=4
 Planning:
   Buffers: shared hit=114
 Planning Time: 0.769 ms
 Execution Time: 0.128 ms
(8 rows)
```

---

## Задание 9. Диапазонный запрос по дате

**До индекса: интервал 7 дней**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE created_at >= NOW() - INTERVAL '7 days';
```

```text
                                                          QUERY PLAN                                                          
------------------------------------------------------------------------------------------------------------------------------
 Gather  (cost=1000.00..19266.67 rows=11710 width=45) (actual time=0.568..94.651 rows=9538.00 loops=1)
   Workers Planned: 2
   Workers Launched: 2
   Buffers: shared hit=9804
   ->  Parallel Seq Scan on orders  (cost=0.00..17095.67 rows=4879 width=45) (actual time=0.063..87.586 rows=3179.33 loops=3)
         Filter: (created_at >= (now() - '7 days'::interval))
         Rows Removed by Filter: 330154
         Buffers: shared hit=9804
 Planning:
   Buffers: shared hit=115
 Planning Time: 0.659 ms
 Execution Time: 95.199 ms
(12 rows)
```

**Создание индекса по `created_at`**

```sql
CREATE INDEX idx_orders_created_at ON orders(created_at);
```

```text
(команда выполнена, вывода нет)
```

**После индекса: 1 день**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE created_at >= NOW() - INTERVAL '1 day';
```

```text
                                                               QUERY PLAN                                                               
----------------------------------------------------------------------------------------------------------------------------------------
 Bitmap Heap Scan on orders  (cost=33.91..4523.09 rows=1740 width=45) (actual time=0.649..5.508 rows=1354.00 loops=1)
   Recheck Cond: (created_at >= (now() - '1 day'::interval))
   Heap Blocks: exact=1263
   Buffers: shared hit=1272
   ->  Bitmap Index Scan on idx_orders_created_at  (cost=0.00..33.48 rows=1740 width=0) (actual time=0.382..0.382 rows=1354.00 loops=1)
         Index Cond: (created_at >= (now() - '1 day'::interval))
         Index Searches: 1
         Buffers: shared hit=9
 Planning:
   Buffers: shared hit=140
 Planning Time: 0.806 ms
 Execution Time: 5.711 ms
(12 rows)
```

**После индекса: 7 дней**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE created_at >= NOW() - INTERVAL '7 days';
```

```text
                                                                QUERY PLAN                                                                
------------------------------------------------------------------------------------------------------------------------------------------
 Bitmap Heap Scan on orders  (cost=223.18..10734.23 rows=11710 width=45) (actual time=2.897..12.936 rows=9538.00 loops=1)
   Recheck Cond: (created_at >= (now() - '7 days'::interval))
   Heap Blocks: exact=6104
   Buffers: shared hit=6136
   ->  Bitmap Index Scan on idx_orders_created_at  (cost=0.00..220.25 rows=11710 width=0) (actual time=1.678..1.679 rows=9538.00 loops=1)
         Index Cond: (created_at >= (now() - '7 days'::interval))
         Index Searches: 1
         Buffers: shared hit=32
 Planning:
   Buffers: shared hit=140
 Planning Time: 0.742 ms
 Execution Time: 13.507 ms
(12 rows)
```

**После индекса: 1 месяц**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE created_at >= NOW() - INTERVAL '1 month';
```

```text
                                                                QUERY PLAN                                                                 
-------------------------------------------------------------------------------------------------------------------------------------------
 Bitmap Heap Scan on orders  (cost=838.50..11423.94 rows=44654 width=45) (actual time=6.969..23.185 rows=42161.00 loops=1)
   Recheck Cond: (created_at >= (now() - '1 mon'::interval))
   Heap Blocks: exact=9670
   Buffers: shared hit=9791
   ->  Bitmap Index Scan on idx_orders_created_at  (cost=0.00..827.33 rows=44654 width=0) (actual time=4.528..4.529 rows=42161.00 loops=1)
         Index Cond: (created_at >= (now() - '1 mon'::interval))
         Index Searches: 1
         Buffers: shared hit=121
 Planning:
   Buffers: shared hit=136
 Planning Time: 0.517 ms
 Execution Time: 25.267 ms
(12 rows)
```

**После индекса: 1 год**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE created_at >= NOW() - INTERVAL '1 year';
```

```text
                                                     QUERY PLAN                                                     
--------------------------------------------------------------------------------------------------------------------
 Seq Scan on orders  (cost=0.00..27304.00 rows=503789 width=45) (actual time=0.018..229.719 rows=500330.00 loops=1)
   Filter: (created_at >= (now() - '1 year'::interval))
   Rows Removed by Filter: 499670
   Buffers: shared hit=9804
 Planning:
   Buffers: shared hit=136
 Planning Time: 0.514 ms
 Execution Time: 250.334 ms
(8 rows)
```

---

## Задание 10. Bitmap Scan

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE status = 'NEW';
```

```text
                                                                QUERY PLAN                                                                
------------------------------------------------------------------------------------------------------------------------------------------
 Bitmap Heap Scan on orders  (cost=2775.46..15681.12 rows=248133 width=45) (actual time=10.165..47.450 rows=250000.00 loops=1)
   Recheck Cond: ((status)::text = 'NEW'::text)
   Heap Blocks: exact=9804
   Buffers: shared hit=10017
   ->  Bitmap Index Scan on idx_orders_status  (cost=0.00..2713.42 rows=248133 width=0) (actual time=7.963..7.964 rows=250000.00 loops=1)
         Index Cond: ((status)::text = 'NEW'::text)
         Index Searches: 1
         Buffers: shared hit=213
 Planning:
   Buffers: shared hit=134
 Planning Time: 0.398 ms
 Execution Time: 59.932 ms
(12 rows)
```

---

## Задание 11. Два отдельных индекса в одном запросе

**Индексы, существующие на этот момент**

```sql
SELECT indexname FROM pg_indexes
WHERE schemaname='krasova_lab1' ORDER BY indexname;
```

```text
       indexname       
-----------------------
 idx_orders_created_at
 idx_orders_status
 idx_orders_user_id
 orders_pkey
(4 rows)
```

**Запрос по двум колонкам**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE user_id = 123 AND status = 'PAID';
```

```text
                                                          QUERY PLAN                                                           
-------------------------------------------------------------------------------------------------------------------------------
 Index Scan using idx_orders_user_id on orders  (cost=0.42..8.62 rows=2 width=45) (actual time=0.028..0.033 rows=3.00 loops=1)
   Index Cond: (user_id = 123)
   Filter: ((status)::text = 'PAID'::text)
   Rows Removed by Filter: 7
   Index Searches: 1
   Buffers: shared hit=5
 Planning:
   Buffers: shared hit=142
 Planning Time: 0.704 ms
 Execution Time: 0.087 ms
(10 rows)
```

---

## Задание 12. Составной индекс

**Создание составного индекса**

```sql
CREATE INDEX idx_orders_user_status ON orders(user_id, status);
```

```text
(команда выполнена, вывода нет)
```

**Составной индекс создан, но одиночный `idx_orders_user_id` ещё на месте**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE user_id = 123 AND status = 'PAID';
```

```text
                                                          QUERY PLAN                                                           
-------------------------------------------------------------------------------------------------------------------------------
 Index Scan using idx_orders_user_id on orders  (cost=0.42..8.62 rows=2 width=45) (actual time=0.029..0.035 rows=3.00 loops=1)
   Index Cond: (user_id = 123)
   Filter: ((status)::text = 'PAID'::text)
   Rows Removed by Filter: 7
   Index Searches: 1
   Buffers: shared hit=5
 Planning:
   Buffers: shared hit=157
 Planning Time: 0.890 ms
 Execution Time: 0.081 ms
(10 rows)
```

**Убираем одиночный индекс, чтобы сравнение было честным**

```sql
DROP INDEX idx_orders_user_id;
```

```text
(команда выполнена, вывода нет)
```

**Тот же запрос: работает составной индекс**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE user_id = 123 AND status = 'PAID';
```

```text
                                                             QUERY PLAN                                                             
------------------------------------------------------------------------------------------------------------------------------------
 Index Scan using idx_orders_user_status on orders  (cost=0.42..10.21 rows=2 width=45) (actual time=0.031..0.034 rows=3.00 loops=1)
   Index Cond: ((user_id = 123) AND ((status)::text = 'PAID'::text))
   Index Searches: 1
   Buffers: shared hit=5
 Planning:
   Buffers: shared hit=145
 Planning Time: 0.732 ms
 Execution Time: 0.086 ms
(8 rows)
```

**Возвращаем одиночный индекс**

```sql
CREATE INDEX idx_orders_user_id ON orders(user_id);
```

```text
(команда выполнена, вывода нет)
```

---

## Задание 13. Порядок колонок в составном индексе

### Вариант A: только индекс `(user_id, created_at)`

**Оставляем в схеме единственный рабочий индекс**

```sql
DROP INDEX idx_orders_user_id, idx_orders_status,
  idx_orders_created_at, idx_orders_user_status;
CREATE INDEX idx_orders_user_created ON orders(user_id, created_at);
```

```text
(команда выполнена, вывода нет)
```

**Проверка состава индексов**

```sql
SELECT indexname FROM pg_indexes
WHERE schemaname='krasova_lab1' ORDER BY indexname;
```

```text
        indexname        
-------------------------
 idx_orders_user_created
 orders_pkey
(2 rows)
```

**A1. Условие только по `user_id`**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE user_id = 123;
```

```text
                                                              QUERY PLAN                                                               
---------------------------------------------------------------------------------------------------------------------------------------
 Index Scan using idx_orders_user_created on orders  (cost=0.42..24.35 rows=10 width=45) (actual time=0.016..0.021 rows=10.00 loops=1)
   Index Cond: (user_id = 123)
   Index Searches: 1
   Buffers: shared hit=11
 Planning:
   Buffers: shared hit=105
 Planning Time: 0.668 ms
 Execution Time: 0.091 ms
(8 rows)
```

**A2. `user_id` + диапазон дат**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE user_id = 123
  AND created_at >= NOW() - INTERVAL '1 year';
```

```text
                                                             QUERY PLAN                                                              
-------------------------------------------------------------------------------------------------------------------------------------
 Index Scan using idx_orders_user_created on orders  (cost=0.43..15.53 rows=5 width=45) (actual time=0.024..0.027 rows=6.00 loops=1)
   Index Cond: ((user_id = 123) AND (created_at >= (now() - '1 year'::interval)))
   Index Searches: 1
   Buffers: shared hit=11
 Planning:
   Buffers: shared hit=115
 Planning Time: 0.460 ms
 Execution Time: 0.064 ms
(8 rows)
```

**A3. Условие только по дате**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE created_at >= NOW() - INTERVAL '1 day';
```

```text
                                                         QUERY PLAN                                                         
----------------------------------------------------------------------------------------------------------------------------
 Gather  (cost=1000.00..18269.37 rows=1737 width=45) (actual time=0.553..95.745 rows=1354.00 loops=1)
   Workers Planned: 2
   Workers Launched: 2
   Buffers: shared hit=9804
   ->  Parallel Seq Scan on orders  (cost=0.00..17095.67 rows=724 width=45) (actual time=0.113..87.449 rows=451.33 loops=3)
         Filter: (created_at >= (now() - '1 day'::interval))
         Rows Removed by Filter: 332882
         Buffers: shared hit=9804
 Planning:
   Buffers: shared hit=110
 Planning Time: 0.647 ms
 Execution Time: 95.909 ms
(12 rows)
```

### Вариант B: только индекс `(created_at, user_id)`

**Меняем индекс на обратный по порядку колонок**

```sql
DROP INDEX idx_orders_user_created;
CREATE INDEX idx_orders_created_user ON orders(created_at, user_id);
```

```text
(команда выполнена, вывода нет)
```

**B1. Условие только по `user_id`**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE user_id = 123;
```

```text
                                                       QUERY PLAN                                                        
-------------------------------------------------------------------------------------------------------------------------
 Gather  (cost=1000.00..16013.33 rows=10 width=45) (actual time=0.531..30.843 rows=10.00 loops=1)
   Workers Planned: 2
   Workers Launched: 2
   Buffers: shared hit=9804
   ->  Parallel Seq Scan on orders  (cost=0.00..15012.33 rows=4 width=45) (actual time=15.081..23.929 rows=3.33 loops=3)
         Filter: (user_id = 123)
         Rows Removed by Filter: 333330
         Buffers: shared hit=9804
 Planning:
   Buffers: shared hit=105
 Planning Time: 0.573 ms
 Execution Time: 30.954 ms
(12 rows)
```

**B2. `user_id` + диапазон дат**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE user_id = 123
  AND created_at >= NOW() - INTERVAL '1 year';
```

```text
                                                               QUERY PLAN                                                                
-----------------------------------------------------------------------------------------------------------------------------------------
 Index Scan using idx_orders_created_user on orders  (cost=0.43..12814.37 rows=5 width=45) (actual time=1.280..15.129 rows=6.00 loops=1)
   Index Cond: ((created_at >= (now() - '1 year'::interval)) AND (user_id = 123))
   Index Searches: 1
   Buffers: shared hit=1937
 Planning:
   Buffers: shared hit=115
 Planning Time: 0.682 ms
 Execution Time: 15.188 ms
(8 rows)
```

**B3. Условие только по дате**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE created_at >= NOW() - INTERVAL '1 day';
```

```text
                                                                QUERY PLAN                                                                
------------------------------------------------------------------------------------------------------------------------------------------
 Bitmap Heap Scan on orders  (cost=41.91..4531.09 rows=1740 width=45) (actual time=0.592..5.249 rows=1354.00 loops=1)
   Recheck Cond: (created_at >= (now() - '1 day'::interval))
   Heap Blocks: exact=1263
   Buffers: shared hit=1274
   ->  Bitmap Index Scan on idx_orders_created_user  (cost=0.00..41.48 rows=1740 width=0) (actual time=0.338..0.339 rows=1354.00 loops=1)
         Index Cond: (created_at >= (now() - '1 day'::interval))
         Index Searches: 1
         Buffers: shared hit=11
 Planning:
   Buffers: shared hit=114
 Planning Time: 0.669 ms
 Execution Time: 5.427 ms
(12 rows)
```

---

## Задание 14. `WHERE` + `ORDER BY`

**Исходное состояние: одиночный индекс по `user_id`**

```sql
DROP INDEX idx_orders_created_user;
CREATE INDEX idx_orders_user_id ON orders(user_id);
```

```text
(команда выполнена, вывода нет)
```

**До оптимизации**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE user_id = 123 ORDER BY created_at DESC;
```

```text
                                                              QUERY PLAN                                                               
---------------------------------------------------------------------------------------------------------------------------------------
 Sort  (cost=8.77..8.79 rows=10 width=45) (actual time=0.064..0.066 rows=10.00 loops=1)
   Sort Key: created_at DESC
   Sort Method: quicksort  Memory: 25kB
   Buffers: shared hit=8
   ->  Index Scan using idx_orders_user_id on orders  (cost=0.42..8.60 rows=10 width=45) (actual time=0.021..0.024 rows=10.00 loops=1)
         Index Cond: (user_id = 123)
         Index Searches: 1
         Buffers: shared hit=5
 Planning:
   Buffers: shared hit=124
 Planning Time: 0.454 ms
 Execution Time: 0.129 ms
(12 rows)
```

**Заменяем одиночный индекс на составной с нужным порядком сортировки**

```sql
DROP INDEX idx_orders_user_id;
CREATE INDEX idx_orders_user_created_desc ON orders(user_id, created_at DESC);
```

```text
(команда выполнена, вывода нет)
```

**После оптимизации**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE user_id = 123 ORDER BY created_at DESC;
```

```text
                                                                 QUERY PLAN                                                                 
--------------------------------------------------------------------------------------------------------------------------------------------
 Index Scan using idx_orders_user_created_desc on orders  (cost=0.42..24.35 rows=10 width=45) (actual time=0.013..0.018 rows=10.00 loops=1)
   Index Cond: (user_id = 123)
   Index Searches: 1
   Buffers: shared hit=11
 Planning:
   Buffers: shared hit=132
 Planning Time: 0.642 ms
 Execution Time: 0.073 ms
(8 rows)
```

---

## Задание 15. Pagination Query

**До оптимизации (действует индекс `(user_id, created_at DESC)` из задания 14)**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE user_id = 123
  ORDER BY created_at DESC LIMIT 20;
```

```text
                                                                    QUERY PLAN                                                                    
--------------------------------------------------------------------------------------------------------------------------------------------------
 Limit  (cost=0.42..24.35 rows=10 width=45) (actual time=0.018..0.024 rows=10.00 loops=1)
   Buffers: shared hit=11
   ->  Index Scan using idx_orders_user_created_desc on orders  (cost=0.42..24.35 rows=10 width=45) (actual time=0.017..0.021 rows=10.00 loops=1)
         Index Cond: (user_id = 123)
         Index Searches: 1
         Buffers: shared hit=11
 Planning:
   Buffers: shared hit=132
 Planning Time: 0.453 ms
 Execution Time: 0.059 ms
(10 rows)
```

**Заменяем его на покрывающий индекс и обновляем visibility map**

```sql
DROP INDEX idx_orders_user_created_desc;
CREATE INDEX idx_orders_pagination ON orders(user_id, created_at DESC)
  INCLUDE (id, product_id, status, amount, updated_at);
VACUUM (ANALYZE) orders;
```

```text
(команда выполнена, вывода нет)
```

**После оптимизации**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM orders WHERE user_id = 123
  ORDER BY created_at DESC LIMIT 20;
```

```text
                                                                  QUERY PLAN                                                                   
-----------------------------------------------------------------------------------------------------------------------------------------------
 Limit  (cost=0.42..4.60 rows=10 width=45) (actual time=0.060..0.067 rows=10.00 loops=1)
   Buffers: shared hit=4
   ->  Index Only Scan using idx_orders_pagination on orders  (cost=0.42..4.60 rows=10 width=45) (actual time=0.058..0.062 rows=10.00 loops=1)
         Index Cond: (user_id = 123)
         Heap Fetches: 0
         Index Searches: 1
         Buffers: shared hit=4
 Planning:
   Buffers: shared hit=147
 Planning Time: 1.056 ms
 Execution Time: 0.153 ms
(11 rows)
```

**Контрольный замер: 15 повторов на каждый вариант индекса**

```sql
-- 15 повторов EXPLAIN (ANALYZE) для каждого из двух вариантов индекса
SELECT * FROM orders WHERE user_id = 123 ORDER BY created_at DESC LIMIT 20;
```

```text
Покрывающий индекс idx_orders_pagination (Index Only Scan, 4 буфера):
  min=0.061  median=0.081  max=0.275 ms
Обычный индекс (user_id, created_at DESC) (Index Scan, 11 буферов):
  min=0.059  median=0.087  max=0.111 ms
```

**Итоговые размеры индексов**

```sql
SELECT indexrelname AS index_name,
       pg_size_pretty(pg_relation_size(indexrelid)) AS size
FROM pg_stat_user_indexes WHERE schemaname='krasova_lab1'
ORDER BY pg_relation_size(indexrelid) DESC;
```

```text
      index_name       | size  
-----------------------+-------
 idx_orders_pagination | 69 MB
 orders_pkey           | 21 MB
(2 rows)
```

**Цена покрывающего индекса: сравнение размеров**

```sql
CREATE INDEX tmp_plain_idx ON orders(user_id, created_at DESC);
SELECT pg_size_pretty(pg_relation_size('orders'))                AS heap,
       pg_size_pretty(pg_relation_size('tmp_plain_idx'))         AS plain_index,
       pg_size_pretty(pg_relation_size('idx_orders_pagination')) AS covering_index;
DROP INDEX tmp_plain_idx;
```

```text
 heap  | plain_index | covering_index 
-------+-------------+----------------
 77 MB | 30 MB       | 69 MB
(1 row)
```

