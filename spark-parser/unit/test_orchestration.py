"""Unit tests for v0.2 §7 — orchestration-layer parsing."""
from __future__ import annotations

from pathlib import Path

from spark_parser.orchestration.airflow_parser import parse_airflow_dag
from spark_parser.orchestration.databricks_workflow import parse_databricks_workflow
from spark_parser.orchestration.spark_submit import parse_spark_submit

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "orchestration"


# ---------------------------------------------------------------------------
# Airflow
# ---------------------------------------------------------------------------

def test_airflow_extracts_dag_id_and_schedule():
    job = parse_airflow_dag(FIXTURES / "orders_etl_dag.py")
    assert job.job_id == "orders_etl"
    assert job.schedule == "0 2 * * *"


def test_airflow_extracts_all_four_operator_types():
    job = parse_airflow_dag(FIXTURES / "orders_etl_dag.py")
    by_id = {t.task_id: t for t in job.tasks}
    assert set(by_id) == {"ingest", "transform", "aggregate", "publish"}
    assert by_id["ingest"].operator == "SparkSubmitOperator"
    assert by_id["ingest"].target_script == "/jobs/ingest_orders.py"
    assert by_id["transform"].operator == "BashOperator"
    # BashOperator pulled the `.py` token from the spark-submit command.
    assert by_id["transform"].target_script == "/jobs/transform_orders.py"
    assert by_id["aggregate"].operator == "PythonOperator"
    assert by_id["aggregate"].target_script == "aggregate_orders_callable"
    assert by_id["publish"].operator == "DatabricksRunNowOperator"
    assert by_id["publish"].target_script == "/Repos/team/orders_publish"


def test_airflow_extracts_dependencies_from_rshift_chain():
    job = parse_airflow_dag(FIXTURES / "orders_etl_dag.py")
    edges = {(e.upstream, e.downstream) for e in job.edges}
    assert ("ingest", "transform") in edges
    assert ("transform", "aggregate") in edges
    assert ("aggregate", "publish") in edges


# ---------------------------------------------------------------------------
# Databricks workflow JSON
# ---------------------------------------------------------------------------

def test_databricks_workflow_top_level_metadata():
    job = parse_databricks_workflow(FIXTURES / "databricks_workflow.json")
    assert job.job_id == "orders_workflow"
    assert job.schedule == "0 0 2 * * ?"
    assert job.source == "databricks_workflow"


def test_databricks_workflow_task_targets():
    job = parse_databricks_workflow(FIXTURES / "databricks_workflow.json")
    by_id = {t.task_id: t for t in job.tasks}
    assert by_id["ingest"].operator == "notebook"
    assert by_id["ingest"].target_script == "/Repos/team/ingest_orders"
    assert by_id["transform"].operator == "spark_python"
    assert by_id["transform"].target_script == "dbfs:/jobs/transform_orders.py"
    assert by_id["aggregate"].operator == "spark_jar"
    assert by_id["aggregate"].target_script == "com.team.AggregateOrders"


def test_databricks_workflow_dependencies():
    job = parse_databricks_workflow(FIXTURES / "databricks_workflow.json")
    edges = {(e.upstream, e.downstream) for e in job.edges}
    assert edges == {("ingest", "transform"), ("transform", "aggregate")}


# ---------------------------------------------------------------------------
# spark-submit shell script
# ---------------------------------------------------------------------------

def test_spark_submit_extracts_both_invocations():
    job = parse_spark_submit(FIXTURES / "run_jobs.sh")
    targets = [t.target_script for t in job.tasks]
    assert "/jobs/ingest_orders.py" in targets
    assert "/jobs/transform_orders.py" in targets


def test_spark_submit_captures_conf_and_argv():
    job = parse_spark_submit(FIXTURES / "run_jobs.sh")
    ingest = next(t for t in job.tasks if t.target_script == "/jobs/ingest_orders.py")
    assert ingest.parameters.get("conf.spark.executor.memory") == "4g"
    assert ingest.parameters.get("master") == "yarn"
    assert ingest.parameters.get("py-files") == "lib/utils.py,lib/connectors.py"
    # Positional argv preserved
    assert ingest.parameters.get("argv[0]") == "prod"
    assert ingest.parameters.get("argv[1]") == "2024-01-01"
