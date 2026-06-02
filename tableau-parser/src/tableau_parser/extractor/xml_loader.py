"""Load a .twb file (XML) into an lxml ElementTree."""

from __future__ import annotations

from pathlib import Path

from lxml import etree


def load_twb(twb_path: str | Path) -> etree._ElementTree:
    """Return an ElementTree with namespaces stripped (so XPath is simple)."""
    parser = etree.XMLParser(recover=True, huge_tree=True)
    tree = etree.parse(str(twb_path), parser=parser)
    root = tree.getroot()
    if root is not None:
        for el in root.iter():
            if isinstance(el.tag, str) and "}" in el.tag:
                el.tag = el.tag.split("}", 1)[1]
    return tree
