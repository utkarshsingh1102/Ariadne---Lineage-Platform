"""Domain IR for the QlikView parser.

v0.1 (kept intact): ``QlikViewApp/LoadStatement/Connection/Field/Join/
Variable/Subroutine/Concatenation`` — mutable dataclasses used by the
~12 existing unit tests and the current Neo4j writer.

v0.2 (additive, per the v2 enterprise plan): frozen value-typed records
populated alongside the v0.1 model — ``DataPlatform``, ``DataConnection``,
``PhysicalSource``, ``Dataset``, ``Attribute``, ``KeyConstraint``,
``LineageEdge``, ``Diagnostic``. The visitor emits both layers in the
same pass so the writer can incrementally light up the richer graph
schema without invalidating existing tests.

Identity contract for v0.2 entities — qualified-name grammar (the
"stitching contract" with sibling parsers):

  platform::<kind>:<account_locator>
  conn::<connection_name>
  source::<conn_or_file>/<schema.table|path>
  dataset::<app_path>/table::<table_name>
  attr::<app_path>/table::<table_name>/field::<field_name>
  constraint::<dataset_qname>/<kind>/<col1+col2+...>

Hashing is `sha256(qname.encode("utf-8")).hexdigest()` — full 64 chars,
not truncated; see ``graph/writer.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SourceType(str, Enum):
    SQL = "SQL"
    RESIDENT = "RESIDENT"
    FILE = "FILE"
    QVD = "QVD"
    INLINE = "INLINE"
    UNKNOWN = "UNKNOWN"


class ConnectionType(str, Enum):
    ODBC = "ODBC"
    OLEDB = "OLEDB"
    LIB = "LIB"


@dataclass
class Connection:
    name: str
    type: ConnectionType
    data_source: str | None = None
    connection_string: str | None = None


@dataclass
class Field:
    name: str
    is_synthetic: bool = False
    formula: str | None = None
    source_fields: list[str] = field(default_factory=list)
    id: str | None = None


@dataclass
class LoadStatement:
    table_name: str
    source_type: SourceType
    fields: list[str] = field(default_factory=list)
    sql_query: str | None = None
    source_table: str | None = None
    line_number: int = 0
    is_mapping: bool = False


@dataclass
class Join:
    target_table: str
    source_table: str
    join_type: str


@dataclass
class Variable:
    name: str
    expression: str
    scope: str  # "set" or "let"
    # Remediation §3 (v0.3) — app-scoped provenance + post-expansion
    # value so the explorer can render the actual value the engine
    # bound, not just the raw RHS. Both ``raw_value`` and
    # ``resolved_value`` are pre-scrubbed of secrets before reaching the
    # writer.
    app: str | None = None
    line: int | None = None
    raw_value: str | None = None
    resolved_value: str | None = None
    is_connection_ref: bool = False

    @property
    def qname(self) -> str:
        app = self.app or "_unknown_app"
        return f"var::{app}/{self.name}"


@dataclass
class Subroutine:
    name: str
    params: list[str] = field(default_factory=list)


@dataclass
class Concatenation:
    target_table: str
    source_table: str | None = None


# ===========================================================================
# v0.2 — additive, value-typed records (per the v2 plan §3 IR)
# ===========================================================================


@dataclass(frozen=True)
class DataPlatform:
    """Logical data platform (Snowflake / Redshift / SQL Server / file / ...).

    Identity: ``platform::<kind>:<account_locator>``.
    """
    kind: str
    vendor_cloud: str | None = None
    account_locator: str | None = None

    @property
    def qname(self) -> str:
        loc = self.account_locator or ""
        return f"platform::{self.kind}:{loc}"


@dataclass(frozen=True)
class DataConnection:
    """A QlikView/Sense data connection.

    Identity: ``conn::<name>``. ``secret_ref`` is a vault path/key — never
    the secret value. ``secret_fingerprint`` is a salted SHA-256 used only
    for change-detection. ``raw_locator_redacted`` is the connection
    string with secret material masked by ``secrets.py``.
    """
    name: str
    platform_kind: str
    driver: str | None = None
    host: str | None = None
    database: str | None = None
    schema: str | None = None
    warehouse: str | None = None
    role: str | None = None
    region: str | None = None
    auth_method: str | None = None
    secret_ref: str | None = None
    secret_fingerprint: str | None = None
    raw_locator_redacted: str = ""

    @property
    def qname(self) -> str:
        return f"conn::{self.name}"


@dataclass(frozen=True)
class PhysicalSource:
    """A real table / file / endpoint reachable through a connection
    (or a free-standing file/qvd path).

    Identity: ``source::<conn_or_file>/<schema.table|path>``.
    """
    connection: str | None
    kind: str  # db_table|db_view|file|qvd|rest_endpoint|inline|generated
    locator: str
    declared_in: str

    @property
    def qname(self) -> str:
        prefix = self.connection or "_local"
        return f"source::{prefix}/{self.locator}"


@dataclass(frozen=True)
class Dataset:
    """An in-memory QlikView table OR stored QVD OR Qlik Sense table.

    Identity: ``dataset::<app_path>/table::<table_name>``.
    """
    name: str
    origin: str  # load|resident|sql|qvd_store|inline|generated|join_result|concat_result|mapping|binary_inherited|resident_placeholder
    app: str | None = None
    section: str | None = None
    is_synthetic_key_table: bool = False
    is_mapping_table: bool = False
    # Phase 3.5 (remediation plan §1): when a dataset enters the host
    # app's IR via a BINARY directive or a lazy resident placeholder, we
    # mark it here so the writer / explorer can surface the provenance.
    # ``inherited_via`` ∈ {"BINARY", "RESIDENT_PLACEHOLDER", None}.
    # ``inherited_from`` is the upstream Dataset.qname (BINARY case) or
    # the host app's own file_path (placeholder case).
    inherited_via: str | None = None
    inherited_from: str | None = None

    @property
    def qname(self) -> str:
        app = self.app or "_unknown_app"
        return f"dataset::{app}/table::{self.name}"


@dataclass(frozen=True)
class Attribute:
    """A field/column — the LEAF NODE the v2 plan demands.

    Identity: ``attr::<app_path>/table::<table_name>/field::<field_name>``.
    Field name casing is preserved (QV field names are case-sensitive).
    """
    dataset: str            # parent Dataset.qname OR raw `(app, table)` key
    name: str
    ordinal: int | None = None
    data_type: str | None = None
    nullable: bool | None = None
    source_expr: str | None = None
    transform_chain: tuple[str, ...] = ()
    is_key: bool = False
    is_synthetic_key_member: bool = False

    @property
    def qname(self) -> str:
        return f"{self.dataset}/field::{self.name}"


@dataclass(frozen=True)
class KeyConstraint:
    """A PK / FK / UNIQUE / SYNTHETIC constraint candidate on a dataset.

    Identity: ``constraint::<dataset_qname>/<kind>/<col1+col2+...>``.
    """
    dataset: str            # Dataset.qname
    columns: tuple[str, ...]
    kind: str               # primary|unique|foreign|synthetic
    references: tuple[str, str] | None = None  # (target_dataset_qname, target_columns_csv)
    source: str = ""        # introspected|join_inferred|qvd_hint|naming_inferred
    confidence: float = 0.0

    @property
    def qname(self) -> str:
        cols = "+".join(self.columns)
        return f"constraint::{self.dataset}/{self.kind}/{cols}"


@dataclass(frozen=True)
class LineageEdge:
    """A single typed edge in the lineage DAG. ``sig`` becomes part of the
    MERGE key so two edges with the same (src, dst, rel) but different
    transforms stay distinct in the graph."""
    src_id: str             # SHA-256(qname)
    dst_id: str
    rel: str                # DERIVES_FROM|MAPS_TO|STORED_AS|JOINS|CONNECTS_VIA|HAS_ATTRIBUTE|REFERENCES_FK|...
    transform: str | None = None
    join_type: str | None = None
    join_keys: tuple[str, ...] = ()
    confidence: float = 1.0
    evidence: str = ""

    @property
    def sig(self) -> str:
        # Distinguishes parallel edges with the same endpoint pair.
        return f"{self.rel}|{self.transform or ''}|{self.join_type or ''}|{'+'.join(self.join_keys)}"


@dataclass(frozen=True)
class Diagnostic:
    """A structured parser/preprocessor finding. Replaces the
    free-text ``parse_errors: list[str]`` shape for the v0.2 surface;
    the legacy list is still populated in parallel during the migration
    window."""
    level: str              # info|warn|error
    code: str               # QV-INC-CYCLE|QV-VAR-UNRESOLVED|QV-PARSE-*|QV-QVW-*|QV-SYNKEY|...
    message: str
    artifact: str = ""
    line: int | None = None

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "code": self.code,
            "message": self.message,
            "artifact": self.artifact,
            "line": self.line,
        }


# ===========================================================================
# v0.1 app container — gains a parallel ``diagnostics`` field for v0.2
# ===========================================================================


@dataclass
class QlikViewApp:
    app_name: str
    file_path: str
    loads: list[LoadStatement] = field(default_factory=list)
    connections: list[Connection] = field(default_factory=list)
    fields: list[Field] = field(default_factory=list)
    joins: list[Join] = field(default_factory=list)
    includes: list[str] = field(default_factory=list)
    variables: list[Variable] = field(default_factory=list)
    subroutines: list[Subroutine] = field(default_factory=list)
    concatenations: list[Concatenation] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)

    # v0.2 — populated by the visitor alongside the v0.1 fields. The
    # writer reads these to emit the richer graph schema; existing tests
    # keep reading the v0.1 fields above.
    platforms: list[DataPlatform] = field(default_factory=list)
    data_connections: list[DataConnection] = field(default_factory=list)
    physical_sources: list[PhysicalSource] = field(default_factory=list)
    datasets: list[Dataset] = field(default_factory=list)
    attributes: list[Attribute] = field(default_factory=list)
    key_constraints: list[KeyConstraint] = field(default_factory=list)
    lineage_edges: list[LineageEdge] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    # Phase 3 — Qlik Sense UI objects (sheets/charts/dimensions/measures).
    # Populated only when the input was a .qvf.
    ui_objects: list = field(default_factory=list)   # list[UiObject]
    # Phase 3 — server-meta records: QMC tasks + EDX triggers parsed from
    # QlikView Server XML / .meta / .shared files.
    server_tasks: list = field(default_factory=list)
    server_triggers: list = field(default_factory=list)
    # Phase 2 — path declared by ``BINARY '<upstream.qvw>';``. QlikView
    # semantics: at most ONE BINARY per script, and only at the very
    # top. The orchestrator follows this directive (with depth guard +
    # cycle detection) and merges the upstream app's data model into
    # this one.
    binary_load_path: str | None = None

    def to_dict(self) -> dict:
        return {
            "app_name": self.app_name,
            "file_path": self.file_path,
            "loads": [
                {
                    "table_name": l.table_name,
                    "source_type": l.source_type.value,
                    "fields": list(l.fields),
                    "sql_query": l.sql_query,
                    "source_table": l.source_table,
                    "line_number": l.line_number,
                    "is_mapping": l.is_mapping,
                }
                for l in self.loads
            ],
            "connections": [
                {"name": c.name, "type": c.type.value, "data_source": c.data_source}
                for c in self.connections
            ],
            "fields": [
                {
                    "name": f.name,
                    "is_synthetic": f.is_synthetic,
                    "formula": f.formula,
                    "source_fields": list(f.source_fields),
                    "id": f.id,
                }
                for f in self.fields
            ],
            "joins": [
                {"target_table": j.target_table, "source_table": j.source_table,
                 "join_type": j.join_type}
                for j in self.joins
            ],
            "includes": list(self.includes),
            "variables": [
                {"name": v.name, "expression": v.expression, "scope": v.scope,
                 "app": v.app, "line": v.line,
                 "raw_value": v.raw_value, "resolved_value": v.resolved_value,
                 "is_connection_ref": v.is_connection_ref,
                 "qname": v.qname}
                for v in self.variables
            ],
            "subroutines": [{"name": s.name, "params": list(s.params)}
                            for s in self.subroutines],
            "concatenations": [{"target_table": c.target_table,
                                "source_table": c.source_table}
                               for c in self.concatenations],
            "parse_errors": list(self.parse_errors),
            # v0.2 surface — additive
            "platforms": [
                {"kind": p.kind, "vendor_cloud": p.vendor_cloud,
                 "account_locator": p.account_locator, "qname": p.qname}
                for p in self.platforms
            ],
            "data_connections": [
                {"name": c.name, "platform_kind": c.platform_kind,
                 "driver": c.driver, "host": c.host, "database": c.database,
                 "schema": c.schema, "warehouse": c.warehouse, "role": c.role,
                 "region": c.region, "auth_method": c.auth_method,
                 "secret_ref": c.secret_ref,
                 "secret_fingerprint": c.secret_fingerprint,
                 "raw_locator_redacted": c.raw_locator_redacted,
                 "qname": c.qname}
                for c in self.data_connections
            ],
            "physical_sources": [
                {"connection": s.connection, "kind": s.kind,
                 "locator": s.locator, "declared_in": s.declared_in,
                 "qname": s.qname}
                for s in self.physical_sources
            ],
            "datasets": [
                {"name": d.name, "origin": d.origin, "app": d.app,
                 "section": d.section,
                 "is_synthetic_key_table": d.is_synthetic_key_table,
                 "is_mapping_table": d.is_mapping_table,
                 "inherited_via": d.inherited_via,
                 "inherited_from": d.inherited_from,
                 "qname": d.qname}
                for d in self.datasets
            ],
            "attributes": [
                {"dataset": a.dataset, "name": a.name, "ordinal": a.ordinal,
                 "data_type": a.data_type, "nullable": a.nullable,
                 "source_expr": a.source_expr,
                 "transform_chain": list(a.transform_chain),
                 "is_key": a.is_key,
                 "is_synthetic_key_member": a.is_synthetic_key_member,
                 "qname": a.qname}
                for a in self.attributes
            ],
            "key_constraints": [
                {"dataset": k.dataset, "columns": list(k.columns),
                 "kind": k.kind, "references": list(k.references) if k.references else None,
                 "source": k.source, "confidence": k.confidence,
                 "qname": k.qname}
                for k in self.key_constraints
            ],
            "lineage_edges": [
                {"src_id": e.src_id, "dst_id": e.dst_id, "rel": e.rel,
                 "transform": e.transform, "join_type": e.join_type,
                 "join_keys": list(e.join_keys), "confidence": e.confidence,
                 "evidence": e.evidence, "sig": e.sig}
                for e in self.lineage_edges
            ],
            "diagnostics": [d.to_dict() for d in self.diagnostics],
        }
