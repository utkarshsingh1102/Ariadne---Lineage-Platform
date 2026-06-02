"""Resolve calculated-field formula dependencies inside a single datasource."""

from __future__ import annotations

from tableau_parser.models.domain import (
    CrossDatasourceRefIR,
    DatasourceIR,
    DerivesFromIR,
    FormulaRefIR,
    ParameterIR,
)
from tableau_parser.utils.brackets import (
    find_cross_source_refs,
    find_lod_dimensions,
    find_refs,
    find_refs_with_spans,
)
from tableau_parser.utils.ids import cross_ds_ref_id


def resolve_dependencies(datasource: DatasourceIR) -> list[DerivesFromIR]:
    """For each calculated field, link it to the fields its formula references.

    Scoping is per-datasource — refs are resolved against `datasource.fields`
    only. String literals (`'High'`, `'Mid'`, …) are never refs because they're
    not bracketed. Cyclic dependencies are dropped (both edges of the cycle).
    Unresolved references are silently skipped.
    """
    by_name = {f.name: f for f in datasource.fields}
    edges: list[DerivesFromIR] = []

    for calc in datasource.fields:
        if not calc.is_calculated or not calc.formula:
            continue
        seen: list[str] = []
        seen_set: set[str] = set()
        for ref in find_refs(calc.formula):
            if ref == calc.name:
                continue  # self-ref — ignore
            if ref not in by_name:
                continue  # unresolved
            if ref in seen_set:
                continue
            seen_set.add(ref)
            seen.append(ref)
        if not seen:
            continue
        edges.append(
            DerivesFromIR(
                target_field=calc.name,
                source_fields=seen,
                datasource_id=datasource.id,
                formula=calc.formula,
                # The calc field was stamped from its <calculation> child; reuse
                # that line so the dependency edge can scroll to the formula.
                line=calc.line,
                refs=_collect_refs(calc.formula, calc.name),
            )
        )

    return _drop_cycles(edges)


def _collect_refs(formula: str, target_name: str) -> list[FormulaRefIR]:
    """Token-level decomposition of a calc formula.

    The order of operations matters here: we identify LOD-dimension refs
    and cross-source refs FIRST and remember their char ranges, so the
    plain `[bracket]` sweep can demote any ref that's already been
    categorised. This avoids double-counting `[a]` inside `{FIXED [a]:...}`
    as both ``lod_dim`` and a plain ``field``.
    """
    out: list[FormulaRefIR] = []
    consumed_spans: set[tuple[int, int]] = set()

    # 1. LOD dimensions
    for kind, name, start, end in find_lod_dimensions(formula):
        if name == target_name:
            continue  # self-ref
        out.append(FormulaRefIR(
            source_name=name, char_start=start, char_end=end, kind="lod_dim",
        ))
        consumed_spans.add((start, end))

    # 2. Cross-source refs (whole `[ds].[field]` span is recorded as one ref)
    for ds_name, field_name, start, end in find_cross_source_refs(formula):
        if field_name == target_name:
            continue
        out.append(FormulaRefIR(
            source_name=field_name,
            datasource_name=ds_name,
            char_start=start, char_end=end,
            kind="cross_source",
        ))
        # The two-part span subsumes its inner two single-bracket refs.
        # Mark BOTH so the plain sweep below skips them.
        for inner in find_refs_with_spans(formula[start:end]):
            inner_start = start + inner[1]
            inner_end = start + inner[2]
            consumed_spans.add((inner_start, inner_end))

    # 3. Plain bracket refs — anything not already consumed.
    for name, start, end in find_refs_with_spans(formula):
        if (start, end) in consumed_spans:
            continue
        if name == target_name:
            continue
        out.append(FormulaRefIR(
            source_name=name, char_start=start, char_end=end, kind="field",
        ))

    # Stable order: by char_start so a UI iterating refs gets them in
    # the order they appear in the formula.
    out.sort(key=lambda r: (r.char_start, r.char_end))
    return out


def resolve_cross_source_refs(
    datasources: list[DatasourceIR],
    parameters: list[ParameterIR],
) -> list[CrossDatasourceRefIR]:
    """Workbook-level post-pass for resolved cross-datasource refs.

    Each datasource's ``DerivesFromIR.refs`` already carry
    ``kind='cross_source'`` rows with the foreign datasource *name* and
    field *name*. This pass turns each into a resolved (target_id,
    source_id) pair so the writer can emit a real Cypher edge.

    Symbol table layout:
      symbols[ds.name]            -> {field_name: field_id}
      symbols['Parameters']        -> {parameter_name: parameter_id}

    Defensive: §6 says reference resolution must be datasource-scoped to
    disambiguate name collisions like ``net_amount`` appearing in two
    datasources. The per-datasource ``resolve_dependencies`` already
    enforces that within a single datasource; this pass extends the same
    discipline across datasources.
    """
    symbols: dict[str, dict[str, str]] = {
        ds.name: {f.name: f.id for f in ds.fields} for ds in datasources
    }
    # Parameters resolve under their literal scope name ``Parameters``.
    if parameters:
        symbols["Parameters"] = {p.name: p.id for p in parameters}

    out: list[CrossDatasourceRefIR] = []
    seen: set[str] = set()
    for ds in datasources:
        # Build a target lookup: calc field name -> field id (within this DS).
        calc_id_by_name = {f.name: f.id for f in ds.fields if f.is_calculated}
        for edge in ds.derives_from:
            target_id = calc_id_by_name.get(edge.target_field)
            if not target_id:
                continue
            for ref in edge.refs:
                if ref.kind != "cross_source":
                    continue
                foreign = symbols.get(ref.datasource_name, {})
                source_id = foreign.get(ref.source_name)
                if not source_id:
                    continue  # unresolved foreign ref — silently skip
                rid = cross_ds_ref_id(target_id, source_id, ref.char_start)
                if rid in seen:
                    continue
                seen.add(rid)
                snippet = edge.formula[ref.char_start:ref.char_end]
                out.append(CrossDatasourceRefIR(
                    id=rid,
                    target_field_id=target_id,
                    source_field_id=source_id,
                    source_datasource_name=ref.datasource_name,
                    char_start=ref.char_start,
                    char_end=ref.char_end,
                    formula_snippet=snippet,
                ))
    return out


def _drop_cycles(edges: list[DerivesFromIR]) -> list[DerivesFromIR]:
    """Remove edges whose target participates in a cycle."""
    adj: dict[str, set[str]] = {}
    for e in edges:
        adj.setdefault(e.target_field, set()).update(e.source_fields)

    BLACK, GRAY = 1, 2
    color: dict[str, int] = {}
    cycle_nodes: set[str] = set()

    def dfs(node: str, stack: list[str]) -> None:
        color[node] = GRAY
        stack.append(node)
        for nxt in adj.get(node, ()):
            if color.get(nxt) == GRAY:
                if nxt in stack:
                    cycle_nodes.update(stack[stack.index(nxt):])
            elif color.get(nxt) is None:
                dfs(nxt, stack)
        stack.pop()
        color[node] = BLACK

    for n in list(adj.keys()):
        if color.get(n) is None:
            dfs(n, [])

    return [e for e in edges if e.target_field not in cycle_nodes]
