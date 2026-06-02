"""Phase 2 unit tests — QVD header reader."""
from __future__ import annotations

import tempfile

import pytest

from qlikview_parser.qvd_header import (
    QvdHeaderError,
    read_header,
    write_synthetic_qvd,
)


def _build(path: str, **kwargs) -> None:
    defaults = dict(
        table_name="Customers",
        fields=[
            ("CustomerID", 20, 10000),     # 10000 distinct values → likely unique
            ("Name", 18, 9800),
            ("Region", 4, 6),              # only 6 distinct → low-cardinality
        ],
        no_of_records=10000,
        lineage_statements=[
            "STORE Customers INTO 'qvd/customers.qvd' (qvd);",
        ],
    )
    defaults.update(kwargs)
    write_synthetic_qvd(path, **defaults)


def test_basic_header_round_trip(tmp_path):
    qvd = tmp_path / "customers.qvd"
    _build(str(qvd))
    h = read_header(str(qvd))
    assert h.table_name == "Customers"
    assert h.no_of_records == 10000
    assert [f.name for f in h.fields] == ["CustomerID", "Name", "Region"]
    assert h.fields[0].ordinal == 0
    assert h.fields[2].ordinal == 2
    assert h.diagnostics == []


def test_unique_hint_fires_when_symbols_equal_records(tmp_path):
    qvd = tmp_path / "customers.qvd"
    _build(str(qvd))
    h = read_header(str(qvd))
    customer_id = next(f for f in h.fields if f.name == "CustomerID")
    name = next(f for f in h.fields if f.name == "Name")
    region = next(f for f in h.fields if f.name == "Region")
    assert customer_id.is_likely_unique is True   # 10000 / 10000
    assert name.is_likely_unique is False          # 9800 / 10000
    assert region.is_likely_unique is False        # 6 / 10000


def test_lineage_statements_extracted(tmp_path):
    qvd = tmp_path / "customers.qvd"
    _build(str(qvd))
    h = read_header(str(qvd))
    assert h.lineage_statements == [
        "STORE Customers INTO 'qvd/customers.qvd' (qvd);",
    ]


def test_bit_width_preserved(tmp_path):
    qvd = tmp_path / "customers.qvd"
    _build(str(qvd))
    h = read_header(str(qvd))
    customer_id = next(f for f in h.fields if f.name == "CustomerID")
    assert customer_id.bit_width == 20
    assert customer_id.no_of_symbols == 10000


def test_missing_file_raises(tmp_path):
    with pytest.raises(QvdHeaderError, match="not found"):
        read_header(str(tmp_path / "does_not_exist.qvd"))


def test_garbage_file_raises_no_terminator(tmp_path):
    bogus = tmp_path / "bogus.qvd"
    bogus.write_bytes(b"this isn't a qvd at all" * 100)
    with pytest.raises(QvdHeaderError, match="no QVD header terminator"):
        read_header(str(bogus))


def test_no_lineage_section_is_fine(tmp_path):
    qvd = tmp_path / "no_lineage.qvd"
    _build(str(qvd), lineage_statements=None)
    h = read_header(str(qvd))
    assert h.lineage_statements == []


def test_table_name_falls_back_to_filename_when_missing(tmp_path):
    qvd = tmp_path / "orders.qvd"
    # Header without an explicit TableName element — write it raw.
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<QvdTableHeader>\n'
        '  <NoOfRecords>0</NoOfRecords>\n'
        '  <Fields></Fields>\n'
        '</QvdTableHeader>\r\n\x00'
    )
    qvd.write_bytes(xml.encode("utf-8"))
    h = read_header(str(qvd))
    assert h.table_name == "orders"     # filename stem
