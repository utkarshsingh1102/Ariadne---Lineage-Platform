"""Translate a `WorkbookIR` into batched Cypher MERGE statements.

`GraphWriter(driver).write_workbook(ir)` is the public entrypoint. Pass a
`neo4j.Driver` (real or mock); `.ensure_constraints()` applies the contract
constraints idempotently.
"""

from __future__ import annotations

import json
from typing import Iterable

from neo4j import Driver, Session

from tableau_parser.config import settings
from tableau_parser.graph import queries
from tableau_parser.models.domain import WorkbookIR


class GraphWriter:
    def __init__(self, driver: Driver, database: str | None = None):
        self.driver = driver
        self.database = database or settings.neo4j_database

    # ------------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------------

    def ensure_constraints(self) -> None:
        with self._session() as s:
            for stmt in _CONSTRAINTS:
                s.run(stmt).consume()

    def write_workbook(self, ir: WorkbookIR, overwrite: bool = False) -> dict[str, int]:
        with self._session() as s:
            if overwrite:
                s.execute_write(
                    lambda tx: tx.run(queries.DELETE_WORKBOOK_SUBGRAPH, workbook_id=ir.id)
                )
            self._write_nodes(s, ir)
            self._write_relationships(s, ir)
        return {"nodes_written": _count_nodes(ir)}

    # ------------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------------

    def _session(self) -> Session:
        return self.driver.session(database=self.database)

    def _write_nodes(self, s: Session, ir: WorkbookIR) -> None:
        _batched(s, queries.MERGE_WORKBOOK, [{
            "id": ir.id,
            "name": ir.name,
            "file_path": ir.file_path,
            "version": ir.version,
            "parsed_at": ir.parsed_at,
            "line": ir.line,
            "line_end": ir.line_end,
        }])

        # Connections — flatten across all datasources, dedup by id. Line
        # is intentionally stored on the CONNECTS_VIA / WRITES_TO_CONNECTION
        # edges (handled in _write_relationships), not on the shared
        # :Connection node which is MERGEd across scripts.
        connections: dict[str, dict] = {}
        for d in ir.datasources:
            for c in d.connections:
                connections.setdefault(c.id, {
                    "id": c.id, "class": c.klass, "server": c.server,
                    "dbname": c.dbname, "schema": c.schema, "port": c.port,
                    "username": c.username,
                })
        _batched(s, queries.MERGE_CONNECTION, list(connections.values()),
                 parser=queries.PARSER_NAME)

        _batched(s, queries.MERGE_DATASOURCE, [{
            "id": d.id, "name": d.name, "caption": d.caption,
            "is_federated": d.is_federated, "has_extract": d.has_extract,
            "workbook_id": d.workbook_id,
            "line": d.line,
        } for d in ir.datasources])

        # Tables — dedup across datasources. Same rationale as connections:
        # line goes on the READS_TABLE edge, not the shared :Table node.
        tables: dict[str, dict] = {}
        for d in ir.datasources:
            for t in d.tables:
                tables.setdefault(t.id, {
                    "id": t.id, "name": t.name, "schema": t.schema,
                    "database": t.database,
                    "fully_qualified_name": t.fully_qualified_name,
                    "source_type": t.source_type,
                    "raw_sql": t.raw_sql,
                })
        _batched(s, queries.MERGE_TABLE, list(tables.values()),
                 parser=queries.PARSER_NAME)

        # Attributes can be physical (FQN-derived id, shared across scripts
        # via HAS_COLUMN) or calculated (datasource-scoped id, script-owned).
        # We push line on the node and use coalesce in MERGE_ATTRIBUTE so a
        # physical column already written from another script keeps its own
        # earliest-known line.
        attributes: dict[str, dict] = {}
        for d in ir.datasources:
            for f in d.fields:
                # value_aliases is a Python dict; Neo4j can't store maps as
                # a single property so we JSON-encode for round-trip
                # parity with how the Spark parser persists transform_chain.
                aliases_json = json.dumps(f.value_aliases) if f.value_aliases else ""
                attributes.setdefault(f.id, {
                    "id": f.id, "name": f.name, "datatype": f.datatype,
                    "role": f.role, "is_calculated": f.is_calculated,
                    "formula": f.formula,
                    "line": f.line,
                    "default_aggregation": f.default_aggregation,
                    "ordinal": f.ordinal,
                    "precision": f.precision,
                    "scale": f.scale,
                    "contains_null": f.contains_null,
                    "value_aliases": aliases_json,
                })
        _batched(s, queries.MERGE_ATTRIBUTE, list(attributes.values()))

        _batched(s, queries.MERGE_WORKSHEET, [{
            "id": w.id, "name": w.name, "workbook_id": w.workbook_id,
            "line": w.line, "line_end": w.line_end,
        } for w in ir.worksheets])

        _batched(s, queries.MERGE_DASHBOARD, [{
            "id": d.id, "name": d.name, "workbook_id": d.workbook_id,
            "line": d.line, "line_end": d.line_end,
        } for d in ir.dashboards])

        _batched(s, queries.MERGE_PARAMETER, [{
            "id": p.id, "name": p.name, "datatype": p.datatype,
            "current_value": p.current_value, "workbook_id": p.workbook_id,
            "line": p.line,
        } for p in ir.parameters])

        # Improvement-v2 §4 — synthetic :TableauParameterScope node + edges.
        _batched(s, queries.MERGE_PARAMETER_SCOPE, [{
            "id": ps.id, "name": ps.name, "workbook_id": ps.workbook_id,
            "line": ps.line,
        } for ps in ir.parameter_scopes])
        _batched(s, queries.HAS_PARAMETER_SCOPE, [
            {"workbook_id": ir.id, "scope_id": ps.id}
            for ps in ir.parameter_scopes
        ])
        _batched(s, queries.SCOPE_HAS_PARAMETER, [
            {"scope_id": p.scope_id, "parameter_id": p.id}
            for p in ir.parameters if p.scope_id
        ])

        zone_rows: list[dict] = []
        for d in ir.dashboards:
            for z in d.zones:
                zone_rows.append({
                    "id": z.id, "kind": z.kind, "name": z.name,
                    "target_worksheet": z.target_worksheet,
                    "target_parameter": z.target_parameter,
                    "dashboard_id": z.dashboard_id, "line": z.line,
                })
        _batched(s, queries.MERGE_DASHBOARD_ZONE, zone_rows)

        # Step 6 — derived-field families. One batch per family.
        group_rows: list[dict] = []
        set_rows: list[dict] = []
        bin_rows: list[dict] = []
        hier_rows: list[dict] = []
        for d in ir.datasources:
            for g in d.groups:
                group_rows.append({
                    "id": g.id, "name": g.name,
                    "datasource_id": g.datasource_id, "line": g.line,
                })
            for st in d.sets:
                set_rows.append({
                    "id": st.id, "name": st.name,
                    "datasource_id": st.datasource_id,
                    "condition_expr": st.condition_expr, "line": st.line,
                })
            for b in d.bins:
                bin_rows.append({
                    "id": b.id, "name": b.name,
                    "datasource_id": b.datasource_id,
                    "size": b.size, "line": b.line,
                })
            for h in d.hierarchies:
                hier_rows.append({
                    "id": h.id, "name": h.name,
                    "datasource_id": h.datasource_id, "line": h.line,
                })
        _batched(s, queries.MERGE_TABLEAU_GROUP, group_rows)
        _batched(s, queries.MERGE_TABLEAU_SET, set_rows)
        _batched(s, queries.MERGE_TABLEAU_BIN, bin_rows)
        _batched(s, queries.MERGE_TABLEAU_HIERARCHY, hier_rows)

    def _write_relationships(self, s: Session, ir: WorkbookIR) -> None:
        _batched(s, queries.CONTAINS_DATASOURCE, [
            {"workbook_id": ir.id, "datasource_id": d.id} for d in ir.datasources
        ])

        ds_conn_pairs: list[dict] = []
        for d in ir.datasources:
            for c in d.connections:
                ds_conn_pairs.append({
                    "datasource_id": d.id, "connection_id": c.id,
                    "line": c.line,
                })
        _batched(s, queries.CONNECTS_VIA, ds_conn_pairs)

        # Resolve table_id → fully_qualified_name so cross-parser MERGE
        # (which keys on FQN, not id) lands the edges on the right node even
        # when another parser created the :Table with a different `id`.
        fqn_by_table_id: dict[str, str] = {}
        for d in ir.datasources:
            for t in d.tables:
                fqn_by_table_id[t.id] = t.fully_qualified_name

        reads: list[dict] = []
        for d in ir.datasources:
            for r in d.reads_tables:
                fqn = fqn_by_table_id.get(r.table_id)
                if not fqn:
                    continue
                reads.append({"datasource_id": r.datasource_id,
                              "fully_qualified_name": fqn,
                              "relation_type": r.relation_type,
                              "line": r.line})
        _batched(s, queries.READS_TABLE, reads)

        has_field: list[dict] = []
        for d in ir.datasources:
            for f in d.fields:
                has_field.append({"datasource_id": d.id, "field_id": f.id})
        _batched(s, queries.HAS_FIELD, has_field)

        has_col: list[dict] = []
        for d in ir.datasources:
            for h in d.has_columns:
                fqn = fqn_by_table_id.get(h.table_id)
                if not fqn:
                    continue
                has_col.append({"fully_qualified_name": fqn, "field_id": h.field_id})
        _batched(s, queries.HAS_COLUMN, has_col)

        derives: list[dict] = []
        derive_refs: list[dict] = []
        for d in ir.datasources:
            name_to_id = {f.name: f.id for f in d.fields}
            for e in d.derives_from:
                from_id = name_to_id.get(e.target_field)
                if not from_id:
                    continue
                for src_name in e.source_fields:
                    to_id = name_to_id.get(src_name)
                    if not to_id:
                        continue
                    derives.append({"from_id": from_id, "to_id": to_id,
                                    "formula": e.formula, "line": e.line})
                # Per-occurrence DERIVES_FROM_REF rows. Resolved refs land
                # on the same target as the DERIVES_FROM edge; unresolved
                # ones are dropped here because there's no Attribute to MATCH.
                # Cross-source refs whose datasource isn't in the workbook
                # are dropped for the same reason — they can be revived once
                # cross-datasource resolution lands.
                for ref in e.refs:
                    src_id = name_to_id.get(ref.source_name)
                    if not src_id:
                        continue
                    derive_refs.append({
                        "from_id": from_id, "to_id": src_id,
                        "char_start": ref.char_start,
                        "char_end": ref.char_end,
                        "kind": ref.kind,
                        "line": e.line,
                        "datasource_name": ref.datasource_name,
                    })
        _batched(s, queries.DERIVES_FROM, derives)
        _batched(s, queries.DERIVES_FROM_REF, derive_refs)

        _batched(s, queries.CONTAINS_WORKSHEET, [
            {"workbook_id": ir.id, "worksheet_id": w.id} for w in ir.worksheets
        ])
        _batched(s, queries.CONTAINS_DASHBOARD, [
            {"workbook_id": ir.id, "dashboard_id": d.id} for d in ir.dashboards
        ])

        # USES_FIELD — resolve field name within its datasource
        usages: list[dict] = []
        ds_name_to_id = {d.name: d.id for d in ir.datasources}
        ds_fields_by_name: dict[str, dict[str, str]] = {
            d.id: {f.name: f.id for f in d.fields} for d in ir.datasources
        }
        for w in ir.worksheets:
            for u in w.field_usages:
                ds_id = ds_name_to_id.get(u.datasource_name)
                if not ds_id:
                    continue
                fid = ds_fields_by_name.get(ds_id, {}).get(u.field_name)
                if not fid:
                    continue
                usages.append({"worksheet_id": w.id, "field_id": fid,
                               "shelf": u.shelf, "line": u.line,
                               "aggregation": u.aggregation})
        _batched(s, queries.USES_FIELD, usages)

        # FILTERS_BY — Worksheet → Attribute (worksheet-scoped filters) and
        # Datasource → Attribute (datasource-wide filters). Same IR shape
        # routed to two different Cypher templates by checking worksheet_id.
        ws_filters: list[dict] = []
        for w in ir.worksheets:
            for fil in w.filters:
                ds_id = ds_name_to_id.get(fil.datasource_name)
                if not ds_id:
                    continue
                fid = ds_fields_by_name.get(ds_id, {}).get(fil.field_name)
                if not fid:
                    continue
                ws_filters.append({
                    "worksheet_id": w.id, "field_id": fid,
                    "filter_class": fil.filter_class,
                    "expression": fil.expression, "line": fil.line,
                })
        _batched(s, queries.FILTERS_BY_WORKSHEET, ws_filters)

        ds_filters: list[dict] = []
        for d in ir.datasources:
            for fil in d.filters:
                fid = ds_fields_by_name.get(d.id, {}).get(fil.field_name)
                if not fid:
                    continue
                ds_filters.append({
                    "datasource_id": d.id, "field_id": fid,
                    "filter_class": fil.filter_class,
                    "expression": fil.expression, "line": fil.line,
                })
        _batched(s, queries.FILTERS_BY_DATASOURCE, ds_filters)

        # SORTS_BY — Worksheet → Attribute, keyed by direction so a single
        # field can support both an ascending and descending sort role.
        sorts: list[dict] = []
        for w in ir.worksheets:
            for srt in w.sorts:
                ds_id = ds_name_to_id.get(srt.datasource_name)
                if not ds_id:
                    continue
                fid = ds_fields_by_name.get(ds_id, {}).get(srt.field_name)
                if not fid:
                    continue
                sorts.append({
                    "worksheet_id": w.id, "field_id": fid,
                    "direction": srt.direction, "line": srt.line,
                })
        _batched(s, queries.SORTS_BY, sorts)

        # DISPLAYS_WORKSHEET — resolve worksheet name → id (workbook-scoped)
        ws_name_to_id = {w.name: w.id for w in ir.worksheets}
        displays = []
        for d in ir.dashboards:
            for wname in d.displayed_worksheets:
                wsid = ws_name_to_id.get(wname)
                if not wsid:
                    continue
                displays.append({"dashboard_id": d.id, "worksheet_id": wsid})
        _batched(s, queries.DISPLAYS_WORKSHEET, displays)

        _batched(s, queries.HAS_PARAMETER, [
            {"workbook_id": ir.id, "parameter_id": p.id} for p in ir.parameters
        ])

        # HAS_ZONE / CONTROLS_PARAMETER / FILTERS_VIA_ACTION / SETS_PARAMETER
        # — dashboard internals (plan §5).
        param_name_to_id = {p.name: p.id for p in ir.parameters}
        has_zone_rows: list[dict] = []
        controls_param_rows: list[dict] = []
        for d in ir.dashboards:
            for z in d.zones:
                has_zone_rows.append({
                    "dashboard_id": d.id, "zone_id": z.id, "line": z.line,
                })
                if z.target_parameter and z.target_parameter in param_name_to_id:
                    controls_param_rows.append({
                        "zone_id": z.id,
                        "parameter_id": param_name_to_id[z.target_parameter],
                        "line": z.line,
                    })
        _batched(s, queries.HAS_ZONE, has_zone_rows)
        _batched(s, queries.CONTROLS_PARAMETER, controls_param_rows)

        # Resolve action source/target sheet names → worksheet ids.
        filter_action_rows: list[dict] = []
        sets_param_rows: list[dict] = []
        for d in ir.dashboards:
            for act in d.actions:
                src_ids = [ws_name_to_id[s] for s in act.source_sheets
                           if s in ws_name_to_id]
                tgt_ids = [ws_name_to_id[t] for t in act.target_sheets
                           if t in ws_name_to_id]
                if act.kind in {"filter", "highlight"}:
                    # One row per (source, target) pair. Tableau actions
                    # commonly have a single source and multiple targets;
                    # we materialise the cross-product so the edges fan out
                    # the same way the runtime does.
                    for src in src_ids:
                        for tgt in tgt_ids:
                            filter_action_rows.append({
                                "source_worksheet_id": src,
                                "target_worksheet_id": tgt,
                                "kind": act.kind,
                                "action_id": act.id,
                                "fields": act.fields,
                                "line": act.line,
                            })
                elif act.kind == "parameter":
                    pid = param_name_to_id.get(act.parameter_name)
                    if pid:
                        for src in src_ids:
                            sets_param_rows.append({
                                "source_worksheet_id": src,
                                "parameter_id": pid,
                                "action_id": act.id,
                                "line": act.line,
                            })
        _batched(s, queries.FILTERS_VIA_ACTION, filter_action_rows)
        _batched(s, queries.SETS_PARAMETER, sets_param_rows)

        # Step 6 — derived-field containment + lineage edges.
        has_group: list[dict] = []
        has_set: list[dict] = []
        has_bin: list[dict] = []
        has_hier: list[dict] = []
        derived_lineage: list[dict] = []
        has_level: list[dict] = []
        for d in ir.datasources:
            field_id_by_name = ds_fields_by_name.get(d.id, {})
            for g in d.groups:
                has_group.append({
                    "datasource_id": d.id, "group_id": g.id,
                })
                for src_name in g.source_field_names:
                    fid = field_id_by_name.get(src_name)
                    if fid:
                        derived_lineage.append({
                            "derived_id": g.id, "field_id": fid,
                            "kind": "group", "line": g.line,
                        })
            for st in d.sets:
                has_set.append({
                    "datasource_id": d.id, "set_id": st.id,
                })
                for src_name in st.source_field_names:
                    fid = field_id_by_name.get(src_name)
                    if fid:
                        derived_lineage.append({
                            "derived_id": st.id, "field_id": fid,
                            "kind": "set", "line": st.line,
                        })
            for b in d.bins:
                has_bin.append({
                    "datasource_id": d.id, "bin_id": b.id,
                })
                for src_name in b.source_field_names:
                    fid = field_id_by_name.get(src_name)
                    if fid:
                        derived_lineage.append({
                            "derived_id": b.id, "field_id": fid,
                            "kind": "bin", "line": b.line,
                        })
            for h in d.hierarchies:
                has_hier.append({
                    "datasource_id": d.id, "hierarchy_id": h.id,
                })
                for ordinal, lvl in enumerate(h.levels):
                    fid = field_id_by_name.get(lvl)
                    if fid:
                        has_level.append({
                            "hierarchy_id": h.id, "field_id": fid,
                            "ordinal": ordinal,
                        })
        _batched(s, queries.HAS_GROUP, has_group)
        _batched(s, queries.HAS_SET, has_set)
        _batched(s, queries.HAS_BIN, has_bin)
        _batched(s, queries.HAS_HIERARCHY, has_hier)
        _batched(s, queries.DERIVES_FROM_DERIVED, derived_lineage)
        _batched(s, queries.HAS_LEVEL, has_level)

        # Improvement-v2 §6 — DERIVES_FROM_CROSS_DS edges. We pre-compute
        # the set of :Parameter ids so we can route each row to the right
        # MERGE template (the source label changes when the foreign
        # reference is a Parameter vs an Attribute).
        param_ids = {p.id for p in ir.parameters}
        cross_to_attr: list[dict] = []
        cross_to_param: list[dict] = []
        for r in ir.cross_ds_refs:
            row = {
                "id": r.id,
                "target_field_id": r.target_field_id,
                "source_field_id": r.source_field_id,
                "source_datasource_name": r.source_datasource_name,
                "char_start": r.char_start,
                "char_end": r.char_end,
                "formula_snippet": r.formula_snippet,
            }
            if r.source_field_id in param_ids:
                cross_to_param.append(row)
            else:
                cross_to_attr.append(row)
        _batched(s, queries.DERIVES_FROM_CROSS_DS_TO_ATTR, cross_to_attr)
        _batched(s, queries.DERIVES_FROM_CROSS_DS_TO_PARAM, cross_to_param)

        # Improvement-v2 §9 — :WorksheetBlend nodes + HAS_BLEND / BLENDS_WITH
        # edges. Datasource names in blend declarations need resolving to
        # ids; build a name → id map first.
        ds_id_by_name = {d.name: d.id for d in ir.datasources}
        blend_rows: list[dict] = []
        has_blend: list[dict] = []
        blends_with: list[dict] = []
        for w in ir.worksheets:
            for bl in w.blends:
                blend_rows.append({
                    "id": bl.id,
                    "worksheet_id": bl.worksheet_id,
                    "primary_datasource_name": bl.primary_datasource_name,
                    "secondary_datasource_name": bl.secondary_datasource_name,
                    "on_field_names": list(bl.on_field_names),
                    "line": bl.line,
                })
                has_blend.append({"worksheet_id": w.id, "id": bl.id})
                pid = ds_id_by_name.get(bl.primary_datasource_name)
                sid = ds_id_by_name.get(bl.secondary_datasource_name)
                if pid:
                    blends_with.append({
                        "blend_id": bl.id, "datasource_id": pid, "role": "primary",
                    })
                if sid:
                    blends_with.append({
                        "blend_id": bl.id, "datasource_id": sid, "role": "secondary",
                    })
        _batched(s, queries.MERGE_WORKSHEET_BLEND, blend_rows)
        _batched(s, queries.HAS_BLEND, has_blend)
        _batched(s, queries.BLENDS_WITH, blends_with)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _batched(s: Session, cypher: str, rows: list[dict], **kwargs) -> None:
    if not rows:
        return
    n = settings.batch_size
    for chunk in _chunks(rows, n):
        s.execute_write(lambda tx, c=chunk: tx.run(cypher, rows=c, **kwargs).consume())


