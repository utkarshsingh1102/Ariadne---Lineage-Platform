"""QlikView parser — orchestrator.

Wires the 4-stage pipeline:
    Stage 1 — preprocessor.py    (encoding, includes, macros)
    Stage 2 — ANTLR + ir_visitor (the QlikView DSL itself)
    Stage 3 — sql_block.py       (sqlglot for embedded SQL SELECT)
    Stage 4 — xml_metadata.py    (optional lxml for sheet/chart metadata)

External surface matches the test contract in ``tests/`` — ``QlikViewParser``
exposes ``parse_qvs_file``, ``push_to_neo4j``, ``export_to_json``,
``extract_sql_tables``, ``_extract_source_fields_from_expression``, ``close``,
and ``driver``.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from antlr4 import CommonTokenStream, InputStream

from .generated.QlikViewLexer import QlikViewLexer
from .generated.QlikViewParser import QlikViewParser as _AntlrParser
from .graph.writer import write_app as _write_app_to_neo4j
from .models import QlikViewApp, Subroutine, Variable
from .preprocessor import default_include_root, preprocess
from .sql_block import extract_tables as _sql_extract_tables
from .visitor.error_listener import CollectingErrorListener
from .visitor.ir_visitor import QlikViewIRVisitor, extract_source_fields
from .xml_metadata import parse_xml_metadata


_VAR_REF = re.compile(r"\$\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)")


def _expand_var_chain(raw: str, raw_map: dict[str, str], origin: str,
                      max_passes: int = 5) -> str:
    """Best-effort variable-chain expansion. Recursively substitutes
    ``$(otherVar)`` references inside ``raw`` against ``raw_map``,
    bounded to ``max_passes`` to avoid runaway expansion on a cycle.
    Unknown variables are left as the literal ``$(name)``; the caller
    treats that as an unresolved sentinel."""
    cur = raw
    seen = {origin}
    for _ in range(max_passes):
        prev = cur
        def _sub(m):
            name = m.group(1)
            if name in seen:
                return m.group(0)   # break cycle
            if name not in raw_map:
                return m.group(0)
            seen.add(name)
            return raw_map[name]
        cur = _VAR_REF.sub(_sub, cur)
        if cur == prev:
            break
    return cur


def _referenced_vars(text: str) -> list[str]:
    """Distinct variable names referenced via ``$(name)`` in ``text``,
    preserving order of first occurrence."""
    out: list[str] = []
    seen: set[str] = set()
    if not text:
        return out
    for m in _VAR_REF.finditer(text):
        name = m.group(1)
        if name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _emit_variable_edges(app: QlikViewApp) -> None:
    """Remediation §3.4 — emit ``RESOLVES_TO`` lineage edges.

    Two flavours:
      1. **var → var** — for every ``$(otherVar)`` reference inside a
         variable's raw value, edge from this variable to the referenced
         one. The double-expansion case
         ``vQvdRoot = '$(vQvdRoot$(vEnv))'`` produces edges to both
         ``vQvdRoot`` (self-loop indicating double-bind) and ``vEnv``.
      2. **consumer → var** — for every node whose user-visible value
         expands a ``$(name)``, edge from that node to the referenced
         variable. Currently emitted for ``PhysicalSource.locator``,
         ``DataConnection.raw_locator_redacted`` /
         ``DataConnection.name``, and ``LoadStatement.sql_query``.

    Idempotent — uses the ``LineageEdge.sig`` property to dedup.
    """
    from .ids import sha256_id
    from .models import LineageEdge

    by_name: dict[str, "Variable"] = {v.name: v for v in app.variables}
    if not by_name:
        return

    existing_sigs: set[tuple[str, str, str]] = {
        (e.src_id, e.dst_id, e.sig) for e in app.lineage_edges
    }

    def _add_edge(edge: LineageEdge) -> None:
        key = (edge.src_id, edge.dst_id, edge.sig)
        if key in existing_sigs:
            return
        existing_sigs.add(key)
        app.lineage_edges.append(edge)

    # ----- 1. var → var ---------------------------------------------------
    for v in app.variables:
        raw = v.raw_value or v.expression or ""
        for ref in _referenced_vars(raw):
            target = by_name.get(ref)
            if target is None:
                continue
            _add_edge(LineageEdge(
                src_id=sha256_id(v.qname),
                dst_id=sha256_id(target.qname),
                rel="RESOLVES_TO",
                transform="var_ref",
                confidence=1.0,
                evidence=raw[:120],
            ))

    # ----- 2. consumer → var ---------------------------------------------
    def _emit_consumer(consumer_qname: str, text: str, context: str) -> None:
        if not text:
            return
        for ref in _referenced_vars(text):
            target = by_name.get(ref)
            if target is None:
                continue
            _add_edge(LineageEdge(
                src_id=sha256_id(consumer_qname),
                dst_id=sha256_id(target.qname),
                rel="RESOLVES_TO",
                transform=f"consumer:{context}",
                confidence=1.0,
                evidence=text[:120],
            ))

    for src in app.physical_sources:
        _emit_consumer(src.qname, src.locator or "", "path")
    for c in app.data_connections:
        _emit_consumer(c.qname, c.raw_locator_redacted or "", "connection")
        _emit_consumer(c.qname, c.name or "", "connection")
    for ld in app.loads:
        if ld.sql_query:
            # SQL fragments live under the LoadStatement's Dataset qname.
            from .ids import dataset_qname as _dq
            _emit_consumer(_dq(app.file_path, ld.table_name), ld.sql_query, "sql")
        if ld.source_table:
            from .ids import dataset_qname as _dq
            _emit_consumer(_dq(app.file_path, ld.table_name), ld.source_table, "from")


def _binary_fallback_resolve(missing: Path, host_dir: Path) -> Path | None:
    """When ``BINARY [..\\foo\\App.qvw]`` resolves to a non-existent path,
    try locating ``App.qvs`` (or ``App.qvw``) anywhere within the host's
    directory tree. Real-world exports routinely BINARY into a sibling
    ``.qvs`` even though the directive itself names a ``.qvw`` — both
    are valid since QVS is just the exported script of the QVW.

    Returns the first matching file or None if nothing plausible exists.
    """
    stem = missing.stem
    if not stem:
        return None
    # Try the other extension first, in the same parent the missing path
    # pointed at.
    other_ext = ".qvs" if missing.suffix.lower() == ".qvw" else ".qvw"
    swapped = missing.with_suffix(other_ext)
    if swapped.exists():
        return swapped
    # Sibling search — walk up to two ancestors and look for either
    # extension. Bounded to keep the resolver O(host-tree-leaves) at
    # worst on small estates; production callers can override BINARY
    # resolution by passing an absolute path.
    search_roots = {host_dir, host_dir.parent, host_dir.parent.parent}
    for root in search_roots:
        if not root or not root.exists():
            continue
        for ext in (".qvs", ".qvw"):
            for candidate in root.rglob(f"{stem}{ext}"):
                if candidate.is_file():
                    return candidate.resolve()
    return None


def _lazy_resident_placeholders(app: QlikViewApp) -> None:
    """Remediation plan §1.3 — for every ``LOAD ... RESIDENT <table>``
    whose ``<table>`` was never declared in the host app (typically
    because it lives in an upstream BINARY chain we couldn't resolve),
    create a placeholder Dataset so the resident chain doesn't dangle.

    Idempotent — repeated calls are no-ops.
    """
    from .models import Dataset, Diagnostic, SourceType

    declared: set[str] = {d.name for d in app.datasets}
    # The visitor's LoadStatement also defines a "in-memory table" name —
    # those count as declared too.
    declared.update(ld.table_name for ld in app.loads if ld.table_name)

    for ld in app.loads:
        if ld.source_type != SourceType.RESIDENT or not ld.source_table:
            continue
        if ld.source_table in declared:
            continue
        # Lazy-create the placeholder. ``inherited_via`` carries the
        # provenance so the writer / explorer can render a distinct
        # affordance (and a downstream consumer can fail-soft on the
        # absent attributes).
        placeholder = Dataset(
            name=ld.source_table,
            origin="resident_placeholder",
            app=app.file_path,
            inherited_via="RESIDENT_PLACEHOLDER",
            inherited_from=app.file_path,
        )
        if not any(d.qname == placeholder.qname for d in app.datasets):
            app.datasets.append(placeholder)
            declared.add(placeholder.name)
            app.diagnostics.append(Diagnostic(
                level="info", code="QV-RESIDENT-INHERITED",
                message=(
                    f"RESIDENT {ld.source_table!r} references a table not "
                    f"declared in this app; placeholder created. Likely "
                    f"inherited via BINARY but the upstream was missing."
                ),
                artifact=app.file_path, line=ld.line_number or None,
            ))


def _resolve_star_fields(app: QlikViewApp) -> None:
    """Expand ``LOAD *`` into the concrete field list of the source table.

    For ``LOAD * RESIDENT X`` the source is another in-memory LOAD; for
    ``LOAD * FROM file`` we leave the star in place because file headers
    aren't read at parse time.
    """
    by_name: dict[str, list[str]] = {}
    for load in app.loads:
        if "*" not in load.fields:
            by_name[load.table_name] = list(load.fields)

    for load in app.loads:
        if "*" not in load.fields:
            continue
        if load.source_table and load.source_table in by_name:
            inherited = by_name[load.source_table]
            # Preserve any non-star fields the user explicitly listed.
            explicit = [f for f in load.fields if f != "*"]
            merged: list[str] = []
            for f in inherited + explicit:
                if f not in merged:
                    merged.append(f)
            load.fields = merged


class QlikViewParser:
    """Top-level entry point — see ``tests/conftest.py`` for the contract."""

    def __init__(
        self,
        neo4j_uri: str | None = None,
        neo4j_user: str | None = None,
        neo4j_password: str | None = None,
        *,
        include_root: str | None = None,
        max_include_depth: int = 10,
        dconn_dir: str | None = None,
        settings_ini: str | None = None,
        odbc_ini: str | None = None,
    ):
        # Look up GraphDatabase via the package namespace so tests can patch it
        # via ``unittest.mock.patch("qlikview_parser.GraphDatabase")``.
        from qlikview_parser import GraphDatabase

        self.neo4j_uri = neo4j_uri or os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        self.neo4j_user = neo4j_user or os.environ.get("NEO4J_USER", "neo4j")
        self.neo4j_password = neo4j_password or os.environ.get("NEO4J_PASSWORD", "neo4j")
        self.driver = GraphDatabase.driver(
            self.neo4j_uri, auth=(self.neo4j_user, self.neo4j_password)
        )
        self.include_root = include_root or default_include_root()
        self.max_include_depth = int(
            os.environ.get("QLIK_MAX_INCLUDE_DEPTH", str(max_include_depth))
        )
        # Phase 3 — connection-store resolver. Optional; absent stores fall
        # back to the visitor's inline classification. Env vars let estate
        # walks pick the stores up without rewriting call sites.
        self.connection_store = None
        dconn_dir = dconn_dir or os.environ.get("QLIK_DCONN_DIR")
        settings_ini = settings_ini or os.environ.get("QLIK_SETTINGS_INI")
        odbc_ini = odbc_ini or os.environ.get("QLIK_ODBC_INI")
        if dconn_dir or settings_ini or odbc_ini:
            from .connections import ConnectionStore
            self.connection_store = ConnectionStore.from_paths(
                dconn_dir=dconn_dir,
                settings_ini=settings_ini,
                odbc_ini=odbc_ini,
            )
        self.apps: list[QlikViewApp] = []

    # ----- lifecycle ----------------------------------------------------

    def close(self) -> None:
        if getattr(self, "driver", None) is not None:
            try:
                self.driver.close()
            except Exception:
                pass

    # ----- public entry: parse a binary .qvw ---------------------------

    def parse_qvw_file(
        self,
        path: str,
        *,
        xml_metadata_path: str | None = None,
    ) -> QlikViewApp:
        """Pull the load script out of a binary QVW (OLE compound file)
        and route it through the same pipeline as ``parse_qvs_file``.

        Diagnostics from the OLE walk + secret-scrub are merged onto the
        resulting ``QlikViewApp`` so the caller sees them via the
        ``diagnostics`` field.
        """
        import tempfile

        from .extract_qvw import QvwExtractionError, extract

        p_path = Path(path)
        try:
            extraction = extract(p_path)
        except QvwExtractionError as e:
            # Synthesize a failed app — the caller still gets an IR back
            # with diagnostics, never an exception that aborts an estate
            # walk (v2 plan §0 invariant 5: fail-soft).
            app = QlikViewApp(app_name=p_path.stem, file_path=str(p_path))
            app.parse_errors.append(f"QVW extraction failed: {e}")
            from .models import Diagnostic
            app.diagnostics.append(Diagnostic(
                level="error", code="QV-QVW-EXTRACT",
                message=str(e), artifact=str(p_path), line=None,
            ))
            self.apps.append(app)
            return app

        # Materialise the extracted script as a transient .qvs and route
        # through the existing pipeline. We keep the .qvw path on the
        # resulting app so source_file provenance is honest.
        with tempfile.NamedTemporaryFile(
            suffix=".qvs", delete=False, mode="w", encoding="utf-8",
        ) as f:
            f.write(extraction.script_text)
            transient = f.name
        try:
            app = self.parse_qvs_file(transient, xml_metadata_path=xml_metadata_path)
        finally:
            try:
                os.unlink(transient)
            except OSError:
                pass

        # Re-stamp the app's file_path with the ORIGINAL .qvw — the
        # transient .qvs is an implementation detail.
        app.file_path = str(p_path)
        app.app_name = extraction.app_name
        # Merge extractor diagnostics.
        app.diagnostics.extend(extraction.diagnostics)
        return app

    # ----- public entry: parse QlikView Server meta -------------------

    def parse_server_meta(
        self, path: str, target_app: QlikViewApp | None = None,
    ) -> QlikViewApp:
        """Parse a QMC task XML export (or a directory of them) and
        attach the resulting :ServerTask + :Trigger records to a host
        app. When ``target_app`` is None a fresh host app is created
        named after the path stem — useful for standalone server-meta
        ingestion."""
        from .server_meta import parse_directory, parse_tasks_xml

        p_path = Path(path)
        if p_path.is_dir():
            result = parse_directory(p_path)
        else:
            result = parse_tasks_xml(p_path)

        host = target_app or QlikViewApp(
            app_name=p_path.stem, file_path=str(p_path),
        )
        host.server_tasks.extend(result.tasks)
        host.server_triggers.extend(result.triggers)
        host.lineage_edges.extend(result.edges)
        host.diagnostics.extend(result.diagnostics)
        if target_app is None:
            self.apps.append(host)
        return host

    # ----- public entry: parse a Qlik Sense .qvf -----------------------

    def parse_qvf_file(self, path: str) -> QlikViewApp:
        """Pull the load script + Sense AppObjects out of a .qvf
        (SQLite) and route the script through the same pipeline as
        ``parse_qvs_file``. Sense charts / sheets / dimensions / measures
        become :UiObject IR records with FEEDS_OBJECT edges back to any
        :Attribute they reference."""
        import tempfile

        from .extract_qvf import extract as _qvf_extract
        from .sense_objects import parse_app_objects as _sense_parse

        p_path = Path(path)
        extraction = _qvf_extract(p_path)

        if not extraction.script_text:
            # Soft-fail — still return an app with diagnostics so the
            # estate walker can surface what went wrong.
            app = QlikViewApp(app_name=extraction.app_name, file_path=str(p_path))
            app.diagnostics.extend(extraction.diagnostics)
            self.apps.append(app)
            return app

        with tempfile.NamedTemporaryFile(
            suffix=".qvs", delete=False, mode="w", encoding="utf-8",
        ) as f:
            f.write(extraction.script_text)
            transient = f.name
        try:
            app = self.parse_qvs_file(transient)
        finally:
            try:
                os.unlink(transient)
            except OSError:
                pass

        # Re-stamp the app's file_path with the ORIGINAL .qvf — the
        # transient .qvs is an implementation detail.
        app.file_path = str(p_path)
        app.app_name = extraction.app_name
        app.diagnostics.extend(extraction.diagnostics)

        # Walk Sense AppObjects → :UiObject + FEEDS_OBJECT edges.
        sense = _sense_parse(app, extraction.app_objects_raw)
        app.ui_objects.extend(sense.objects)
        app.lineage_edges.extend(sense.edges)
        app.diagnostics.extend(sense.diagnostics)
        return app

    # ----- public entry: parse a .qvs ----------------------------------

    def parse_qvs_file(
        self,
        path: str,
        *,
        xml_metadata_path: str | None = None,
    ) -> QlikViewApp:
        p = Path(path)
        app = QlikViewApp(app_name=p.stem, file_path=str(p))

        # ---- Stage 1: preprocessor (encoding, includes, macros) --------
        try:
            pre = preprocess(
                p,
                include_root=self.include_root,
                max_depth=self.max_include_depth,
            )
        except FileNotFoundError as e:
            app.parse_errors.append(str(e))
            self.apps.append(app)
            return app
        app.includes.extend(pre.includes)
        # Scrub the preprocessor's harvested SET/LET values — they routinely
        # carry connection strings, bearer tokens, and other secret material.
        # (Same scrub call the visitor's visitSetStmt/visitLetStmt makes; both
        # paths converge on app.variables.)
        from .secrets import scrub as _scrub
        # Build a (name → raw_value) map so we can resolve $(otherVar)
        # references inside each variable's RHS. Used to produce the
        # ``resolved_value`` field that the explorer shows.
        raw_map = {name: (value or "") for name, value, _ in pre.variables}
        for line_no, (name, value, scope) in enumerate(pre.variables, start=1):
            raw_value = value or ""
            value_scrubbed, diags = _scrub(raw_value, artifact=str(p))
            if diags:
                app.diagnostics.extend(diags)
            resolved = _expand_var_chain(raw_value, raw_map, name)
            resolved_scrubbed, rdiags = _scrub(resolved, artifact=str(p))
            if rdiags:
                app.diagnostics.extend(rdiags)
            is_conn_ref = bool(re.match(r"^\s*'?LIB://", raw_value, re.IGNORECASE))
            app.variables.append(Variable(
                name=name,
                expression=value_scrubbed,
                scope=scope,
                app=str(p),
                line=line_no,
                raw_value=value_scrubbed,
                resolved_value=resolved_scrubbed if resolved_scrubbed != value_scrubbed else None,
                is_connection_ref=is_conn_ref,
            ))
        for sub_name, sub_params in pre.subroutines:
            app.subroutines.append(Subroutine(name=sub_name, params=sub_params))
        app.parse_errors.extend(pre.parse_errors)
        # Merge structured diagnostics from the preprocessor (control-flow
        # unrolling emits QV-FOR-DYNAMIC / QV-IF-DYNAMIC / QV-DO-LOOP-* etc).
        app.diagnostics.extend(pre.diagnostics)

        # ---- Stage 2: ANTLR lexer + parser + visitor ------------------
        try:
            self._run_antlr(pre.text, app)
        except Exception as e:
            app.parse_errors.append(f"ANTLR pipeline failed: {e}")

        # ---- Stage 2.4 (Phase 3): connection-store enrichment ---------
        # When LIB CONNECT TO 'foo' yields a DataConnection that's mostly
        # empty (because the script doesn't embed the full string), look
        # the name up in the configured .dconn / odbc.ini / Settings.ini
        # stores and replace the empty record with the resolved one.
        if self.connection_store is not None:
            self._enrich_from_connection_store(app)

        # ---- Stage 2.5 (Phase 2): BINARY load inheritance -------------
        # If the script declared ``BINARY 'upstream.qvw';`` recursively
        # parse the referenced QVW and merge its data model into this
        # app. Depth + cycle guarded so a BINARY chain (A→B→C→A) breaks
        # cleanly with a Diagnostic instead of infinite-recursing.
        if app.binary_load_path:
            self._inherit_from_binary(app, p)

        # ---- Stage 2.6 (Remediation §1.3): lazy resident placeholders -
        # Any ``LOAD ... RESIDENT X`` that doesn't have an X in this
        # app's IR (typically because X lives in a missing BINARY
        # upstream) gets a placeholder Dataset so resident chains never
        # dangle in the graph.
        _lazy_resident_placeholders(app)

        # ---- Stage 5 (Phase 3): leaf-to-root attribute resolver -------
        # Walks every Attribute backward through alias chains, RESIDENT
        # / JOIN / CONCATENATE merges, STORE → QVD links, and embedded
        # SQL column lineage. Emits LineageEdge records the writer
        # consumes. Idempotent — runs after the visitor's own DERIVES_FROM
        # edges, dedup happens via the seen-set in resolve_lineage.
        try:
            from .resolver import resolve_lineage
            resolved = resolve_lineage(app)
            app.lineage_edges.extend(resolved.edges)
            app.diagnostics.extend(resolved.diagnostics)
        except Exception as e:
            app.parse_errors.append(f"Lineage resolver failed: {e}")

        # ---- Stage 5.5 (Remediation §3): variable RESOLVES_TO edges ----
        # var→var (double-expansion) + consumer→var (any node whose
        # user-visible value embeds ``$(name)``).
        try:
            _emit_variable_edges(app)
        except Exception as e:
            app.parse_errors.append(f"Variable edge emission failed: {e}")

        # ---- Stage 6 (Phase 2): constraint inference engine -----------
        # Heuristic-only signals — naming/QVD-hint/auto-association/
        # synthetic-key. Live DB introspection is OUT for v0.2.
        try:
            from .constraints import infer_constraints
            constraints, constraint_diags = infer_constraints(app)
            app.key_constraints.extend(constraints)
            app.diagnostics.extend(constraint_diags)
        except Exception as e:
            app.parse_errors.append(f"Constraint inference failed: {e}")

        # ---- Stage 4: optional XML metadata ---------------------------
        if xml_metadata_path:
            try:
                meta = parse_xml_metadata(xml_metadata_path)
                # We don't currently project sheets/charts onto the IR object;
                # the writer in Phase 4 will consume meta directly when added.
                app.parse_errors.extend([])  # placeholder for symmetry
                self._last_xml_metadata = meta  # type: ignore[attr-defined]
            except Exception as e:
                app.parse_errors.append(f"XML metadata failed: {e}")

        self.apps.append(app)
        return app

    # ----- public: write to Neo4j ---------------------------------------

    def push_to_neo4j(self, app: QlikViewApp) -> None:
        _write_app_to_neo4j(self.driver, app)

    # ----- public: serialise the in-memory apps to JSON ----------------

    def export_to_json(self, output_path: str) -> None:
        payload = {
            "export_date": datetime.now(timezone.utc).isoformat(),
            "total_apps": len(self.apps),
            "apps": [a.to_dict() for a in self.apps],
        }
        Path(output_path).write_text(json.dumps(payload, indent=2))

    # ----- public: thin sqlglot / expression helpers used by tests -----

    def extract_sql_tables(self, sql: str) -> list[str]:
        return _sql_extract_tables(sql)

    def _extract_source_fields_from_expression(self, expr: str) -> list[str]:
        return extract_source_fields(expr)

    # ----- private: connection-store enrichment (Phase 3) -------------

    def _enrich_from_connection_store(self, app: QlikViewApp) -> None:
        """Resolve each ``LIB CONNECT TO 'name'`` against the configured
        store and replace the bare DataConnection with the richer record.

        A DataConnection is considered "bare" when it has no host AND no
        database — the typical shape produced when the script only
        references a name. If the visitor already pulled host/db from an
        inline string, we leave it alone.
        """
        if not app.data_connections:
            return
        resolved: list = []
        replaced = False
        for c in app.data_connections:
            if c.host or c.database:
                resolved.append(c)
                continue
            hit = self.connection_store.resolve(c.name)
            if hit is None:
                resolved.append(c)
                continue
            replaced = True
            # Preserve the original name (the LIB key in the script wins
            # over any name stored in the .dconn file).
            from dataclasses import replace as _replace
            resolved.append(_replace(hit, name=c.name))
        app.data_connections = resolved
        # Merge any diagnostics the store accumulated during the resolve
        # (e.g. unparseable .dconn files).
        if self.connection_store.diagnostics:
            app.diagnostics.extend(self.connection_store.diagnostics)
            self.connection_store.diagnostics = []

        # Also re-emit the platform record if the resolved kind differs
        # from the visitor's classification (a bare LIB name like
        # 'snowflake-prod' was previously classified by string-sniffing).
        if replaced:
            from .models import DataPlatform
            for c in app.data_connections:
                if not any(p.kind == c.platform_kind for p in app.platforms):
                    app.platforms.append(DataPlatform(
                        kind=c.platform_kind,
                        vendor_cloud=None,
                        account_locator=c.host,
                    ))

    # ----- private: BINARY load inheritance (Phase 2) -----------------

    # Tracks BINARY chains during a single parse_qvs_file call so a
    # cyclic reference (A → B → A) breaks deterministically with a
    # diagnostic instead of infinite-recursing.
    _BINARY_MAX_DEPTH = 5

    def _inherit_from_binary(self, app: QlikViewApp, current_path: Path) -> None:
        """Follow ``app.binary_load_path`` recursively and merge the
        upstream app's data model into ``app``.

        Cross-app stitching is FREE at the graph layer because
        :PhysicalSource, :Dataset, and :Attribute all hash by qualified-
        name including the producing file path — two scripts that touch
        the same QVD share its node id without any extra plumbing.

        Here we additionally COPY the upstream's Datasets + Attributes
        into the current app (preserving the upstream qname so the IDs
        collide) and emit ``DERIVES_FROM`` edges so the lineage view can
        walk the inheritance chain.
        """
        from .models import Diagnostic, LineageEdge
        from .ids import sha256_id

        visited: set[str] = {str(current_path.resolve())}
        self._inherit_recursive(app, app.binary_load_path, current_path,
                                visited, depth=0)

    def _inherit_recursive(
        self,
        host_app: QlikViewApp,
        upstream_rel_path: str,
        from_path: Path,
        visited: set[str],
        depth: int,
    ) -> None:
        from .models import Diagnostic, LineageEdge
        from .ids import sha256_id

        if depth > self._BINARY_MAX_DEPTH:
            host_app.diagnostics.append(Diagnostic(
                level="warn", code="QV-BINARY-DEPTH",
                message=(
                    f"BINARY chain exceeded {self._BINARY_MAX_DEPTH} levels — "
                    f"stopping recursion at {upstream_rel_path!r}"
                ),
                artifact=host_app.file_path, line=None,
            ))
            return

        # Resolve the upstream path relative to the host's directory
        # (matches QlikView's resolution semantics for BINARY paths).
        # Windows backslashes routinely appear in real scripts (the
        # exporter writes the OS-native separator); normalise so POSIX
        # hosts can resolve them.
        upstream_norm = upstream_rel_path.replace("\\", "/")
        upstream_p = (from_path.parent / upstream_norm).resolve()

        # Real fixtures frequently reference a ``.qvw`` upstream while
        # only the ``.qvs`` export is on disk (and vice-versa). Try
        # extension swap + sibling-folder search before giving up — this
        # is what makes the v0.2 plan's vertical slice run on the
        # ``DSH_Executive`` fixture which BINARYs into a sibling .qvs.
        if not upstream_p.exists():
            alt = _binary_fallback_resolve(upstream_p, from_path.parent)
            if alt is not None:
                host_app.diagnostics.append(Diagnostic(
                    level="info", code="QV-BINARY-FALLBACK",
                    message=(
                        f"BINARY target resolved via extension fallback: "
                        f"{upstream_rel_path!r} → {alt.name!r}"
                    ),
                    artifact=host_app.file_path, line=None,
                ))
                upstream_p = alt
        upstream_key = str(upstream_p)
        if upstream_key in visited:
            host_app.diagnostics.append(Diagnostic(
                level="warn", code="QV-BINARY-CYCLE",
                message=(
                    f"BINARY chain cycle detected at {upstream_p!s} — "
                    f"chain broken to prevent infinite recursion"
                ),
                artifact=host_app.file_path, line=None,
            ))
            return
        if not upstream_p.exists():
            host_app.diagnostics.append(Diagnostic(
                level="warn", code="QV-BINARY-NOT-FOUND",
                message=f"BINARY target not found: {upstream_p!s}",
                artifact=host_app.file_path, line=None,
            ))
            return

        # Parse the upstream app — QVW (binary) or QVS (text).
        # Use a fresh parser instance so its own writer doesn't recurse
        # into Neo4j and its apps list stays isolated.
        upstream_parser = QlikViewParser(
            self.neo4j_uri, self.neo4j_user, self.neo4j_password,
            include_root=self.include_root,
            max_include_depth=self.max_include_depth,
        )
        # Mock the driver to a no-op so the upstream parse doesn't write
        # to Neo4j twice (the host write will pick up the merged IR).
        from unittest.mock import MagicMock
        upstream_parser.driver = MagicMock()
        try:
            if upstream_p.suffix.lower() == ".qvw":
                upstream_app = upstream_parser.parse_qvw_file(str(upstream_p))
            else:
                upstream_app = upstream_parser.parse_qvs_file(str(upstream_p))
        finally:
            upstream_parser.close()

        visited.add(upstream_key)

        # Merge data-model layer. Use a set keyed on qname for dedup so a
        # diamond-shaped chain (A → B + A → C → B) doesn't double-insert.
        # Each imported Dataset is re-stamped with inherited_via="BINARY"
        # so the writer / explorer can distinguish first-hand tables from
        # inherited ones (the remediation plan §1 requires this property).
        from dataclasses import replace as _replace
        existing_ds = {d.qname for d in host_app.datasets}
        for ds in upstream_app.datasets:
            if ds.qname in existing_ds:
                continue
            host_app.datasets.append(_replace(
                ds,
                inherited_via=ds.inherited_via or "BINARY",
                inherited_from=ds.inherited_from or upstream_app.file_path,
            ))
            existing_ds.add(ds.qname)

        existing_attrs = {a.qname for a in host_app.attributes}
        for a in upstream_app.attributes:
            if a.qname not in existing_attrs:
                host_app.attributes.append(a)
                existing_attrs.add(a.qname)

        # Merge physical sources + connections too (the QVDs the upstream
        # produced are inputs from the host's perspective).
        existing_sources = {s.qname for s in host_app.physical_sources}
        for s in upstream_app.physical_sources:
            if s.qname not in existing_sources:
                host_app.physical_sources.append(s)
                existing_sources.add(s.qname)

        # Emit DERIVES_FROM edges from the host (as a single conceptual
        # node — the script itself) to each inherited Dataset. The
        # graph-resolver in Phase 3 will fan these out per attribute.
        host_node_id = sha256_id(f"qlikscript::{host_app.file_path}")
        for ds in upstream_app.datasets:
            host_app.lineage_edges.append(LineageEdge(
                src_id=host_node_id,
                dst_id=sha256_id(ds.qname),
                rel="DERIVES_FROM",
                transform="BINARY_LOAD",
                evidence=f"BINARY '{upstream_rel_path}'",
            ))

        host_app.diagnostics.append(Diagnostic(
            level="info", code="QV-BINARY-INHERITED",
            message=(
                f"Inherited {len(upstream_app.datasets)} datasets / "
                f"{len(upstream_app.attributes)} attributes from "
                f"BINARY upstream {upstream_p.name}"
            ),
            artifact=host_app.file_path, line=None,
        ))

        # If the upstream itself has a BINARY directive, recurse.
        if upstream_app.binary_load_path:
            self._inherit_recursive(
                host_app, upstream_app.binary_load_path, upstream_p,
                visited, depth=depth + 1,
            )

    # ----- private: drive the ANTLR pipeline ---------------------------

    def _run_antlr(self, text: str, app: QlikViewApp) -> None:
        stream = InputStream(text)
        lexer = QlikViewLexer(stream)
        lex_errs = CollectingErrorListener(source="lexer")
        lexer.removeErrorListeners()
        lexer.addErrorListener(lex_errs)

        tokens = CommonTokenStream(lexer)
        parser = _AntlrParser(tokens)
        parse_errs = CollectingErrorListener(source="parser")
        parser.removeErrorListeners()
        parser.addErrorListener(parse_errs)

        tree = parser.script()

        if not os.environ.get("STRICT_PARSING"):
            # Soft mode — annotate, but don't stop downstream IR build.
            for e in lex_errs.errors + parse_errs.errors:
                app.parse_errors.append(
                    f"[{e.source}] line {e.line}:{e.column} {e.message}"
                )
        else:
            if lex_errs.errors or parse_errs.errors:
                raise RuntimeError("STRICT_PARSING enabled and grammar errors detected")

        visitor = QlikViewIRVisitor(app, token_stream=tokens)
        visitor.visit(tree)

        # ---- Stage 2.5: star-field resolution -------------------------
        # `LOAD *` inherits its column list from the LOAD it derives from.
        # Resolution happens AFTER the full visitor pass because a star LOAD
        # may reference a table that wasn't loaded yet at visit-time.
        _resolve_star_fields(app)
