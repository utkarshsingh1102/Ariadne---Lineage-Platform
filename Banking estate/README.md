# Banking Data Estate — Ariadne Test Fixture

A heavily interconnected, moderately complex banking data estate for end-to-end
lineage testing. 18 files across four tools, all stitched through shared physical
tables and shared script paths.

## Files (18)

**Spark (6)**
- `ingest_raw.py` — lands raw sources → `prod.raw.{accounts,transactions,customers}`
- `build_dimensions.py` — → `prod.dim.{customers,accounts,branch}`
- `transform_transactions.py` — fact + MERGE → `prod.fact.{transactions,balances}`
- `fraud_scoring.py` — Databricks notebook → `prod.mart.fraud_scores`
- `risk_aggregation.py` — → `prod.mart.{daily_balances,risk_exposure}`
- `customer_360.sql` — → `prod.mart.customer_360`

**QlikView (3)**
- `risk_dashboard.qvs` — base doc, reads `risk_exposure`, `daily_balances`, `dim.customers`
- `fraud_monitoring.qvs` — `BINARY` inherits risk_dashboard, reads `fraud_scores`
- `branch_ops.qvs` — reads `dim.branch`, `daily_balances`

**Tableau (3)**
- `exec_risk.twb` — reads `mart.risk_exposure`
- `fraud_analytics.twb` — reads `mart.fraud_scores`, blends `mart.customer_360`
- `branch_ops.twb` — custom SQL over `mart.daily_balances` + `dim.branch`

**TWS (3)**
- `tws_core_banking.txt` — master ETL spine (triggers all 6 Spark jobs)
- `tws_bi_refresh.txt` — FOLLOWS the master; triggers QlikView reloads + Tableau refreshes
- `tws_weekly_regulatory.xml` — XML form; re-runs `customer_360.sql` for regulators

## The lineage spine (what connects to what)

```
TWS DAILY_CORE_BANKING_LOAD
  INGEST_RAW ──► ingest_raw.py ──► prod.raw.*
  BUILD_DIMENSIONS ──► build_dimensions.py ──► prod.dim.*
  TRANSFORM_TXNS ──► transform_transactions.py ──► prod.fact.*
  FRAUD_SCORING ──► fraud_scoring.py ──► prod.mart.fraud_scores
  RISK_AGG ──► risk_aggregation.py ──► prod.mart.{daily_balances,risk_exposure}
  CUSTOMER_360 ──► customer_360.sql ──► prod.mart.customer_360
        │
        ▼  (schedule FOLLOWS)
TWS DAILY_BI_REFRESH
  RELOAD_*_QV ──► *.qvs ──► (read prod.mart.* / prod.dim.*)
  REFRESH_*_TAB ──► *.twb ──► (read prod.mart.* / prod.dim.*)
```

## Shared merge points

**Physical tables** (same SHA-256 ID across parsers):
`prod.raw.*`, `prod.dim.{customers,accounts,branch}`, `prod.fact.{transactions,balances}`,
`prod.mart.{fraud_scores,daily_balances,risk_exposure,customer_360}`

**Shared columns:** `customer_id`, `account_id`, `txn_id`, `amount`, `txn_date`,
`branch_id`, `region`, `fraud_score`, `fraud_flag`, `risk_weighted_exposure`

**Shared script paths** (TWS `CALLS_SCRIPT` merges onto the real files):
`ingest_raw.py`, `build_dimensions.py`, `transform_transactions.py`, `fraud_scoring.py`,
`risk_aggregation.py`, `customer_360.sql`, `risk_dashboard.qvs`, `fraud_monitoring.qvs`,
`branch_ops.qvs` — and `customer_360.sql` is shared by BOTH the daily and the regulatory schedule.

## Deliberate edge cases

- Tableau: federated joins, custom SQL, LOD/CASE/table calcs, data blending, hierarchy, scrubbable passwords
- QlikView: `BINARY` inheritance, RESIDENT/CONCATENATE/MAPPING/INLINE, `$(Include)`, dynamic SQL (partial), scrubbable passwords
- TWS: composer-text + XML, cross-file schedule FOLLOWS, `WEEKDAYS_EXCEPT_HOLIDAYS` (partial cron), one script shared across two schedules
- Spark: parquet/CSV/JDBC reads, embedded SQL, MERGE INTO, window functions, UDF, notebook cells, column-level derivations

## The cross-tool query this fixture is built to satisfy

"Find every Tableau dashboard, trace down to the physical mart table, find the Spark job
that writes it, and find the TWS schedule that triggers that job — including when it runs."

Example: `Executive Risk Dashboard` → `prod.mart.risk_exposure` → `risk_aggregation.py`
→ TWS job `RISK_AGG` → schedule `DAILY_CORE_BANKING_LOAD` (runs daily 02:00).
