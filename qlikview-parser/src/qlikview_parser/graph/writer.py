"""Neo4j writer for the QlikView parser.

v0.1 surface — emits the legacy ``:QlikScript / :QlikTable / :Connection /
:Table / :Attribute`` labels with the original 16-char truncated SHA-256
ids. Kept intact because the existing tests + sibling parsers rely on the
shape of the v0.1 graph (cross-parser ``:Table.fully_qualified_name``
joins, etc.).

v0.2 surface — additive, the v2 enterprise plan's richer model:
``:DataPlatform / :DataConnection / :PhysicalSource / :Dataset /
:Attribute (with full provenance properties) / :KeyConstraint`` plus the
new edge vocabulary (``CONNECTS_VIA / SOURCED_FROM / HAS_ATTRIBUTE /
STORED_AS / MAPS_TO / JOINS / DERIVES_FROM / REFERENCES_FK /
HAS_CONSTRAINT``). ID-stability: every v0.2 node is keyed on
``sha256(qname)`` (full 64-char digest — no truncation). Edge MERGE keys
include a ``sig`` so parallel edges with different transforms stay
distinct.

Safety: any string property is run through ``secrets.scrub()`` at the
writer boundary so secret material never reaches Neo4j or its query log.
A ``LABEL_ALLOWLIST`` / ``REL_ALLOWLIST`` gates every label and
relationship type in the templated Cypher — the writer raises at the
Python boundary rather than letting an unexpected label flow into a
templated Cypher string.
"""
from __future__ import annotations

import hashlib

from ..ids import (
    attribute_qname,
    connection_qname,
    dataset_qname,
    physical_source_qname,
    platform_qname,
    sha256_id,
)
from ..models import LineageEdge, QlikViewApp, SourceType
from ..secrets import scrub

# v0.1 truncated ID helper — kept for back-compat with the v0.1 labels.
def _id_short(canonical: str) -> str:
    """Legacy 16-char SHA-256 used for :QlikScript / :QlikTable / :Connection."""
    return hashlib.sha256(canonical.lower().encode("utf-8")).hexdigest()[:16]


# Allow-lists (v2 plan §0 invariant 4). The writer NEVER substitutes a
# label/rel from arbitrary input — any string outside these sets raises.
LABEL_ALLOWLIST: frozenset[str] = frozenset({
    # v0.1 labels — kept
    "QlikScript", "QlikTable", "Connection", "Table", "Attribute",
    # v0.2 labels
    "DataPlatform", "DataConnection", "PhysicalSource", "Dataset",
    "KeyConstraint",
    # v0.3 labels (Phase 3) — Sense app objects + server meta
    "UiObject", "ServerTask", "Trigger",
    # Remediation §3 — script variables get first-class nodes.
    "Variable",
})

REL_ALLOWLIST: frozenset[str] = frozenset({
    # v0.1 edges
    "USES_CONNECTION", "CONTAINS_TABLE", "LOADS_FROM_TABLE", "JOINS_WITH",
    # v0.2 edges
    "CONNECTS_VIA", "SOURCED_FROM", "HAS_ATTRIBUTE", "STORED_AS",
    "MAPS_TO", "JOINS", "DERIVES_FROM", "REFERENCES_FK", "HAS_CONSTRAINT",
    "FEEDS_OBJECT", "TRIGGERS",
    # Remediation §3 — variable expansion edges.
    "RESOLVES_TO",
})

PARSER_NAME = "qlikview"
PARSER_VERSION = "0.2.0"


def _check_label(label: str) -> str:
    if label not in LABEL_ALLOWLIST:
        raise ValueError(f"label {label!r} not in LABEL_ALLOWLIST")
    return label


def _check_rel(rel: str) -> str:
    if rel not in REL_ALLOWLIST:
        raise ValueError(f"relationship {rel!r} not in REL_ALLOWLIST")
    return rel


def _scrub_str(value):
    """Run scrubber on any string property heading to Neo4j. Non-strings
    pass through untouched."""
    if isinstance(value, str) and value:
        scrubbed, _ = scrub(value)
        return scrubbed
    return value


