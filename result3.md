# Результаты выполнения — лабораторная работа №3

Полный протокол: SQL-запросы, вывод `psql`, запуски job и health check.
Отчёт с выводами — в [`README3.md`](README3.md).

Среда: PostgreSQL 18.0, контейнер `database_postgres`, схемы `krasova_lab3`
(части 1-11) и `carrent_lab3` (часть 12). Дата выполнения — 2026-09-12.

---

# Части 1-9. Стратегии партиционирования

## Создание партиционированных таблиц

```sql
-- ==========================================================================
-- Лабораторная работа №3. Части 1-9: стратегии партиционирования.
-- ==========================================================================
DROP SCHEMA IF EXISTS krasova_lab3 CASCADE;
CREATE SCHEMA krasova_lab3;
SET search_path TO krasova_lab3;

-- --- Часть 1. RANGE по дате ------------------------------------------------
CREATE TABLE events (
    id         BIGINT      NOT NULL,
    user_id    BIGINT      NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    payload    TEXT,
    created_at TIMESTAMP   NOT NULL
) PARTITION BY RANGE (created_at);

CREATE TABLE events_2026_09_09 PARTITION OF events
    FOR VALUES FROM ('2026-09-09') TO ('2026-09-10');
CREATE TABLE events_2026_09_10 PARTITION OF events
    FOR VALUES FROM ('2026-09-10') TO ('2026-09-11');
CREATE TABLE events_2026_09_11 PARTITION OF events
    FOR VALUES FROM ('2026-09-11') TO ('2026-09-12');

-- --- Часть 3. RANGE по числовому значению ----------------------------------
CREATE TABLE products (
    id    BIGINT  NOT NULL,
    name  TEXT    NOT NULL,
    price NUMERIC NOT NULL
) PARTITION BY RANGE (price);

CREATE TABLE products_cheap     PARTITION OF products FOR VALUES FROM (0)    TO (100);
CREATE TABLE products_medium    PARTITION OF products FOR VALUES FROM (100)  TO (1000);
CREATE TABLE products_expensive PARTITION OF products FOR VALUES FROM (1000) TO (MAXVALUE);

-- --- Часть 4. LIST ---------------------------------------------------------
CREATE TABLE customers (
    id            BIGINT      NOT NULL,
    name          TEXT        NOT NULL,
    customer_type VARCHAR(30) NOT NULL
) PARTITION BY LIST (customer_type);

CREATE TABLE customers_b2c        PARTITION OF customers FOR VALUES IN ('B2C');
CREATE TABLE customers_b2b        PARTITION OF customers FOR VALUES IN ('B2B');
CREATE TABLE customers_enterprise PARTITION OF customers FOR VALUES IN ('Enterprise');

-- --- Часть 6. HASH ---------------------------------------------------------
CREATE TABLE user_events (
    id         BIGINT      NOT NULL,
    user_id    BIGINT      NOT NULL,
    event_type VARCHAR(50),
    created_at TIMESTAMP   NOT NULL
) PARTITION BY HASH (user_id);

CREATE TABLE user_events_0 PARTITION OF user_events FOR VALUES WITH (MODULUS 4, REMAINDER 0);
CREATE TABLE user_events_1 PARTITION OF user_events FOR VALUES WITH (MODULUS 4, REMAINDER 1);
CREATE TABLE user_events_2 PARTITION OF user_events FOR VALUES WITH (MODULUS 4, REMAINDER 2);
CREATE TABLE user_events_3 PARTITION OF user_events FOR VALUES WITH (MODULUS 4, REMAINDER 3);
```

**Часть 1. Структура партиционированной таблицы**

```sql
\d+ events
```

```text
                                             Partitioned table "krasova_lab3.events"
   Column   |            Type             | Collation | Nullable | Default | Storage  | Compression | Stats target | Description 
------------+-----------------------------+-----------+----------+---------+----------+-------------+--------------+-------------
 id         | bigint                      |           | not null |         | plain    |             |              | 
 user_id    | bigint                      |           | not null |         | plain    |             |              | 
 event_type | character varying(50)       |           | not null |         | extended |             |              | 
 payload    | text                        |           |          |         | extended |             |              | 
 created_at | timestamp without time zone |           | not null |         | plain    |             |              | 
Partition key: RANGE (created_at)
Not-null constraints:
    "events_id_not_null" NOT NULL "id"
    "events_user_id_not_null" NOT NULL "user_id"
    "events_event_type_not_null" NOT NULL "event_type"
    "events_created_at_not_null" NOT NULL "created_at"
Partitions: events_2026_09_09 FOR VALUES FROM ('2026-09-09 00:00:00') TO ('2026-09-10 00:00:00'),
            events_2026_09_10 FOR VALUES FROM ('2026-09-10 00:00:00') TO ('2026-09-11 00:00:00'),
            events_2026_09_11 FOR VALUES FROM ('2026-09-11 00:00:00') TO ('2026-09-12 00:00:00')
```

**Часть 1. Заполнение тестовыми данными**

```sql
INSERT INTO events (id, user_id, event_type, payload, created_at)
SELECT i,
       (random() * 100000)::bigint,
       (ARRAY['click','view','login','purchase','logout'])[1 + (i % 5)],
       'payload-' || i,
       TIMESTAMP '2026-09-09' + ((i % 3) || ' days')::interval
                              + ((random() * 86399)::int || ' seconds')::interval
FROM generate_series(1, 3000000) AS i;
-- гарантируем наличие пользователя 12345 в каждой партиции
INSERT INTO events (id, user_id, event_type, payload, created_at)
SELECT 9000000 + i, 12345, 'click', 'vip', 
       TIMESTAMP '2026-09-09' + ((i % 3) || ' days')::interval + ((i * 37 % 86399) || ' seconds')::interval
FROM generate_series(1, 30) AS i;
ANALYZE events;
```

```text
(команда выполнена, вывода нет)
```

**Часть 1. Распределение по партициям**

```sql
SELECT tableoid::regclass AS partition_name, COUNT(*)
FROM events GROUP BY tableoid ORDER BY partition_name;
```

```text
  partition_name   |  count  
-------------------+---------
 events_2026_09_09 | 1000010
 events_2026_09_10 | 1000010
 events_2026_09_11 | 1000010
(3 rows)
```

**Часть 1. Запись с created_at = 2026-09-10 12:00:00**

```sql
SELECT tableoid::regclass AS partition_name, created_at
FROM events WHERE created_at = TIMESTAMP '2026-09-10 12:00:00' LIMIT 1;
```

```text
  partition_name   |     created_at      
-------------------+---------------------
 events_2026_09_10 | 2026-09-10 12:00:00
(1 row)
```

**Часть 1. Куда попадают граничные значения**

```sql
INSERT INTO events VALUES (99000001, 1, 'probe', 'p', '2026-09-10 12:00:00');
INSERT INTO events VALUES (99000002, 1, 'probe', 'p', '2026-09-11 00:00:00');
SELECT id, tableoid::regclass AS partition_name, created_at
FROM events WHERE id IN (99000001, 99000002) ORDER BY id;
```

```text
    id    |  partition_name   |     created_at      
----------+-------------------+---------------------
 99000001 | events_2026_09_10 | 2026-09-10 12:00:00
 99000002 | events_2026_09_11 | 2026-09-11 00:00:00
(2 rows)
```

**Часть 1. Вставка за 2026-09-12 (партиции нет)**

```sql
INSERT INTO events VALUES (99000003, 1, 'probe', 'p', '2026-09-12 10:00:00');
```

```text
ERROR:  no partition of relation "events" found for row
DETAIL:  Partition key of the failing row contains (created_at) = (2026-09-12 10:00:00).
```

**Часть 1. Фактические границы партиций**

```sql
SELECT c.relname AS partition, pg_get_expr(c.relpartbound, c.oid) AS bounds
FROM pg_class c
JOIN pg_inherits i ON i.inhrelid = c.oid
JOIN pg_class p ON p.oid = i.inhparent
WHERE p.relname = 'events' ORDER BY c.relname;
```

```text
     partition     |                               bounds                               
-------------------+--------------------------------------------------------------------
 events_2026_09_09 | FOR VALUES FROM ('2026-09-09 00:00:00') TO ('2026-09-10 00:00:00')
 events_2026_09_10 | FOR VALUES FROM ('2026-09-10 00:00:00') TO ('2026-09-11 00:00:00')
 events_2026_09_11 | FOR VALUES FROM ('2026-09-11 00:00:00') TO ('2026-09-12 00:00:00')
(3 rows)
```

**Часть 2. Запрос по диапазону дат — partition pruning**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT COUNT(*) FROM events
WHERE created_at >= '2026-09-10' AND created_at < '2026-09-11';
```

```text
                                                                               QUERY PLAN                                                                               
------------------------------------------------------------------------------------------------------------------------------------------------------------------------
 Finalize Aggregate  (cost=17756.75..17756.76 rows=1 width=8) (actual time=72.532..79.805 rows=1.00 loops=1)
   Buffers: shared hit=5575 read=3890 written=182
   ->  Gather  (cost=17756.53..17756.74 rows=2 width=8) (actual time=70.203..79.791 rows=3.00 loops=1)
         Workers Planned: 2
         Workers Launched: 2
         Buffers: shared hit=5575 read=3890 written=182
         ->  Partial Aggregate  (cost=16756.53..16756.54 rows=1 width=8) (actual time=66.940..66.942 rows=1.00 loops=3)
               Buffers: shared hit=5575 read=3890 written=182
               ->  Parallel Seq Scan on events_2026_09_10 events  (cost=0.00..15715.06 rows=416588 width=0) (actual time=0.131..48.361 rows=333337.00 loops=3)
                     Filter: ((created_at >= '2026-09-10 00:00:00'::timestamp without time zone) AND (created_at < '2026-09-11 00:00:00'::timestamp without time zone))
                     Buffers: shared hit=5575 read=3890 written=182
 Planning:
   Buffers: shared hit=116
 Planning Time: 0.824 ms
 Execution Time: 79.892 ms
(15 rows)
```

**Часть 2. Запрос по event_type — pruning невозможен**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT COUNT(*) FROM events WHERE event_type = 'click';
```

