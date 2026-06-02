-- Fixture SQL 01 — Simple CREATE TABLE AS SELECT with column-level mapping
-- Plan §2.2.

CREATE TABLE prod.mart.orders_enriched AS
SELECT
    o.order_id,
    o.customer_id,
    o.amount,
    CAST(o.region AS STRING) AS region_upper,
    CASE WHEN o.amount > 1000 THEN TRUE ELSE FALSE END AS is_high_value
FROM prod.raw.orders o
LEFT JOIN prod.dim.customers c
    ON o.customer_id = c.id;
