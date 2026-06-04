-- customer_360.sql — Builds the unified customer 360 mart.
-- Triggered by TWS job CUSTOMER_360 (FOLLOWS RISK_AGG).
-- Reads:  prod.dim.customers, prod.fact.balances, prod.mart.fraud_scores, prod.mart.risk_exposure
-- Writes: prod.mart.customer_360

CREATE OR REPLACE TABLE prod.mart.customer_360 AS
SELECT
    c.customer_id,
    c.customer_name,
    c.segment,
    c.region,
    b.net_balance_change,
    b.txn_count,
    b.last_txn_date,
    f.max_fraud_score,
    f.flagged_txns,
    r.risk_weighted_exposure
FROM prod.dim.customers c
LEFT JOIN prod.fact.balances b
    ON c.customer_id = b.customer_id
LEFT JOIN (
    SELECT customer_id,
           MAX(fraud_score) AS max_fraud_score,
           SUM(CASE WHEN fraud_flag THEN 1 ELSE 0 END) AS flagged_txns
    FROM prod.mart.fraud_scores
    GROUP BY customer_id
) f ON c.customer_id = f.customer_id
LEFT JOIN prod.mart.risk_exposure r
    ON c.customer_id = r.customer_id;