```text
                                                                              QUERY PLAN                                                                              
----------------------------------------------------------------------------------------------------------------------------------------------------------------------
 Finalize Aggregate  (cost=46917.06..46917.07 rows=1 width=8) (actual time=126.961..133.070 rows=1.00 loops=1)
   Buffers: shared hit=15905 read=12490 written=277
   ->  Gather  (cost=46916.85..46917.06 rows=2 width=8) (actual time=126.800..133.062 rows=3.00 loops=1)
         Workers Planned: 2
         Workers Launched: 2
         Buffers: shared hit=15905 read=12490 written=277
         ->  Partial Aggregate  (cost=45916.85..45916.86 rows=1 width=8) (actual time=122.645..122.647 rows=1.00 loops=3)
               Buffers: shared hit=15905 read=12490 written=277
               ->  Parallel Append  (cost=0.00..45284.62 rows=252892 width=0) (actual time=0.446..110.791 rows=200010.00 loops=3)
                     Buffers: shared hit=15905 read=12490 written=277
                     ->  Parallel Seq Scan on events_2026_09_09 events_1  (cost=0.00..14673.39 rows=85668 width=0) (actual time=0.193..32.866 rows=66670.00 loops=3)
                           Filter: ((event_type)::text = 'click'::text)
                           Rows Removed by Filter: 266667
                           Buffers: shared hit=4895 read=4570 written=94
                     ->  Parallel Seq Scan on events_2026_09_10 events_2  (cost=0.00..14673.39 rows=82862 width=0) (actual time=0.034..90.260 rows=200010.00 loops=1)
                           Filter: ((event_type)::text = 'click'::text)
                           Rows Removed by Filter: 800001
                           Buffers: shared hit=5859 read=3606 written=94
                     ->  Parallel Seq Scan on events_2026_09_11 events_3  (cost=0.00..14673.39 rows=84362 width=0) (actual time=0.370..49.121 rows=100005.00 loops=2)
                           Filter: ((event_type)::text = 'click'::text)
                           Rows Removed by Filter: 400000
                           Buffers: shared hit=5151 read=4314 written=89
 Planning:
   Buffers: shared hit=126
 Planning Time: 0.881 ms
 Execution Time: 133.209 ms
(26 rows)
```

**Часть 3. Товары с разными ценами**

```sql
INSERT INTO products (id, name, price) VALUES
 (1,'Ручка', 25.00), (2,'Тетрадь', 99.99), (3,'Наушники', 100.00),
 (4,'Клавиатура', 450.00), (5,'Монитор', 999.99), (6,'Ноутбук', 1000.00),
 (7,'Сервер', 25000.00);
ANALYZE products;
```

```text
(команда выполнена, вывода нет)
```

**Часть 3. Распределение по ценовым партициям**

```sql
SELECT tableoid::regclass AS partition_name, count(*), min(price), max(price)
FROM products GROUP BY tableoid ORDER BY partition_name;
```

```text
   partition_name   | count |   min   |   max    
--------------------+-------+---------+----------
 products_cheap     |     2 |   25.00 |    99.99
 products_medium    |     3 |  100.00 |   999.99
 products_expensive |     2 | 1000.00 | 25000.00
(3 rows)
```

**Часть 3. Запрос price >= 100 AND price < 500**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM products WHERE price >= 100 AND price < 500;
```

```text
                                                      QUERY PLAN                                                      
----------------------------------------------------------------------------------------------------------------------
 Seq Scan on products_medium products  (cost=0.00..1.04 rows=1 width=30) (actual time=0.010..0.012 rows=2.00 loops=1)
   Filter: ((price >= '100'::numeric) AND (price < '500'::numeric))
   Rows Removed by Filter: 1
   Buffers: shared hit=1
 Planning:
   Buffers: shared hit=115
 Planning Time: 0.476 ms
 Execution Time: 0.026 ms
(8 rows)
```

**Часть 4. Заполнение customers**

```sql
INSERT INTO customers (id, name, customer_type)
SELECT i, 'Client ' || i,
       (ARRAY['B2C','B2B','Enterprise'])[1 + (i % 3)]
FROM generate_series(1, 30000) AS i;
ANALYZE customers;
```

```text
(команда выполнена, вывода нет)
```

**Часть 4. Распределение по типам клиентов**

```sql
SELECT tableoid::regclass AS partition_name, COUNT(*)
FROM customers GROUP BY tableoid ORDER BY partition_name;
```

```text
    partition_name    | count 
----------------------+-------
 customers_b2c        | 10000
 customers_b2b        | 10000
 customers_enterprise | 10000
(3 rows)
```

**Часть 4. Запрос customer_type = 'B2B'**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM customers WHERE customer_type = 'B2B';
```

```text
                                                          QUERY PLAN                                                           
-------------------------------------------------------------------------------------------------------------------------------
 Seq Scan on customers_b2b customers  (cost=0.00..196.00 rows=10000 width=24) (actual time=0.019..1.753 rows=10000.00 loops=1)
   Filter: ((customer_type)::text = 'B2B'::text)
   Buffers: shared hit=71
 Planning:
   Buffers: shared hit=91
 Planning Time: 0.586 ms
 Execution Time: 2.341 ms
(7 rows)
```

**Часть 5. Вставка неизвестного значения 'VIP'**

```sql
INSERT INTO customers VALUES (100, 'Test User', 'VIP');
```

```text
ERROR:  no partition of relation "customers" found for row
DETAIL:  Partition key of the failing row contains (customer_type) = (VIP).
```

**Часть 5. Создание DEFAULT-партиции**

```sql
CREATE TABLE customers_default PARTITION OF customers DEFAULT;
```

```text
(команда выполнена, вывода нет)
```

**Часть 5. Повторная вставка после DEFAULT**

```sql
INSERT INTO customers VALUES (100, 'Test User', 'VIP');
SELECT id, name, customer_type, tableoid::regclass AS partition_name
FROM customers WHERE id = 100;
```

```text
 id  |    name    | customer_type |  partition_name   
-----+------------+---------------+-------------------
 100 | Client 100 | B2B           | customers_b2b
 100 | Test User  | VIP           | customers_default
(2 rows)
```

**Часть 5. Попытка создать партицию для 'VIP' позже**

```sql
CREATE TABLE customers_vip PARTITION OF customers FOR VALUES IN ('VIP');
```

```text
ERROR:  updated partition constraint for default partition "customers_default" would be violated by some row
```

**Часть 6. Загрузка 1 000 000 строк в HASH-таблицу**

```sql
INSERT INTO user_events (id, user_id, event_type, created_at)
SELECT i, (random() * 1000000)::bigint, 'evt', NOW()
FROM generate_series(1, 1000000) AS i;
ANALYZE user_events;
```

```text
(команда выполнена, вывода нет)
```

**Часть 6. Равномерность распределения**

```sql
SELECT tableoid::regclass AS partition_name, COUNT(*),
       round(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct
FROM user_events GROUP BY tableoid ORDER BY partition_name;
```

```text
 partition_name | count  |  pct  
----------------+--------+-------
 user_events_0  | 249361 | 24.94
 user_events_1  | 250392 | 25.04
 user_events_2  | 250071 | 25.01
 user_events_3  | 250176 | 25.02
(4 rows)
```

**Часть 6. Запрос по user_id — pruning работает**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM user_events WHERE user_id = 555;
```

```text
                                                                QUERY PLAN                                                                 
-------------------------------------------------------------------------------------------------------------------------------------------
 Gather  (cost=1000.00..4683.32 rows=2 width=28) (actual time=16.326..19.285 rows=0.00 loops=1)
   Workers Planned: 1
   Workers Launched: 1
   Buffers: shared hit=1842
   ->  Parallel Seq Scan on user_events_1 user_events  (cost=0.00..3683.12 rows=1 width=28) (actual time=13.747..13.748 rows=0.00 loops=2)
         Filter: (user_id = 555)
         Rows Removed by Filter: 125196
         Buffers: shared hit=1842
 Planning:
   Buffers: shared hit=93
 Planning Time: 0.538 ms
 Execution Time: 19.365 ms
(12 rows)
```

**Часть 6. Запрос по дате — pruning не работает**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT COUNT(*) FROM user_events WHERE created_at >= NOW() - INTERVAL '1 day';
```

```text
                                                                              QUERY PLAN                                                                               
-----------------------------------------------------------------------------------------------------------------------------------------------------------------------
 Finalize Aggregate  (cost=21774.34..21774.35 rows=1 width=8) (actual time=130.449..135.278 rows=1.00 loops=1)
   Buffers: shared hit=7355
   ->  Gather  (cost=21774.12..21774.33 rows=2 width=8) (actual time=130.359..135.270 rows=3.00 loops=1)
         Workers Planned: 2
         Workers Launched: 2
         Buffers: shared hit=7355
         ->  Partial Aggregate  (cost=20774.12..20774.13 rows=1 width=8) (actual time=126.649..126.651 rows=1.00 loops=3)
               Buffers: shared hit=7355
               ->  Parallel Append  (cost=0.00..19732.45 rows=416667 width=0) (actual time=0.015..108.086 rows=333333.33 loops=3)
                     Buffers: shared hit=7355
                     ->  Parallel Seq Scan on user_events_1 user_events_2  (cost=0.00..4419.56 rows=147289 width=0) (actual time=0.014..64.478 rows=250392.00 loops=1)
                           Filter: (created_at >= (now() - '1 day'::interval))
                           Buffers: shared hit=1842
                     ->  Parallel Seq Scan on user_events_3 user_events_4  (cost=0.00..4415.34 rows=147162 width=0) (actual time=0.016..65.888 rows=250176.00 loops=1)
                           Filter: (created_at >= (now() - '1 day'::interval))
                           Buffers: shared hit=1840
                     ->  Parallel Seq Scan on user_events_2 user_events_3  (cost=0.00..4413.26 rows=147101 width=0) (actual time=0.006..19.186 rows=83357.00 loops=3)
                           Filter: (created_at >= (now() - '1 day'::interval))
                           Buffers: shared hit=1839
                     ->  Parallel Seq Scan on user_events_0 user_events_1  (cost=0.00..4400.95 rows=146683 width=0) (actual time=0.013..65.199 rows=249361.00 loops=1)
                           Filter: (created_at >= (now() - '1 day'::interval))
                           Buffers: shared hit=1834
 Planning:
   Buffers: shared hit=123
 Planning Time: 0.724 ms
 Execution Time: 135.378 ms
(26 rows)
```

