"""Phase 3 — leaf-to-root attribute resolver (v2 plan §5 Stage 7).

Walks every ``Attribute`` in the IR backwards through:

  - alias chains (``UPPER(name) AS clean`` → ``clean`` derives from ``name``)
  - preceding LOAD statements (``RESIDENT`` → upstream dataset)
  - JOIN merges (the dependent table's attributes derive from both
    parents on the join key)
  - CONCATENATE unions (every concatenated table's attributes feed the
    target)
  - ``MAPPING LOAD`` + ``APPLYMAP`` (mapping-table lookups become
    ``MAPS_TO`` edges)
  - ``STORE INTO 'foo.qvd'`` → downstream-app reloads (cross-app
    stitching is free because :Dataset.qname collides on the qvd path)
  - embedded ``SQL SELECT`` column lineage (from :class:`sql_block.extract_column_lineage`)

The output is a list of :class:`LineageEdge` records carrying the canonical
``DERIVES_FROM`` / ``MAPS_TO`` / ``STORED_AS`` / ``REFERENCES_FK``
relationships the writer consumes. Confidence propagates multiplicatively
— a chain is only as confident as its weakest hop.

Cross-app stitching is free at this layer (shared :Dataset.id collides
deterministically through ``ids.dataset_qname``) — we just emit edges
to the upstream qname and let Neo4j's MERGE land them on the same node.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .ids import (
    attribute_qname,
    dataset_qname,
    physical_source_qname,
    sha256_id,
)
from .models import (
    Attribute,
    Diagnostic,
    LineageEdge,
    LoadStatement,
    QlikViewApp,
    SourceType,
)
from .sql_block import extract_column_lineage as _extract_col_lineage


# Edges produced by the resolver — always one of these (allow-list in
# the writer is the source of truth, this is a typed mirror).
REL_DERIVES_FROM = "DERIVES_FROM"
REL_STORED_AS = "STORED_AS"
REL_MAPS_TO = "MAPS_TO"
REL_REFERENCES_FK = "REFERENCES_FK"


@dataclass
class ResolverResult:
    edges: list[LineageEdge] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)


def resolve_lineage(app: QlikViewApp) -> ResolverResult:
    """Build the attribute-level DAG for ``app``.

    Returns a :class:`ResolverResult` the caller appends onto
    ``app.lineage_edges`` and ``app.diagnostics``. Idempotent — running
    twice on the same app produces the same edges (signed via the
    ``LineageEdge.sig`` property so the writer's MERGE collides).
    """
    result = ResolverResult()
    seen: set[tuple[str, str, str]] = set()    # (src_id, dst_id, sig) dedup

    def _add(edge: LineageEdge) -> None:
        key = (edge.src_id, edge.dst_id, edge.sig)
        if key in seen:
            return
        seen.add(key)
        result.edges.append(edge)

    # Index attributes by (dataset_qname, lowercased attribute name) so
    # joins / alias chains can find them quickly. QV field names are
    # case-sensitive in semantics but commonly typed inconsistently in
    # scripts — we lowercase the lookup key and let the qname carry the
    # original casing.
    attrs_by_ds_lc: dict[tuple[str, str], Attribute] = {}
    for a in app.attributes:
        attrs_by_ds_lc[(a.dataset, a.name.lower())] = a

    # Index loads by their target table name for RESIDENT / CONCATENATE
    # / JOIN backreferencing.
    loads_by_target: dict[str, LoadStatement] = {}
    for ld in app.loads:
        if ld.table_name:
            loads_by_target.setdefault(ld.table_name, ld)

    # ----- 1. Embedded-SQL column lineage --------------------------------
    # For every LOAD whose source is SQL, lift sqlglot's per-column
    # lineage and emit DERIVES_FROM from each Attribute to a synthetic
    # external-source Attribute on the PhysicalSource's table.
    for ld in app.loads:
        if ld.source_type != SourceType.SQL or not ld.sql_query:
            continue
        ds_q = dataset_qname(app.file_path, ld.table_name)
        col_lineage = _extract_col_lineage(ld.sql_query)
        for cl in col_lineage:
            this_attr_q = attribute_qname(ds_q, cl.alias)
            this_id = sha256_id(this_attr_q)
            # Synthesise an "external source attribute" qname rooted at
            # the FQN sqlglot returned. The dataset half is the source
            # table — without a containing-app marker so cross-parser
            # stitching collides when the same physical table is read
            # by, say, a Tableau workbook later.
            if cl.source_column:
                src_tbl = cl.source_table or ld.source_table or "?"
                src_ds_q = f"dataset::external::{src_tbl}"
                src_attr_q = attribute_qname(src_ds_q, cl.source_column)
                # Convention: ``dependent -[DERIVES_FROM]-> upstream``.
                # The dependent is the in-memory Attribute; the upstream
                # is the SQL-table column we just lifted.
                _add(LineageEdge(
                    src_id=this_id,
                    dst_id=sha256_id(src_attr_q),
                    rel=REL_DERIVES_FROM,
                    transform=":".join(cl.transform_chain) or None,
                    confidence=cl.confidence,
                    evidence=ld.sql_query[:120],
                ))

    # ----- 2. RESIDENT loads -------------------------------------------
    # ``LOAD * RESIDENT upstream`` — every attribute on this dataset
    # derives from the same-named attribute on the upstream. We ONLY
    # emit this edge when an upstream attribute of the same name
    # actually exists. Aliased loads (``LOAD EmpID AS EmployeeID
    # RESIDENT …``) are handled by the visitor's per-field-ref pass
    # which knows the source_expr's true identifiers — emitting a
    # same-name edge here would create phantom edges to non-existent
    # upstream attributes.
    upstream_attr_names: dict[str, set[str]] = {}
    for a in app.attributes:
        upstream_attr_names.setdefault(a.dataset, set()).add(a.name.lower())
    for ld in app.loads:
        if ld.source_type != SourceType.RESIDENT or not ld.source_table:
            continue
        this_ds_q = dataset_qname(app.file_path, ld.table_name)
        upstream_ds_q = dataset_qname(app.file_path, ld.source_table)
        upstream_names_lc = upstream_attr_names.get(upstream_ds_q, set())
        for a in app.attributes:
            if a.dataset != this_ds_q:
                continue
            if a.name.lower() not in upstream_names_lc:
                # Renamed or transformed — the visitor's per-ref pass has
                # already emitted the correct edge.
                continue
            upstream_attr_q = attribute_qname(upstream_ds_q, a.name)
            # Dependent → upstream.
            _add(LineageEdge(
                src_id=sha256_id(a.qname),
                dst_id=sha256_id(upstream_attr_q),
                rel=REL_DERIVES_FROM,
                transform="RESIDENT",
                confidence=0.95,
                evidence=f"RESIDENT {ld.source_table}",
            ))

    # ----- 3. JOIN / CONCATENATE merges --------------------------------
    # ``JOIN (target) LOAD … RESIDENT source`` — fields appearing on
    # BOTH tables get FK candidate edges; non-shared fields on the
    # source flow into the target.
    for join in app.joins:
        target_q = dataset_qname(app.file_path, join.target_table)
        source_q = dataset_qname(app.file_path, join.source_table)
        target_attrs = [a for a in app.attributes if a.dataset == target_q]
        source_attrs = [a for a in app.attributes if a.dataset == source_q]
        if not target_attrs or not source_attrs:
            continue
        target_names = {a.name.lower() for a in target_attrs}
        for sa in source_attrs:
            if sa.name.lower() in target_names:
                # Shared field → FK candidate (resolver-level signal;
                # the constraint engine also emits this from a different
                # angle. Idempotent dedup means we don't double-count.)
                target_match_q = attribute_qname(target_q, sa.name)
                _add(LineageEdge(
                    src_id=sha256_id(sa.qname),
                    dst_id=sha256_id(target_match_q),
                    rel=REL_REFERENCES_FK,
                    join_type=join.join_type,
                    join_keys=(sa.name,),
                    confidence=0.6,
                    evidence=f"JOIN {join.source_table} → {join.target_table}",
                ))
            else:
                # Non-shared field would flow through into the target —
                # but only if a matching target attribute actually exists
                # in the IR (the visitor's per-field-ref pass would have
                # created it if the join body projected the field with
                # an alias, e.g. ``DeptID AS Department``). Without that
                # check we'd emit a DERIVES_FROM edge whose dst is a
                # phantom attribute id that no node will ever back.
                target_attr_q = attribute_qname(target_q, sa.name)
                if target_attr_q not in {a.qname for a in target_attrs}:
                    continue
                # Dependent (target) → upstream (source).
                _add(LineageEdge(
                    src_id=sha256_id(target_attr_q),
                    dst_id=sha256_id(sa.qname),
                    rel=REL_DERIVES_FROM,
                    transform=f"JOIN:{join.join_type}",
                    join_type=join.join_type,
                    confidence=0.85,
                    evidence=f"JOIN {join.source_table} → {join.target_table}",
                ))

    for concat in app.concatenations:
        target_q = dataset_qname(app.file_path, concat.target_table)
        if not concat.source_table:
            continue
        source_q = dataset_qname(app.file_path, concat.source_table)
        for a in app.attributes:
            if a.dataset != source_q:
                continue
            target_attr_q = attribute_qname(target_q, a.name)
            # Dependent (target) → upstream (source).
            _add(LineageEdge(
                src_id=sha256_id(target_attr_q),
                dst_id=sha256_id(a.qname),
                rel=REL_DERIVES_FROM,
                transform="CONCATENATE",
                confidence=0.9,
                evidence=f"CONCATENATE INTO {concat.target_table}",
            ))

    # ----- 4. STORE → QVD links -----------------------------------------
    # A ``STORE table INTO 'qvd/foo.qvd' (qvd);`` produces a
    # PhysicalSource of kind='qvd' AND signals that the in-memory
    # Dataset is materialised. The dataset gets a STORED_AS edge.
    for src in app.physical_sources:
        if src.kind != "qvd":
            continue
        # The producing dataset shares the qvd's locator stem in the
        # declared_in field (visitor stores 'STORE <table> INTO <path>').
        owner_name = (src.declared_in or "").split("STORE", 1)[-1]
        owner_name = owner_name.strip().split(" ", 1)[0]
        owner_q = dataset_qname(app.file_path, owner_name) if owner_name else None
        if owner_q and any(d.qname == owner_q for d in app.datasets):
            _add(LineageEdge(
                src_id=sha256_id(owner_q),
                dst_id=sha256_id(src.qname),
                rel=REL_STORED_AS,
                transform="STORE",
                confidence=1.0,
                evidence=src.declared_in or "",
            ))

    # ----- 5. Mapping tables (APPLYMAP) ---------------------------------
    # ``MAPPING LOAD key, value RESIDENT lookup;`` flagged on the Load.
    # APPLYMAP references inside field expressions are detected by
    # scanning v0.1 ``app.fields[].formula`` for the function call.
    mapping_tables = {
        ld.table_name for ld in app.loads if ld.is_mapping
    }
    if mapping_tables:
        for f in app.fields:
            if not f.formula:
                continue
            up = f.formula.upper()
            if "APPLYMAP" not in up:
                continue
            # Pull the first quoted argument out of APPLYMAP('<name>', …).
            import re
            m = re.search(r"APPLYMAP\s*\(\s*'([^']+)'", f.formula, re.IGNORECASE)
            if not m:
                continue
            map_name = m.group(1)
            if map_name not in mapping_tables:
                continue
            # The mapped field becomes a MAPS_TO edge to the mapping
            # table. Field IDs in the v0.1 surface aren't qname'd, so
            # we synthesise an attribute reference for the v0.2 layer.
            field_q = attribute_qname(dataset_qname(app.file_path, "_synthetic"), f.name)
            map_ds_q = dataset_qname(app.file_path, map_name)
            _add(LineageEdge(
                src_id=sha256_id(field_q),
                dst_id=sha256_id(map_ds_q),
                rel=REL_MAPS_TO,
                transform="APPLYMAP",
                confidence=0.85,
                evidence=f.formula[:80],
            ))

    return result
