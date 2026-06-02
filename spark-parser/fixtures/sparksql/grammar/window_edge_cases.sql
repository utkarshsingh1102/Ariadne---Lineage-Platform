-- Window functions: ROW_NUMBER (anonymous frame), RANK (named WINDOW),
-- and a RANGE-BETWEEN aggregate window.
CREATE TABLE prod.mart.orders_ranked AS
SELECT
    order_id,
    customer_id,
    amount,
    ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY amount DESC) AS rn,
    RANK() OVER w AS rk,
    SUM(amount) OVER (
        PARTITION BY customer_id
        ORDER BY order_date
        RANGE BETWEEN INTERVAL '7' DAY PRECEDING AND CURRENT ROW
    ) AS amount_rolling_7d
FROM prod.raw.orders
WINDOW w AS (PARTITION BY customer_id ORDER BY amount DESC);
