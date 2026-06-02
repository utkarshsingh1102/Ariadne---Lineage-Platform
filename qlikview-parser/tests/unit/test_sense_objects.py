"""Phase 3 — Qlik Sense app-object → UiObject + FEEDS_OBJECT IR."""
from __future__ import annotations

import json

from qlikview_parser.ids import attribute_qname, dataset_qname, sha256_id
from qlikview_parser.models import Attribute, Dataset, QlikViewApp
from qlikview_parser.sense_objects import parse_app_objects


def _app_with_field(field_name: str = "Region") -> QlikViewApp:
    app = QlikViewApp(app_name="x", file_path="/apps/x.qvf")
    ds_q = dataset_qname(app.file_path, "Sales")
    app.datasets.append(Dataset(name="Sales", origin="sql", app=app.file_path))
    app.attributes.append(Attribute(dataset=ds_q, name=field_name))
    return app


def test_parses_chart_and_emits_feeds_object_edge_for_known_field():
    app = _app_with_field("Region")
    raw = [{
        "qId": "obj-1",
        "qType": "barchart",
        "qTitle": "Sales by Region",
        "qData": json.dumps({
            "qHyperCubeDef": {
                "qDimensions": [{"qDef": {"qFieldDefs": ["Region"]}}],
                "qMeasures": [{"qDef": {"qDef": "Sum(Sales)"}}],
            }
        }),
    }]
    result = parse_app_objects(app, raw)
    assert len(result.objects) == 1
    obj = result.objects[0]
    assert obj.qtype == "barchart"
    assert obj.qtitle == "Sales by Region"

    feeds = [e for e in result.edges if e.rel == "FEEDS_OBJECT"]
    assert len(feeds) >= 1
    expected_src = sha256_id(attribute_qname(
        dataset_qname(app.file_path, "Sales"), "Region",
    ))
    assert any(e.src_id == expected_src for e in feeds)


def test_unknown_fields_in_expression_do_not_emit_edges():
    """Identifiers in chart expressions that aren't on any :Attribute in
    the app must NOT produce phantom FEEDS_OBJECT edges."""
    app = _app_with_field("Region")
    raw = [{
        "qId": "obj-2",
        "qType": "kpi",
        "qData": json.dumps({
            "qHyperCubeDef": {
                "qMeasures": [{"qDef": {"qDef": "Sum(NonExistentField)"}}],
            }
        }),
    }]
    result = parse_app_objects(app, raw)
    assert len(result.objects) == 1
    assert all(e.rel != "FEEDS_OBJECT" for e in result.edges)


def test_bracketed_field_names_are_recognised():
    """``[Customer Region]`` (Sense's spaced-name syntax) must be picked
    up as a field reference."""
    app = _app_with_field("Customer Region")
    raw = [{
        "qId": "obj-3",
        "qType": "table",
        "qData": json.dumps({
            "qHyperCubeDef": {
                "qDimensions": [{"qDef": {"qFieldDefs": ["[Customer Region]"]}}],
            }
        }),
    }]
    result = parse_app_objects(app, raw)
    feeds = [e for e in result.edges if e.rel == "FEEDS_OBJECT"]
    assert feeds, "bracketed [Customer Region] should match the attribute"


def test_malformed_json_emits_warn_diagnostic_not_exception():
    app = _app_with_field("Region")
    raw = [{
        "qId": "broken",
        "qType": "chart",
        "qData": "<<<not json>>>",
    }]
    result = parse_app_objects(app, raw)
    assert len(result.objects) == 1   # still creates the object shell
    codes = [d.code for d in result.diagnostics]
    assert "QV-SENSE-PARSE" in codes


def test_dedup_when_same_field_referenced_twice_in_same_object():
    """An expression referencing the same field in two places must
    produce exactly ONE FEEDS_OBJECT edge."""
    app = _app_with_field("Region")
    raw = [{
        "qId": "obj-dedup",
        "qType": "table",
        "qData": json.dumps({
            "qHyperCubeDef": {
                "qDimensions": [{"qDef": {"qFieldDefs": ["Region"]}}],
                "qMeasures": [{"qDef": {"qDef": "Sum(Region) + Count(Region)"}}],
            }
        }),
    }]
    result = parse_app_objects(app, raw)
    feeds = [e for e in result.edges
             if e.rel == "FEEDS_OBJECT" and e.dst_id == sha256_id(result.objects[0].qname)]
    assert len(feeds) == 1
