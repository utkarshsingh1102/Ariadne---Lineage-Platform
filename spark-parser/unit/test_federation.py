"""Unit tests for v0.2 §10 — federation + external catalog sync."""
from __future__ import annotations

from spark_parser.federation.cross_parser import canonical_table_id, shared_table_ids
from spark_parser.federation.openlineage_emitter import (
    emit_project_events,
    emit_script_event,
)
from spark_parser.federation.unity_catalog import UnityCatalogClient
from spark_parser.models.domain import (
    DataFrameIR,
    DerivationIR,
    ProjectIR,
    SparkScriptIR,
    TableIR,
)


def _make_script() -> SparkScriptIR:
    ir = SparkScriptIR(
        id="deadbeef", name="orders_etl", file_path="/jobs/orders_etl.py",
        script_type="pyspark",
    )
    df = DataFrameIR(var_name="orders", id="aa01", creation_order=0)
    df.reads_from.append(TableIR(
        fully_qualified_name="prod.raw.orders",
        storage_format="parquet",
        location="s3://raw/orders/",
    ))
    df.writes_to.append(TableIR(
        fully_qualified_name="prod.mart.orders_out",
        storage_format="delta",
    ))
    df.derivations.append(DerivationIR(
        target_column="amount_doubled",
        source_columns=["amount"],
        via="withColumn",
        formula="amount * 2",
    ))
    ir.dataframes.append(df)
    return ir


# ---------------------------------------------------------------------------
# OpenLineage emitter
# ---------------------------------------------------------------------------

def test_emit_event_shape():
    event = emit_script_event(_make_script())
    assert event["eventType"] == "COMPLETE"
    assert event["run"]["runId"]                       # deterministic UUID
    assert event["job"]["name"] == "orders_etl"
    assert event["job"]["namespace"] == "spark-parser"
    # Source code facet present
    assert "sourceCode" in event["job"]["facets"]


def test_emit_event_inputs_and_outputs():
    event = emit_script_event(_make_script())
    in_names = {d["name"] for d in event["inputs"]}
    out_names = {d["name"] for d in event["outputs"]}
    assert "prod.raw.orders" in in_names
    assert "prod.mart.orders_out" in out_names


def test_emit_event_column_lineage_facet():
    event = emit_script_event(_make_script())
    out = next(d for d in event["outputs"] if d["name"] == "prod.mart.orders_out")
    col_lineage = out["facets"]["columnLineage"]["fields"]
    assert "amount_doubled" in col_lineage
    sources = col_lineage["amount_doubled"]["inputFields"]
    assert sources[0]["field"] == "amount"
    assert sources[0]["name"] == "prod.raw.orders"


def test_emit_run_id_is_deterministic():
    a = emit_script_event(_make_script())["run"]["runId"]
    b = emit_script_event(_make_script())["run"]["runId"]
    assert a == b


def test_emit_project_events():
    project = ProjectIR(
        entry_script_id="deadbeef",
        project_root="/jobs",
        modules=[_make_script()],
    )
    events = emit_project_events(project)
    assert len(events) == 1
    assert events[0]["job"]["name"] == "orders_etl"


# ---------------------------------------------------------------------------
# Cross-parser FQN canonicalization
# ---------------------------------------------------------------------------

def test_canonical_id_three_part_fqn():
    a = canonical_table_id("prod.dim.customers")
    b = canonical_table_id("Prod.Dim.Customers")  # case-insensitive
    assert a == b


def test_canonical_id_two_part_fqn():
    assert canonical_table_id("dim.customers") is not None


def test_canonical_id_falls_back_to_location():
    assert canonical_table_id(None, location="s3://raw/orders/") is not None


def test_shared_table_ids_intersection():
    shared = shared_table_ids(
        spark_fqns=["prod.dim.customers", "prod.mart.events"],
        other_fqns=["prod.dim.customers", "prod.dim.products"],
    )
    # exactly one shared FQN → one shared canonical id
    assert len(shared) == 1


# ---------------------------------------------------------------------------
# Unity Catalog client (HTTP injected)
# ---------------------------------------------------------------------------

def _fake_http(known: set[str]):
    def _client(url: str, headers: dict[str, str]):
        full_name = url.rsplit("/", 1)[-1]
        return (200, {"name": full_name}) if full_name in known else (404, None)
    return _client


def test_unity_catalog_table_exists_hit():
    c = UnityCatalogClient(
        base_url="https://dbx.example.com", token="tk",
        http=_fake_http({"prod.dim.customers"}),
    )
    assert c.table_exists("prod.dim.customers") is True


def test_unity_catalog_table_exists_miss():
    c = UnityCatalogClient(
        base_url="https://dbx.example.com", token="tk",
        http=_fake_http({"prod.dim.customers"}),
    )
    assert c.table_exists("prod.mart.missing") is False


def test_unity_catalog_verify_script_emits_warnings():
    ir = _make_script()
    c = UnityCatalogClient(
        base_url="https://dbx.example.com", token="tk",
        http=_fake_http({"prod.raw.orders"}),     # output is missing
    )
    warnings = c.verify_script(ir)
    assert any(w.type == "unity_catalog_mismatch" for w in warnings)
    # And the warning is attached to the IR as well.
    assert any(w.type == "unity_catalog_mismatch" for w in ir.warnings)