def _chunks(rows: list[dict], n: int) -> Iterable[list[dict]]:
    for i in range(0, len(rows), n):
        yield rows[i : i + n]


def _count_nodes(ir: WorkbookIR) -> int:
    n = 1 + len(ir.datasources) + len(ir.worksheets) + len(ir.dashboards) + len(ir.parameters)
    for d in ir.datasources:
        n += len(d.connections) + len(d.tables) + len(d.fields)
    return n


# ---------------------------------------------------------------------------
# Constraints — mirrors lineage-contracts/schema/neo4j-constraints.cypher
# Kept in code so `GraphWriter.ensure_constraints()` is callable in a fresh DB.
# ---------------------------------------------------------------------------

_CONSTRAINTS = [
    "CREATE CONSTRAINT tableau_workbook_id IF NOT EXISTS FOR (w:TableauWorkbook) REQUIRE w.id IS UNIQUE",
    "CREATE CONSTRAINT tableau_datasource_id IF NOT EXISTS FOR (d:TableauDatasource) REQUIRE d.id IS UNIQUE",
    "CREATE CONSTRAINT tableau_worksheet_id IF NOT EXISTS FOR (w:TableauWorksheet) REQUIRE w.id IS UNIQUE",
    "CREATE CONSTRAINT tableau_dashboard_id IF NOT EXISTS FOR (d:TableauDashboard) REQUIRE d.id IS UNIQUE",
    "CREATE CONSTRAINT dashboard_zone_id IF NOT EXISTS FOR (z:DashboardZone) REQUIRE z.id IS UNIQUE",
    "CREATE CONSTRAINT tableau_group_id IF NOT EXISTS FOR (g:TableauGroup) REQUIRE g.id IS UNIQUE",
    "CREATE CONSTRAINT tableau_set_id IF NOT EXISTS FOR (s:TableauSet) REQUIRE s.id IS UNIQUE",
    "CREATE CONSTRAINT tableau_bin_id IF NOT EXISTS FOR (b:TableauBin) REQUIRE b.id IS UNIQUE",
    "CREATE CONSTRAINT tableau_hierarchy_id IF NOT EXISTS FOR (h:TableauHierarchy) REQUIRE h.id IS UNIQUE",
    "CREATE CONSTRAINT parameter_id IF NOT EXISTS FOR (p:Parameter) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT tableau_parameter_scope_id IF NOT EXISTS FOR (s:TableauParameterScope) REQUIRE s.id IS UNIQUE",
    "CREATE CONSTRAINT worksheet_blend_id IF NOT EXISTS FOR (b:WorksheetBlend) REQUIRE b.id IS UNIQUE",
    "CREATE CONSTRAINT connection_id IF NOT EXISTS FOR (c:Connection) REQUIRE c.id IS UNIQUE",
    "CREATE CONSTRAINT table_fqn IF NOT EXISTS FOR (t:Table) REQUIRE t.fully_qualified_name IS UNIQUE",
    "CREATE CONSTRAINT attribute_id IF NOT EXISTS FOR (a:Attribute) REQUIRE a.id IS UNIQUE",
]