**Часть 8. Создание индекса на партиционированной таблице**

```sql
CREATE INDEX idx_events_user_id ON events (user_id);
```

```text
(команда выполнена, вывода нет)
```

**Часть 8. Где физически существует индекс**

```sql
SELECT c.relname AS index_name, c.relispartition AS is_partition_index,
       t.relname AS on_table
FROM pg_class c
JOIN pg_index ix ON ix.indexrelid = c.oid
JOIN pg_class t ON t.oid = ix.indrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'krasova_lab3' AND c.relname LIKE '%user_id%'
ORDER BY c.relname;
```

```text
          index_name           | is_partition_index |     on_table      
-------------------------------+--------------------+-------------------
 events_2026_09_09_user_id_idx | t                  | events_2026_09_09
 events_2026_09_10_user_id_idx | t                  | events_2026_09_10
 events_2026_09_11_user_id_idx | t                  | events_2026_09_11
 idx_events_user_id            | f                  | events
(4 rows)
```

**Часть 8. Partition pruning вместе с индексом**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM events
WHERE created_at >= '2026-09-10' AND created_at < '2026-09-11' AND user_id = 12345;
```

```text
                                                                      QUERY PLAN                                                                      
------------------------------------------------------------------------------------------------------------------------------------------------------
 Bitmap Heap Scan on events_2026_09_10 events  (cost=4.51..47.58 rows=11 width=45) (actual time=0.073..0.148 rows=27.00 loops=1)
   Recheck Cond: (user_id = 12345)
   Filter: ((created_at >= '2026-09-10 00:00:00'::timestamp without time zone) AND (created_at < '2026-09-11 00:00:00'::timestamp without time zone))
   Heap Blocks: exact=18
   Buffers: shared hit=24
   ->  Bitmap Index Scan on events_2026_09_10_user_id_idx  (cost=0.00..4.51 rows=11 width=0) (actual time=0.046..0.047 rows=27.00 loops=1)
         Index Cond: (user_id = 12345)
         Index Searches: 1
         Buffers: shared hit=6
 Planning:
   Buffers: shared hit=174
 Planning Time: 1.840 ms
 Execution Time: 0.247 ms
(13 rows)
```

**Часть 8. Индекс без pruning**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM events WHERE user_id = 12345;
```

```text
                                                                   QUERY PLAN                                                                    
-------------------------------------------------------------------------------------------------------------------------------------------------
 Append  (cost=4.51..142.73 rows=33 width=45) (actual time=0.040..0.210 rows=63.00 loops=1)
   Buffers: shared hit=48
   ->  Bitmap Heap Scan on events_2026_09_09 events_1  (cost=4.51..47.52 rows=11 width=45) (actual time=0.039..0.065 rows=16.00 loops=1)
         Recheck Cond: (user_id = 12345)
         Heap Blocks: exact=7
         Buffers: shared hit=13
         ->  Bitmap Index Scan on events_2026_09_09_user_id_idx  (cost=0.00..4.51 rows=11 width=0) (actual time=0.028..0.028 rows=16.00 loops=1)
               Index Cond: (user_id = 12345)
               Index Searches: 1
               Buffers: shared hit=6
   ->  Bitmap Heap Scan on events_2026_09_10 events_2  (cost=4.51..47.52 rows=11 width=45) (actual time=0.033..0.089 rows=27.00 loops=1)
         Recheck Cond: (user_id = 12345)
         Heap Blocks: exact=18
         Buffers: shared hit=21
         ->  Bitmap Index Scan on events_2026_09_10_user_id_idx  (cost=0.00..4.51 rows=11 width=0) (actual time=0.020..0.020 rows=27.00 loops=1)
               Index Cond: (user_id = 12345)
               Index Searches: 1
               Buffers: shared hit=3
   ->  Bitmap Heap Scan on events_2026_09_11 events_3  (cost=4.51..47.52 rows=11 width=45) (actual time=0.025..0.048 rows=20.00 loops=1)
         Recheck Cond: (user_id = 12345)
         Heap Blocks: exact=11
         Buffers: shared hit=14
         ->  Bitmap Index Scan on events_2026_09_11_user_id_idx  (cost=0.00..4.51 rows=11 width=0) (actual time=0.015..0.015 rows=20.00 loops=1)
               Index Cond: (user_id = 12345)
               Index Searches: 1
               Buffers: shared hit=3
 Planning:
   Buffers: shared hit=231
 Planning Time: 0.858 ms
 Execution Time: 0.320 ms
(30 rows)
```

**Часть 9. COUNT по event_type до индекса**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT COUNT(*) FROM events WHERE event_type = 'click';
```

```text
                                                                              QUERY PLAN                                                                              
----------------------------------------------------------------------------------------------------------------------------------------------------------------------
 Finalize Aggregate  (cost=46917.07..46917.08 rows=1 width=8) (actual time=122.007..127.204 rows=1.00 loops=1)
   Buffers: shared hit=8239 read=20156
   ->  Gather  (cost=46916.86..46917.07 rows=2 width=8) (actual time=121.885..127.195 rows=3.00 loops=1)
         Workers Planned: 2
         Workers Launched: 2
         Buffers: shared hit=8239 read=20156
         ->  Partial Aggregate  (cost=45916.86..45916.87 rows=1 width=8) (actual time=118.046..118.049 rows=1.00 loops=3)
               Buffers: shared hit=8239 read=20156
               ->  Parallel Append  (cost=0.00..45284.63 rows=252892 width=0) (actual time=0.181..106.292 rows=200010.00 loops=3)
                     Buffers: shared hit=8239 read=20156
                     ->  Parallel Seq Scan on events_2026_09_10 events_2  (cost=0.00..14673.39 rows=82862 width=0) (actual time=0.013..30.458 rows=66670.00 loops=3)
                           Filter: ((event_type)::text = 'click'::text)
                           Rows Removed by Filter: 266667
                           Buffers: shared hit=3330 read=6135
                     ->  Parallel Seq Scan on events_2026_09_11 events_3  (cost=0.00..14673.39 rows=84362 width=0) (actual time=0.108..45.758 rows=100005.00 loops=2)
                           Filter: ((event_type)::text = 'click'::text)
                           Rows Removed by Filter: 400000
                           Buffers: shared hit=2553 read=6912
                     ->  Parallel Seq Scan on events_2026_09_09 events_1  (cost=0.00..14673.39 rows=85668 width=0) (actual time=0.304..91.560 rows=200010.00 loops=1)
                           Filter: ((event_type)::text = 'click'::text)
                           Rows Removed by Filter: 800000
                           Buffers: shared hit=2356 read=7109
 Planning:
   Buffers: shared hit=198
 Planning Time: 0.683 ms
 Execution Time: 127.319 ms
(26 rows)
```

**Часть 9. Создание индекса по event_type**

```sql
CREATE INDEX idx_events_event_type ON events (event_type);
```

```text
(команда выполнена, вывода нет)
```

**Часть 9. COUNT по event_type после индекса**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT COUNT(*) FROM events WHERE event_type = 'click';
```

```text
                                                                                    QUERY PLAN                                                                                     
-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
 Finalize Aggregate  (cost=41234.11..41234.12 rows=1 width=8) (actual time=120.107..124.594 rows=1.00 loops=1)
   Buffers: shared hit=42 read=28908 written=7
   ->  Gather  (cost=41233.90..41234.11 rows=2 width=8) (actual time=119.749..124.581 rows=3.00 loops=1)
         Workers Planned: 2
         Workers Launched: 2
         Buffers: shared hit=42 read=28908 written=7
         ->  Partial Aggregate  (cost=40233.90..40233.91 rows=1 width=8) (actual time=115.824..115.827 rows=1.00 loops=3)
               Buffers: shared hit=42 read=28908 written=7
               ->  Parallel Append  (cost=2261.56..39601.67 rows=252892 width=0) (actual time=14.216..98.674 rows=200010.00 loops=3)
                     Buffers: shared hit=42 read=28908 written=7
                     ->  Parallel Bitmap Heap Scan on events_2026_09_09 events_1  (cost=2297.84..12833.68 rows=85668 width=0) (actual time=5.452..26.698 rows=66670.00 loops=3)
                           Recheck Cond: ((event_type)::text = 'click'::text)
                           Heap Blocks: exact=256
                           Buffers: shared hit=21 read=9636 written=5
                           Worker 0:  Heap Blocks: exact=8926
                           Worker 1:  Heap Blocks: exact=283
                           ->  Bitmap Index Scan on events_2026_09_09_event_type_idx  (cost=0.00..2246.44 rows=205602 width=0) (actual time=12.764..12.764 rows=200010.00 loops=1)
                                 Index Cond: ((event_type)::text = 'click'::text)
                                 Index Searches: 1
                                 Buffers: shared hit=1 read=171
                     ->  Parallel Bitmap Heap Scan on events_2026_09_11 events_3  (cost=2261.56..12781.09 rows=84362 width=0) (actual time=6.236..37.719 rows=100005.00 loops=2)
                           Recheck Cond: ((event_type)::text = 'click'::text)
                           Heap Blocks: exact=357
                           Buffers: shared hit=21 read=9636
                           Worker 1:  Heap Blocks: exact=9108
                           ->  Bitmap Index Scan on events_2026_09_11_event_type_idx  (cost=0.00..2210.94 rows=202469 width=0) (actual time=9.491..9.491 rows=200010.00 loops=1)
                                 Index Cond: ((event_type)::text = 'click'::text)
                                 Index Searches: 1
                                 Buffers: shared hit=1 read=171
                     ->  Parallel Bitmap Heap Scan on events_2026_09_10 events_2  (cost=2221.66..12722.44 rows=82862 width=0) (actual time=14.083..75.366 rows=200010.00 loops=1)
                           Recheck Cond: ((event_type)::text = 'click'::text)
                           Heap Blocks: exact=9465
                           Buffers: shared read=9636 written=2
                           ->  Bitmap Index Scan on events_2026_09_10_event_type_idx  (cost=0.00..2171.94 rows=198869 width=0) (actual time=11.292..11.292 rows=200010.00 loops=1)
                                 Index Cond: ((event_type)::text = 'click'::text)
                                 Index Searches: 1
                                 Buffers: shared read=171
 Planning:
   Buffers: shared hit=235 read=18
 Planning Time: 1.589 ms
 Execution Time: 124.753 ms
(41 rows)
```

