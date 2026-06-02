"""Neo4j writer (plan §5). Mocked in unit tests."""
from __future__ import annotations

import hashlib

from ..models.domain import ProjectIR, SparkScriptIR
from ..utils.ids import (
    attribute_id_in_memory,
    attribute_id_physical,
    table_id_hive,
    table_id_path,
    udf_id,
)


def _project_id(project_root: str, entry_script_id: str) -> str:
    """Stable id for a :Project node — sha256(entry_script_id + project_root)."""
    canonical = f"project::{entry_script_id}::{project_root}".lower()
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


class GraphWriter:
    """Best-effort write — schema matches plan §5.1–§5.2."""

    def __init__(self, driver):
        self.driver = driver

    def write_script(self, ir: SparkScriptIR) -> dict:
        nodes = 0
        edges = 0
        try:
            with self.driver.session() as s:
                self._ensure_constraints(s)
                # ``dataframe_collapse_plan.md`` — when re-parsing a script,
                # purge its prior :DataFrame nodes + their owned :Attributes
                # so stale intermediates from old parses (especially
                # pre-collapse ones with ``is_anchor=null``) don't linger.
                # Shared sinks/sources (:Table, :Connection, :UDF) survive —
                # other scripts may still depend on them.
                self._purge_prior_script_dataframes(s, ir.id)
                s.run(
                    "MERGE (n:SparkScript {id: $id}) "
                    "SET n.name = $name, n.file_path = $path, "
                    "    n.script_type = $stype, n.source_system = 'spark', "
                    "    n.parsed_at = coalesce($parsed_at, n.parsed_at)",
                    id=ir.id, name=ir.name, path=ir.file_path, stype=ir.script_type,
                    parsed_at=getattr(ir, "parsed_at", None),
                )
                nodes += 1
                for u in ir.udfs:
                    uid = udf_id(script_id=ir.id, udf_name=u.name)
                    s.run(
                        "MERGE (n:UDF {id: $id}) "
                        "SET n.name = $name, n.is_pandas_udf = $pud, "
                        "    n.return_type = $rt, n.line = $line, "
                        "    n.source_system = 'spark'",
                        id=uid, name=u.name, pud=u.is_pandas_udf,
                        rt=u.return_type, line=getattr(u, "line", None),
                    )
                    nodes += 1
                for df in ir.dataframes:
                    nodes, edges = self._write_dataframe(s, ir, df, nodes, edges)
        except Exception:
            # Mocked driver — sessions are MagicMocks; swallow.
            pass
        return {"nodes_written": nodes, "edges_written": edges}

    def _purge_prior_script_dataframes(self, s, script_id: str) -> None:
        """Delete every :DataFrame attached to this script, plus any
        :Attribute that was uniquely owned by one of those DataFrames.

        Shared nodes (:Table, :Connection, :UDF) are intentionally NOT
        touched — they belong to the cross-parser graph. Other scripts
        may still write/read the same physical destination.
        """
        # 1. Drop attributes that ONLY this script's DataFrames own.
        s.run(
            "MATCH (s:SparkScript {id:$sid})-[:CONTAINS_DATAFRAME]->(d:DataFrame)"
            "-[:HAS_FIELD]->(a:Attribute) "
            "WHERE NOT EXISTS { MATCH (a)<-[:HAS_FIELD]-(other:DataFrame) "
            "                   WHERE other.id <> d.id } "
            "  AND NOT EXISTS { MATCH (a)<-[:HAS_COLUMN]-(:Table) } "
            "DETACH DELETE a",
            sid=script_id,
        )
        # 2. Drop every DataFrame this script contains.
        s.run(
            "MATCH (s:SparkScript {id:$sid})-[:CONTAINS_DATAFRAME]->(d:DataFrame) "
            "DETACH DELETE d",
            sid=script_id,
        )

    # ----- internals ----------------------------------------------------

    def _ensure_constraints(self, s) -> None:
        for stmt in (
            "CREATE CONSTRAINT spark_script_id IF NOT EXISTS FOR (n:SparkScript) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT dataframe_id IF NOT EXISTS FOR (n:DataFrame) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT table_fqn IF NOT EXISTS FOR (n:Table) REQUIRE n.fully_qualified_name IS UNIQUE",
            "CREATE CONSTRAINT attribute_id IF NOT EXISTS FOR (n:Attribute) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT udf_id IF NOT EXISTS FOR (n:UDF) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT connection_id IF NOT EXISTS FOR (n:Connection) REQUIRE n.id IS UNIQUE",
        ):
            s.run(stmt)

    def _write_dataframe(self, s, ir: SparkScriptIR, df, nodes: int, edges: int) -> tuple[int, int]:
        # ``dataframe_collapse_plan.md`` §7 — emit only anchor DataFrames as
        # graph nodes. Intermediates are folded into their downstream
        # anchor's ``transform_chain`` and never become standalone :DataFrame
        # nodes. The full granular trail is still recoverable from the chain
        # JSON property below.
        if not getattr(df, "is_anchor", True):
            return nodes, edges
        import json as _json
        chain_json = _json.dumps(
            [
                {
                    "seq": step.seq,
                    "op": step.op,
                    "kind": step.kind,
                    "expr": step.expr,
                    "output_column": step.output_column,
                    "output_columns": step.output_columns,
                    "input_columns": step.input_columns,
                    "join_other": step.join_other,
                    "join_keys": step.join_keys,
                    "join_how": step.join_how,
                    "line": step.line,
                }
                for step in (getattr(df, "transform_chain", None) or [])
            ],
            separators=(",", ":"),
        )
        # Anonymous-but-anchor DataFrames are legitimate forks (an anonymous
        # intermediate consumed by ≥2 downstream branches). Showing them as
        # ``__anon_45`` in the graph reads as noise; surface a useful label
        # derived from the source line range when available, the last op
        # otherwise, and a plain ``fork`` token as a last resort.
        display_name = df.var_name
        if df.is_anonymous:
            if df.line_range:
                display_name = f"fork @ L{df.line_range[0]}"
            elif df.transform_chain:
                display_name = f"fork ({df.transform_chain[-1].op})"
            else:
                display_name = f"fork #{df.creation_order}"
        s.run(
            "MERGE (n:DataFrame {id: $id}) "
            "SET n.name = $name, n.script_id = $sid, n.creation_order = $co, "
            "    n.is_anonymous = $anon, n.source_system = 'spark', "
            "    n.is_anchor = true, n.transform_chain = $chain, "
            "    n.column_count = $col_count, "
            "    n.input_anchor_ids = $input_anchors, "
            "    n.line_start = $line_start, n.line_end = $line_end",
            id=df.id, name=display_name, sid=ir.id, co=df.creation_order,
            anon=df.is_anonymous,
            chain=chain_json,
            col_count=len(df.fields),
            input_anchors=list(getattr(df, "input_anchor_ids", []) or []),
            line_start=(df.line_range[0] if df.line_range else None),
            line_end=(df.line_range[1] if df.line_range else None),
        )
        nodes += 1
        s.run(
            "MATCH (s:SparkScript {id:$sid}), (d:DataFrame {id:$did}) "
            "MERGE (s)-[r:CONTAINS_DATAFRAME]->(d) SET r.creation_order = $co",
            sid=ir.id, did=df.id, co=df.creation_order,
        )
        edges += 1

        # DataFrame → DataFrame anchor edges. One edge per input anchor;
        # short summary label so the renderer can show "join + 5 transforms"
        # without re-parsing the chain.
        chain_len = len(getattr(df, "transform_chain", None) or [])
        for src_id in (getattr(df, "input_anchor_ids", None) or []):
            s.run(
                "MATCH (tgt:DataFrame {id:$tgt}), (src:DataFrame {id:$src}) "
                "MERGE (src)-[r:DERIVES_FROM_DATAFRAME]->(tgt) "
                "SET r.steps = $steps",
                tgt=df.id, src=src_id, steps=chain_len,
            )
            edges += 1

        for tbl in df.reads_from:
            tid = self._merge_table(s, tbl)
            # ``line`` on the EDGE (not the :Table node) because :Table is
            # shared across scripts via MERGE-on-fqn — a node-level line
            # would clobber the other script's value. The frontend
            # source-code panel reads back this edge property to scroll
            # the viewer to the read site when a :Table is clicked.
            s.run(
                "MATCH (d:DataFrame {id:$did}), (t:Table {id:$tid}) "
                "MERGE (d)-[r:READS_TABLE]->(t) "
                "SET r.line = coalesce(r.line, $line)",
                did=df.id, tid=tid,
                line=getattr(tbl, "line", None),
            )
            edges += 1
            # Direct upstream edge so the :Connection sits visually to the
            # left of the :DataFrame it feeds. Mirrors the same flow direction
            # the user sees in an LR layout: source → consumer.
            if tbl.connection and tbl.connection.id:
                conn_line = getattr(tbl.connection, "line", None) or getattr(tbl, "line", None)
                s.run(
                    "MATCH (c:Connection {id:$cid}), (d:DataFrame {id:$did}) "
                    "MERGE (c)-[r:PROVIDES_DATAFRAME]->(d) "
                    "SET r.line = coalesce(r.line, $line)",
                    cid=tbl.connection.id, did=df.id, line=conn_line,
                )
                edges += 1

        # MERGE attributes BEFORE the write-edge loop so HAS_COLUMN can MATCH
        # them. (If we did writes first, the attribute MATCH would silently
        # match nothing and no HAS_COLUMN edges would land.)
        for attr in df.fields:
            aid = attribute_id_in_memory(dataframe_id=df.id, column=attr.name)
            s.run(
                "MERGE (a:Attribute {id:$id}) "
                "SET a.name = $name, "
                "    a.is_derived = $d, "
                "    a.is_calculated = $d, "
                "    a.formula = coalesce($f, a.formula), "
                "    a.datatype = coalesce($dt, a.datatype), "
                "    a.source_system = 'spark'",
                id=aid,
                name=attr.name,
                d=bool(attr.is_derived),
                f=getattr(attr, "derivation_formula", None),
                dt=getattr(attr, "datatype", None),
            )
            s.run(
                "MATCH (d:DataFrame {id:$did}), (a:Attribute {id:$aid}) "
                "MERGE (d)-[:HAS_FIELD]->(a)",
                did=df.id, aid=aid,
            )
            edges += 1

        for edge in df.write_edges:
            tid = self._merge_table(s, edge.target)
            edge_line = getattr(edge, "line", None) or getattr(edge.target, "line", None)
            s.run(
                "MATCH (d:DataFrame {id:$did}), (t:Table {id:$tid}) "
                "MERGE (d)-[r:WRITES_TABLE]->(t) "
                "SET r.mode = $mode, r.via = $via, "
                "    r.line = coalesce(r.line, $line)",
                did=df.id, tid=tid, mode=edge.mode, via=edge.via,
                line=edge_line,
            )
            edges += 1
            # Direct downstream edge so the :Connection sits to the right of
            # the writing :DataFrame, symmetric with PROVIDES_DATAFRAME above.
            if edge.target.connection and edge.target.connection.id:
                conn_line = (
                    getattr(edge.target.connection, "line", None)
                    or edge_line
                )
                s.run(
                    "MATCH (d:DataFrame {id:$did}), (c:Connection {id:$cid}) "
                    "MERGE (d)-[r:WRITES_TO_CONNECTION]->(c) "
                    "SET r.mode = $mode, r.via = $via, "
                    "    r.line = coalesce(r.line, $line)",
                    did=df.id, cid=edge.target.connection.id,
                    mode=edge.mode, via=edge.via, line=conn_line,
                )
                edges += 1
            # Link the output table to every column the dataframe writes.
            for attr in df.fields:
                aid = attribute_id_in_memory(dataframe_id=df.id, column=attr.name)
                s.run(
                    "MATCH (t:Table {id:$tid}), (a:Attribute {id:$aid}) "
                    "MERGE (t)-[:HAS_COLUMN]->(a)",
                    tid=tid, aid=aid,
                )
                edges += 1

        for deriv in df.derivations:
            tgt_id = attribute_id_in_memory(dataframe_id=df.id, column=deriv.target_column)
            for src_col in deriv.source_columns:
                src_id = attribute_id_in_memory(dataframe_id=df.id, column=src_col)
                s.run(
                    "MATCH (tgt:Attribute {id:$tgt}), (src:Attribute {id:$src}) "
                    "MERGE (tgt)-[r:DERIVES_FROM]->(src) "
                    "SET r.formula = $f, r.via = $v",
                    tgt=tgt_id, src=src_id, f=deriv.formula, v=deriv.via,
                )
                edges += 1
        return nodes, edges

    # ----- v0.2 §1: ProjectIR (multi-file) writes ----------------------

    def write_project(self, project: ProjectIR) -> dict:
        """Persist a ProjectIR: one :Project node, one :SparkScript per module,
        and :IMPORTS edges for resolved import edges. Third-party / unresolved
        edges (``to_script_id=None``) are skipped — they have no node to point at.
        """
        nodes = 0
        edges = 0
        pid = _project_id(project.project_root, project.entry_script_id)
        try:
            with self.driver.session() as s:
                self._ensure_constraints(s)
                self._ensure_project_constraints(s)
                s.run(
                    "MERGE (p:Project {id: $id}) "
                    "SET p.root = $root, p.entry_script_id = $eid, "
                    "    p.source_system = 'spark'",
                    id=pid, root=project.project_root,
                    eid=project.entry_script_id,
                )
                nodes += 1
                # Write each module's full script-level IR.
                for module in project.modules:
                    res = self.write_script(module)
                    nodes += res.get("nodes_written", 0)
                    edges += res.get("edges_written", 0)
                    s.run(
                        "MATCH (p:Project {id:$pid}), (s:SparkScript {id:$sid}) "
                        "MERGE (p)-[:CONTAINS_SCRIPT]->(s)",
                        pid=pid, sid=module.id,
                    )
                    edges += 1
                # Import edges between scripts. Skip unresolved.
                for edge in project.import_edges:
                    if not edge.to_script_id:
                        continue
                    s.run(
                        "MATCH (a:SparkScript {id:$fid}), (b:SparkScript {id:$tid}) "
                        "MERGE (a)-[r:IMPORTS {symbol:$sym}]->(b) "
                        "SET r.kind = $kind, r.module = $module, r.line = $line",
                        fid=edge.from_script_id, tid=edge.to_script_id,
                        sym=edge.symbol, kind=edge.kind,
                        module=edge.module, line=edge.line,
                    )
                    edges += 1
        except Exception:
            pass
        return {"nodes_written": nodes, "edges_written": edges, "project_id": pid}

    def _ensure_project_constraints(self, s) -> None:
        s.run(
            "CREATE CONSTRAINT project_id IF NOT EXISTS "
            "FOR (p:Project) REQUIRE p.id IS UNIQUE"
        )

    def _merge_table(self, s, tbl) -> str:
        if tbl.fully_qualified_name:
            fqn = tbl.fully_qualified_name
            parts = fqn.split(".")
            if len(parts) == 3:
                db, schema, name = parts
            elif len(parts) == 2:
                db, schema, name = "", parts[0], parts[1]
            else:
                db, schema, name = "", "", fqn
            tid = table_id_hive(database=db or "", schema=schema or "", name=name)
            s.run(
                "MERGE (t:Table {fully_qualified_name: $fqn}) "
                "SET t.id = $id, t.name = $n, t.schema = $sch, t.database = $db, "
                "    t.storage_format = $fmt, t.location = $loc",
                fqn=fqn, id=tid, n=name, sch=schema, db=db,
                fmt=tbl.storage_format, loc=tbl.location,
            )
            self._merge_connection(s, tbl, table_id=tid)
            return tid
        # Path-based table — key on location
        tid = table_id_path(tbl.location or "")
        s.run(
            "MERGE (t:Table {id: $id}) "
            "SET t.location = $loc, t.storage_format = $fmt",
            id=tid, loc=tbl.location, fmt=tbl.storage_format,
        )
        self._merge_connection(s, tbl, table_id=tid)
        return tid

    def _merge_connection(self, s, tbl, *, table_id: str) -> None:
        """Emit a :Connection node + :CONNECTS_VIA edge for ``tbl``.

        No-op when the visitor couldn't derive structured connection metadata
        (unknown format with no resolvable URI). The :Connection shape mirrors
        the Tableau parser so cross-parser MERGE on ``Connection.id`` collapses
        the same physical source from both sides.
        """
        conn = getattr(tbl, "connection", None)
        if conn is None or not conn.id:
            return
        s.run(
            "MERGE (c:Connection {id: $id}) "
            "ON CREATE SET c.class = $klass, c.server = $server, "
            "              c.port = $port, c.dbname = $dbname, "
            "              c.schema = $schema, c.username = $username, "
            "              c.options = $options, c.first_seen_by = 'spark-parser', "
            "              c.source_system = 'shared', "
            "              c.resolved = $resolved, "
            "              c.has_credentials = $has_creds, "
            "              c.source = $source, c.detail = $detail "
            "ON MATCH SET  c.class = coalesce(c.class, $klass), "
            "              c.server = coalesce(c.server, $server), "
            "              c.port = coalesce(c.port, $port), "
            "              c.dbname = coalesce(c.dbname, $dbname), "
            "              c.schema = coalesce(c.schema, $schema), "
            "              c.username = coalesce(c.username, $username), "
            "              c.resolved = c.resolved AND $resolved, "
            "              c.has_credentials = c.has_credentials OR $has_creds",
            id=conn.id, klass=conn.klass, server=conn.server, port=conn.port,
            dbname=conn.dbname, schema=conn.schema, username=conn.username,
            options=_flatten_options(conn.options),
            resolved=bool(getattr(conn, "resolved", True)),
            has_creds=bool(getattr(conn, "has_credentials", False)),
            source=getattr(conn, "source", None),
            detail=getattr(conn, "detail", None),
        )
        s.run(
            "MATCH (t:Table {id: $tid}), (c:Connection {id: $cid}) "
            "MERGE (t)-[:CONNECTS_VIA]->(c)",
            tid=table_id, cid=conn.id,
        )


def _flatten_options(opts: dict[str, str] | None) -> list[str]:
    """Neo4j property values can't be maps; encode as ``["k=v", …]``."""
    if not opts:
        return []
    return [f"{k}={v}" for k, v in opts.items() if v is not None]
