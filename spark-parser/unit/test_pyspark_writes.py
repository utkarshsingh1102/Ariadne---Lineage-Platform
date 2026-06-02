"""
PySpark write detection (plan §6 step 6).
saveAsTable / save / insertInto with mode tracking.
"""
import pytest


def test_save_as_table_target_captured(pyspark_fixture):
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("01_simple_read_write.py")))

    targets = [t for df in ir.dataframes for t in df.writes_to]
    assert len(targets) == 1
    assert targets[0].fully_qualified_name.lower() == "prod.mart.orders"


def test_write_mode_captured(pyspark_fixture):
    """The WRITES_TABLE edge must carry mode='overwrite'."""
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("01_simple_read_write.py")))

    writes = [w for df in ir.dataframes for w in df.write_edges]
    assert len(writes) == 1
    assert writes[0].mode == "overwrite"
    assert writes[0].via == "saveAsTable"


def test_storage_format_captured(pyspark_fixture):
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("01_simple_read_write.py")))
    targets = [t for df in ir.dataframes for t in df.writes_to]
    assert targets[0].storage_format == "delta"


def test_insert_into_captured(pyspark_fixture):
    """The realistic fixture uses .insertInto() — must distinguish from saveAsTable."""
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("09_realistic_etl.py")))

    write_vias = {w.via for df in ir.dataframes for w in df.write_edges}
    assert "insertInto" in write_vias
    assert "saveAsTable" in write_vias


def test_path_save_captured(pyspark_fixture):
    """.save('s3://...') must produce a :Table with location set, no FQN."""
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("09_realistic_etl.py")))

    path_targets = [
        t for df in ir.dataframes for t in df.writes_to
        if t.location and t.location.startswith("s3://mart/summary")
    ]
    assert len(path_targets) >= 1


def test_multiple_write_targets_in_one_script(pyspark_fixture):
    """Realistic fixture has 3 write targets."""
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("09_realistic_etl.py")))

    targets = [t for df in ir.dataframes for t in df.writes_to]
    assert len(targets) >= 3


def test_append_mode_captured(pyspark_fixture):
    """summary_daily insertInto uses mode='append'."""
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("09_realistic_etl.py")))

    modes = {w.mode for df in ir.dataframes for w in df.write_edges}
    assert "append" in modes