**Часть 9. Тот же запрос вместе с условием по дате**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT COUNT(*) FROM events
WHERE event_type = 'click' AND created_at >= '2026-09-10' AND created_at < '2026-09-11';
```

```text
                                                                                QUERY PLAN                                                                                 
---------------------------------------------------------------------------------------------------------------------------------------------------------------------------
 Finalize Aggregate  (cost=14344.06..14344.07 rows=1 width=8) (actual time=32.809..37.583 rows=1.00 loops=1)
   Buffers: shared hit=9676
   ->  Gather  (cost=14343.85..14344.06 rows=2 width=8) (actual time=32.553..37.577 rows=3.00 loops=1)
         Workers Planned: 2
         Workers Launched: 2
         Buffers: shared hit=9676
         ->  Partial Aggregate  (cost=13343.85..13343.86 rows=1 width=8) (actual time=29.221..29.222 rows=1.00 loops=3)
               Buffers: shared hit=9676
               ->  Parallel Bitmap Heap Scan on events_2026_09_10 events  (cost=2221.65..13136.74 rows=82845 width=0) (actual time=7.080..25.236 rows=66670.00 loops=3)
                     Recheck Cond: ((event_type)::text = 'click'::text)
                     Filter: ((created_at >= '2026-09-10 00:00:00'::timestamp without time zone) AND (created_at < '2026-09-11 00:00:00'::timestamp without time zone))
                     Heap Blocks: exact=3951
                     Buffers: shared hit=9676
                     Worker 0:  Heap Blocks: exact=3150
                     Worker 1:  Heap Blocks: exact=2364
                     ->  Bitmap Index Scan on events_2026_09_10_event_type_idx  (cost=0.00..2171.94 rows=198869 width=0) (actual time=7.754..7.754 rows=200010.00 loops=1)
                           Index Cond: ((event_type)::text = 'click'::text)
                           Index Searches: 1
                           Buffers: shared hit=171
 Planning:
   Buffers: shared hit=202
 Planning Time: 0.824 ms
 Execution Time: 37.651 ms
(23 rows)
```

**Размеры партиций и их индексов**

```sql
SELECT c.relname AS partition,
       pg_size_pretty(pg_relation_size(c.oid)) AS table_size,
       pg_size_pretty(pg_indexes_size(c.oid)) AS indexes_size
FROM pg_class c JOIN pg_inherits i ON i.inhrelid = c.oid
JOIN pg_class p ON p.oid = i.inhparent
WHERE p.relname = 'events' ORDER BY c.relname;
```

```text
     partition     | table_size | indexes_size 
-------------------+------------+--------------
 events_2026_09_09 | 74 MB      | 16 MB
 events_2026_09_10 | 74 MB      | 16 MB
 events_2026_09_11 | 74 MB      | 16 MB
(3 rows)
```

**Часть 9. Доля строк с event_type = 'click'**

```sql
SELECT count(*) AS total_rows,
       count(*) FILTER (WHERE event_type = 'click') AS click_rows,
       round(100.0 * count(*) FILTER (WHERE event_type = 'click') / count(*), 2) AS click_pct
FROM events;
```

```text
 total_rows | click_rows | click_pct 
------------+------------+-----------
    3000032 |     600030 |     20.00
(1 row)
```


---

# Части 10-11. Автоматизация, контроль и alerting

Ниже — полный прогон production-сценария: проверка, создание партиций,
имитация сбоя ночной job, alert, подавление дубликатов, восстановление и
recovery-уведомление. Уведомления реально доставлялись по HTTP на локальный
приёмник (`jobs/alert_receiver.py`), который играет роль внешнего канала.

```text
########## Шаг 0. Исходное состояние: какие партиции есть сейчас

$ psql -c "SELECT c.relname AS partition FROM pg_class c
JOIN pg_inherits i ON i.inhrelid = c.oid
JOIN pg_class p ON p.oid = i.inhparent
WHERE p.relname = 'events' ORDER BY 1;"
     partition     
-------------------
 events_2026_09_09
 events_2026_09_10
 events_2026_09_11
 events_2026_09_12
 events_2026_09_13
 events_2026_09_14
 events_2026_09_15
(7 rows)


$ date '+сегодня: %Y-%m-%d'
сегодня: 2026-09-12
(код возврата: 0)


########## Шаг 1. Проверка ДО запуска job — партиций на горизонт нет, ожидаем CRITICAL и alert

$ python3 partition_health_check.py --table events --horizon 3
2026-09-12 14:03:29  PartitionHealthCheck: table=events, today=2026-09-12, horizon=3 day(s)
    events_2026_09_12  ok
    events_2026_09_13  ok
    events_2026_09_14  ok
    events_2026_09_15  ok
    Result: OK
    Уведомление не отправлено: всё в порядке, уведомлять не о чем
(код возврата: 0)


########## Шаг 2. Повторная проверка — состояние не изменилось, alert НЕ должен уйти повторно

$ python3 partition_health_check.py --table events --horizon 3
2026-09-12 14:03:29  PartitionHealthCheck: table=events, today=2026-09-12, horizon=3 day(s)
    events_2026_09_12  ok
    events_2026_09_13  ok
    events_2026_09_14  ok
    events_2026_09_15  ok
    Result: OK
    Уведомление не отправлено: всё в порядке, уведомлять не о чем
(код возврата: 0)


########## Шаг 3. CreatePartitionsJob создаёт недостающие партиции

$ python3 create_partitions_job.py --table events --horizon 3
2026-09-12 14:03:29  Partition job started.
2026-09-12 14:03:29  Table: events, today: 2026-09-12, horizon: 3 day(s)
2026-09-12 14:03:29  Existing partitions: 7
2026-09-12 14:03:29  Required partitions: 4
2026-09-12 14:03:29  Missing partitions: 0
2026-09-12 14:03:29  Nothing to create — all required partitions already exist.
2026-09-12 14:03:29  Partition job finished.
(код возврата: 0)


########## Шаг 4. Повторный запуск job — идемпотентность

$ python3 create_partitions_job.py --table events --horizon 3
2026-09-12 14:03:29  Partition job started.
2026-09-12 14:03:29  Table: events, today: 2026-09-12, horizon: 3 day(s)
2026-09-12 14:03:29  Existing partitions: 7
2026-09-12 14:03:29  Required partitions: 4
2026-09-12 14:03:29  Missing partitions: 0
2026-09-12 14:03:29  Nothing to create — all required partitions already exist.
2026-09-12 14:03:29  Partition job finished.
(код возврата: 0)


########## Шаг 5. Проверка после восстановления — ожидаем OK и recovery-уведомление

$ python3 partition_health_check.py --table events --horizon 3
2026-09-12 14:03:30  PartitionHealthCheck: table=events, today=2026-09-12, horizon=3 day(s)
    events_2026_09_12  ok
    events_2026_09_13  ok
    events_2026_09_14  ok
    events_2026_09_15  ok
    Result: OK
    Уведомление не отправлено: всё в порядке, уведомлять не о чем
(код возврата: 0)


########## Шаг 6. Проверка ещё раз — состояние OK не изменилось, уведомлений нет

$ python3 partition_health_check.py --table events --horizon 3
2026-09-12 14:03:30  PartitionHealthCheck: table=events, today=2026-09-12, horizon=3 day(s)
    events_2026_09_12  ok
    events_2026_09_13  ok
    events_2026_09_14  ok
    events_2026_09_15  ok
    Result: OK
    Уведомление не отправлено: всё в порядке, уведомлять не о чем
(код возврата: 0)


########## Шаг 7. Имитация сбоя ночной job: удаляем партицию последнего дня горизонта

$ psql -c "DROP TABLE events_2026_09_15;"

$ psql -c "SELECT c.relname AS partition FROM pg_class c
JOIN pg_inherits i ON i.inhrelid = c.oid
JOIN pg_class p ON p.oid = i.inhparent
WHERE p.relname = 'events' ORDER BY 1;"
     partition     
-------------------
 events_2026_09_09
 events_2026_09_10
 events_2026_09_11
 events_2026_09_12
 events_2026_09_13
 events_2026_09_14
(6 rows)



########## Шаг 8. PartitionHealthCheck обнаруживает проблему — CRITICAL и alert

$ python3 partition_health_check.py --table events --horizon 3
2026-09-12 14:03:30  PartitionHealthCheck: table=events, today=2026-09-12, horizon=3 day(s)
    events_2026_09_12  ok
    events_2026_09_13  ok
    events_2026_09_14  ok
    events_2026_09_15  MISSING
    Result: CRITICAL
    Уведомление (alert):
🚨 Partition alert
Table: events
Missing partitions:
events_2026_09_15
Expected horizon: 3 days
Checked at:
2026-09-12 14:03:30
      console: напечатано в stdout
      webhook: HTTP 200 от http://127.0.0.1:8099/
(код возврата: 2)


########## Шаг 9. Ещё две проверки подряд — alert подавлен, спама нет

$ python3 partition_health_check.py --table events --horizon 3
2026-09-12 14:03:31  PartitionHealthCheck: table=events, today=2026-09-12, horizon=3 day(s)
    events_2026_09_12  ok
    events_2026_09_13  ok
    events_2026_09_14  ok
    events_2026_09_15  MISSING
    Result: CRITICAL
    Уведомление не отправлено: состояние не изменилось — alert подавлен
(код возврата: 2)

$ python3 partition_health_check.py --table events --horizon 3
2026-09-12 14:03:31  PartitionHealthCheck: table=events, today=2026-09-12, horizon=3 day(s)
    events_2026_09_12  ok
    events_2026_09_13  ok
    events_2026_09_14  ok
    events_2026_09_15  MISSING
    Result: CRITICAL
    Уведомление не отправлено: состояние не изменилось — alert подавлен
(код возврата: 2)


########## Шаг 10. Восстановление: запускаем job

$ python3 create_partitions_job.py --table events --horizon 3
2026-09-12 14:03:31  Partition job started.
2026-09-12 14:03:31  Table: events, today: 2026-09-12, horizon: 3 day(s)
2026-09-12 14:03:31  Existing partitions: 6
2026-09-12 14:03:31  Required partitions: 4
2026-09-12 14:03:31  Missing partitions: 1
2026-09-12 14:03:31  Creating: events_2026_09_15
2026-09-12 14:03:31  Partition events_2026_09_15 created successfully.
2026-09-12 14:03:31  Partition job finished.
(код возврата: 0)


