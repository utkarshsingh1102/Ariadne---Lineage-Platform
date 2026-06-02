"""Data-driven registries for connection extraction.

Two tables drive everything connection-related:

* ``URI_SCHEMES`` — recognised URI schemes (``s3``, ``gs``, ``abfss``, …) and
  how each parses into klass + authority + path.
* ``FORMATS`` — recognised Spark data-source format strings (``jdbc``, ``kafka``,
  ``mongo``, ``cassandra``, ``elasticsearch``, ``snowflake``, ``bigquery``,
  ``redshift``, ``delta``, ``iceberg``, ``hudi``, plain file formats, …) and
  which option keys identify the *connection* vs. the *dataset*.

Adding a new system is editing one of these tables — not the parser logic.
Unknown schemes / formats still produce a node (``klass='unknown:<x>'``)
so the I/O site is never dropped silently.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class URISchemeEntry:
    """Describes one URI-scheme family (e.g. all three S3 variants)."""
    schemes: tuple[str, ...]
    klass: str
    # Human-readable description of what the URI authority means for this scheme;
    # never compared, just useful when the resolver returns ``unknown``.
    authority_role: str
    # Default port when none is given in the URI. Used by `connection_id` to
    # collapse ``host`` and ``host:default_port`` to one node.
    default_port: int | None = None
    # True when the authority is itself two parts joined by ``@`` (Azure
    # container@account, MongoDB user@host pre-strip).
    authority_has_at: bool = False


URI_SCHEMES: tuple[URISchemeEntry, ...] = (
    URISchemeEntry(("s3", "s3a", "s3n"), "s3", "bucket"),
    URISchemeEntry(("gs", "gcs"), "gcs", "bucket"),
    URISchemeEntry(("abfs", "abfss"), "adls", "container@account", authority_has_at=True),
    URISchemeEntry(("wasb", "wasbs"), "azure_blob", "container@account", authority_has_at=True),
    URISchemeEntry(("hdfs",), "hdfs", "namenode host:port", default_port=8020),
    URISchemeEntry(("file",), "local_fs", "path root"),
    URISchemeEntry(("dbfs",), "dbfs", "mount"),
    URISchemeEntry(("http", "https"), "http", "endpoint host:port"),
    URISchemeEntry(("ftp", "sftp"), "ftp", "host[:port]"),
)


def lookup_scheme(scheme: str | None) -> URISchemeEntry | None:
    if not scheme:
        return None
    s = scheme.lower()
    for entry in URI_SCHEMES:
        if s in entry.schemes:
            return entry
    return None


@dataclass(frozen=True)
class FormatEntry:
    """Describes how to extract a connection from one Spark format string."""
    format_aliases: tuple[str, ...]
    klass: str
    # Option-key names that identify the *connection* (host/URL). At least one
    # must resolve for the connection to be flagged ``resolved=True``.
    connection_keys: tuple[str, ...] = ()
    # Option-key names that identify the *dataset* (table/topic/index).
    dataset_keys: tuple[str, ...] = ()
    # Default port for the canonical-key calculation.
    default_port: int | None = None
    # Option keys whose VALUE is sensitive (kept in detail but never indexed,
    # never stored as a top-level field).
    credential_keys: tuple[str, ...] = (
        "user", "username", "password", "passwd", "pwd",
        "token", "secret", "key", "access_key", "secret_key",
        "aws_access_key_id", "aws_secret_access_key",
    )


FORMATS: tuple[FormatEntry, ...] = (
    FormatEntry(
        ("jdbc",),
        klass="jdbc",
        connection_keys=("url",),
        dataset_keys=("dbtable", "query"),
    ),
    FormatEntry(
        ("kafka",),
        klass="kafka",
        connection_keys=("kafka.bootstrap.servers", "bootstrap.servers"),
        dataset_keys=("subscribe", "topic", "assign", "subscribePattern"),
        default_port=9092,
    ),
    FormatEntry(
        ("mongo", "mongodb", "com.mongodb.spark.sql.defaultsource"),
        klass="mongodb",
        connection_keys=(
            "spark.mongodb.read.connection.uri",
            "spark.mongodb.write.connection.uri",
            "spark.mongodb.input.uri",
            "spark.mongodb.output.uri",
            "uri",
            "connection.uri",
        ),
        dataset_keys=("collection", "database"),
        default_port=27017,
    ),
    FormatEntry(
        ("cassandra", "org.apache.spark.sql.cassandra"),
        klass="cassandra",
        connection_keys=("spark.cassandra.connection.host", "host"),
        dataset_keys=("table", "keyspace"),
        default_port=9042,
    ),
    FormatEntry(
        ("elasticsearch", "es", "org.elasticsearch.spark.sql"),
        klass="elasticsearch",
        connection_keys=("es.nodes", "nodes"),
        dataset_keys=("es.resource", "resource"),
        default_port=9200,
    ),
    FormatEntry(
        ("redis",),
        klass="redis",
        connection_keys=("host",),
        dataset_keys=("table", "keys"),
        default_port=6379,
    ),
    FormatEntry(
        ("snowflake", "net.snowflake.spark.snowflake"),
        klass="snowflake",
        connection_keys=("sfUrl", "sfurl"),
        dataset_keys=("dbtable", "query", "sfDatabase", "sfdatabase"),
        default_port=443,
    ),
    FormatEntry(
        ("bigquery", "com.google.cloud.spark.bigquery"),
        klass="bigquery",
        connection_keys=("parentProject", "project"),
        dataset_keys=("table",),
    ),
    FormatEntry(
        ("redshift",
         "io.github.spark_redshift_community.spark.redshift",
         "com.databricks.spark.redshift"),
        klass="redshift",
        connection_keys=("url",),
        dataset_keys=("dbtable", "query"),
        default_port=5439,
    ),
    FormatEntry(
        ("delta",),
        klass="delta",
        connection_keys=("path",),
        dataset_keys=("path",),
    ),
    FormatEntry(
        ("iceberg",),
        klass="iceberg",
        connection_keys=("path",),
        dataset_keys=("path",),
    ),
    FormatEntry(
        ("hudi", "org.apache.hudi"),
        klass="hudi",
        connection_keys=("path",),
        dataset_keys=("path",),
    ),
    # File-format families addressed by path.
    FormatEntry(("parquet",), klass="parquet"),
    FormatEntry(("csv",), klass="csv"),
    FormatEntry(("json",), klass="json"),
    FormatEntry(("orc",), klass="orc"),
    FormatEntry(("avro",), klass="avro"),
    FormatEntry(("text",), klass="text"),
    FormatEntry(("xml",), klass="xml"),
    FormatEntry(("hive",), klass="hive"),
    FormatEntry(("cloudFiles", "cloudfiles"), klass="databricks-autoloader"),
)


def lookup_format(format_str: str | None) -> FormatEntry | None:
    if not format_str:
        return None
    target = format_str.lower()
    for entry in FORMATS:
        for alias in entry.format_aliases:
            if alias.lower() == target:
                return entry
    return None


# JDBC subprotocol → default port. Used when the URL has no port; lets
# ``localhost:5432`` and ``localhost`` collapse to the same canonical id.
JDBC_DEFAULT_PORTS: dict[str, int] = {
    "postgresql": 5432,
    "postgres": 5432,
    "mysql": 3306,
    "mariadb": 3306,
    "oracle": 1521,
    "sqlserver": 1433,
    "db2": 50000,
    "redshift": 5439,
    "snowflake": 443,
    "hive2": 10000,
    "hive": 10000,
    "h2": 9092,
    "sqlite": 0,
    "clickhouse": 8123,
    "bigquery": 443,
    "vertica": 5433,
    "teradata": 1025,
    "presto": 8080,
    "trino": 8080,
}


def jdbc_default_port(subprotocol: str | None) -> int | None:
    if not subprotocol:
        return None
    return JDBC_DEFAULT_PORTS.get(subprotocol.lower())


# Hosts that should canonicalize to the same node. Always lowercase the LHS.
LOCALHOST_ALIASES: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})


def canonical_host(host: str | None) -> str:
    if not host:
        return ""
    h = host.strip().lower()
    return "localhost" if h in LOCALHOST_ALIASES else h


def normalize_path(path: str | None) -> str:
    """Drop trailing slashes — ``/gold/orders`` and ``/gold/orders/`` are
    the same bucket location.
    """
    if not path:
        return ""
    p = path.rstrip("/")
    return p or "/"


def sort_host_list(hosts: Iterable[str]) -> tuple[str, ...]:
    """Sort multi-host lists so ``b,a`` and ``a,b`` collapse together."""
    seen: list[str] = []
    for h in hosts:
        s = (h or "").strip()
        if s and s not in seen:
            seen.append(s)
    return tuple(sorted(seen, key=str.lower))


def is_credential_option_key(key: str) -> bool:
    """Heuristic: option-key matches one of the registered credential names."""
    if not key:
        return False
    k = key.lower()
    for fmt in FORMATS:
        for cred in fmt.credential_keys:
            if cred.lower() == k:
                return True
    return False
