-- Fixture SQL 04 — CTE chain + UNION ALL + window function
-- Plan §9.2 (CTEs, UNION ALL, OVER).

CREATE TABLE prod.mart.top_customers AS
WITH high_value AS (
    SELECT customer_id, amount, region
    FROM prod.raw.orders
    WHERE amount > 1000
),
ranked AS (
    SELECT
        customer_id,
        region,
        amount,
        ROW_NUMBER() OVER (PARTITION BY region ORDER BY amount DESC) AS rn
    FROM high_value
),
top_per_region AS (
    SELECT customer_id, region, amount
    FROM ranked
    WHERE rn <= 10
)
SELECT * FROM top_per_region
UNION ALL
SELECT customer_id, region, amount
FROM prod.archive.top_customers_legacy;
