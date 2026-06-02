"""XML element-tag normalization for the 2018.1+ Tableau object model.

Tableau wraps some elements in a feature-control-point namespace prefix
like ``_.fcp.<Feature>.<value>...<realTag>`` — e.g.
``_.fcp.ObjectModelEncapsulateLegacy.false...relation``. The real local
name is the segment after the last ``...``.

If left alone, every ``findall(".//relation")`` or ``iter("relation")``
in the parser silently misses the prefixed form, which is why the
custom-SQL relation in fixtures like ``lineage_stress_test.twb`` was
invisible to the table walker.

The fix is a single pre-walk that re-tags those elements in place. lxml
allows ``el.tag = "..."`` so the mutation is cheap and every downstream
findall/iter continues to work without changes.
"""
from __future__ import annotations

import re
from typing import Any


# ``_.fcp.<Feature>.<value>...<realTag>`` — capture the real tag at the end.
# The middle group is greedy so multiple ``...`` (rare but legal) still
# pick the LAST ``...``-delimited segment.
_FCP_RE = re.compile(r"^_\.fcp\..+\.\.\.(.+)$")


def normalize_tag(tag: str) -> str:
    """Return the real local-name for a Tableau FCP-prefixed tag.

    ``_.fcp.ObjectModelEncapsulateLegacy.false...relation`` -> ``relation``.
    A non-prefixed tag is returned unchanged.
    """
    if not isinstance(tag, str):
        return tag
    m = _FCP_RE.match(tag)
    return m.group(1) if m else tag


def normalize_tree(root: Any) -> int:
    """Mutate every FCP-prefixed element under ``root`` in place.

    Returns the count of elements re-tagged so callers can log the volume
    when a workbook touches the legacy object model.
    """
    if root is None:
        return 0
    n = 0
    for el in root.iter():
        tag = el.tag
        if not isinstance(tag, str):
            continue
        norm = normalize_tag(tag)
        if norm != tag:
            el.tag = norm
            n += 1
    return n
