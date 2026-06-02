"""Coverage for utils.tags — the 2018.1+ FCP-prefix normaliser."""
from __future__ import annotations

from io import BytesIO

from lxml import etree

from tableau_parser.utils.tags import normalize_tag, normalize_tree


def test_normalize_tag_returns_suffix_after_triple_dot():
    assert (
        normalize_tag("_.fcp.ObjectModelEncapsulateLegacy.false...relation")
        == "relation"
    )


def test_normalize_tag_passthrough_when_no_prefix():
    assert normalize_tag("relation") == "relation"
    assert normalize_tag("worksheet") == "worksheet"


def test_normalize_tag_picks_last_triple_dot_segment():
    # Defensive: ``...`` in the middle and a trailing real segment.
    assert (
        normalize_tag("_.fcp.X.y...inner...relation")
        == "relation"
    )


def test_normalize_tag_handles_non_string_safely():
    # lxml comment/PI nodes have non-string tags (functions). Must not raise.
    sentinel = object()
    assert normalize_tag(sentinel) is sentinel  # type: ignore[arg-type]


def test_normalize_tree_mutates_in_place_and_findall_now_matches():
    xml = b"""
    <workbook>
      <datasources>
        <datasource name='x'>
          <connection class='federated'>
            <_.fcp.ObjectModelEncapsulateLegacy.false...relation type='text'>
              SELECT 1
            </_.fcp.ObjectModelEncapsulateLegacy.false...relation>
          </connection>
        </datasource>
      </datasources>
    </workbook>
    """
    tree = etree.parse(BytesIO(xml))
    root = tree.getroot()
    # Pre-normalisation the bare-name search misses the prefixed element.
    assert root.findall(".//relation") == []
    count = normalize_tree(root)
    assert count == 1
    # Post-normalisation the same search lands.
    rels = root.findall(".//relation")
    assert len(rels) == 1
    assert rels[0].get("type") == "text"


def test_normalize_tree_idempotent():
    xml = b"<workbook><_.fcp.A.b...thing /></workbook>"
    tree = etree.parse(BytesIO(xml))
    root = tree.getroot()
    assert normalize_tree(root) == 1
    # Second pass: nothing left to normalise.
    assert normalize_tree(root) == 0


def test_normalize_tree_none_safe():
    assert normalize_tree(None) == 0
