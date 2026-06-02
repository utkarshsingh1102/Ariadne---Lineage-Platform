-- Scalar subquery: a self-contained subquery in an expression position.
CREATE TABLE prod.mart.orders_with_global AS
SELECT
    order_id,
    amount,
    (SELECT MAX(amount) FROM prod.raw.orders) AS global_max
FROM prod.raw.orders;
