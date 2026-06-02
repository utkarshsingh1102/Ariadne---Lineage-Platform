"""Step 7 — ``<datasource-relationship>`` (worksheet data blending)."""
from __future__ import annotations

from io import BytesIO

from lxml import etree

from tableau_parser.parser import worksheet as ws_parser
from tableau_parser.utils.tags import normalize_tree


_BLENDED_WB = b"""
<workbook>
  <worksheets>
    <worksheet name='Blended Sheet'>
      <table>
        <view>
          <datasource-dependencies datasource='ds_primary'>
            <column name='[region_code]' />
          </datasource-dependencies>
          <datasource-dependencies datasource='ds_secondary'>
            <column name='[region_code]' />
          </datasource-dependencies>
          <datasource-relationship>
            <relation primary='ds_primary' secondary='ds_secondary'>
              <clause>
                <expression op='='>
                  <expression op='[ds_primary].[region_code]' />
                  <expression op='[ds_secondary].[region_code]' />
                </expression>
              </clause>
            </relation>
          </datasource-relationship>
        </view>
      </table>
    </worksheet>
  </worksheets>
</workbook>
"""


def _tree():
    tree = etree.parse(BytesIO(_BLENDED_WB))
    normalize_tree(tree.getroot())
    return tree


def test_blend_extracted_from_worksheet():
    sheets = ws_parser.parse_worksheets(_tree(), workbook_id_str="wb1")
    assert len(sheets) == 1
    ws = sheets[0]
    assert len(ws.blends) == 1
    bl = ws.blends[0]
    assert bl.primary_datasource_name == "ds_primary"
    assert bl.secondary_datasource_name == "ds_secondary"
    assert bl.on_field_names == ["region_code"]
    assert bl.id  # populated via worksheet_blend_id
    assert bl.line is not None


def test_worksheet_without_blend_emits_empty_list():
    """Other worksheets in the same workbook must NOT pick up blends."""
    xml = b"""
    <workbook><worksheets><worksheet name='Plain'>
      <table><view>
        <datasource-dependencies datasource='ds' />
      </view></table>
    </worksheet></worksheets></workbook>
    """
    tree = etree.parse(BytesIO(xml))
    normalize_tree(tree.getroot())
    sheets = ws_parser.parse_worksheets(tree, workbook_id_str="wb")
    assert sheets[0].blends == []
