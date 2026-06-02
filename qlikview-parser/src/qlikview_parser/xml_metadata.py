"""Stage 4 — optional XML metadata reader (sheets + charts).

QlikView's Document Analyzer / "Generate XML" export captures the on-screen
model (sheets, charts, expressions, dimensions). When provided, we extract
sheets and charts as additional IR entries that the graph writer turns into
``:QlikSheet`` / ``:QlikChart`` nodes.

This is a *separate* code path from ANTLR — XML in, populated IR side-channel
out. Field-name matching against the ANTLR-derived ``:Attribute`` set happens
at write time, not here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

try:
    from lxml import etree as _etree  # type: ignore
except ImportError:  # pragma: no cover
    _etree = None  # type: ignore


@dataclass
class XmlChart:
    name: str
    chart_type: str
    sheet_id: str
    dimensions: list[str] = field(default_factory=list)
    expressions: list[str] = field(default_factory=list)


@dataclass
class XmlSheet:
    id: str
    name: str
    charts: list[XmlChart] = field(default_factory=list)


@dataclass
class XmlMetadata:
    sheets: list[XmlSheet] = field(default_factory=list)


def parse_xml_metadata(path: str | Path) -> XmlMetadata:
    """Read a QlikView document-analyzer XML export and surface sheets/charts."""
    if _etree is None:
        return XmlMetadata()
    p = Path(path)
    if not p.exists():
        return XmlMetadata()
    try:
        tree = _etree.parse(str(p))
    except Exception:
        return XmlMetadata()

    root = tree.getroot()
    meta = XmlMetadata()
    # Tolerate slight schema variants — we only need sheets and chart objects.
    for sheet_el in root.iter("Sheet"):
        sid = sheet_el.get("Id") or sheet_el.findtext("Id") or sheet_el.get("ID") or ""
        sname = sheet_el.findtext("Name") or sheet_el.get("Name") or sid
        sheet = XmlSheet(id=sid, name=sname)
        for obj_tag in ("ChartObject", "TableObject", "TextObject"):
            for chart_el in sheet_el.iter(obj_tag):
                cname = chart_el.findtext("Name") or chart_el.get("Name") or ""
                ctype = chart_el.findtext("Type") or obj_tag
                dims = [d.text for d in chart_el.iter("Dimension") if d.text]
                exprs = [e.text for e in chart_el.iter("Expression") if e.text]
                sheet.charts.append(XmlChart(
                    name=cname, chart_type=ctype, sheet_id=sid,
                    dimensions=dims, expressions=exprs,
                ))
        meta.sheets.append(sheet)
    return meta