########## Шаг 11. Контрольная проверка — OK и recovery-уведомление

$ python3 partition_health_check.py --table events --horizon 3
2026-09-12 14:03:32  PartitionHealthCheck: table=events, today=2026-09-12, horizon=3 day(s)
    events_2026_09_12  ok
    events_2026_09_13  ok
    events_2026_09_14  ok
    events_2026_09_15  ok
    Result: OK
    Уведомление (recovery):
🟢 Partition check OK
Table: events
All required partitions exist.
Checked at:
2026-09-12 14:03:32
      console: напечатано в stdout
      webhook: HTTP 200 от http://127.0.0.1:8099/
(код возврата: 0)


########## Шаг 12. Что реально доставлено во внешний канал

$ cat received_alerts.log
=== доставлено 2026-09-12 14:03:31 ===
🚨 Partition alert
Table: events
Missing partitions:
events_2026_09_15
Expected horizon: 3 days
Checked at:
2026-09-12 14:03:30

=== доставлено 2026-09-12 14:03:32 ===
🟢 Partition check OK
Table: events
All required partitions exist.
Checked at:
2026-09-12 14:03:32

(код возврата: 0)


########## Шаг 13. Итоговое состояние партиций и файла состояния алертов

$ psql -c "SELECT c.relname AS partition, pg_get_expr(c.relpartbound, c.oid) AS bounds
FROM pg_class c JOIN pg_inherits i ON i.inhrelid = c.oid
JOIN pg_class p ON p.oid = i.inhparent
WHERE p.relname = 'events' ORDER BY 1;"
     partition     |                               bounds                               
-------------------+--------------------------------------------------------------------
 events_2026_09_09 | FOR VALUES FROM ('2026-09-09 00:00:00') TO ('2026-09-10 00:00:00')
 events_2026_09_10 | FOR VALUES FROM ('2026-09-10 00:00:00') TO ('2026-09-11 00:00:00')
 events_2026_09_11 | FOR VALUES FROM ('2026-09-11 00:00:00') TO ('2026-09-12 00:00:00')
 events_2026_09_12 | FOR VALUES FROM ('2026-09-12 00:00:00') TO ('2026-09-13 00:00:00')
 events_2026_09_13 | FOR VALUES FROM ('2026-09-13 00:00:00') TO ('2026-09-14 00:00:00')
 events_2026_09_14 | FOR VALUES FROM ('2026-09-14 00:00:00') TO ('2026-09-15 00:00:00')
 events_2026_09_15 | FOR VALUES FROM ('2026-09-15 00:00:00') TO ('2026-09-16 00:00:00')
(7 rows)


$ cat alert_state.json
{
  "events:day": {
    "status": "OK",
    "last_alert_at": "2026-09-12T14:03:32",
    "checked_at": "2026-09-12T14:03:32"
  }
}(код возврата: 0)
```

---

# Часть 12. Партиционирование собственной базы (CarRent)

## Схема

```sql
-- ==========================================================================
-- Часть 12. Партиционирование собственной базы: CarRent, таблица rentals.
-- RANGE по created_at с помесячными партициями.
-- ==========================================================================
DROP SCHEMA IF EXISTS carrent_lab3 CASCADE;
CREATE SCHEMA carrent_lab3;
SET search_path TO carrent_lab3;

CREATE TABLE rentals (
    id          BIGSERIAL     NOT NULL,
    car_id      BIGINT        NOT NULL,
    user_id     BIGINT        NOT NULL,
    started_at  TIMESTAMP     NOT NULL,
    finished_at TIMESTAMP,
    created_at  TIMESTAMP     NOT NULL,
    updated_at  TIMESTAMP     NOT NULL,
    minute_fee  NUMERIC(8,2)  NOT NULL,
    -- В партиционированной таблице первичный ключ обязан включать ключ
    -- партиционирования, поэтому (id) превращается в (id, created_at).
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

CREATE TABLE rentals_2026_06 PARTITION OF rentals
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE rentals_2026_07 PARTITION OF rentals
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE rentals_2026_08 PARTITION OF rentals
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE rentals_2026_09 PARTITION OF rentals
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
```

**Шаг 5. Созданные партиции и их границы**

```sql
SELECT c.relname AS partition, pg_get_expr(c.relpartbound, c.oid) AS bounds
FROM pg_class c JOIN pg_inherits i ON i.inhrelid = c.oid
JOIN pg_class p ON p.oid = i.inhparent
WHERE p.relname = 'rentals' ORDER BY 1;
```

```text
    partition    |                               bounds                               
-----------------+--------------------------------------------------------------------
 rentals_2026_06 | FOR VALUES FROM ('2026-06-01 00:00:00') TO ('2026-07-01 00:00:00')
 rentals_2026_07 | FOR VALUES FROM ('2026-07-01 00:00:00') TO ('2026-08-01 00:00:00')
 rentals_2026_08 | FOR VALUES FROM ('2026-08-01 00:00:00') TO ('2026-09-01 00:00:00')
 rentals_2026_09 | FOR VALUES FROM ('2026-09-01 00:00:00') TO ('2026-10-01 00:00:00')
(4 rows)
```

**Шаг 5. Заполнение 2 000 000 аренд**

```sql
INSERT INTO rentals (car_id, user_id, started_at, finished_at, created_at, updated_at, minute_fee)
SELECT (random() * 50000)::bigint + 1,
       (random() * 200000)::bigint + 1,
       ts, ts + ((random() * 120)::int || ' minutes')::interval,
       ts, ts, round((5 + random() * 20)::numeric, 2)
FROM (
  SELECT TIMESTAMP '2026-06-01' + ((random() * 121 * 86400)::int || ' seconds')::interval AS ts
  FROM generate_series(1, 2000000)
) g;
ANALYZE rentals;
```

```text
(команда выполнена, вывода нет)
```

**Шаг 5. Распределение по месяцам**

```sql
SELECT tableoid::regclass AS partition_name, COUNT(*),
       pg_size_pretty(pg_relation_size(tableoid)) AS size
FROM rentals GROUP BY tableoid ORDER BY partition_name;
```

```text
 partition_name  | count  | size  
-----------------+--------+-------
 rentals_2026_06 | 495184 | 44 MB
 rentals_2026_07 | 512213 | 45 MB
 rentals_2026_08 | 514428 | 46 MB
 rentals_2026_09 | 478175 | 42 MB
(4 rows)
```

**Шаг 6. Индекс поверх партиционированной таблицы**

```sql
CREATE INDEX idx_rentals_user_created ON rentals (user_id, created_at DESC);
ANALYZE rentals;
```

```text
(команда выполнена, вывода нет)
```

**Шаг 6. Запрос 1 — GET /rentals?from=&to=**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM rentals
WHERE created_at >= '2026-08-01' AND created_at < '2026-09-01'
ORDER BY created_at DESC LIMIT 50;
```

```text
                                                                               QUERY PLAN                                                                               
------------------------------------------------------------------------------------------------------------------------------------------------------------------------
 Limit  (cost=17180.16..17185.98 rows=50 width=62) (actual time=50.352..54.311 rows=50.00 loops=1)
   Buffers: shared hit=3489 read=2431 written=182
   ->  Gather Merge  (cost=17180.16..77081.78 rows=514325 width=62) (actual time=50.350..54.303 rows=50.00 loops=1)
         Workers Planned: 2
         Workers Launched: 2
         Buffers: shared hit=3489 read=2431 written=182
         ->  Sort  (cost=16180.13..16715.89 rows=214302 width=62) (actual time=47.021..47.026 rows=40.67 loops=3)
               Sort Key: rentals.created_at DESC
               Sort Method: top-N heapsort  Memory: 36kB
               Buffers: shared hit=3489 read=2431 written=182
               Worker 0:  Sort Method: top-N heapsort  Memory: 36kB
               Worker 1:  Sort Method: top-N heapsort  Memory: 36kB
               ->  Parallel Seq Scan on rentals_2026_08 rentals  (cost=0.00..9061.17 rows=214302 width=62) (actual time=0.157..29.556 rows=171476.00 loops=3)
                     Filter: ((created_at >= '2026-08-01 00:00:00'::timestamp without time zone) AND (created_at < '2026-09-01 00:00:00'::timestamp without time zone))
                     Buffers: shared hit=3415 read=2431 written=182
 Planning:
   Buffers: shared hit=233
 Planning Time: 0.955 ms
 Execution Time: 54.391 ms
(19 rows)
```

**Шаг 6. Запрос 2 — GET /rentals/{id}**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM rentals WHERE id = 1234567;
```

```text
                                                                        QUERY PLAN                                                                        
----------------------------------------------------------------------------------------------------------------------------------------------------------
 Append  (cost=0.42..33.78 rows=4 width=62) (actual time=0.081..0.119 rows=1.00 loops=1)
   Buffers: shared hit=16
   ->  Index Scan using rentals_2026_06_pkey on rentals_2026_06 rentals_1  (cost=0.42..8.44 rows=1 width=62) (actual time=0.051..0.051 rows=0.00 loops=1)
         Index Cond: (id = 1234567)
         Index Searches: 1
         Buffers: shared hit=6
   ->  Index Scan using rentals_2026_07_pkey on rentals_2026_07 rentals_2  (cost=0.42..8.44 rows=1 width=62) (actual time=0.029..0.029 rows=1.00 loops=1)
         Index Cond: (id = 1234567)
         Index Searches: 1
         Buffers: shared hit=4
   ->  Index Scan using rentals_2026_08_pkey on rentals_2026_08 rentals_3  (cost=0.42..8.44 rows=1 width=62) (actual time=0.016..0.016 rows=0.00 loops=1)
         Index Cond: (id = 1234567)
         Index Searches: 1
         Buffers: shared hit=3
   ->  Index Scan using rentals_2026_09_pkey on rentals_2026_09 rentals_4  (cost=0.42..8.44 rows=1 width=62) (actual time=0.020..0.020 rows=0.00 loops=1)
         Index Cond: (id = 1234567)
         Index Searches: 1
         Buffers: shared hit=3
 Planning:
   Buffers: shared hit=414
 Planning Time: 1.245 ms
 Execution Time: 0.202 ms
(22 rows)
```

**Шаг 6. Запрос 2 с уточнением месяца**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM rentals
WHERE id = 1234567 AND created_at >= '2026-08-01' AND created_at < '2026-09-01';
```

```text
                                                                                 QUERY PLAN                                                                                  
-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------
 Index Scan using rentals_2026_08_pkey on rentals_2026_08 rentals  (cost=0.42..8.44 rows=1 width=62) (actual time=0.047..0.047 rows=0.00 loops=1)
   Index Cond: ((id = 1234567) AND (created_at >= '2026-08-01 00:00:00'::timestamp without time zone) AND (created_at < '2026-09-01 00:00:00'::timestamp without time zone))
   Index Searches: 1
   Buffers: shared hit=6
 Planning:
   Buffers: shared hit=229
 Planning Time: 1.042 ms
 Execution Time: 0.086 ms
(8 rows)
```

**Шаг 6. Запрос 3 — GET /rentals/statistics**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) AS rentals,
       round(avg(minute_fee), 2) AS avg_fee,
       round(sum(minute_fee), 2) AS total_fee
