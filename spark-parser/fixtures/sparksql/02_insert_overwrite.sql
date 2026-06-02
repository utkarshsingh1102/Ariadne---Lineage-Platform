-- Fixture SQL 02 — INSERT OVERWRITE with explicit column list
-- Plan §9.2.

INSERT OVERWRITE TABLE prod.mart.orders_daily
    (order_id, customer_id, amount, order_date)
SELECT
    o.order_id,
    o.customer_id,
    o.amount,
    DATE(o.created_at) AS order_date
FROM prod.raw.orders o
WHERE DATE(o.created_at) = CURRENT_DATE();
