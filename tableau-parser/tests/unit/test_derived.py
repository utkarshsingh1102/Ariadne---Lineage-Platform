"""Step 6 — groups, sets, bins, hierarchies."""
from __future__ import annotations

from io import BytesIO

from lxml import etree

from tableau_parser.parser.derived import (
    parse_bins,
    parse_groups,
    parse_hierarchies,
    parse_sets,
)


def _ds(xml_inner: str):
    """Return a `<datasource>` element wrapping the given XML body."""
    full = f"""<datasource name='ds1'>{xml_inner}</datasource>"""
    return etree.fromstring(full.encode("utf-8"))


def test_group_captures_source_field():
    ds = _ds("""
    <group name="[Region Bucket]" column="[City]">
      <bucket name="East" />
      <bucket name="West" />
    </group>
    """)
    out = parse_groups(ds, datasource_id_str="ds1id")
    assert len(out) == 1
    g = out[0]
    assert g.name == "Region Bucket"
    assert g.source_field_names == ["City"]
    assert g.line is not None


def test_set_captures_condition_expr():
    ds = _ds("""
    <set name="[Top Customers]" column="[Customer ID]" expression="AVG([Sales]) > 1000" />
    """)
    out = parse_sets(ds, datasource_id_str="ds1id")
    assert len(out) == 1
    st = out[0]
    assert st.name == "Top Customers"
    assert st.source_field_names == ["Customer ID"]
    assert "AVG" in st.condition_expr


def test_bin_captures_size_and_source():
    ds = _ds("""
    <column name="[Sales (bin)]" caption="Sales binned">
      <bin column="[Sales]" size="100" />
    </column>
    """)
    out = parse_bins(ds, datasource_id_str="ds1id")
    assert len(out) == 1
    b = out[0]
    assert b.name == "Sales (bin)"
    assert b.source_field_names == ["Sales"]
    assert b.size == "100"


def test_hierarchy_captures_ordered_levels():
    ds = _ds("""
    <drill-path name="[Geography]">
      <field>[Country]</field>
      <field>[Region]</field>
      <field>[City]</field>
    </drill-path>
    """)
    out = parse_hierarchies(ds, datasource_id_str="ds1id")
    assert len(out) == 1
    h = out[0]
    assert h.name == "Geography"
    assert h.levels == ["Country", "Region", "City"]


def test_empty_when_no_derived_elements_present():
    ds = _ds("<connection class='postgres' server='x' dbname='y' />")
    assert parse_groups(ds, "id") == []
    assert parse_sets(ds, "id") == []
    assert parse_bins(ds, "id") == []
    assert parse_hierarchies(ds, "id") == []
