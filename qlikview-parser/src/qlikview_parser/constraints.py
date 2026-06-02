"""Phase 2 — constraint inference engine (v2 plan §5 Stage 6).

Multi-signal reconciliation. Each signal proposes ``KeyConstraint``
candidates with an explicit ``source`` tag + ``confidence`` so the
downstream graph can grade them rationally:

   Signal                Source tag         Confidence
   ─────────────────────────────────────────────────────
   QVD header hint       qvd_hint           0.7
   JOIN/KEEP ON keys     join_inferred      0.6
   Auto-association      naming_inferred    0.5
   QlikView synthetic    synthetic          (n/a — see below)
   Naming heuristic      naming_inferred    0.4
   (introspected)        introspected       1.0    ← OUT for v0.2

Conflicts are NOT resolved here — we emit every candidate the heuristics
find. The graph can rank them by confidence; the v3 introspection layer
will overwrite any candidate it can verify against a live catalog.

Synthetic-key special case: when ≥2 tables share ≥2 common field names,
QlikView's runtime fabricates a ``$Syn`` bridge table. We surface this
explicitly as a ``Diagnostic(QV-SYNKEY, warn)`` plus ``synthetic`` -kind
constraints on each participating table so the graph carries the
modelling smell.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable

from .ids import constraint_qname, dataset_qname
from .models import Attribute, Diagnostic, KeyConstraint, QlikViewApp
from .qvd_header import QvdHeader

# Patterns that suggest a column is a primary or foreign key purely on
# its name. Lowest confidence — used only when no better signal exists.
# CamelCase ``CustomerID`` and snake_case ``customer_id`` BOTH count, as
# does the synthetic-key convention ``%FOOKEY``.
_PK_NAME_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Anything ending in "ID" (with or without underscore separator),
    # but not bare "ID" alone — that has its own catch-all below.
    re.compile(r"^[A-Za-z_][A-Za-z0-9_]*ID$", re.IGNORECASE),
    re.compile(r"^[A-Za-z_][A-Za-z0-9_]*Key$", re.IGNORECASE),
    re.compile(r"^%[A-Za-z_][A-Za-z0-9_]*KEY$", re.IGNORECASE),
    re.compile(r"^ID$", re.IGNORECASE),
)


def infer_constraints(
    app: QlikViewApp,
    qvd_headers: dict[str, QvdHeader] | None = None,
) -> tuple[list[KeyConstraint], list[Diagnostic]]:
    """Run every heuristic + reconcile candidates.

    ``qvd_headers`` is an optional ``locator → QvdHeader`` map for any QVDs
    the script's lineage chain touches. When supplied, the QVD-hint signal
    fires for every field whose ``NoOfSymbols == NoOfRecords``.

    Returns (constraints, diagnostics). Caller is expected to extend
    ``app.key_constraints`` and ``app.diagnostics`` with these.
    """
    out: list[KeyConstraint] = []
    diagnostics: list[Diagnostic] = []
    seen: set[str] = set()   # dedup constraints by qname

    def _add(kc: KeyConstraint) -> None:
        if kc.qname in seen:
            return
        seen.add(kc.qname)
        out.append(kc)

    # Pre-index attributes by dataset qname for fast lookup.
    attrs_by_dataset: dict[str, list[Attribute]] = defaultdict(list)
    for a in app.attributes:
        attrs_by_dataset[a.dataset].append(a)

    dataset_qnames = {d.qname for d in app.datasets}

    # ----- Signal 1: QVD header hints (highest non-introspected) ------
    if qvd_headers:
        for locator, header in qvd_headers.items():
            ds_q = dataset_qname(app.file_path, header.table_name)
            for field in header.fields:
                if not field.is_likely_unique:
                    continue
                _add(KeyConstraint(
                    dataset=ds_q,
                    columns=(field.name,),
                    kind="unique",
                    references=None,
                    source="qvd_hint",
                    confidence=0.7,
                ))

    # ----- Signal 2: JOIN/KEEP ON keys → FK candidates ---------------
    # ``app.joins`` records (target_table, source_table, join_type). The
    # shared field that drives the join is the FK candidate — but the v0.1
    # IR doesn't capture explicit JOIN keys, so we approximate by looking
    # for fields that appear in BOTH tables' attribute lists.
    for join in app.joins:
        target_ds = _dataset_q_for_load(app, join.target_table)
        source_ds = _dataset_q_for_load(app, join.source_table)
        if not target_ds or not source_ds:
            continue
        shared = _shared_field_names(
            attrs_by_dataset.get(target_ds, []),
            attrs_by_dataset.get(source_ds, []),
        )
        for col in shared:
            # FK runs from the dependent table (target — the one being
            # joined onto an existing one) to the source.
            _add(KeyConstraint(
                dataset=target_ds,
                columns=(col,),
                kind="foreign",
                references=(source_ds, col),
                source="join_inferred",
                confidence=0.6,
            ))

    # ----- Signal 3: auto-association across tables -------------------
    # A field-name appearing in ≥2 datasets means QlikView would auto-
    # associate those tables. Each pair gets an FK candidate.
    field_to_datasets: dict[str, list[str]] = defaultdict(list)
    for ds_q, attrs in attrs_by_dataset.items():
        for a in attrs:
            field_to_datasets[a.name].append(ds_q)
    for field_name, ds_list in field_to_datasets.items():
        if len(set(ds_list)) < 2:
            continue
        # Emit one FK per (dataset, peer-dataset) pair so the graph carries
        # the candidate from every direction the user might trace.
        for i, this_ds in enumerate(ds_list):
            for peer_ds in ds_list:
                if peer_ds == this_ds:
                    continue
                _add(KeyConstraint(
                    dataset=this_ds,
                    columns=(field_name,),
                    kind="foreign",
                    references=(peer_ds, field_name),
                    source="naming_inferred",
                    confidence=0.5,
                ))

    # ----- Signal 4: synthetic-key ($Syn) detection ------------------
    # QlikView fabricates a synthetic key table when ≥2 tables share ≥2
    # common fields. Flag this explicitly — it's a modelling smell and a
    # frequent cause of runtime data-model bugs.
    pair_to_shared: dict[tuple[str, str], list[str]] = defaultdict(list)
    for field_name, ds_list in field_to_datasets.items():
        uniq = sorted(set(ds_list))
        if len(uniq) < 2:
            continue
        for i in range(len(uniq)):
            for j in range(i + 1, len(uniq)):
                pair_to_shared[(uniq[i], uniq[j])].append(field_name)
    for (a_ds, b_ds), shared_fields in pair_to_shared.items():
        if len(shared_fields) < 2:
            continue
        # Both tables get a ``synthetic`` constraint over the full set of
        # shared fields, signalling they'd be bridged by $Syn at runtime.
        for ds in (a_ds, b_ds):
            _add(KeyConstraint(
                dataset=ds,
                columns=tuple(sorted(shared_fields)),
                kind="synthetic",
                references=None,
                source="join_inferred",   # functionally the same signal
                confidence=0.55,
            ))
        diagnostics.append(Diagnostic(
            level="warn",
            code="QV-SYNKEY",
            message=(
                f"Synthetic-key risk: tables share {len(shared_fields)} common "
                f"fields ({', '.join(sorted(shared_fields))!s}). QlikView would "
                f"fabricate a $Syn bridge between them at runtime. Consider "
                f"renaming or QUALIFY-ing."
            ),
            artifact=app.file_path,
            line=None,
        ))

    # ----- Signal 5: naming heuristics --------------------------------
    # Lowest-confidence catch-all for fields that look like keys by name
    # alone. Only fires when no stronger candidate already covers the
    # column (the seen-set dedup at the head of this function handles it).
    for ds_q, attrs in attrs_by_dataset.items():
        if ds_q not in dataset_qnames:
            continue
        for a in attrs:
            if any(p.match(a.name) for p in _PK_NAME_PATTERNS):
                _add(KeyConstraint(
                    dataset=ds_q,
                    columns=(a.name,),
                    kind="primary",
                    references=None,
                    source="naming_inferred",
                    confidence=0.4,
                ))

    return out, diagnostics


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dataset_q_for_load(app: QlikViewApp, table_name: str) -> str | None:
    """Find the Dataset.qname for a QlikView table name. Returns None if
    the table isn't in the app's datasets list (which is normal — JOIN
    targets sometimes reference temp tables we didn't materialise as
    datasets, e.g. RESIDENT loads against a stream of LOAD bodies)."""
    if not table_name:
        return None
    for d in app.datasets:
        if d.name == table_name:
            return d.qname
    # Fall back to building the qname directly so JOIN/KEEP heuristics
    # still emit something useful when the matching Dataset wasn't
    # produced (Phase 2's grammar will keep widening to cover more).
    return dataset_qname(app.file_path, table_name)


def _shared_field_names(
    left: Iterable[Attribute], right: Iterable[Attribute],
) -> list[str]:
    """Case-sensitive intersection of two attribute lists' names."""
    L = {a.name for a in left}
    R = {a.name for a in right}
    return sorted(L & R)
