-- Fixture SQL 05 — Partition-aware INSERT OVERWRITE
-- Plan §9.2 + §3 (sqlglot dialect='spark' must understand PARTITION clause).

INSERT OVERWRITE TABLE prod.mart.orders_by_day
PARTITION (order_date)
SELECT
    o.order_id,
    o.customer_id,
    o.amount,
    o.region,
    DATE(o.created_at) AS order_date
FROM prod.raw.orders o
WHERE o.created_at >= CURRENT_DATE() - INTERVAL 7 DAYS;