FROM rentals
WHERE created_at >= '2026-08-01' AND created_at < '2026-09-01';
```

```text
                                                                               QUERY PLAN                                                                               
------------------------------------------------------------------------------------------------------------------------------------------------------------------------
 Finalize Aggregate  (cost=11132.92..11132.93 rows=1 width=72) (actual time=75.198..79.011 rows=1.00 loops=1)
   Buffers: shared hit=3979 read=1867
   ->  Gather  (cost=11132.69..11132.90 rows=2 width=72) (actual time=74.916..78.993 rows=3.00 loops=1)
         Workers Planned: 2
         Workers Launched: 2
         Buffers: shared hit=3979 read=1867
         ->  Partial Aggregate  (cost=10132.69..10132.70 rows=1 width=72) (actual time=71.201..71.203 rows=1.00 loops=3)
               Buffers: shared hit=3979 read=1867
               ->  Parallel Seq Scan on rentals_2026_08 rentals  (cost=0.00..9061.17 rows=214302 width=6) (actual time=0.189..42.117 rows=171476.00 loops=3)
                     Filter: ((created_at >= '2026-08-01 00:00:00'::timestamp without time zone) AND (created_at < '2026-09-01 00:00:00'::timestamp without time zone))
                     Buffers: shared hit=3979 read=1867
 Planning:
   Buffers: shared hit=242
 Planning Time: 1.033 ms
 Execution Time: 79.091 ms
(15 rows)
```

**Шаг 6. Запрос 4 — история пользователя**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM rentals WHERE user_id = 4242
ORDER BY created_at DESC LIMIT 50;
```

```text
                                                                                    QUERY PLAN                                                                                     
-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
 Limit  (cost=1.69..65.96 rows=12 width=62) (actual time=0.051..0.199 rows=15.00 loops=1)
   Buffers: shared hit=30
   ->  Append  (cost=1.69..65.96 rows=12 width=62) (actual time=0.049..0.195 rows=15.00 loops=1)
         Buffers: shared hit=30
         ->  Index Scan using rentals_2026_09_user_id_created_at_idx on rentals_2026_09 rentals_4  (cost=0.42..16.47 rows=3 width=62) (actual time=0.048..0.083 rows=5.00 loops=1)
               Index Cond: (user_id = 4242)
               Index Searches: 1
               Buffers: shared hit=11
         ->  Index Scan using rentals_2026_08_user_id_created_at_idx on rentals_2026_08 rentals_3  (cost=0.42..16.47 rows=3 width=62) (actual time=0.020..0.030 rows=4.00 loops=1)
               Index Cond: (user_id = 4242)
               Index Searches: 1
               Buffers: shared hit=7
         ->  Index Scan using rentals_2026_07_user_id_created_at_idx on rentals_2026_07 rentals_2  (cost=0.42..16.47 rows=3 width=62) (actual time=0.029..0.031 rows=2.00 loops=1)
               Index Cond: (user_id = 4242)
               Index Searches: 1
               Buffers: shared hit=5
         ->  Index Scan using rentals_2026_06_user_id_created_at_idx on rentals_2026_06 rentals_1  (cost=0.42..16.47 rows=3 width=62) (actual time=0.021..0.047 rows=4.00 loops=1)
               Index Cond: (user_id = 4242)
               Index Searches: 1
               Buffers: shared hit=7
 Planning:
   Buffers: shared hit=420
 Planning Time: 1.781 ms
 Execution Time: 0.283 ms
(24 rows)
```

**Шаг 6. Запрос 4 с уточнением месяца**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM rentals
WHERE user_id = 4242 AND created_at >= '2026-09-01' AND created_at < '2026-10-01'
ORDER BY created_at DESC LIMIT 50;
```

```text
                                                                                     QUERY PLAN                                                                                      
-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
 Limit  (cost=0.42..16.49 rows=3 width=62) (actual time=0.072..0.127 rows=5.00 loops=1)
   Buffers: shared hit=11
   ->  Index Scan using rentals_2026_09_user_id_created_at_idx on rentals_2026_09 rentals  (cost=0.42..16.49 rows=3 width=62) (actual time=0.069..0.122 rows=5.00 loops=1)
         Index Cond: ((user_id = 4242) AND (created_at >= '2026-09-01 00:00:00'::timestamp without time zone) AND (created_at < '2026-10-01 00:00:00'::timestamp without time zone))
         Index Searches: 1
         Buffers: shared hit=11
 Planning:
   Buffers: shared hit=239
 Planning Time: 1.848 ms
 Execution Time: 0.211 ms
(10 rows)
```

**Непартиционированная копия для сравнения**

```sql
CREATE TABLE rentals_plain (LIKE rentals INCLUDING DEFAULTS);
INSERT INTO rentals_plain SELECT * FROM rentals;
CREATE INDEX idx_plain_created ON rentals_plain (created_at);
ANALYZE rentals_plain;
```

```text
(команда выполнена, вывода нет)
```

**Та же статистика без партиционирования**

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) AS rentals,
       round(avg(minute_fee), 2) AS avg_fee,
       round(sum(minute_fee), 2) AS total_fee
FROM rentals_plain
WHERE created_at >= '2026-08-01' AND created_at < '2026-09-01';
```

```text
                                                                                  QUERY PLAN                                                                                   
-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
 Finalize Aggregate  (cost=30943.05..30943.07 rows=1 width=72) (actual time=150.642..156.043 rows=1.00 loops=1)
   Buffers: shared hit=515730
   ->  Gather  (cost=30942.82..30943.03 rows=2 width=72) (actual time=150.380..156.026 rows=3.00 loops=1)
         Workers Planned: 2
         Workers Launched: 2
         Buffers: shared hit=515730
         ->  Partial Aggregate  (cost=29942.82..29942.83 rows=1 width=72) (actual time=145.393..145.394 rows=1.00 loops=3)
               Buffers: shared hit=515730
               ->  Parallel Index Scan using idx_plain_created on rentals_plain  (cost=0.43..28873.46 rows=213872 width=6) (actual time=0.042..118.943 rows=171476.00 loops=3)
                     Index Cond: ((created_at >= '2026-08-01 00:00:00'::timestamp without time zone) AND (created_at < '2026-09-01 00:00:00'::timestamp without time zone))
                     Index Searches: 1
                     Buffers: shared hit=515730
 Planning:
   Buffers: shared hit=96
 Planning Time: 0.423 ms
 Execution Time: 156.136 ms
(16 rows)
```

**Шаг 7. Строк до удаления старого месяца**

```sql
SELECT count(*) AS rows_before FROM rentals;
```

```text
 rows_before 
-------------
     2000000
(1 row)
```

**Шаг 7. DETACH + DROP партиции за июнь**

```sql
ALTER TABLE rentals DETACH PARTITION rentals_2026_06;
DROP TABLE rentals_2026_06;
SELECT count(*) AS rows_after FROM rentals;
```

```text
 rows_after 
------------
    1504816
(1 row)
```


---

## Часть 12, шаги 7-9. Автоматизация и alerting для `rentals`

