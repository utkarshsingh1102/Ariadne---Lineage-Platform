"""Step 8 — sub-field metadata: default-aggregation, ordinal, precision,
scale, contains-null, value_aliases."""
from __future__ import annotations

from io import BytesIO

from lxml import etree

from tableau_parser.parser.column import parse_columns


def _ds(xml_inner: str):
    return etree.fromstring(
        f"""<datasource name="ds1">{xml_inner}</datasource>""".encode("utf-8")
    )


def test_default_aggregation_captured():
    ds = _ds("""
    <column name="[Sales]" datatype="real" role="measure"
            default-aggregation="sum" />
    """)
    f = parse_columns(ds, datasource_id_str="ds1id")[0]
    assert f.default_aggregation == "sum"


def test_precision_and_scale_captured():
    ds = _ds("""
    <column name="[Price]" datatype="real" role="measure"
            precision="10" scale="2" />
    """)
    f = parse_columns(ds, datasource_id_str="ds1id")[0]
    assert f.precision == 10
    assert f.scale == 2


def test_contains_null_captured():
    ds = _ds("""
    <column name="[OptionalRef]" datatype="integer" role="dimension"
            contains-null="true" />
    """)
    f = parse_columns(ds, datasource_id_str="ds1id")[0]
    assert f.contains_null is True


def test_value_aliases_from_aliases_element():
    ds = _ds("""
    <column name="[Status]" datatype="string" role="dimension">
      <aliases>
        <alias key="A" value="Active" />
        <alias key="I" value="Inactive" />
      </aliases>
    </column>
    """)
    f = parse_columns(ds, datasource_id_str="ds1id")[0]
    assert f.value_aliases == {"A": "Active", "I": "Inactive"}


def test_value_aliases_from_map_buckets():
    ds = _ds("""
    <column name="[Region]" datatype="string" role="dimension">
      <map>
        <bucket value="East">
          <member value="NY" />
          <member value="NJ" />
        </bucket>
        <bucket value="West">
          <member value="CA" />
        </bucket>
      </map>
    </column>
    """)
    f = parse_columns(ds, datasource_id_str="ds1id")[0]
    assert f.value_aliases == {"NY": "East", "NJ": "East", "CA": "West"}


def test_missing_attrs_leave_fields_none_or_empty():
    ds = _ds("""<column name="[X]" datatype="string" />""")
    f = parse_columns(ds, datasource_id_str="ds1id")[0]
    assert f.default_aggregation == ""
    assert f.ordinal is None
    assert f.precision is None
    assert f.contains_null is None
    assert f.value_aliases == {}
