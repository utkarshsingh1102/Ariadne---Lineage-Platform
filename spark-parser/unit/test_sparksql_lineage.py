"""
Spark SQL lineage (plan §6 step 8–9 + §9.2).
sqlglot with dialect='spark' — CTAS, INSERT, MERGE, CTEs, partitions, windows.
Plan §9.5: 100% coverage required.
"""
import pytest


def test_simple_ctas(sparksql_fixture, read_text):
    from spark_parser.sparksql.lineage import extract_lineage
    sql = read_text(sparksql_fixture("01_simple_ctas.sql"))
    lin = extract_lineage(sql, dialect="spark")

    targets = {t.lower() for t in lin.target_tables}
    sources = {s.lower() for s in lin.source_tables}
    assert "prod.mart.orders_enriched" in targets
    assert "prod.raw.orders" in sources
    assert "prod.dim.customers" in sources


def test_ctas_column_mapping(sparksql_fixture, read_text):
    """Source-to-target column mapping (plan §6 step 8)."""
    from spark_parser.sparksql.lineage import extract_lineage
    sql = read_text(sparksql_fixture("01_simple_ctas.sql"))
    lin = extract_lineage(sql, dialect="spark")

    by_target = {d.target_column: set(d.source_columns) for d in lin.derivations}
    # region_upper derives from region
    assert "region" in by_target.get("region_upper", set())
    # is_high_value derives from amount
    assert "amount" in by_target.get("is_high_value", set())


def test_insert_overwrite_with_column_list(sparksql_fixture, read_text):
    from spark_parser.sparksql.lineage import extract_lineage
    sql = read_text(sparksql_fixture("02_insert_overwrite.sql"))
    lin = extract_lineage(sql, dialect="spark")
    assert "prod.mart.orders_daily" in {t.lower() for t in lin.target_tables}
    assert "prod.raw.orders" in {s.lower() for s in lin.source_tables}


def test_merge_into(sparksql_fixture, read_text):
    from spark_parser.sparksql.lineage import extract_lineage
    sql = read_text(sparksql_fixture("03_merge_into.sql"))
    lin = extract_lineage(sql, dialect="spark")
    assert "prod.mart.customer_summary" in {t.lower() for t in lin.target_tables}
    assert "prod.raw.orders" in {s.lower() for s in lin.source_tables}
    # Both matched UPDATE columns must be captured as derivations
    target_cols = {d.target_column for d in lin.derivations}
    assert "total_amount" in target_cols
    assert "order_count" in target_cols


def test_cte_chain_resolves_through_aliases(sparksql_fixture, read_text):
    """CTE aliases (high_value, ranked, top_per_region) must NOT show up as
    physical tables. Only real tables in source/target."""
    from spark_parser.sparksql.lineage import extract_lineage
    sql = read_text(sparksql_fixture("04_cte_chain.sql"))
    lin = extract_lineage(sql, dialect="spark")

    sources = {s.lower() for s in lin.source_tables}
    assert "prod.raw.orders" in sources
    assert "prod.archive.top_customers_legacy" in sources
    # CTE names absent
    for cte in ("high_value", "ranked", "top_per_region"):
        assert not any(cte in s for s in sources)


def test_window_function_captured(sparksql_fixture, read_text):
    """ROW_NUMBER() OVER (...) must parse cleanly."""
    from spark_parser.sparksql.lineage import extract_lineage
    sql = read_text(sparksql_fixture("04_cte_chain.sql"))
    lin = extract_lineage(sql, dialect="spark")
    # Just assert the parse succeeded and we got a target
    assert lin.target_tables  # CTAS top_customers


def test_partition_write_target(sparksql_fixture, read_text):
    """INSERT OVERWRITE ... PARTITION (...) — target captured, partition column recorded."""
    from spark_parser.sparksql.lineage import extract_lineage
    sql = read_text(sparksql_fixture("05_partition_write.sql"))
    lin = extract_lineage(sql, dialect="spark")
    assert "prod.mart.orders_by_day" in {t.lower() for t in lin.target_tables}
    # partition column appears in the derivations
    target_cols = {d.target_column for d in lin.derivations}
    assert "order_date" in target_cols


def test_malformed_sql_returns_empty_with_warning():
    """Plan §13: sqlglot parse failure → return empty lineage + warning, don't raise."""
    from spark_parser.sparksql.lineage import extract_lineage
    lin = extract_lineage("SELECT FROM WHERE INTO", dialect="spark")
    assert lin.target_tables == [] or lin.target_tables is None
    assert lin.warnings  # at least one warning recorded
