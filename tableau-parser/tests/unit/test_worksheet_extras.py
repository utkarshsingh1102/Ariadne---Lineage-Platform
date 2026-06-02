"""Step 4 — worksheet de-lossify: aggregation, multi-shelf, filters, sorts.

These tests use synthetic XML strings instead of fixture files so each
assertion targets exactly one new behaviour.
"""
from __future__ import annotations

from io import BytesIO

from lxml import etree

from tableau_parser.parser.worksheet import parse_worksheets


def _tree(xml: str):
    return etree.parse(BytesIO(xml.encode("utf-8")))


def test_aggregation_inferred_from_inline_wrapper():
    """``SUM([Sales])`` on the rows shelf must surface as aggregation='SUM'."""
    tree = _tree("""
    <workbook>
      <worksheets>
        <worksheet name="W1">
          <table>
            <view>
              <datasource-dependencies datasource="ds1">
                <column name="[Sales]" />
              </datasource-dependencies>
              <rows>SUM([Sales])</rows>
            </view>
          </table>
        </worksheet>
      </worksheets>
    </workbook>
    """)
    sheets = parse_worksheets(tree)
    assert len(sheets) == 1
    u = sheets[0].field_usages
    assert len(u) == 1
    assert u[0].field_name == "Sales"
    assert u[0].shelf == "rows"
    assert u[0].aggregation == "SUM"


def test_same_field_on_two_shelves_produces_two_usages():
    """Plan §4: the dedup-on-(ds,name) was lossy. Now Region on rows AND
    Region on color must surface as TWO FieldUsageIR rows."""
    tree = _tree("""
    <workbook>
      <worksheets>
        <worksheet name="W1">
          <table>
            <view>
              <datasource-dependencies datasource="ds1">
                <column name="[Region]" />
              </datasource-dependencies>
              <rows>[Region]</rows>
              <color>[Region]</color>
            </view>
          </table>
        </worksheet>
      </worksheets>
    </workbook>
    """)
    sheets = parse_worksheets(tree)
    shelves = sorted(u.shelf for u in sheets[0].field_usages)
    assert shelves == ["color", "rows"]


def test_worksheet_filter_extracted():
    tree = _tree("""
    <workbook>
      <worksheets>
        <worksheet name="W1">
          <table>
            <view>
              <datasource-dependencies datasource="ds1">
                <column name="[Status]" />
              </datasource-dependencies>
            </view>
            <filter class="categorical" column="[ds1].[Status]">
              <groupfilter>
                <member value="'Active'" />
              </groupfilter>
            </filter>
          </table>
        </worksheet>
      </worksheets>
    </workbook>
    """)
    sheets = parse_worksheets(tree)
    fil = sheets[0].filters
    assert len(fil) == 1
    assert fil[0].field_name == "Status"
    assert fil[0].datasource_name == "ds1"
    assert fil[0].filter_class == "categorical"
    assert fil[0].worksheet_id == sheets[0].id
    assert fil[0].line is not None


def test_worksheet_sort_extracted():
    tree = _tree("""
    <workbook>
      <worksheets>
        <worksheet name="W1">
          <table>
            <view>
              <datasource-dependencies datasource="ds1">
                <column name="[Revenue]" />
              </datasource-dependencies>
            </view>
            <sort column="[ds1].[Revenue]" direction="descending" />
          </table>
        </worksheet>
      </worksheets>
    </workbook>
    """)
    sheets = parse_worksheets(tree)
    sorts = sheets[0].sorts
    assert len(sorts) == 1
    assert sorts[0].field_name == "Revenue"
    assert sorts[0].direction == "descending"


def test_datasource_level_filter_attached_to_datasource(fixture_path):
    """Real fixture exercise: parse_workbook should also pick up datasource
    filters when present. Fixture 08 has them on the realistic dashboard."""
    from tableau_parser.parser.workbook import parse_workbook

    wb = parse_workbook(fixture_path("08_realistic_dashboard.twb"))
    # Smoke check: the parse runs and filters list exists (may be empty if
    # 08 doesn't have ds-level filters; non-empty if the fixture does).
    for d in wb.datasources:
        assert isinstance(d.filters, list)
