"""Stage 2 — ANTLR visitor that turns the parse tree into ``QlikViewApp`` IR.

The grammar captures LOAD bodies and SQL blocks loosely so we don't duplicate
SQL parsing inside the QlikView grammar. This visitor pulls the structured
data back out by:
* Re-tokenising the LOAD body to find column-list / clauses.
* Handing SQL bodies to sqlglot via ``sql_block.extract_tables``.
"""
from __future__ import annotations

import re

from ..generated.QlikViewParser import QlikViewParser
from ..generated.QlikViewParserVisitor import QlikViewParserVisitor
from ..models import (
    Attribute,
    Concatenation,
    Connection,
    ConnectionType,
    DataConnection,
    DataPlatform,
    Dataset,
    Diagnostic,
    Field,
    Join,
    LineageEdge,
    LoadStatement,
    PhysicalSource,
    QlikViewApp,
    SourceType,
    Subroutine,
    Variable,
)
from ..secrets import REDACTED, fingerprint, scrub
from ..sql_block import extract_columns as sql_extract_columns
from ..sql_block import extract_tables as sql_extract_tables

# ---------------------------------------------------------------------------
# Field-expression utilities — used by the visitor to decompose LOAD bodies
# without re-grammaring the whole expression sub-language.
# ---------------------------------------------------------------------------

