"""Step 5 — `<calculation class='X'>` discriminator.

Only ``class='tableau'`` becomes a calc field. ``class='bin'`` lands as a
BinIR; ``class='categorical-bin'`` lands as a SetIR. Neither should
double-count.
"""
from __future__ import annotations

from io import BytesIO

from lxml import etree

from tableau_parser.parser import column, derived
from tableau_parser.utils.tags import normalize_tree


_XML = b"""
<datasource name='sales_ds'>
  <connection class='postgres' server='x' dbname='y' />
  <relation type='table' name='orders' table='[public].[orders]' />

  <!-- raw physical -->
  <column datatype='real' name='[net_amount]' role='measure' />
  <column datatype='string' name='[region]' role='dimension' />

  <!-- real calc (class='tableau') -->
  <column caption='Profit' name='[Profit]' role='measure'>
    <calculation class='tableau' formula='[net_amount] - 5' />
  </column>

  <!-- bin (class='bin') -->
  <column caption='Net (bin)' name='[net_amount (bin)]' role='dimension'>
    <calculation class='bin' formula='[net_amount]' size='100' />
  </column>

  <!-- set (class='categorical-bin') -->
  <column caption='High Set' name='[Set 1]' role='dimension'>
    <calculation class='categorical-bin' column='[region]'>
      <bin value='East' />
      <bin value='West' />
    </calculation>
  </column>
</datasource>
"""


def _ds():
    tree = etree.parse(BytesIO(_XML))
    root = tree.getroot()
    normalize_tree(root)
    return root


def test_only_class_tableau_becomes_calc_field():
    ds = _ds()
    fields = column.parse_columns(ds, datasource_id_str="ds_a")
    by_name = {f.name: f for f in fields}
    # Real calc + raw fields land.
    assert by_name["Profit"].is_calculated is True
    assert by_name["net_amount"].is_calculated is False
    assert by_name["region"].is_calculated is False
    # Bin and set columns must NOT appear as FieldIRs.
    assert "net_amount (bin)" not in by_name
    assert "Set 1" not in by_name


def test_class_bin_lands_as_bin_ir():
    ds = _ds()
    bins = derived.parse_bins(ds, datasource_id_str="ds_a")
    assert len(bins) == 1
    b = bins[0]
    assert b.name == "net_amount (bin)"
    assert b.source_field_names == ["net_amount"]
    assert b.size == "100"


def test_class_categorical_bin_lands_as_set_ir():
    ds = _ds()
    sets = derived.parse_sets(ds, datasource_id_str="ds_a")
    assert len(sets) == 1
    s = sets[0]
    assert s.name == "Set 1"
    assert s.source_field_names == ["region"]
    # Membership preserved in condition_expr.
    assert "East" in s.condition_expr
    assert "West" in s.condition_expr
