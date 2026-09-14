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
