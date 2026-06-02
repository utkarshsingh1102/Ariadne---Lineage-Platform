"""Phase 3 — QVF (Qlik Sense) extractor."""
from __future__ import annotations

import json

import pytest

from qlikview_parser.extract_qvf import (
    QvfExtraction,
    QvfExtractionError,
    extract,
    extract_strict,
    write_synthetic_qvf,
)


def test_extract_missing_file_returns_diagnostic(tmp_path):
    """A missing file produces an error diagnostic, NOT an exception."""
    extraction = extract(tmp_path / "nope.qvf")
    assert extraction.script_text == ""
    codes = [d.code for d in extraction.diagnostics]
    assert "QV-QVF-NOT-FOUND" in codes


def test_extract_strict_raises_on_missing_file(tmp_path):
    with pytest.raises(QvfExtractionError):
        extract_strict(tmp_path / "missing.qvf")


def test_extract_recovers_script_text_from_synthetic_qvf(tmp_path):
    qvf = tmp_path / "sample.qvf"
    write_synthetic_qvf(qvf, script_text="LOAD * INLINE [X\n1\n];\n",
                        app_name="Sales Insights")
    extraction = extract(qvf)
    assert "LOAD" in extraction.script_text
    assert extraction.app_name == "Sales Insights"


def test_extract_handles_json_wrapped_script(tmp_path):
    """Some QVFs store the script as ``{"qScript": "..."}``. The
    extractor must unwrap that to plain text."""
    qvf = tmp_path / "wrapped.qvf"
    wrapped = json.dumps({"qScript": "LOAD * INLINE [X\n1\n];\n"})
    write_synthetic_qvf(qvf, script_text=wrapped)
    extraction = extract(qvf)
    assert extraction.script_text.startswith("LOAD")


def test_extract_picks_up_app_objects(tmp_path):
    qvf = tmp_path / "with_objects.qvf"
    objects = [
        {
            "qId": "obj-1",
            "qType": "barchart",
            "qTitle": "Sales by Region",
            "qData": json.dumps({
                "qHyperCubeDef": {
                    "qDimensions": [
                        {"qDef": {"qFieldDefs": ["Region"]}},
                    ],
                    "qMeasures": [
                        {"qDef": {"qDef": "Sum(Sales)"}},
                    ],
                }
            }),
        },
    ]
    write_synthetic_qvf(qvf, script_text="LOAD * INLINE [Region, Sales\nA, 1];\n",
                        app_objects=objects)
    extraction = extract(qvf)
    assert len(extraction.app_objects_raw) == 1
    assert extraction.app_objects_raw[0]["qType"] == "barchart"