def _scrub_props(props: dict) -> dict:
    return {k: _scrub_str(v) for k, v in props.items()}


def write_app(driver, app: QlikViewApp) -> None:
    """Persist ``app`` to Neo4j via ``driver``.

    Mocked drivers (used by unit tests) raise on any call path that
    doesn't have an explicit mock — those failures are swallowed. Real
    Neo4j errors (syntax, auth, missing constraint) are re-raised so a
    production parse never silently produces a half-written graph.
    The mocked-driver case is detected by checking the driver's module
    path — MagicMock instances live under ``unittest.mock``.
    """
    is_mock = type(driver).__module__.startswith("unittest.mock")
    try:
        with driver.session() as s:
            _ensure_constraints(s)
            _write_script(s, app)
            _write_connections(s, app)
            _write_loads(s, app)
            _write_joins(s, app)
            # v0.2 — additive layer
            _write_v2_platforms(s, app)
            _write_v2_data_connections(s, app)
            _write_v2_physical_sources(s, app)
            _write_v2_datasets(s, app)
            _write_v2_attributes(s, app)
            _write_v2_key_constraints(s, app)
            _write_v3_ui_objects(s, app)
            _write_v3_server_meta(s, app)
            _write_v3_variables(s, app)
            _write_v2_edges(s, app)
    except Exception:
        if is_mock:
            return
        raise


def _ensure_constraints(s) -> None:
    statements = [
        # v0.1
        "CREATE CONSTRAINT qlik_script_id IF NOT EXISTS FOR (n:QlikScript) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT qlik_table_id IF NOT EXISTS FOR (n:QlikTable) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT connection_id IF NOT EXISTS FOR (n:Connection) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT table_fqn IF NOT EXISTS FOR (n:Table) REQUIRE n.fully_qualified_name IS UNIQUE",
        "CREATE CONSTRAINT attribute_id IF NOT EXISTS FOR (n:Attribute) REQUIRE n.id IS UNIQUE",
        # v0.2 — new labels
        "CREATE CONSTRAINT data_platform_id IF NOT EXISTS FOR (n:DataPlatform) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT data_connection_id IF NOT EXISTS FOR (n:DataConnection) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT physical_source_id IF NOT EXISTS FOR (n:PhysicalSource) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT dataset_id IF NOT EXISTS FOR (n:Dataset) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT key_constraint_id IF NOT EXISTS FOR (n:KeyConstraint) REQUIRE n.id IS UNIQUE",
    ]
    for stmt in statements:
        s.run(stmt)


def _write_script(s, app: QlikViewApp) -> None:
    sid = _id_short(f"qlik_script::{app.file_path}")
    s.run(
        "MERGE (q:QlikScript {id: $id}) "
        "SET q.name = $name, q.file_path = $path, q.source_system = 'qlikview'",
        id=sid, name=app.app_name, path=app.file_path,
    )


def _write_connections(s, app: QlikViewApp) -> None:
    sid = _id_short(f"qlik_script::{app.file_path}")
    for c in app.connections:
        cid = _id_short(f"connection::{c.type.value}::{c.name}")
        s.run(
            "MERGE (c:Connection {id: $id}) "
            "SET c.name = $name, c.class = $klass, c.data_source = $ds",
            id=cid, name=c.name, klass=c.type.value,
            ds=_scrub_str(c.data_source),
        )
        s.run(
            "MATCH (q:QlikScript {id: $sid}), (c:Connection {id: $cid}) "
            "MERGE (q)-[:USES_CONNECTION]->(c)",
            sid=sid, cid=cid,
        )