```text
########## Шаг 8. Контроль: есть ли партиции rentals на текущий и 2 следующих месяца

$ python3 partition_health_check.py --table rentals --granularity month --horizon 2
2026-09-12 14:04:59  PartitionHealthCheck: table=rentals, today=2026-09-12, horizon=2 month(s)
    rentals_2026_09  ok
    rentals_2026_10  MISSING
    rentals_2026_11  MISSING
    Result: CRITICAL
    Уведомление (alert):
🚨 Partition alert
Table: rentals
Missing partitions:
rentals_2026_10
rentals_2026_11
Expected horizon: 2 months
Checked at:
2026-09-12 14:04:59
      console: напечатано в stdout
      webhook: HTTP 200 от http://127.0.0.1:8099/
(код возврата: 2)


########## Шаг 9. Alert ушёл. Повторная проверка — дубликат подавлен

$ python3 partition_health_check.py --table rentals --granularity month --horizon 2
2026-09-12 14:04:59  PartitionHealthCheck: table=rentals, today=2026-09-12, horizon=2 month(s)
    rentals_2026_09  ok
    rentals_2026_10  MISSING
    rentals_2026_11  MISSING
    Result: CRITICAL
    Уведомление не отправлено: состояние не изменилось — alert подавлен
(код возврата: 2)


########## Шаг 7. Ночная job создаёт будущие месячные партиции

$ python3 create_partitions_job.py --table rentals --granularity month --horizon 2
2026-09-12 14:04:59  Partition job started.
2026-09-12 14:04:59  Table: rentals, today: 2026-09-12, horizon: 2 month(s)
2026-09-12 14:04:59  Existing partitions: 3
2026-09-12 14:04:59  Required partitions: 3
2026-09-12 14:04:59  Missing partitions: 2
2026-09-12 14:04:59  Creating: rentals_2026_10
2026-09-12 14:04:59  Partition rentals_2026_10 created successfully.
2026-09-12 14:04:59  Creating: rentals_2026_11
2026-09-12 14:05:00  Partition rentals_2026_11 created successfully.
2026-09-12 14:05:00  Partition job finished.
(код возврата: 0)


########## Шаг 7б. Повторный запуск — идемпотентность

$ python3 create_partitions_job.py --table rentals --granularity month --horizon 2
2026-09-12 14:05:00  Partition job started.
2026-09-12 14:05:00  Table: rentals, today: 2026-09-12, horizon: 2 month(s)
2026-09-12 14:05:00  Existing partitions: 5
2026-09-12 14:05:00  Required partitions: 3
2026-09-12 14:05:00  Missing partitions: 0
2026-09-12 14:05:00  Nothing to create — all required partitions already exist.
2026-09-12 14:05:00  Partition job finished.
(код возврата: 0)


########## Контрольная проверка — OK и recovery-уведомление

$ python3 partition_health_check.py --table rentals --granularity month --horizon 2
2026-09-12 14:05:00  PartitionHealthCheck: table=rentals, today=2026-09-12, horizon=2 month(s)
    rentals_2026_09  ok
    rentals_2026_10  ok
    rentals_2026_11  ok
    Result: OK
    Уведомление (recovery):
🟢 Partition check OK
Table: rentals
All required partitions exist.
Checked at:
2026-09-12 14:05:00
      console: напечатано в stdout
      webhook: HTTP 200 от http://127.0.0.1:8099/
(код возврата: 0)


########## Проверка перехода через год: горизонт 4 месяца от ноября

$ python3 create_partitions_job.py --table rentals --granularity month --horizon 4 --today 2026-11-15
2026-09-12 14:05:00  Partition job started.
2026-09-12 14:05:00  Table: rentals, today: 2026-11-15, horizon: 4 month(s)
2026-09-12 14:05:00  Existing partitions: 5
2026-09-12 14:05:00  Required partitions: 5
2026-09-12 14:05:00  Missing partitions: 4
2026-09-12 14:05:00  Creating: rentals_2026_12
2026-09-12 14:05:01  Partition rentals_2026_12 created successfully.
2026-09-12 14:05:01  Creating: rentals_2027_01
2026-09-12 14:05:01  Partition rentals_2027_01 created successfully.
2026-09-12 14:05:01  Creating: rentals_2027_02
2026-09-12 14:05:01  Partition rentals_2027_02 created successfully.
2026-09-12 14:05:01  Creating: rentals_2027_03
2026-09-12 14:05:01  Partition rentals_2027_03 created successfully.
2026-09-12 14:05:01  Partition job finished.
(код возврата: 0)

$ python3 partition_health_check.py --table rentals --granularity month --horizon 4 --today 2026-11-15
2026-09-12 14:05:01  PartitionHealthCheck: table=rentals, today=2026-11-15, horizon=4 month(s)
    rentals_2026_11  ok
    rentals_2026_12  ok
    rentals_2027_01  ok
    rentals_2027_02  ok
    rentals_2027_03  ok
    Result: OK
    Уведомление не отправлено: всё в порядке, уведомлять не о чем
(код возврата: 0)


########## Итоговый список партиций rentals

$ printf 'SET search_path TO carrent_lab3;\nSELECT c.relname AS partition, pg_get_expr(c.relpartbound, c.oid) AS bounds FROM pg_class c JOIN pg_inherits i ON i.inhrelid = c.oid JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = %s ORDER BY 1;\n' "'rentals'" | docker exec -i -e PGPASSWORD=12341234 database_postgres psql -U postgres -d postgres -X -q
    partition    |                               bounds                               
-----------------+--------------------------------------------------------------------
 rentals_2026_07 | FOR VALUES FROM ('2026-07-01 00:00:00') TO ('2026-08-01 00:00:00')
 rentals_2026_08 | FOR VALUES FROM ('2026-08-01 00:00:00') TO ('2026-09-01 00:00:00')
 rentals_2026_09 | FOR VALUES FROM ('2026-09-01 00:00:00') TO ('2026-10-01 00:00:00')
 rentals_2026_10 | FOR VALUES FROM ('2026-10-01 00:00:00') TO ('2026-11-01 00:00:00')
 rentals_2026_11 | FOR VALUES FROM ('2026-11-01 00:00:00') TO ('2026-12-01 00:00:00')
 rentals_2026_12 | FOR VALUES FROM ('2026-12-01 00:00:00') TO ('2027-01-01 00:00:00')
 rentals_2027_01 | FOR VALUES FROM ('2027-01-01 00:00:00') TO ('2027-02-01 00:00:00')
 rentals_2027_02 | FOR VALUES FROM ('2027-02-01 00:00:00') TO ('2027-03-01 00:00:00')
 rentals_2027_03 | FOR VALUES FROM ('2027-03-01 00:00:00') TO ('2027-04-01 00:00:00')
(9 rows)

(код возврата: 0)


########## Доставленные уведомления

$ cat received_alerts.log
=== доставлено 2026-09-12 14:04:59 ===
🚨 Partition alert
Table: rentals
Missing partitions:
rentals_2026_10
rentals_2026_11
Expected horizon: 2 months
Checked at:
2026-09-12 14:04:59

=== доставлено 2026-09-12 14:05:00 ===
🟢 Partition check OK
Table: rentals
All required partitions exist.
Checked at:
2026-09-12 14:05:00

(код возврата: 0)
```

---

## Alert-система в Docker

Тот же код, упакованный в контейнер: планировщик вместо cron, уведомления
уходят в Telegram. Ниже — реальный лог контейнера, включая имитацию сбоя.

```text
### Сборка и запуск
$ docker compose --env-file ./.env up -d partition_alerts

### Полный лог контейнера: старт, сбой, alert, подавление, восстановление
2026-09-12 14:23:57  [scheduler] старт. JOB_INTERVAL=600s, CHECK_INTERVAL=30s
2026-09-12 14:23:57  [scheduler] наблюдаем krasova_lab3.events (day, горизонт 3)
2026-09-12 14:23:57  [scheduler] наблюдаем carrent_lab3.rentals (month, горизонт 2)
2026-09-12 14:23:57  [scheduler] канал уведомлений: telegram
2026-09-12 14:23:57  [scheduler] CreatePartitionsJob -> krasova_lab3.events
    2026-09-12 14:23:57  Partition job started.
    2026-09-12 14:23:57  Table: events, today: 2026-09-12, horizon: 3 day(s)
    2026-09-12 14:23:57  Existing partitions: 7
    2026-09-12 14:23:57  Required partitions: 4
    2026-09-12 14:23:57  Missing partitions: 0
    2026-09-12 14:23:57  Nothing to create — all required partitions already exist.
    2026-09-12 14:23:57  Partition job finished.
2026-09-12 14:23:57  [scheduler] CreatePartitionsJob -> carrent_lab3.rentals
    2026-09-12 14:23:58  Partition job started.
    2026-09-12 14:23:58  Table: rentals, today: 2026-09-12, horizon: 2 month(s)
    2026-09-12 14:23:58  Existing partitions: 9
    2026-09-12 14:23:58  Required partitions: 3
    2026-09-12 14:23:58  Missing partitions: 0
    2026-09-12 14:23:58  Nothing to create — all required partitions already exist.
    2026-09-12 14:23:58  Partition job finished.
2026-09-12 14:23:58  [scheduler] PartitionHealthCheck -> krasova_lab3.events
    2026-09-12 14:23:58  PartitionHealthCheck: table=events, today=2026-09-12, horizon=3 day(s)
        events_2026_09_12  ok
        events_2026_09_13  ok
        events_2026_09_14  ok
        events_2026_09_15  ok
        Result: OK
        Уведомление не отправлено: всё в порядке, уведомлять не о чем
2026-09-12 14:23:58  [scheduler] PartitionHealthCheck -> carrent_lab3.rentals
    2026-09-12 14:23:59  PartitionHealthCheck: table=rentals, today=2026-09-12, horizon=2 month(s)
        rentals_2026_09  ok
        rentals_2026_10  ok
        rentals_2026_11  ok
        Result: OK
        Уведомление не отправлено: всё в порядке, уведомлять не о чем
2026-09-12 14:24:29  [scheduler] PartitionHealthCheck -> krasova_lab3.events
    2026-09-12 14:24:29  PartitionHealthCheck: table=events, today=2026-09-12, horizon=3 day(s)
        events_2026_09_12  ok
        events_2026_09_13  ok
        events_2026_09_14  ok
        events_2026_09_15  MISSING
        Result: CRITICAL
        Уведомление (alert):
          telegram: telegram HTTP 200
2026-09-12 14:24:29  [scheduler] состояние events: CRITICAL
2026-09-12 14:24:29  [scheduler] PartitionHealthCheck -> carrent_lab3.rentals
    2026-09-12 14:24:30  PartitionHealthCheck: table=rentals, today=2026-09-12, horizon=2 month(s)
        rentals_2026_09  ok
        rentals_2026_10  ok
        rentals_2026_11  ok
        Result: OK
        Уведомление не отправлено: всё в порядке, уведомлять не о чем
2026-09-12 14:25:00  [scheduler] PartitionHealthCheck -> krasova_lab3.events
    2026-09-12 14:25:00  PartitionHealthCheck: table=events, today=2026-09-12, horizon=3 day(s)
        events_2026_09_12  ok
        events_2026_09_13  ok
        events_2026_09_14  ok
        events_2026_09_15  MISSING
        Result: CRITICAL
        Уведомление не отправлено: состояние не изменилось — alert подавлен
2026-09-12 14:25:00  [scheduler] состояние events: CRITICAL
2026-09-12 14:25:00  [scheduler] PartitionHealthCheck -> carrent_lab3.rentals
    2026-09-12 14:25:01  PartitionHealthCheck: table=rentals, today=2026-09-12, horizon=2 month(s)
        rentals_2026_09  ok
        rentals_2026_10  ok
        rentals_2026_11  ok
        Result: OK
        Уведомление не отправлено: всё в порядке, уведомлять не о чем
2026-09-12 14:25:31  [scheduler] PartitionHealthCheck -> krasova_lab3.events
    2026-09-12 14:25:31  PartitionHealthCheck: table=events, today=2026-09-12, horizon=3 day(s)
        events_2026_09_12  ok
        events_2026_09_13  ok
        events_2026_09_14  ok
        events_2026_09_15  MISSING
        Result: CRITICAL
        Уведомление не отправлено: состояние не изменилось — alert подавлен
2026-09-12 14:25:31  [scheduler] состояние events: CRITICAL
2026-09-12 14:25:31  [scheduler] PartitionHealthCheck -> carrent_lab3.rentals
    2026-09-12 14:25:32  PartitionHealthCheck: table=rentals, today=2026-09-12, horizon=2 month(s)
        rentals_2026_09  ok
        rentals_2026_10  ok
        rentals_2026_11  ok
        Result: OK
        Уведомление не отправлено: всё в порядке, уведомлять не о чем
2026-09-12 14:26:02  [scheduler] PartitionHealthCheck -> krasova_lab3.events
    2026-09-12 14:26:02  PartitionHealthCheck: table=events, today=2026-09-12, horizon=3 day(s)
        events_2026_09_12  ok
        events_2026_09_13  ok
        events_2026_09_14  ok
        events_2026_09_15  ok
        Result: OK
        Уведомление (recovery):
          telegram: telegram HTTP 200
2026-09-12 14:26:03  [scheduler] PartitionHealthCheck -> carrent_lab3.rentals
    2026-09-12 14:26:03  PartitionHealthCheck: table=rentals, today=2026-09-12, horizon=2 month(s)
        rentals_2026_09  ok
        rentals_2026_10  ok
        rentals_2026_11  ok
        Result: OK
        Уведомление не отправлено: всё в порядке, уведомлять не о чем
2026-09-12 14:26:33  [scheduler] PartitionHealthCheck -> krasova_lab3.events
    2026-09-12 14:26:34  PartitionHealthCheck: table=events, today=2026-09-12, horizon=3 day(s)
        events_2026_09_12  ok
        events_2026_09_13  ok
        events_2026_09_14  ok
        events_2026_09_15  ok
        Result: OK
        Уведомление не отправлено: всё в порядке, уведомлять не о чем
2026-09-12 14:26:34  [scheduler] PartitionHealthCheck -> carrent_lab3.rentals
    2026-09-12 14:26:34  PartitionHealthCheck: table=rentals, today=2026-09-12, horizon=2 month(s)
        rentals_2026_09  ok
        rentals_2026_10  ok
        rentals_2026_11  ok
        Result: OK
        Уведомление не отправлено: всё в порядке, уведомлять не о чем

### Состояние в томе
total 8
-rw-r--r-- 1 partitions partitions 115 Sep 12 14:26 alert_state_carrent_lab3_rentals.json
-rw-r--r-- 1 partitions partitions 129 Sep 12 14:26 alert_state_krasova_lab3_events.json
{
  "events:day": {
    "status": "OK",
    "last_alert_at": "2026-09-12T14:26:02",
    "checked_at": "2026-09-12T14:26:34"
  }
}
### Статус healthcheck
health: healthy
```