_RE_AS = re.compile(r"\bAS\s+([A-Za-z_][A-Za-z0-9_]*|\[[^\]]+\])", re.IGNORECASE)
_RE_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_RE_RESIDENT = re.compile(r"\bRESIDENT\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
_RE_FROM = re.compile(
    r"\bFROM\s+(?:'([^']+)'|\[([^\]]+)\])\s*(?:\(([^)]*)\))?",
    re.IGNORECASE,
)

_BUILTIN_FUNCTIONS = {
    "IF", "THEN", "ELSE", "END", "AND", "OR", "NOT", "NULL", "TRUE", "FALSE",
    "UPPER", "LOWER", "TRIM", "LEFT", "RIGHT", "MID", "LEN", "REPLACE",
    "SUM", "COUNT", "AVG", "MIN", "MAX", "ONLY", "FIRSTVALUE", "LASTVALUE",
    "DATE", "DATE_FORMAT", "DATE_HASH", "TODAY", "NOW", "YEAR", "MONTH", "DAY",
    "MAKEDATE", "ADDMONTHS", "WEEK", "WEEKDAY",
    "APPLYMAP", "PICK", "MATCH", "WILDMATCH", "ROUND", "CEIL", "FLOOR", "ABS",
    "DUAL", "NUM", "TEXT", "ROW_NUMBER", "OVER", "PARTITION", "BY", "ORDER",
    "AS", "IS", "IN", "LIKE", "BETWEEN", "DESC", "ASC", "DISTINCT",
}
_SQL_KEYWORDS = {
    "SELECT", "FROM", "WHERE", "GROUP", "ORDER", "BY", "HAVING", "JOIN",
    "INNER", "LEFT", "RIGHT", "OUTER", "FULL", "ON", "AND", "OR", "NOT",
    "UNION", "ALL", "DISTINCT", "AS", "QUALIFY",
}


def split_top_level_commas(text: str) -> list[str]:
    """Split ``text`` on commas that aren't inside parens or string literals."""
    chunks: list[str] = []
    buf: list[str] = []
    depth = 0
    in_str = False
    str_char = ""
    for ch in text:
        if in_str:
            buf.append(ch)
            if ch == str_char:
                in_str = False
            continue
        if ch in ("'", '"'):
            in_str = True
            str_char = ch
            buf.append(ch)
            continue
        if ch == "(":
            depth += 1
            buf.append(ch)
            continue
        if ch == ")":
            depth = max(0, depth - 1)
            buf.append(ch)
            continue
        if ch == "," and depth == 0:
            chunks.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if buf:
        chunks.append("".join(buf))
    return chunks


def extract_source_fields(expression: str) -> list[str]:
    """Identifiers in ``expression`` that look like field references."""
    if not expression:
        return []
    # Strip strings so identifiers inside literals don't leak through.
    cleaned = re.sub(r"'[^']*'", "", expression)
    cleaned = re.sub(r'"[^"]*"', "", cleaned)
    out: list[str] = []
    seen: set[str] = set()
    for m in _RE_IDENT.finditer(cleaned):
        tok = m.group(0)
        up = tok.upper()
        if up in _BUILTIN_FUNCTIONS or up in _SQL_KEYWORDS:
            continue
        if up in seen:
            continue
        seen.add(up)
        out.append(tok)
    return out


# ---------------------------------------------------------------------------
# The visitor
# ---------------------------------------------------------------------------

class QlikViewIRVisitor(QlikViewParserVisitor):
    """Walk the parse tree and populate a ``QlikViewApp``."""

    def __init__(self, app: QlikViewApp, *, token_stream=None, secret_salt: bytes | None = None):
        super().__init__()
        self.app = app
        self.token_stream = token_stream
        # State carried across LOAD statements for implicit-target resolution.
        self._last_table: str | None = None
        # v0.2 parser-state tracking — QUALIFY/UNQUALIFY affect subsequent LOADs.
        self._qualify_active: bool = False
        # Salt for secret_fingerprint. Tests pass a fixed salt for determinism;
        # production callers supply a per-deployment random salt.
        self._secret_salt: bytes = secret_salt or b"qlikview-parser-default-salt"

    # ----- connection ----------------------------------------------------

    def visitConnectStmt(self, ctx: QlikViewParser.ConnectStmtContext):
        kind_ctx = ctx.connectKind()
        kind = ConnectionType.ODBC
        if kind_ctx.OLEDB():
            kind = ConnectionType.OLEDB
        elif kind_ctx.LIB():
            kind = ConnectionType.LIB
        raw = self._connection_target_text(ctx.connectionTarget())
        name, ds = _parse_connection_target(kind, raw)

        # v0.2 — scrub the connection string BEFORE we let it touch the IR.
        scrubbed, scrub_diags = scrub(raw, artifact=self.app.file_path)
        self.app.diagnostics.extend(scrub_diags)

        # Scrub the data_source too — ODBC connection strings carry the
        # full DSN-style payload (including PWD=...) in this field.
        ds_scrubbed, ds_diags = scrub(ds or "", artifact=self.app.file_path)
        if ds_diags:
            self.app.diagnostics.extend(ds_diags)

        # dedup v0.1 by (type, name uppercased)
        already = False
        for existing in self.app.connections:
            if existing.type == kind and existing.name.upper() == name.upper():
                already = True
                break
        if not already:
            self.app.connections.append(Connection(
                name=name, type=kind,
                data_source=ds_scrubbed if ds else None,
                # Always store the SCRUBBED form, never the raw.
                connection_string=scrubbed if kind != ConnectionType.ODBC else None,
            ))

        # v0.2 — emit DataPlatform + DataConnection. We sniff a plaintext
        # secret to compute a fingerprint, then immediately drop it.
        platform_kind = _classify_platform(raw)
        platform = DataPlatform(
            kind=platform_kind,
            vendor_cloud=_vendor_cloud_for(platform_kind),
            account_locator=_account_locator(raw),
        )
        if not any(p.qname == platform.qname for p in self.app.platforms):
            self.app.platforms.append(platform)

        secret_fp = None
        secret_match = re.search(
            r"(?i)(?:password|pwd|accountkey|sas|token)\s*=\s*([^;\s\"']+)",
            raw,
        )
        if secret_match and secret_match.group(1) != REDACTED:
            secret_fp = fingerprint(secret_match.group(1), self._secret_salt)

        conn_v2 = DataConnection(
            name=name,
            platform_kind=platform.kind,
            driver=kind.value,
            host=_extract_kv(raw, "server") or _extract_kv(raw, "host"),
            database=_extract_kv(raw, "database") or _extract_kv(raw, "db"),
            schema=_extract_kv(raw, "schema"),
            warehouse=_extract_kv(raw, "warehouse"),
            role=_extract_kv(raw, "role"),
            region=_extract_kv(raw, "region"),
            auth_method=_auth_method(raw),
            secret_ref=None,             # vault mapping happens at config time
            secret_fingerprint=secret_fp,
            raw_locator_redacted=scrubbed,
        )
        if not any(c.qname == conn_v2.qname for c in self.app.data_connections):
            self.app.data_connections.append(conn_v2)
        return None

    def _connection_target_text(self, ctx: QlikViewParser.ConnectionTargetContext) -> str:
        # STRING | LBRACK ... RBRACK | BRACKETED
        if ctx.STRING():
            return ctx.STRING().getText().strip("'")
        if ctx.BRACKETED():
            return ctx.BRACKETED().getText().strip("[]")
        body = ctx.connectionBody()
        if body is not None:
            return body.getText()
        return ctx.getText()

    # ----- LOAD ----------------------------------------------------------

    def visitLoadStmt(self, ctx: QlikViewParser.LoadStmtContext):
        label = None
        if ctx.tableLabel() is not None:
            tl = ctx.tableLabel()
            if tl.ID() is not None:
                label = tl.ID().getText()
            elif tl.BRACKETED() is not None:
                label = tl.BRACKETED().getText().strip("[]")
        body_text = self._original_text(ctx.loadBody())
        line = ctx.start.line if ctx.start else 0
        load = self._build_load_from_body(label, body_text, line, ctx)
        if load is not None:
            load.is_mapping = ctx.mappingFlag() is not None
            self.app.loads.append(load)
            self._last_table = load.table_name
            self._emit_synthetic_fields(load, body_text)
            # Phase 3.5 — LOAD ... FROM '<file>' (qvd|csv|...) emits a
            # :PhysicalSource so the cross-parser stitching contract
            # holds (two scripts that LOAD the same QVD path collide on
            # the same source id). Previously only STORE INTO emitted
            # this side; LOAD FROM was the missing half.
            if load.source_type in (SourceType.QVD, SourceType.FILE) and load.source_table:
                kind = "qvd" if load.source_type == SourceType.QVD else "file"
                src = PhysicalSource(
                    connection=None, kind=kind, locator=load.source_table,
                    declared_in=self.app.file_path,
                )
                if not any(p.qname == src.qname for p in self.app.physical_sources):
                    self.app.physical_sources.append(src)
            # Remediation §2 — emit Attribute IR (leaf nodes the writer
            # turns into :Attribute + HAS_ATTRIBUTE) and DERIVES_FROM
            # edges from each attribute to the source-field identifiers
            # referenced inside its expression. SQL-sourced loads get
            # their cross-source DERIVES_FROM in the resolver pass.
            self._emit_attributes_for_load(load, body_text)
        return None

    # ----- SQL block (statement-level — rare, usually paired with LOAD) --

    def visitSqlStmt(self, ctx: QlikViewParser.SqlStmtContext):
        # Most SQL blocks are absorbed by an adjacent LOAD via the build path.
        # A stand-alone SQL statement still produces a synthetic LoadStatement
        # so the source table is captured.
        sql = ctx.SQL_BLOCK().getText().rstrip(";")
        attached_target = None
        if self.app.loads and self.app.loads[-1].sql_query is None and \
                self.app.loads[-1].source_type == SourceType.UNKNOWN:
            # Attach to the immediately preceding LOAD if it was orphaned.
            target = self.app.loads[-1]
            target.sql_query = sql
            target.source_type = SourceType.SQL
            tables = sql_extract_tables(sql)
            if tables:
                target.source_table = tables[0]
            attached_target = target

        # v0.2 — emit PhysicalSource(db_table) + Dataset + Attribute records.
        # We attribute the connection to the most recently seen DataConnection
        # (QlikView semantics: the active CONNECT scope at this point in the
        # script). When no connection is in scope yet, we still emit a
        # source with connection=None.
        active_conn_name = (
            self.app.data_connections[-1].name
            if self.app.data_connections else None
        )
        tables = sql_extract_tables(sql) or []
        columns = sql_extract_columns(sql) or []
        for tbl in tables:
            src = PhysicalSource(
                connection=active_conn_name,
                kind="db_table",
                locator=tbl,
                declared_in=self.app.file_path,
            )
            if not any(p.qname == src.qname for p in self.app.physical_sources):
                self.app.physical_sources.append(src)

        # Dataset: prefer the attached LOAD's table_name, fall back to the
        # source table name.
        ds_name = (
            attached_target.table_name if attached_target else (
                tables[0] if tables else None
            )
        )
        if ds_name:
            ds = Dataset(
                name=ds_name,
                origin="sql",
                app=self.app.file_path,
            )
            if not any(d.qname == ds.qname for d in self.app.datasets):
                self.app.datasets.append(ds)
            # Emit Attributes — one per projected column we resolved from SQL.
            for i, col in enumerate(columns):
                attr = Attribute(
                    dataset=ds.qname,
                    name=col,
                    ordinal=i,
                    source_expr=col,
                )
                if not any(a.qname == attr.qname for a in self.app.attributes):
                    self.app.attributes.append(attr)
        return None

    # ----- JOIN ----------------------------------------------------------

    def visitJoinStmt(self, ctx: QlikViewParser.JoinStmtContext):
        prefix = ctx.joinPrefix() or ctx.keepPrefix()
        join_type = (prefix.getText() if prefix is not None else "INNER").upper()
        if ctx.KEEP() is not None:
            join_type = f"{join_type} KEEP"
        explicit = ctx.joinTarget() is not None
        target = ctx.joinTarget().ID().getText() if explicit else (self._last_table or "")
        body_text = self._original_text(ctx.loadBody())
        src_m = _RE_RESIDENT.search(body_text)
        source = src_m.group(1) if src_m else ""
        join = Join(target_table=target, source_table=source, join_type=join_type)
        # Explicit-target joins are emitted at the head so consumers iterating
        # ``app.joins[0]`` after filtering by ``target_table`` get the explicit
        # match before any implicit fallback.
        if explicit:
            self.app.joins.insert(0, join)
        else:
            self.app.joins.append(join)

        # Solution plan §1.4 — JOIN-block LOADs are a regression trap:
        # the field walk on visitLoadStmt never runs for them. Emit the
        # join's projected attributes ONTO THE JOIN TARGET (that's the
        # QlikView semantic — LEFT JOIN(EmpSummary) brings the source
        # fields into EmpSummary's row), and emit DERIVES_FROM from the
        # source-table's matching attribute → the merged attribute.
        if target:
            self._emit_attributes_for_join(
                target_table=target, source_table=source,
                join_type=join_type, body=body_text,
            )
        return None

    def _emit_attributes_for_join(
        self, *, target_table: str, source_table: str,
        join_type: str, body: str,
    ) -> None:
        """Solution plan §2.2 — JOIN body LOAD emits attributes onto the
        join *target* dataset and DERIVES_FROM edges from the source
        table's same-named attribute → the new attribute.

        Plain renames (``DeptID AS Department``) are preserved as a
        rename DERIVES_FROM chain.
        """
        from ..ids import attribute_qname, dataset_qname, sha256_id
        from ..models import Dataset, LineageEdge

        # Make sure the target Dataset exists in the IR — joins onto an
        # inherited-via-BINARY upstream don't visit the LOAD that
        # declared it.
        target_ds = Dataset(name=target_table, origin="join_result",
                            app=self.app.file_path)
        if not any(d.qname == target_ds.qname for d in self.app.datasets):
            self.app.datasets.append(target_ds)
        target_q = target_ds.qname
        source_q = (
            dataset_qname(self.app.file_path, source_table) if source_table else None
        )
        existing_attrs = {a.qname for a in self.app.attributes}

        # The body is the LOAD body of the join. Strip the RESIDENT
        # trailer so the field-section walk stays inside the projection.
        boundary = _RE_RESIDENT.search(body)
        field_section = body[: boundary.start()] if boundary else body
        field_section = field_section.split(";")[0]

        # Use the same alias logic as the main LOAD walker.
        for ordinal, chunk in enumerate(split_top_level_commas(field_section)):
            piece = chunk.strip().rstrip(";").strip()
            if not piece or piece == "*":
                continue
            alias_m = _RE_AS.search(piece)
            if alias_m:
                name = alias_m.group(1).strip("[]")
                source_expr = piece[: alias_m.start()].strip()
            else:
                ident = _RE_IDENT.search(piece)
                if ident is None:
                    continue
                name = ident.group(0)
                source_expr = piece
            transform_chain = tuple(
                m.group(1) for m in re.finditer(
                    r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", source_expr,
                )
                if m.group(1).upper() in _BUILTIN_FUNCTIONS
            )
            attr = Attribute(
                dataset=target_q, name=name, ordinal=ordinal,
                source_expr=source_expr, transform_chain=transform_chain,
            )
            if attr.qname not in existing_attrs:
                self.app.attributes.append(attr)
                existing_attrs.add(attr.qname)

            # DERIVES_FROM edges from the source table's matching
            # identifiers → this merged attribute.
            if source_q is None:
                continue
            for ref in extract_source_fields(source_expr):
                upstream_q = attribute_qname(source_q, ref)
                # Convention: dependent (merged attr) → upstream (join source).
                self.app.lineage_edges.append(LineageEdge(
                    src_id=sha256_id(attr.qname),
                    dst_id=sha256_id(upstream_q),
                    rel="DERIVES_FROM",
                    transform=":".join(transform_chain) or f"JOIN:{join_type}",
                    join_type=join_type,
                    confidence=0.95,
                    evidence=source_expr[:120],
                ))

    # ----- CONCATENATE ---------------------------------------------------

    def visitConcatStmt(self, ctx: QlikViewParser.ConcatStmtContext):
        target = None
        if ctx.joinTarget() is not None:
            target = ctx.joinTarget().ID().getText()
        target = target or self._last_table or ""
        body_text = self._original_text(ctx.loadBody()) if ctx.loadBody() else ""
        src_m = _RE_RESIDENT.search(body_text) if body_text else None
        source = src_m.group(1) if src_m else None
        self.app.concatenations.append(Concatenation(
            target_table=target, source_table=source,
        ))
        return None

    # ----- SET / LET -----------------------------------------------------

    def visitSetStmt(self, ctx: QlikViewParser.SetStmtContext):
        return self._emit_variable(ctx, scope="set")

    def visitLetStmt(self, ctx: QlikViewParser.LetStmtContext):
        return self._emit_variable(ctx, scope="let")

    def _emit_variable(self, ctx, *, scope: str):
        """Shared SET/LET emission. Skips silently if the preprocessor
        already harvested this variable (the harvester runs before the
        ANTLR pass and produces the same record set)."""
        name = ctx.ID().getText()
        value = self._original_text(ctx.setValue()).strip()
        value_scrubbed, diags = scrub(value, artifact=self.app.file_path)
        if diags:
            self.app.diagnostics.extend(diags)
        # Dedup vs the preprocessor's harvest. The harvest fires from
        # ``core.parse_qvs_file`` BEFORE the ANTLR pass; we never want
        # two Variable records for the same SET/LET.
        if any(v.name == name and v.scope == scope for v in self.app.variables):
            return None
        is_conn_ref = bool(
            re.match(r"^\s*'?LIB://", value, re.IGNORECASE)
        )
        self.app.variables.append(Variable(
            name=name, expression=value_scrubbed, scope=scope,
            app=self.app.file_path,
            line=ctx.start.line if ctx.start else None,
            raw_value=value_scrubbed,
            is_connection_ref=is_conn_ref,
        ))
        return None

    # ----- SUB / CALL ----------------------------------------------------

    def visitSubStmt(self, ctx: QlikViewParser.SubStmtContext):
        name = ctx.ID().getText()
        params: list[str] = []
        if ctx.subParams() is not None:
            params = [p.getText() for p in ctx.subParams().ID()]
        self.app.subroutines.append(Subroutine(name=name, params=params))
        # Recurse so any LOADs inside the body still get captured (best effort).
        return self.visitChildren(ctx)

    # ----- helpers -------------------------------------------------------

    def _original_text(self, ctx) -> str:
        if ctx is None:
            return ""
        if self.token_stream is not None and ctx.start is not None and ctx.stop is not None:
            return self.token_stream.getText(ctx.start, ctx.stop)
        return ctx.getText()

    def _build_load_from_body(
        self,
        label: str | None,
        body: str,
        line: int,
        ctx: QlikViewParser.LoadStmtContext,
    ) -> LoadStatement | None:
        # The body string already includes whatever LOAD captured up to its
        # terminating ';'. We need to split out: (a) the field section, and
        # (b) clauses that determine source_type (SQL block, RESIDENT, FROM).
        if label is None:
            # Unlabelled LOADs in this grammar arise only from JOIN/CONCAT
            # paths, which we handle in their own visit methods.
            return None

        # Find clause boundaries.
        sql_m = re.search(r"\bSQL\s+SELECT\b", body, re.IGNORECASE)
        res_m = _RE_RESIDENT.search(body)
        from_m = _RE_FROM.search(body)

        boundary = self._earliest(sql_m, res_m, from_m)
        field_section = body[:boundary] if boundary is not None else body
        field_section = field_section.split(";")[0]

        fields = self._extract_field_names(field_section)

        source_type = SourceType.UNKNOWN
        source_table: str | None = None
        sql_query: str | None = None

        # Find a sibling SQL_BLOCK token immediately after this LOAD (the
        # grammar split them into separate statements).
        sibling_sql = self._sibling_sql_block(ctx)

        if sql_m is not None and (res_m is None or sql_m.start() <= res_m.start()) \
                and (from_m is None or sql_m.start() <= from_m.start()):
            # Body itself contains the SQL block (rare — grammar usually splits).
            source_type = SourceType.SQL
            sql_query = body[sql_m.start():].rstrip("; \t\r\n")
            tables = sql_extract_tables(sql_query)
            if tables:
                source_table = tables[0]
        elif res_m is not None and (from_m is None or res_m.start() <= from_m.start()):
            source_type = SourceType.RESIDENT
            source_table = res_m.group(1)
        elif from_m is not None:
            file_path = from_m.group(1) or from_m.group(2)
            modifiers = (from_m.group(3) or "").lower()
            if "qvd" in modifiers or (file_path or "").lower().endswith(".qvd"):
                source_type = SourceType.QVD
            else:
                source_type = SourceType.FILE
            source_table = file_path
        elif sibling_sql is not None:
            source_type = SourceType.SQL
            sql_query = sibling_sql.rstrip("; \t\r\n")
            tables = sql_extract_tables(sql_query)
            if tables:
                source_table = tables[0]

        return LoadStatement(
            table_name=label,
            source_type=source_type,
            fields=fields,
            sql_query=sql_query,
            source_table=source_table,
            line_number=line,
        )

    @staticmethod
    def _earliest(*matches) -> int | None:
        positions = [m.start() for m in matches if m is not None]
        return min(positions) if positions else None

    def _sibling_sql_block(self, ctx: QlikViewParser.LoadStmtContext) -> str | None:
        """Find the SQL_BLOCK that immediately follows this LOAD in script order."""
        parent = ctx.parentCtx  # statement
        if parent is None:
            return None
        script_ctx = parent.parentCtx  # script
        if script_ctx is None:
            return None
        children = list(script_ctx.children or [])
        try:
            idx = children.index(parent)
        except ValueError:
            return None
        # Look ahead for the next statement whose body is a sqlStmt.
        for next_stmt in children[idx + 1 : idx + 3]:
            if not hasattr(next_stmt, "getChild"):
                continue
            inner = next_stmt.getChild(0) if next_stmt.getChildCount() else None
            if isinstance(inner, QlikViewParser.SqlStmtContext):
                return inner.SQL_BLOCK().getText()
            # If the next statement is something other than SQL, stop looking.
            if isinstance(inner, (QlikViewParser.LoadStmtContext,
                                  QlikViewParser.ConnectStmtContext,
                                  QlikViewParser.JoinStmtContext,
                                  QlikViewParser.ConcatStmtContext)):
                return None
        return None

    @staticmethod
    def _extract_field_names(field_text: str) -> list[str]:
        out: list[str] = []
        for chunk in split_top_level_commas(field_text):
            piece = chunk.strip().rstrip(";").strip()
            if not piece:
                continue
            # Bare wildcard:  LOAD *   |   LOAD DISTINCT *
            if piece == "*" or re.fullmatch(r"(?:DISTINCT\s+)?\*", piece, re.IGNORECASE):
                out.append("*")
                continue
            alias_m = _RE_AS.search(piece)
            if alias_m:
                out.append(alias_m.group(1).strip("[]"))
                continue
            ident = _RE_IDENT.search(piece)
            if ident:
                out.append(ident.group(0))
        return out

    # ----- v0.2 — new statement visitors --------------------------------

    def visitBinaryStmt(self, ctx: QlikViewParser.BinaryStmtContext):
        """``BINARY '<path>';`` — record the upstream-app path on the IR.
        The orchestrator's ``_inherit_from_binary`` follows it (with
        depth + cycle guards) and merges the upstream data model in
        post-visit."""
        target_ctx = ctx.binaryTarget()
        if target_ctx.STRING() is not None:
            path = target_ctx.STRING().getText().strip("'")
        elif target_ctx.BRACKETED() is not None:
            path = target_ctx.BRACKETED().getText().strip("[]")
        else:
            path = target_ctx.getText()
        # Only honour the FIRST BINARY directive (QlikView allows ≤1 and
        # requires it at the script top; subsequent ones are warnings).
        if self.app.binary_load_path is None:
            self.app.binary_load_path = path
        else:
            self.app.diagnostics.append(Diagnostic(
                level="warn",
                code="QV-BINARY-DUPLICATE",
                message=(
                    f"Multiple BINARY directives — keeping the first "
                    f"({self.app.binary_load_path!r}); ignoring {path!r}"
                ),
                artifact=self.app.file_path,
                line=ctx.start.line if ctx.start else None,
            ))
        return None

    def visitStoreStmt(self, ctx: QlikViewParser.StoreStmtContext):
        """``STORE <table> INTO '<path>' (qvd|csv|txt);`` — producer-side
        of the QVD lineage chain. Emits a ``PhysicalSource(qvd|file)`` plus
        a ``STORED_AS`` lineage edge from the producing Dataset."""
        src_ctx = ctx.storeSource()
        target_ctx = ctx.storeTarget()
        if src_ctx is None or target_ctx is None:
            return None

        # Source table name — the first ID/BRACKETED in storeSource
        src_name = None
        if src_ctx.ID():
            src_name = src_ctx.ID()[0].getText()
        elif src_ctx.BRACKETED():
            src_name = src_ctx.BRACKETED()[0].getText().strip("[]")
        if not src_name:
            return None

        # Target path — STRING or BRACKETED
        if target_ctx.STRING() is not None:
            target_path = target_ctx.STRING().getText().strip("'")
        elif target_ctx.BRACKETED() is not None:
            target_path = target_ctx.BRACKETED().getText().strip("[]")
        else:
            return None

        # Format hint: explicit ``(qvd)`` / ``(csv)`` / ``(txt)`` OR sniff
        # from the path suffix.
        fmt_ctx = target_ctx.storeFormat()
        fmt = fmt_ctx.ID().getText().lower() if fmt_ctx and fmt_ctx.ID() else None
        if not fmt:
            lower = target_path.lower()
            if lower.endswith(".qvd"):
                fmt = "qvd"
            elif lower.endswith(".csv"):
                fmt = "csv"
            elif lower.endswith(".txt"):
                fmt = "txt"
            else:
                fmt = "file"

        sink = PhysicalSource(
            connection=None,
            kind="qvd" if fmt == "qvd" else "file",
            locator=target_path,
            declared_in=self.app.file_path,
        )
        if not any(p.qname == sink.qname for p in self.app.physical_sources):
            self.app.physical_sources.append(sink)

        # Resolve the source Dataset by name if one exists in this app.
        from ..ids import sha256_id  # local import — avoid cycle
        dataset_qname = f"dataset::{self.app.file_path}/table::{src_name}"
        sink_id = sha256_id(sink.qname)
        ds_id = sha256_id(dataset_qname)
        self.app.lineage_edges.append(LineageEdge(
            src_id=ds_id,
            dst_id=sink_id,
            rel="STORED_AS",
            transform=None,
            evidence=self._original_text(ctx)[:200],
        ))
        return None

    def visitRenameStmt(self, ctx: QlikViewParser.RenameStmtContext):
        """``RENAME TABLE old TO new`` / ``RENAME FIELD old TO new``."""
        is_table = ctx.TABLE() is not None
        ids = ctx.ID() if ctx.ID() else []
        bracketed = ctx.BRACKETED() if ctx.BRACKETED() else []
        names = [t.getText() for t in ids] + [b.getText().strip("[]") for b in bracketed]
        # First name is the old, second is the new (grammar order).
        if len(names) < 2:
            return None
        old, new = names[0], names[1]
        self.app.diagnostics.append(Diagnostic(
            level="info",
            code="QV-RENAME-TABLE" if is_table else "QV-RENAME-FIELD",
            message=f"Renamed {'table' if is_table else 'field'} {old!r} → {new!r}",
            artifact=self.app.file_path,
            line=ctx.start.line if ctx.start else None,
        ))
        return None

    def visitQualifyStmt(self, ctx: QlikViewParser.QualifyStmtContext):
        """``QUALIFY *`` / ``UNQUALIFY *`` — track parser state so subsequent
        LOADs know whether to namespace their field names."""
        is_qualify = ctx.QUALIFY() is not None
        self._qualify_active = is_qualify
        self.app.diagnostics.append(Diagnostic(
            level="info",
            code="QV-QUALIFY-STATE",
            message=f"{'QUALIFY' if is_qualify else 'UNQUALIFY'} active for subsequent LOADs",
            artifact=self.app.file_path,
            line=ctx.start.line if ctx.start else None,
        ))
        return None

    def visitSectionStmt(self, ctx: QlikViewParser.SectionStmtContext):
        """``SECTION ACCESS`` / ``SECTION APPLICATION``. Phase 1 records the
        section marker; emitting governance ``SECURED_BY`` edges is deferred
        to v3 once we have a real fixture + security review."""
        is_access = ctx.ACCESS() is not None
        self.app.diagnostics.append(Diagnostic(
            level="info",
            code="QV-SECTION-ACCESS" if is_access else "QV-SECTION-APPLICATION",
            message=("Section " + ("ACCESS" if is_access else "APPLICATION") +
                     " block detected — governance edges deferred"),
            artifact=self.app.file_path,
            line=ctx.start.line if ctx.start else None,
        ))
        return None

    def _emit_attributes_for_load(self, load: LoadStatement, body: str) -> None:
        """Remediation plan §2 — for every column projected by ``load``,
        emit an :class:`Attribute` IR record with its ``source_expr`` and
        ordinal, plus the corresponding :class:`Dataset` if not yet
        present. Also emits ``DERIVES_FROM`` edges from each attribute to
        identifiers referenced inside its expression so the lineage
        explorer can walk attribute → upstream-field.

        Casing is preserved exactly as written in the script.

        Handles all four load variants:
          * INLINE     — column names come from the bracketed header row.
          * RESIDENT   — column names come from the LOAD field list; each
                         attribute gets a DERIVES_FROM edge to the
                         identifier it references on the resident table.
          * SQL/QVD/FILE — column names come from the LOAD field list;
                         cross-source DERIVES_FROM lives in the resolver.
        """
        from ..ids import attribute_qname, dataset_qname, sha256_id
        from ..models import Dataset, LineageEdge, SourceType as _ST

        if not load.table_name:
            return

        # ----- 1. Ensure a Dataset exists for the LOAD target ----------
        is_inline = self._is_inline_body(body)
        # An INLINE load wins the origin classification even when the
        # visitor's load.source_type is still UNKNOWN (the grammar's
        # boundary regexes don't cover INLINE today).
        ds_origin = {
            _ST.SQL: "sql",
            _ST.RESIDENT: "resident",
            _ST.QVD: "qvd",
            _ST.FILE: "file",
            _ST.INLINE: "inline",
            _ST.UNKNOWN: "inline" if is_inline else "load",
        }.get(load.source_type, "load")
        ds = Dataset(name=load.table_name, origin=ds_origin, app=self.app.file_path,
                     is_mapping_table=load.is_mapping)
        if not any(d.qname == ds.qname for d in self.app.datasets):
            self.app.datasets.append(ds)
        ds_qname = ds.qname

        # ----- 2. Extract the *projected* column list ------------------
        if is_inline:
            # LOAD * INLINE [Col1, Col2, …\n<rows>] — column names come
            # from the FIRST line inside the bracket, not the LOAD body
            # (which is just ``*``).
            field_items = self._inline_columns(body)
            from_inline = True
        else:
            # Boundary scan keeps us OUT of the SQL / RESIDENT / FROM
            # trailer so we never split into table data.
            boundary = self._earliest(
                re.search(r"\bSQL\s+SELECT\b", body, re.IGNORECASE),
                _RE_RESIDENT.search(body),
                _RE_FROM.search(body),
            )
            field_section = body[:boundary] if boundary is not None else body
            field_section = field_section.split(";")[0]
            field_items = split_top_level_commas(field_section)
            from_inline = False

        # ----- 3. Walk each field item ---------------------------------
        existing_attrs = {a.qname for a in self.app.attributes}
        for ordinal, chunk in enumerate(field_items):
            piece = chunk.strip().rstrip(";").strip()
            if not piece or piece == "*":
                continue
            if from_inline:
                # Inline header columns: name == expression == bare ident.
                name = piece.strip("[]")
                source_expr = name
            else:
                alias_m = _RE_AS.search(piece)
                if alias_m:
                    name = alias_m.group(1).strip("[]")
                    source_expr = piece[: alias_m.start()].strip()
                else:
                    ident = _RE_IDENT.search(piece)
                    if ident is None:
                        continue
                    name = ident.group(0)
                    source_expr = piece
            # Build the transform chain — outermost-first list of
            # function names wrapping the source field. Cheap heuristic:
            # any token immediately followed by '(' that IS a known
            # function ident counts as a wrapping function.
            transform_chain = tuple(
                m.group(1) for m in re.finditer(
                    r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", source_expr,
                )
                if m.group(1).upper() in _BUILTIN_FUNCTIONS
            )
            attr = Attribute(
                dataset=ds_qname,
                name=name,
                ordinal=ordinal,
                source_expr=source_expr,
                transform_chain=transform_chain,
            )
            if attr.qname in existing_attrs:
                continue
            self.app.attributes.append(attr)
            existing_attrs.add(attr.qname)

            # ----- 4. Field-reference DERIVES_FROM edges --------------
            # RESIDENT loads: every identifier in the source_expr maps
            # to a real attribute on the resident table → emit a
            # DERIVES_FROM edge per ref. Plain renames (``EmpID AS
            # EmployeeID``) produce one edge; computed expressions
            # (``Salary / 12``) one; constants none.
            if load.source_type == _ST.RESIDENT and load.source_table:
                self._emit_field_ref_edges(
                    attr=attr, source_expr=source_expr,
                    upstream_table=load.source_table,
                    transform_chain=transform_chain,
                )

    @staticmethod
    def _is_inline_body(body: str) -> bool:
        """``LOAD * INLINE [...]`` lookahead — case-insensitive."""
        return re.search(r"\bINLINE\b", body or "", re.IGNORECASE) is not None

    @staticmethod
    def _inline_columns(body: str) -> list[str]:
        """Parse the first non-blank line inside ``INLINE [ ... ]`` and
        return its comma-separated column tokens. Real INLINE blocks use
        the first line as the header — every subsequent line is a data
        row, which we deliberately skip."""
        m = re.search(r"INLINE\s*\[(.*?)\]", body or "", re.DOTALL | re.IGNORECASE)
        if not m:
            return []
        block = m.group(1)
        # The header is the first non-blank line.
        for line in block.splitlines():
            stripped = line.strip()
            if stripped:
                return [c.strip() for c in stripped.split(",") if c.strip()]
        return []

    def _emit_field_ref_edges(
        self, *, attr: "Attribute", source_expr: str,
        upstream_table: str, transform_chain: tuple[str, ...],
    ) -> None:
        """Remediation §3 — for every identifier in ``source_expr``
        resolvable against ``upstream_table``, emit a DERIVES_FROM edge
        from the upstream attribute → this attribute. If the upstream
        attribute doesn't exist in the IR yet (rare; the resolver also
        retries this), emit a low-confidence edge anyway plus an
        ``info`` diagnostic so unresolved refs never silently vanish."""
        from ..ids import attribute_qname, dataset_qname, sha256_id
        from ..models import Diagnostic, LineageEdge

        upstream_ds_q = dataset_qname(self.app.file_path, upstream_table)
        upstream_attrs = {
            a.name: a for a in self.app.attributes if a.dataset == upstream_ds_q
        }
        refs = extract_source_fields(source_expr)
        for ref in refs:
            upstream_attr_q = attribute_qname(upstream_ds_q, ref)
            if ref in upstream_attrs:
                confidence = 1.0 if not transform_chain else 0.95
            else:
                confidence = 0.5
                self.app.diagnostics.append(Diagnostic(
                    level="info", code="QV-ATTR-UNRESOLVED",
                    message=(
                        f"field reference {ref!r} in {attr.name!r} "
                        f"could not be resolved on {upstream_table!r} "
                        f"— emitting low-confidence placeholder edge"
                    ),
                    artifact=self.app.file_path, line=None,
                ))
            # Convention: dependent → upstream.
            self.app.lineage_edges.append(LineageEdge(
                src_id=sha256_id(attr.qname),
                dst_id=sha256_id(upstream_attr_q),
                rel="DERIVES_FROM",
                transform=":".join(transform_chain) or None,
                confidence=confidence,
                evidence=source_expr[:120],
            ))

    def _emit_synthetic_fields(self, load: LoadStatement, body: str) -> None:
        """Produce ``Field`` IR entries for ``<expr> AS <alias>`` columns."""
        boundary = self._earliest(
            re.search(r"\bSQL\s+SELECT\b", body, re.IGNORECASE),
            _RE_RESIDENT.search(body),
            _RE_FROM.search(body),
        )
        field_section = body[:boundary] if boundary is not None else body
        field_section = field_section.split(";")[0]

        for chunk in split_top_level_commas(field_section):
            piece = chunk.strip().rstrip(";").strip()
            alias_m = _RE_AS.search(piece)
            if not alias_m:
                continue
            alias = alias_m.group(1).strip("[]")
            expr = piece[: alias_m.start()].strip()
            if not expr:
                continue
            sources = extract_source_fields(expr)
            self.app.fields.append(Field(
                name=alias, is_synthetic=True, formula=expr, source_fields=sources,
            ))


# ---------------------------------------------------------------------------
# Connection-string parsing (lifted out of the visitor for reuse + testing)
# ---------------------------------------------------------------------------

def _parse_connection_target(kind: ConnectionType, raw: str) -> tuple[str, str | None]:
    if kind in (ConnectionType.ODBC, ConnectionType.LIB):
        cleaned = raw.strip().strip("'")
        # Inline ODBC DSN strings like ``DSN=Redshift;UID=etl;PWD=...`` —
        # extract just the DSN value as the name so secret material in the
        # other key=value pairs doesn't land in app.connections[].name.
        # Simple ``'TERADATA_PROD'``-style names pass through unchanged.
        if "=" in cleaned:
            for part in cleaned.split(";"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    if k.strip().lower() == "dsn":
                        cleaned = v.strip()
                        break
        return cleaned, cleaned
    # OLEDB — key=value;key=value;...
    ds = None
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip().lower() in {"data source", "server"}:
                ds = v.strip()
                break
    name = ds or raw.split(";", 1)[0].strip()
    return name, ds


# ---------------------------------------------------------------------------
# v0.2 — DataPlatform / DataConnection classification helpers
# ---------------------------------------------------------------------------

# Substring → platform.kind. First match wins. Case-insensitive.
_PLATFORM_HINTS: tuple[tuple[str, str], ...] = (
    ("snowflake", "snowflake"),
    ("redshift", "redshift"),
    ("sqlserver", "sqlserver"),
    ("mssql", "sqlserver"),
    ("postgres", "postgres"),
    ("postgresql", "postgres"),
    ("mysql", "mysql"),
    ("oracle", "oracle"),
    ("teradata", "teradata"),
    ("bigquery", "bigquery"),
    ("databricks", "databricks"),
    ("synapse", "synapse"),
    ("s3", "s3"),
    ("adls", "adls"),
    (".azure", "synapse"),
    ("rest", "rest"),
    ("sap", "sap"),
    ("sharepoint", "sharepoint"),
)

_CLOUD_HINTS: dict[str, str] = {
    "snowflake": "aws",       # SnowGrid spans clouds; default to aws
    "redshift": "aws",
    "s3": "aws",
    "bigquery": "gcp",
    "synapse": "azure",
    "adls": "azure",
    "databricks": "any",
}


def _classify_platform(raw: str) -> str:
    """Best-effort classification of a connection string → platform kind.

    Returns ``unknown`` if no hint matches. The constraint engine later
    treats ``unknown`` platforms as file-system-y / no FK introspection.
    """
    s = raw.lower()
    for needle, kind in _PLATFORM_HINTS:
        if needle in s:
            return kind
    return "unknown"


def _vendor_cloud_for(kind: str) -> str | None:
    return _CLOUD_HINTS.get(kind)


def _extract_kv(raw: str, key: str) -> str | None:
    """Extract a ``Key=Value`` token from a semicolon-delimited connection
    string. Case-insensitive on the key. Returns None if not found."""
    if not raw:
        return None
    pat = re.compile(rf"(?i)\b{re.escape(key)}\s*=\s*([^;\"']+)")
    m = pat.search(raw)
    return m.group(1).strip() if m else None


def _account_locator(raw: str) -> str | None:
    """Pull out the host / server / account locator. For Snowflake this is
    the account identifier (``acme.us-east-1`` etc); for SQL Server it's
    the hostname; for files it's None."""
    return (
        _extract_kv(raw, "account")
        or _extract_kv(raw, "server")
        or _extract_kv(raw, "host")
        or _extract_kv(raw, "data source")
    )


def _auth_method(raw: str) -> str | None:
    """Sniff authentication method from the connection string. Mostly a
    heuristic — explicit ``Authenticator=`` / ``auth=`` wins; failing that
    we guess from the presence of PWD / KEY / OAUTH / SSO tokens."""
    explicit = _extract_kv(raw, "authenticator") or _extract_kv(raw, "auth")
    if explicit:
        return explicit.lower()
    s = raw.lower()
    if "oauth" in s or "access_token" in s:
        return "oauth"
    if "private_key" in s or "key_path" in s or "key_pair" in s:
        return "key_pair"
    if "iam" in s:
        return "iam"
    if "sso" in s:
        return "sso"
    if "managed" in s and "identity" in s:
        return "managed_identity"
    if "pwd=" in s or "password=" in s:
        return "password"
    return None
