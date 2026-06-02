"""Sample Airflow DAG covering the four operator shapes the parser handles."""
from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator
from airflow.providers.databricks.operators.databricks import DatabricksRunNowOperator


with DAG(
    dag_id="orders_etl",
    schedule="0 2 * * *",
    start_date=None,
    catchup=False,
) as dag:
    ingest = SparkSubmitOperator(
        task_id="ingest",
        application="/jobs/ingest_orders.py",
        conn_id="spark_default",
    )
    transform = BashOperator(
        task_id="transform",
        bash_command="spark-submit --conf spark.master=local /jobs/transform_orders.py prod",
    )
    aggregate = PythonOperator(
        task_id="aggregate",
        python_callable="aggregate_orders_callable",
    )
    publish = DatabricksRunNowOperator(
        task_id="publish",
        notebook_path="/Repos/team/orders_publish",
    )

    ingest >> transform >> aggregate
    aggregate >> publish