---

## pg_cron: создание партиций внутри СУБД

```sql
-- ==========================================================================
-- Создание партиций внутри СУБД: pg_cron + PL/pgSQL.
--
-- Зачем в базе, а не во внешнем планировщике: расписание переживает
-- перезапуск приложения, лежит в том же бэкапе, что и данные, и создание
-- партиции больше не зависит от того, жив ли контейнер с job.
--
-- Уведомления остаются снаружи: у PostgreSQL нет HTTP-клиента, поэтому
-- задание пишет инциденты в таблицу-outbox, а relay забирает их и шлёт
-- в Telegram.
-- ==========================================================================
CREATE SCHEMA IF NOT EXISTS partition_ops;

-- Журнал: что планировщик делал и когда.
CREATE TABLE IF NOT EXISTS partition_ops.run_log (
    id          BIGSERIAL PRIMARY KEY,
    ran_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    target      TEXT        NOT NULL,
    created     INTEGER     NOT NULL DEFAULT 0,
    missing     INTEGER     NOT NULL DEFAULT 0,
    details     TEXT
);

-- Очередь исходящих уведомлений: база не умеет в HTTP, поэтому складывает
-- сообщения сюда, а внешний relay их отправляет и помечает отправленными.
CREATE TABLE IF NOT EXISTS partition_ops.alert_outbox (
    id          BIGSERIAL PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    severity    TEXT        NOT NULL CHECK (severity IN ('CRITICAL', 'OK')),
    target      TEXT        NOT NULL,
    message     TEXT        NOT NULL,
    sent_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_alert_outbox_unsent
    ON partition_ops.alert_outbox (id) WHERE sent_at IS NULL;

-- Начало периода, которому принадлежит дата.
CREATE OR REPLACE FUNCTION partition_ops.period_start(p_day DATE, p_granularity TEXT)
RETURNS DATE LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE p_granularity
             WHEN 'day'   THEN p_day
             WHEN 'month' THEN date_trunc('month', p_day)::date
           END;
$$;

-- Сдвиг на n периодов вперёд.
CREATE OR REPLACE FUNCTION partition_ops.period_shift(p_start DATE, p_granularity TEXT, n INT)
RETURNS DATE LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE p_granularity
             WHEN 'day'   THEN p_start + (n || ' days')::interval
             WHEN 'month' THEN p_start + (n || ' months')::interval
           END::date;
$$;

-- Имя партиции для периода: events_2026_09_12 или rentals_2026_09.
CREATE OR REPLACE FUNCTION partition_ops.partition_name(p_table TEXT, p_start DATE, p_granularity TEXT)
RETURNS TEXT LANGUAGE sql IMMUTABLE AS $$
    SELECT p_table || '_' || to_char(p_start,
           CASE p_granularity WHEN 'day' THEN 'YYYY_MM_DD' ELSE 'YYYY_MM' END);
$$;

-- Каких партиций не хватает на горизонт.
CREATE OR REPLACE FUNCTION partition_ops.missing_partitions(
    p_schema TEXT, p_table TEXT, p_granularity TEXT, p_horizon INT,
    p_today DATE DEFAULT CURRENT_DATE)
RETURNS TABLE (period_start DATE, partition_name TEXT)
LANGUAGE sql STABLE AS $$
    SELECT s.period_start,
           partition_ops.partition_name(p_table, s.period_start, p_granularity)
    FROM (
        SELECT partition_ops.period_shift(
                   partition_ops.period_start(p_today, p_granularity),
                   p_granularity, i) AS period_start
        FROM generate_series(0, p_horizon) AS i
    ) s
    WHERE NOT EXISTS (
        SELECT 1 FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = p_schema
          AND c.relname = partition_ops.partition_name(p_table, s.period_start, p_granularity)
    )
    ORDER BY s.period_start;
$$;

-- Главная процедура: досоздать недостающие партиции и записать результат.
CREATE OR REPLACE FUNCTION partition_ops.ensure_partitions(
    p_schema TEXT, p_table TEXT, p_granularity TEXT, p_horizon INT)
RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE
    r            RECORD;
    v_created    INT := 0;
    v_names      TEXT[] := '{}';
    v_target     TEXT := p_schema || '.' || p_table;
    v_next       DATE;
BEGIN
    FOR r IN SELECT * FROM partition_ops.missing_partitions(
                            p_schema, p_table, p_granularity, p_horizon)
    LOOP
        v_next := partition_ops.period_shift(r.period_start, p_granularity, 1);
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS %I.%I PARTITION OF %I.%I
             FOR VALUES FROM (%L) TO (%L)',
            p_schema, r.partition_name, p_schema, p_table, r.period_start, v_next);
        v_created := v_created + 1;
        v_names := v_names || r.partition_name;
    END LOOP;

    IF v_created > 0 THEN
        INSERT INTO partition_ops.run_log (target, created, missing, details)
        VALUES (v_target, v_created, 0, array_to_string(v_names, ', '));

        -- Создание партиции постфактум означает, что горизонт уже проседал,
        -- поэтому о самовосстановлении сообщаем наружу.
        INSERT INTO partition_ops.alert_outbox (severity, target, message)
        VALUES ('OK', v_target,
                format('🛠 pg_cron восстановил партиции' || chr(10) ||
                       'Table: %s' || chr(10) ||
                       'Created:' || chr(10) || '%s' || chr(10) ||
                       'Checked at:' || chr(10) || '%s',
                       v_target, array_to_string(v_names, chr(10)),
                       to_char(now(), 'YYYY-MM-DD HH24:MI:SS')));
    ELSE
        INSERT INTO partition_ops.run_log (target, created, missing, details)
        VALUES (v_target, 0, 0, 'всё на месте');
    END IF;

    RETURN v_created;
END;
$$;
```

```text
### Расширение и расписание
                          List of installed extensions
  Name   | Version | Default version |   Schema   |         Description          
---------+---------+-----------------+------------+------------------------------
 pg_cron | 1.6     | 1.6             | pg_catalog | Job scheduler for PostgreSQL
(1 row)

 jobid |      jobname       | schedule  | active | database 
-------+--------------------+-----------+--------+----------
     1 | partitions-events  | * * * * * | t      | postgres
     2 | partitions-rentals | * * * * * | t      | postgres
(2 rows)


### Самовосстановление: партиция удалена, pg_cron вернул её за минуту
       ran_at        |        target        | created |      details      
---------------------+----------------------+---------+-------------------
 2026-09-12 11:56:00 | krasova_lab3.events  |       1 | events_2026_09_15
 2026-09-12 11:53:00 | carrent_lab3.rentals |       1 | rentals_2026_11
(2 rows)

### Журнал самого pg_cron
 jobid |  status   | return_message |     start_time      
-------+-----------+----------------+---------------------
     1 | succeeded | 1 row          | 2026-09-12 11:56:00
     2 | succeeded | 1 row          | 2026-09-12 11:56:00
     1 | succeeded | 1 row          | 2026-09-12 11:55:00
     2 | succeeded | 1 row          | 2026-09-12 11:55:00
     1 | succeeded | 1 row          | 2026-09-12 11:54:00
     2 | succeeded | 1 row          | 2026-09-12 11:54:00
(6 rows)

### Очередь уведомлений после доставки
 id | severity |        target        |       sent_at       
----+----------+----------------------+---------------------
  1 | OK       | carrent_lab3.rentals | 2026-09-12 11:53:56
  2 | OK       | krasova_lab3.events  | 2026-09-12 11:56:13
(2 rows)


### Данные и расписание переживают пересоздание контейнера
          t           |  count   
----------------------+----------
 carrent_lab2.rentals |  5000000
 carrent_lab3.rentals |  1504816
 krasova_lab2.events  | 10200000
 krasova_lab3.events  |  3000032
(4 rows)
```
