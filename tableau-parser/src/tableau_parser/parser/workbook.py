"""Top-level orchestrator: a file path in, a populated WorkbookIR out."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from tableau_parser.extractor import archive, xml_loader
from tableau_parser.models.domain import WorkbookIR
from tableau_parser.parser import (
    calculation,
    coverage,
    dashboard,
    datasource,
    worksheet,
)
from tableau_parser.utils.ids import workbook_id
from tableau_parser.utils.lines import first_sourceline
from tableau_parser.utils.tags import normalize_tree


def parse_workbook(file_path: str) -> WorkbookIR:
    abs_path = os.path.abspath(file_path)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(abs_path)

    suffix = Path(abs_path).suffix.lower()
    extract_dir: Path | None = None

    if suffix == ".twbx":
        extract_dir = Path(tempfile.mkdtemp(prefix="tableau-parser-"))
        twb_path = archive.extract_twbx(abs_path, dest_dir=extract_dir)
    elif suffix == ".twb":
        twb_path = Path(abs_path)
    else:
        raise ValueError(f"Unsupported file extension: {abs_path}")

    tree = xml_loader.load_twb(twb_path)
    root = tree.getroot()
    # Strip the 2018.1+ FCP namespace prefix from every element so the
    # downstream walkers' ``findall``/``iter`` calls match on the bare
    # local-name. See utils/tags.normalize_tree.
    normalize_tree(root)
    version = root.get("version", "") if root is not None else ""
    name = Path(abs_path).stem
    wb_id = workbook_id(abs_path)

    dses, params, scopes = datasource.parse_datasources(
        tree, workbook_id_str=wb_id, extract_dir=extract_dir
    )
    sheets = worksheet.parse_worksheets(tree, workbook_id_str=wb_id)
    dashes = dashboard.parse_dashboards(tree, workbook_id_str=wb_id)

    # Improvement-v2 §6 — workbook-level cross-datasource resolution.
    # Per-datasource resolution (in calculation.resolve_dependencies) already
    # produced FormulaRefIR rows of kind='cross_source'. This pass turns
    # the foreign datasource/field names into resolved id pairs.
    cross_refs = calculation.resolve_cross_source_refs(dses, params)

    # Coverage harness — surface every tag the parser walks past without
    # producing IR. Tests assert this list is empty on the reference fixture
    # (step 7), preventing the silent "we forgot to map <foo>" failure mode.
    warnings = coverage.unmapped_warnings(tree)

    # Compute file-end line by walking the tree once; lxml exposes
    # sourceline on every element. The root's deepest descendant's line
    # is a good proxy for the file's last meaningful line.
    line_end: int | None = None
    if root is not None:
        for el in root.iter():
            sl = getattr(el, "sourceline", None)
            if isinstance(sl, int) and (line_end is None or sl > line_end):
                line_end = sl

    return WorkbookIR(
        id=wb_id,
        name=name,
        file_path=abs_path,
        version=version,
        parsed_at=datetime.now(timezone.utc).isoformat(),
        datasources=dses,
        worksheets=sheets,
        dashboards=dashes,
        parameters=params,
        parameter_scopes=scopes,
        cross_ds_refs=cross_refs,
        line=first_sourceline(root),
        line_end=line_end,
        warnings=warnings,
    )