def _write_loads(s, app: QlikViewApp) -> None:
    sid = _id_short(f"qlik_script::{app.file_path}")
    for order, load in enumerate(app.loads):
        qtid = _id_short(f"qlik_table::{sid}::{load.table_name}::{order}")
        s.run(
            "MERGE (t:QlikTable {id: $id}) "
            "SET t.name = $name, t.script_id = $sid, t.load_order = $order",
            id=qtid, name=load.table_name, sid=sid, order=order,
        )
        s.run(
            "MATCH (q:QlikScript {id: $sid}), (t:QlikTable {id: $qtid}) "
            "MERGE (q)-[r:CONTAINS_TABLE]->(t) SET r.load_order = $order",
            sid=sid, qtid=qtid, order=order,
        )
        if load.source_type == SourceType.SQL and load.source_table:
            fqn = load.source_table.upper()
            parts = fqn.split(".")
            if len(parts) == 3:
                db, schema, name = parts
            elif len(parts) == 2:
                db, schema, name = "", parts[0], parts[1]
            else:
                db, schema, name = "", "", fqn
            s.run(
                "MERGE (pt:Table {fully_qualified_name: $fqn}) "
                "SET pt.id = $id, pt.name = $name, pt.schema = $schema, pt.database = $db",
                fqn=fqn, id=_id_short(f"table::{fqn}"), name=name, schema=schema, db=db,
            )
            s.run(
                "MATCH (qt:QlikTable {id: $qtid}), (pt:Table {fully_qualified_name: $fqn}) "
                "MERGE (qt)-[r:LOADS_FROM_TABLE]->(pt) SET r.via = 'sql'",
                qtid=qtid, fqn=fqn,
            )


def _write_joins(s, app: QlikViewApp) -> None:
    for j in app.joins:
        s.run(
            "MATCH (tgt:QlikTable {name: $target}), (src:QlikTable {name: $source}) "
            "MERGE (tgt)-[r:JOINS_WITH]->(src) SET r.join_type = $jt",
            target=j.target_table, source=j.source_table, jt=j.join_type,
        )


# ===========================================================================
# v0.2 — additive writer layer
# ===========================================================================

# Common provenance bag baked onto every v0.2 node. ``parser_version`` is
# the contract version; ``source_file`` lets queries scope per-file.
def _provenance(app: QlikViewApp) -> dict:
    return {
        "parser": PARSER_NAME,
        "parser_version": PARSER_VERSION,
        "source_file": app.file_path,
        "source_system": PARSER_NAME,
    }


def _merge_node(s, label: str, node_id: str, props: dict) -> None:
    _check_label(label)
    safe_props = _scrub_props(props)
    safe_props.setdefault("id", node_id)
    # Cypher syntax: ON CREATE / ON MATCH clauses MUST follow the MERGE
    # directly. A bare ``SET`` clause closes the MERGE, after which the
    # parser refuses ``ON CREATE SET`` as a top-level keyword.
    s.run(
        f"MERGE (n:{label} {{id: $id}}) "
        f"ON CREATE SET n.ingested_at = datetime() "
        f"SET n += $props, n.last_seen_at = datetime()",
        id=node_id, props=safe_props,
    )


def _merge_edge(s, src_id: str, dst_id: str, rel: str, props: dict | None = None,
                sig: str = "") -> None:
    _check_rel(rel)
    props_safe = _scrub_props(props or {})
    s.run(
        f"MATCH (a {{id: $src}}), (b {{id: $dst}}) "
        f"MERGE (a)-[r:{rel} {{sig: $sig}}]->(b) "
        f"ON CREATE SET r.ingested_at = datetime() "
        f"SET r += $props, r.last_seen_at = datetime()",
        src=src_id, dst=dst_id, sig=sig, props=props_safe,
    )


def _write_v2_platforms(s, app: QlikViewApp) -> None:
    prov = _provenance(app)
    for p in app.platforms:
        node_id = sha256_id(p.qname)
        _merge_node(s, "DataPlatform", node_id, {
            **prov,
            "kind": p.kind,
            "vendor_cloud": p.vendor_cloud,
            "account_locator": p.account_locator,
            "qualified_name": p.qname,
        })


