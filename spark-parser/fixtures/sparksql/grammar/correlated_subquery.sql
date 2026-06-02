-- Correlated subquery: inner SELECT references outer alias t1.
CREATE TABLE prod.mart.orders_with_max AS
SELECT
    t1.order_id,
    t1.customer_id,
    (SELECT MAX(t2.amount) FROM prod.raw.orders t2 WHERE t2.customer_id = t1.customer_id) AS max_amount
FROM prod.raw.orders t1;
