"""Step 2 — relation walker must handle ``type='stored-proc'``."""
from __future__ import annotations

from io import BytesIO

from lxml import etree

from tableau_parser.parser import column, relation
from tableau_parser.utils.tags import normalize_tree


_DS_XML = b"""
<datasource name='returns_ds'>
  <connection class='federated'>
    <named-connections>
      <named-connection name='ms'>
        <connection class='sqlserver' server='m' dbname='OPS' />
      </named-connection>
    </named-connections>
    <relation connection='ms' name='usp_returns_summary' type='stored-proc'>
      <actual-name>[dbo].[usp_returns_summary]</actual-name>
      <columns>
        <column datatype='integer' name='order_id' ordinal='1' />
        <column datatype='real' name='net_amount' ordinal='2' />
        <column datatype='string' name='region_code' ordinal='3' />
      </columns>
    </relation>
  </connection>
</datasource>
"""


def _ds():
    tree = etree.parse(BytesIO(_DS_XML))
    root = tree.getroot()
    normalize_tree(root)
    return root


def test_stored_proc_emits_table_with_proc_relation_type():
    ds = _ds()
    tables = relation.parse_relations(ds, default_database="OPS", default_schema="")
    procs = [t for t in tables if t.relation_type == "stored_proc"]
    assert len(procs) == 1
    p = procs[0]
    assert p.name == "usp_returns_summary"
    assert p.schema == "dbo"
    assert p.database == "OPS"
    assert p.source_type == "stored_proc"
    assert p.fully_qualified_name.lower().endswith("usp_returns_summary")


def test_stored_proc_declared_columns_become_fields():
    ds = _ds()
    tables = relation.parse_relations(ds, default_database="OPS", default_schema="")
    fields = column.parse_proc_columns(ds, datasource_id_str="ds_x", proc_tables=tables)
    names = sorted(f.name for f in fields)
    assert names == ["net_amount", "order_id", "region_code"]
    # Anchored to the proc's FQN, not a fake datasource scope.
    for f in fields:
        assert f.table_fqn == tables[0].fully_qualified_name
        assert not f.is_calculated


def test_stored_proc_falls_back_to_name_attr_when_actual_name_missing():
    xml = b"""
    <datasource name='x'>
      <connection class='federated'>
        <relation name='just_proc' type='stored-proc' />
      </connection>
    </datasource>
    """
    root = etree.parse(BytesIO(xml)).getroot()
    normalize_tree(root)
    tables = relation.parse_relations(root, default_database="DB", default_schema="dbo")
    procs = [t for t in tables if t.relation_type == "stored_proc"]
    assert len(procs) == 1
    assert procs[0].name == "just_proc"