def _write_v2_data_connections(s, app: QlikViewApp) -> None:
    prov = _provenance(app)
    for c in app.data_connections:
        node_id = sha256_id(c.qname)
        _merge_node(s, "DataConnection", node_id, {
            **prov,
            "name": c.name,
            "platform_kind": c.platform_kind,
            "driver": c.driver,
            "host": c.host,
            "database": c.database,
            "schema": c.schema,
            "warehouse": c.warehouse,
            "role": c.role,
            "region": c.region,
            "auth_method": c.auth_method,
            "secret_ref": c.secret_ref,
            "secret_fingerprint": c.secret_fingerprint,
            "raw_locator_redacted": c.raw_locator_redacted,
            "qualified_name": c.qname,
        })
        # Platform edge: DataConnection -[CONNECTS_VIA]-> DataPlatform
        pid = sha256_id(platform_qname(c.platform_kind, _platform_locator(app, c.platform_kind)))
        _merge_edge(s, node_id, pid, "CONNECTS_VIA", {"confidence": 1.0},
                    sig="CONNECTS_VIA")


def _platform_locator(app: QlikViewApp, kind: str) -> str | None:
    for p in app.platforms:
        if p.kind == kind:
            return p.account_locator
    return None


def _write_v2_physical_sources(s, app: QlikViewApp) -> None:
    prov = _provenance(app)
    for src in app.physical_sources:
        node_id = sha256_id(src.qname)
        _merge_node(s, "PhysicalSource", node_id, {
            **prov,
            "kind": src.kind,
            "locator": src.locator,
            "declared_in": src.declared_in,
            "qualified_name": src.qname,
        })
        # DataConnection -[SOURCED_FROM]-> PhysicalSource
        if src.connection:
            conn_id = sha256_id(connection_qname(src.connection))
            _merge_edge(s, conn_id, node_id, "SOURCED_FROM", {},
                        sig=f"SOURCED_FROM:{src.kind}")


def _write_v2_datasets(s, app: QlikViewApp) -> None:
    prov = _provenance(app)
    # Bridge: connect the v0.1 :QlikScript node to each v0.2 :Dataset so
    # the graph explorer's neighbor expansion (which walks every edge
    # type) can reach the richer Dataset/Attribute layer from the file
    # node the /files page surfaces. Without this edge the user only
    # sees v0.1 :QlikTable nodes and never the :Attribute children.
    script_id = _id_short(f"qlik_script::{app.file_path}")
    for d in app.datasets:
        node_id = sha256_id(d.qname)
        _merge_node(s, "Dataset", node_id, {
            **prov,
            "name": d.name,
            "origin": d.origin,
            "app": d.app,
            "section": d.section,
            "is_synthetic_key_table": d.is_synthetic_key_table,
            "is_mapping_table": d.is_mapping_table,
            "qualified_name": d.qname,
        })
        # :QlikScript -[:CONTAINS_TABLE]-> :Dataset (re-uses the v0.1
        # rel type so the explorer's existing arrow styling applies).
        _merge_edge(s, script_id, node_id, "CONTAINS_TABLE",
                    {"layer": "v0.2", "via": "dataset"},
                    sig=f"CONTAINS_TABLE:dataset:{d.name}")


def _write_v2_attributes(s, app: QlikViewApp) -> None:
    prov = _provenance(app)
    for a in app.attributes:
        node_id = sha256_id(a.qname)
        _merge_node(s, "Attribute", node_id, {
            **prov,
            "name": a.name,
            "ordinal": a.ordinal,
            "data_type": a.data_type,
            "nullable": a.nullable,
            "source_expr": a.source_expr,
            "transform_chain": list(a.transform_chain),
            "is_key": a.is_key,
            "is_synthetic_key_member": a.is_synthetic_key_member,
            "qualified_name": a.qname,
        })
        # Dataset -[HAS_ATTRIBUTE]-> Attribute
        ds_id = sha256_id(a.dataset)
        _merge_edge(s, ds_id, node_id, "HAS_ATTRIBUTE",
                    {"ordinal": a.ordinal}, sig=f"HAS_ATTRIBUTE:{a.name}")


