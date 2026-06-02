"""OpenLineage 1.0 event emitter — v0.2 §10.

Translates a ``SparkScriptIR`` / ``ProjectIR`` into an OpenLineage event JSON
that downstream catalogs (Marquez, Astro Lineage, DataHub OpenLineage plugin)
can ingest. The spec we target is OpenLineage Spec v1.0.5 — only the fields
required for source/target lineage at the dataset granularity. Column-level
lineage uses the `columnLineage` facet on the output dataset.

The emitter does not perform HTTP. Callers receive the JSON-serialisable dict
and POST it themselves (typical pattern with OpenLineage clients).
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from ..models.domain import ProjectIR, SparkScriptIR


_OPENLINEAGE_VERSION = "1.0.5"
_NAMESPACE = "spark-parser"
_PRODUCER = "https://github.com/anthropics/spark-parser/v0.2"


def emit_script_event(
    ir: SparkScriptIR,
    *,
    event_type: str = "COMPLETE",
    run_id: str | None = None,
    event_time: str | None = None,
) -> dict[str, Any]:
    """Convert one ``SparkScriptIR`` to a single OpenLineage event."""
    if event_type not in {"START", "RUNNING", "COMPLETE", "ABORT", "FAIL", "OTHER"}:
        raise ValueError(f"unknown OpenLineage eventType: {event_type}")

    rid = run_id or _stable_run_id(ir.id)
    when = event_time or datetime.now(timezone.utc).isoformat()

    inputs: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    seen_inputs: set[str] = set()
    seen_outputs: set[str] = set()

    for df in ir.dataframes:
        for tbl in df.reads_from:
            ds = _table_to_dataset(tbl)
            if ds is None or ds["name"] in seen_inputs:
                continue
            seen_inputs.add(ds["name"])
            inputs.append(ds)
        for tbl in df.writes_to:
            ds = _table_to_dataset(tbl)
            if ds is None or ds["name"] in seen_outputs:
                continue
            seen_outputs.add(ds["name"])
            # Attach a column-lineage facet built from the DataFrame's
            # derivations + a SourceCode facet referencing the script.
            ds_with_lineage = _attach_column_lineage(ds, df, inputs)
            outputs.append(ds_with_lineage)

    return {
        "eventType": event_type,
        "eventTime": when,
        "producer": _PRODUCER,
        "schemaURL": f"https://openlineage.io/spec/{_OPENLINEAGE_VERSION}/OpenLineage.json",
        "run": {"runId": rid},
        "job": {
            "namespace": _NAMESPACE,
            "name": ir.name or ir.id,
            "facets": {
                "sourceCode": {
                    "_producer": _PRODUCER,
                    "_schemaURL": f"https://openlineage.io/spec/facets/{_OPENLINEAGE_VERSION}/SourceCodeJobFacet.json",
                    "language": ir.script_type,
                    "sourceCode": ir.file_path,
                },
            },
        },
        "inputs": inputs,
        "outputs": outputs,
    }


def emit_project_events(project: ProjectIR) -> list[dict[str, Any]]:
    """One OpenLineage event per module in the project."""
    return [emit_script_event(m) for m in project.modules]


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _table_to_dataset(tbl) -> dict[str, Any] | None:
    name = tbl.fully_qualified_name or tbl.location
    if not name:
        return None
    return {
        "namespace": _dataset_namespace(tbl.storage_format, tbl.location, name),
        "name": name,
        "facets": {
            "storage": {
                "_producer": _PRODUCER,
                "_schemaURL": f"https://openlineage.io/spec/facets/{_OPENLINEAGE_VERSION}/StorageDatasetFacet.json",
                "storageLayer": tbl.storage_format or "unknown",
                "fileFormat": tbl.storage_format or "",
            },
        },
    }


def _dataset_namespace(storage_format: str | None, location: str | None, name: str) -> str:
    """Map our storage_format → an OpenLineage namespace.

    OpenLineage's convention is ``<scheme>://<authority>`` for paths and
    ``<system>://<host>`` for catalogs. We use the storage format as the
    namespace when no URL-style location is available so the namespace is at
    least homogeneous per system.
    """
    if location and "://" in location:
        # `s3://bucket/path` → `s3://bucket`
        parts = location.split("://", 1)
        host = parts[1].split("/", 1)[0]
        return f"{parts[0]}://{host}"
    return storage_format or "hive"


def _attach_column_lineage(
    ds: dict[str, Any],
    df,
    inputs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the OpenLineage ``columnLineage`` facet from DerivationIRs."""
    if not df.derivations:
        return ds
    # The facet maps target-column name → {inputFields: [{namespace, name, field}]}
    fields: dict[str, dict[str, Any]] = {}
    for deriv in df.derivations:
        sources = []
        for src_col in deriv.source_columns:
            # We don't carry the source table per source-column on the IR; the
            # best we can do is to attach the first input dataset (cheap and
            # matches what most OL consumers expect for code-derived lineage).
            for inp in inputs:
                sources.append({
                    "namespace": inp["namespace"],
                    "name": inp["name"],
                    "field": src_col,
                })
                break
        fields[deriv.target_column] = {
            "inputFields": sources,
            "transformationDescription": deriv.formula or deriv.via,
            "transformationType": deriv.via.upper(),
        }
    facets = ds.setdefault("facets", {})
    facets["columnLineage"] = {
        "_producer": _PRODUCER,
        "_schemaURL": f"https://openlineage.io/spec/facets/{_OPENLINEAGE_VERSION}/ColumnLineageDatasetFacet.json",
        "fields": fields,
    }
    return ds


def _stable_run_id(script_id: str) -> str:
    """Deterministic UUID v5 derived from the script id — re-emitting the
    same script yields the same run_id so downstream dedupes idempotently.
    """
    return str(uuid.UUID(hex=hashlib.sha256(
        f"openlineage::run::{script_id}".encode("utf-8"),
    ).hexdigest()[:32]))
