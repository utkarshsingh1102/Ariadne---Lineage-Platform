-- Fixture SQL 03 — MERGE INTO with matched/not-matched actions
-- Plan §9.2.

MERGE INTO prod.mart.customer_summary t
USING (
    SELECT
        customer_id,
        SUM(amount) AS total_amount,
        COUNT(order_id) AS order_count
    FROM prod.raw.orders
    GROUP BY customer_id
) s
ON t.customer_id = s.customer_id
WHEN MATCHED THEN
    UPDATE SET
        t.total_amount = s.total_amount,
        t.order_count = s.order_count,
        t.updated_at = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN
    INSERT (customer_id, total_amount, order_count, created_at)
    VALUES (s.customer_id, s.total_amount, s.order_count, CURRENT_TIMESTAMP());