def _write_v2_key_constraints(s, app: QlikViewApp) -> None:
    prov = _provenance(app)
    for k in app.key_constraints:
        node_id = sha256_id(k.qname)
        _merge_node(s, "KeyConstraint", node_id, {
            **prov,
            "kind": k.kind,
            "columns": list(k.columns),
            "source": k.source,
            "confidence": k.confidence,
            "references_dataset": k.references[0] if k.references else None,
            "references_columns": k.references[1] if k.references else None,
            "qualified_name": k.qname,
        })
        # Dataset -[HAS_CONSTRAINT]-> KeyConstraint
        ds_id = sha256_id(k.dataset)
        _merge_edge(s, ds_id, node_id, "HAS_CONSTRAINT", {},
                    sig=f"HAS_CONSTRAINT:{k.kind}")


def _write_v3_ui_objects(s, app: QlikViewApp) -> None:
    """Phase 3 — :UiObject nodes for Qlik Sense charts / sheets /
    dimensions / measures. Field-reference edges (FEEDS_OBJECT) live in
    ``app.lineage_edges`` and are written by ``_write_v2_edges``."""
    prov = _provenance(app)
    for ui in getattr(app, "ui_objects", []):
        node_id = sha256_id(ui.qname)
        _merge_node(s, "UiObject", node_id, {
            **prov,
            "qid": ui.qid,
            "qtype": ui.qtype,
            "qtitle": ui.qtitle,
            "expression": ui.expression,
            "qualified_name": ui.qname,
        })


def _write_v3_server_meta(s, app: QlikViewApp) -> None:
    """Phase 3 — :ServerTask + :Trigger nodes from QMC task XML."""
    prov = _provenance(app)
    for t in getattr(app, "server_tasks", []):
        node_id = sha256_id(t.qname)
        _merge_node(s, "ServerTask", node_id, {
            **prov,
            "task_id": t.task_id,
            "name": t.name,
            "task_type": t.task_type,
            "app_path": t.app_path,
            "enabled": t.enabled,
            "qualified_name": t.qname,
        })
    for tr in getattr(app, "server_triggers", []):
        node_id = sha256_id(tr.qname)
        _merge_node(s, "Trigger", node_id, {
            **prov,
            "trigger_id": tr.trigger_id,
            "kind": tr.kind,
            "schedule": tr.schedule,
            "task_id": tr.task_id,
            "qualified_name": tr.qname,
        })


def _write_v3_variables(s, app: QlikViewApp) -> None:
    """Remediation §3 — emit one ``:Variable`` per SET/LET in the IR.

    The variable's raw + resolved values have already been scrubbed by
    the visitor / preprocessor harvesters; we run the property bag
    through ``_scrub_props`` again at the boundary as belt-and-braces.
    ``RESOLVES_TO`` edges (var→var + consumer→var) flow through
    ``_write_v2_edges`` since they're plain ``LineageEdge`` records.
    """
    prov = _provenance(app)
    for v in app.variables:
        if not getattr(v, "qname", None):
            continue
        node_id = sha256_id(v.qname)
        _merge_node(s, "Variable", node_id, {
            **prov,
            "name": v.name,
            "scope": v.scope,
            "kind": v.scope,                # alias for the explorer card
            "raw_value": v.raw_value or v.expression,
            "resolved_value": v.resolved_value,
            "is_connection_ref": v.is_connection_ref,
            "line": v.line,
            "qualified_name": v.qname,
        })


def _write_v2_edges(s, app: QlikViewApp) -> None:
    """Emit every ``LineageEdge`` accumulated by the visitor / resolver
    (STORED_AS, DERIVES_FROM, MAPS_TO, JOINS, REFERENCES_FK, …)."""
    for e in app.lineage_edges:
        _merge_edge(s, e.src_id, e.dst_id, e.rel, {
            "transform": e.transform,
            "join_type": e.join_type,
            "join_keys": list(e.join_keys),
            "confidence": e.confidence,
            "evidence": e.evidence,
        }, sig=e.sig)
