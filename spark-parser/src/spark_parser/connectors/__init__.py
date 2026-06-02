"""External-ecosystem connectors — v0.2 §9.

Each connector recognises a Spark data-source format string + options dict and
returns canonical lineage metadata (``storage_format``, ``fully_qualified_name``,
``location``). The visitor's read/write handlers call ``match_connector`` after
the built-in formats fail to produce a hit.

Six connectors are covered in v0.2:

  - Kafka       — ``format("kafka")``
  - Iceberg     — ``format("iceberg")``
  - Hudi        — ``format("hudi")``
  - Snowflake   — ``format("snowflake")`` / ``format("net.snowflake.spark.snowflake")``
  - BigQuery    — ``format("bigquery")``
  - Redshift    — ``format("redshift")`` / ``format("io.github.spark_redshift_community.spark.redshift")``

Each connector is a tiny function inside this module; we don't split them
into separate files because they all share the same shape and live or die
together.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from ..models.domain import ConnectionIR
from ..utils.ids import connection_id
from .registry import (
    FORMATS,
    URI_SCHEMES,
    canonical_host,
    is_credential_option_key,
    jdbc_default_port,
    lookup_format,
    lookup_scheme,
    normalize_path,
    sort_host_list,
)


@dataclass
class ConnectorMatch:
    """Canonical lineage metadata returned by a connector match."""
    storage_format: str
    fully_qualified_name: str | None = None
    location: str | None = None
    connector: str = ""                  # short ID: "kafka" | "iceberg" | …


_KAFKA_FORMATS = {"kafka"}
_ICEBERG_FORMATS = {"iceberg"}
_HUDI_FORMATS = {"hudi", "org.apache.hudi"}
_SNOWFLAKE_FORMATS = {"snowflake", "net.snowflake.spark.snowflake"}
_BIGQUERY_FORMATS = {"bigquery", "com.google.cloud.spark.bigquery"}
_REDSHIFT_FORMATS = {
    "redshift",
    "io.github.spark_redshift_community.spark.redshift",
    "com.databricks.spark.redshift",
}


def match_connector(
    format_str: str | None,
    options: dict[str, str] | None,
    *,
    path_arg: str | None = None,
    table_arg: str | None = None,
) -> ConnectorMatch | None:
    """Dispatch to the right connector based on ``format_str``."""
    if not format_str:
        return None
    fmt = format_str.lower()
    options = options or {}

    if fmt in _KAFKA_FORMATS:
        return _match_kafka(options)
    if fmt in _ICEBERG_FORMATS:
        return _match_iceberg(options, path_arg=path_arg, table_arg=table_arg)
    if fmt in _HUDI_FORMATS:
        return _match_hudi(options, path_arg=path_arg)
    if fmt in _SNOWFLAKE_FORMATS:
        return _match_snowflake(options)
    if fmt in _BIGQUERY_FORMATS:
        return _match_bigquery(options, table_arg=table_arg)
    if fmt in _REDSHIFT_FORMATS:
        return _match_redshift(options)
    return None


# ---------------------------------------------------------------------------
# Individual matchers — kept short on purpose.
# ---------------------------------------------------------------------------


def _match_kafka(opts: dict[str, str]) -> ConnectorMatch:
    """``spark.read.format("kafka").option("subscribe", "orders") ...``"""
    servers = opts.get("kafka.bootstrap.servers") or opts.get("bootstrap.servers") or ""
    topic = opts.get("subscribe") or opts.get("topic") or opts.get("assign") or ""
    fqn = f"kafka://{servers}/{topic}" if (servers or topic) else None
    return ConnectorMatch(
        storage_format="kafka",
        fully_qualified_name=fqn,
        location=fqn,
        connector="kafka",
    )


def _match_iceberg(
    opts: dict[str, str], *, path_arg: str | None, table_arg: str | None,
) -> ConnectorMatch:
    """``spark.read.format("iceberg").load("catalog.namespace.table")``"""
    # Iceberg uses the same three-part FQN convention as Hive — that means
    # cross-parser merging on `:Table.fully_qualified_name` continues to work.
    fqn = table_arg or path_arg or opts.get("path")
    return ConnectorMatch(
        storage_format="iceberg",
        fully_qualified_name=fqn,
        location=fqn,
        connector="iceberg",
    )


def _match_hudi(opts: dict[str, str], *, path_arg: str | None) -> ConnectorMatch:
    """``spark.read.format("hudi").load("s3://bucket/table")``"""
    location = path_arg or opts.get("path")
    return ConnectorMatch(
        storage_format="hudi",
        fully_qualified_name=location,
        location=location,
        connector="hudi",
    )


def _match_snowflake(opts: dict[str, str]) -> ConnectorMatch:
    """``... .option("dbtable", "X").option("sfDatabase", "DB") ...``"""
    db = opts.get("sfDatabase") or opts.get("sfdatabase") or ""
    schema = opts.get("sfSchema") or opts.get("sfschema") or ""
    table = opts.get("dbtable") or opts.get("query") or ""
    account = opts.get("sfUrl") or opts.get("sfurl") or ""
    if table and "." in table:
        # Snowflake `dbtable` can already be fully qualified — keep it verbatim.
        fqn = table
    else:
        parts = [p for p in (db, schema, table) if p]
        fqn = ".".join(parts) if parts else None
    location = f"snowflake://{account}" if account else None
    return ConnectorMatch(
        storage_format="snowflake",
        fully_qualified_name=fqn,
        location=location,
        connector="snowflake",
    )


def _match_bigquery(opts: dict[str, str], *, table_arg: str | None) -> ConnectorMatch:
    """``spark.read.format("bigquery").option("table", "project.dataset.table")``"""
    table = opts.get("table") or table_arg or ""
    fqn = table if "." in table else None
    location = f"bigquery://{table}" if table else None
    return ConnectorMatch(
        storage_format="bigquery",
        fully_qualified_name=fqn,
        location=location,
        connector="bigquery",
    )


def _match_redshift(opts: dict[str, str]) -> ConnectorMatch:
    """``... .option("url", "jdbc:redshift://…").option("dbtable", "X") ...``"""
    url = opts.get("url") or ""
    table = opts.get("dbtable") or opts.get("query") or ""
    fqn = table or url or None
    return ConnectorMatch(
        storage_format="redshift",
        fully_qualified_name=fqn,
        location=url or None,
        connector="redshift",
    )


# ---------------------------------------------------------------------------
# v0.2 §9 — connection extraction. ``derive_connection`` returns a
# :Connection node payload for any data source the visitor recognises. The
# canonical fields (``klass``/``server``/``port``/``dbname``/``schema``/
# ``username``) mirror the Tableau parser's :Connection shape so cross-parser
# MERGE on `:Connection.id` works out of the box.
# ---------------------------------------------------------------------------


_JDBC_URL = re.compile(
    r"^jdbc:(?P<sub>[A-Za-z0-9_+\-]+):(?://)?"
    r"(?:(?P<user>[^:/?#@]+)(?::(?P<pwd>[^@]*))?@)?"
    r"(?P<host>[^:/?#;]+)?(?::(?P<port>\d+))?"
    r"(?:[/;](?P<path>[^?#;]*))?(?:[?;](?P<query>.*))?$"
)

# Querystring credential keys we want to strip when masking a URL.
_URL_CRED_QUERY_KEYS = frozenset({"user", "username", "password", "passwd", "pwd", "token", "secret"})


def strip_url_credentials(url: str | None) -> tuple[str, bool]:
    """Return ``(masked_url, had_credentials)`` — guarantee no creds slip into
    the graph.

    Handles two leakage paths:

    * ``scheme://user:pwd@host/...`` — userinfo segment of the URL.
    * ``?password=...&token=...`` — querystring secrets.

    Bare ``container@account`` patterns (ABFSS/WASBS object stores) are
    NOT credentials — they're part of the authority itself. We only strip
    when the userinfo includes a ``:`` (proper ``user:pwd`` form) or the
    URL is a JDBC sub-URL.

    Never raises: an unparseable URL is returned verbatim with
    ``had_credentials=False``.
    """
    if not url:
        return "", False
    had_creds = False
    # Userinfo only matters when it actually looks like credentials. A
    # bare ``name@host`` (ABFSS container, MongoDB SRV without password)
    # is structurally identical but isn't a secret.
    if "://" in url and "@" in url.split("://", 1)[1].split("/", 1)[0]:
        scheme_part, rest = url.split("://", 1)
        authority, _, path = rest.partition("/")
        userinfo, _, host = authority.rpartition("@")
        if userinfo and ":" in userinfo:
            had_creds = True
            url = f"{scheme_part}://{host}" + (f"/{path}" if path else "")
    # JDBC-prefixed form: ``jdbc:postgresql://user:pwd@host/db``. The
    # generic "scheme://" check above doesn't catch the ``jdbc:`` prefix
    # because the real scheme follows; do a targeted strip.
    jdbc_m = re.match(
        r"^(jdbc:[A-Za-z0-9_+\-]+:(?://)?)([^@/]+@)([^?]*)(?:\?(.*))?$", url,
    )
    if jdbc_m:
        had_creds = True
        prefix, _userinfo, hostpath, query = jdbc_m.groups()
        url = prefix + hostpath + (f"?{query}" if query else "")
    if "?" in url:
        head, _, query = url.partition("?")
        kept_pairs = []
        for pair in query.split("&"):
            if "=" not in pair:
                kept_pairs.append(pair)
                continue
            k, _, _v = pair.partition("=")
            if k.lower() in _URL_CRED_QUERY_KEYS:
                had_creds = True
                continue
            kept_pairs.append(pair)
        url = head + ("?" + "&".join(kept_pairs) if kept_pairs else "")
    return url, had_creds


def split_credential_options(opts: dict[str, str]) -> tuple[dict[str, str], bool]:
    """Drop credential keys from an options dict. Returns ``(safe_opts, had_creds)``.

    Caller decides whether to surface ``has_credentials`` on the connection.
    """
    if not opts:
        return {}, False
    safe: dict[str, str] = {}
    had = False
    for k, v in opts.items():
        if is_credential_option_key(k):
            had = True
            continue
        safe[k] = v
    return safe, had

# Maps a Spark format string to a default klass label when no JDBC subprotocol
# tells us more. Keep keys lowercase.
_FORMAT_TO_KLASS = {
    "parquet": "parquet",
    "csv": "csv",
    "json": "json",
    "orc": "orc",
    "avro": "avro",
    "delta": "delta",
    "text": "text",
    "xml": "xml",
    "hive": "hive",
    "kafka": "kafka",
    "iceberg": "iceberg",
    "hudi": "hudi",
    "snowflake": "snowflake",
    "bigquery": "bigquery",
    "redshift": "redshift",
    "mongo": "mongodb",
    "mongodb": "mongodb",
    "com.mongodb.spark.sql.defaultsource": "mongodb",
    "cassandra": "cassandra",
    "org.apache.spark.sql.cassandra": "cassandra",
    "elasticsearch": "elasticsearch",
    "es": "elasticsearch",
    "org.elasticsearch.spark.sql": "elasticsearch",
    "cloudfiles": "databricks-autoloader",
}


def _parse_jdbc_url(url: str) -> dict[str, str | int | bool | None]:
    """Extract structured pieces from a JDBC URL.

    Returns a dict with optional ``klass`` (e.g. ``jdbc:postgresql``),
    ``server``, ``port``, ``dbname``, ``username``, ``has_credentials``.
    The default port for the subprotocol is filled in when none was given.
    Credentials (user:pwd) are NEVER returned in the URL — only the flag.
    """
    if not url:
        return {}
    m = _JDBC_URL.match(url.strip())
    if not m:
        return {}
    sub = (m.group("sub") or "").lower()
    out: dict[str, str | int | bool | None] = {
        "klass": f"jdbc:{sub}" if sub else "jdbc",
    }
    if m.group("host"):
        out["server"] = canonical_host(m.group("host"))
    if m.group("port"):
        try:
            out["port"] = int(m.group("port"))
        except ValueError:
            pass
    else:
        dp = jdbc_default_port(sub)
        if dp:
            out["port"] = dp
    if m.group("user") or m.group("pwd"):
        out["has_credentials"] = True
        # Username we KEEP (identity, not a secret); password we drop entirely.
        if m.group("user"):
            out["username"] = m.group("user")
    path = (m.group("path") or "").strip("/")
    if path:
        out["dbname"] = path.split("/", 1)[0] or None
    query = m.group("query") or ""
    if query:
        for kv in re.split(r"[&;]", query):
            if "=" not in kv:
                continue
            k, v = kv.split("=", 1)
            kl = k.lower()
            if kl in {"user", "username"} and "username" not in out:
                out["username"] = v
            elif kl in {"password", "passwd", "pwd", "token", "secret"}:
                out["has_credentials"] = True
            elif kl == "currentschema" and "schema" not in out:
                out["schema"] = v
    return out


def _parse_object_store_uri(location: str) -> dict[str, object]:
    """Classify a URI via the URI_SCHEMES registry.

    Returns a dict with ``klass``, ``server`` (canonicalised), ``port``,
    ``dbname`` (container for Azure), ``path`` (with trailing slash
    stripped). An unrecognised scheme yields ``klass='unknown:<scheme>'``
    so the I/O site is still surfaced — never silently dropped.
    """
    if not location:
        return {}
    p = urlparse(location)
    scheme = (p.scheme or "").lower()
    entry = lookup_scheme(scheme)
    # Local-path fallback: bare ``/abs/path`` with no scheme is local FS.
    if not scheme and location.startswith("/"):
        return {
            "klass": "local_fs",
            "server": "local",
            "path": normalize_path(p.path or location),
        }
    if entry is None and scheme:
        return {
            "klass": f"unknown:{scheme}",
            "server": canonical_host(p.netloc) or None,
            "path": normalize_path(p.path or "") or None,
        }
    if entry is None:
        return {}
    # Authority that includes "@" (Azure containers).
    if entry.authority_has_at:
        container, _, account = (p.netloc or "").partition("@")
        return {
            "klass": entry.klass,
            "server": canonical_host(account) or canonical_host(p.netloc) or None,
            "dbname": container or None,
            "path": normalize_path(p.path or "") or None,
        }
    if entry.klass == "local_fs":
        return {
            "klass": "local_fs",
            "server": "local",
            "path": normalize_path(p.path or location) or None,
        }
    if entry.klass == "dbfs":
        return {
            "klass": "dbfs",
            "server": "databricks",
            "path": normalize_path(p.path or "") or None,
        }
    if entry.klass == "hdfs":
        return {
            "klass": "hdfs",
            "server": canonical_host(p.hostname or p.netloc) or None,
            "port": p.port or entry.default_port,
            "path": normalize_path(p.path or "") or None,
        }
    # Standard scheme://authority/path
    return {
        "klass": entry.klass,
        "server": canonical_host(p.netloc) or None,
        "port": p.port or entry.default_port,
        "path": normalize_path(p.path or "") or None,
    }


def _mint_unresolved_connection(
    *,
    klass: str,
    source: str,
    detail: str | None = None,
) -> ConnectionIR:
    """Connection node for an I/O site whose target is dynamic/secret/env.

    The node still has a deterministic id so multiple references to the
    same env var collapse to one node. ``resolved=False`` flags downstream
    consumers that the value is symbolic.
    """
    server = f"{source}:{detail}" if detail else source
    cid = connection_id(klass=klass, server=server, dbname="")
    return ConnectionIR(
        id=cid, klass=klass, server=server,
        resolved=False, source=source, detail=detail,
    )


def derive_connection(
    format_str: str | None,
    options: dict[str, str] | None,
    *,
    location: str | None = None,
    table_arg: str | None = None,
    database: str | None = None,
    unresolved_source: str | None = None,
    unresolved_detail: str | None = None,
) -> ConnectionIR | None:
    """Return a populated ``ConnectionIR`` for any data source we can place.

    Called from the visitor for every read and every write. The function
    never throws — unrecognised formats with no resolvable URI yield ``None``
    so the visitor can decide whether to attach a node at all.

    When ``unresolved_source`` is supplied the caller is signalling that the
    URL / path argument is runtime/secret/dynamic; we still mint a Connection
    node so the I/O site is visible in the graph.
    """
    opts = dict(options or {})
    fmt = (format_str or "").lower()
    # Runtime/secret/dynamic — mint a node with resolved=False even if the
    # format string is empty. The caller (visitor) decides when to pass this.
    if unresolved_source is not None:
        klass_hint = fmt or "unknown"
        return _mint_unresolved_connection(
            klass=klass_hint, source=unresolved_source,
            detail=unresolved_detail,
        )

    # --- JDBC family (Postgres, MySQL, SQL Server, Oracle, Redshift via JDBC) ---
    if fmt == "jdbc" or fmt in _REDSHIFT_FORMATS:
        raw_url = opts.get("url") or location or ""
        url, url_creds = strip_url_credentials(str(raw_url))
        parsed = _parse_jdbc_url(url)
        dbtable = opts.get("dbtable") or opts.get("query") or table_arg
        schema_val: str | None = None
        table_only = dbtable
        if dbtable and "." in dbtable:
            parts = dbtable.split(".")
            if len(parts) == 2:
                schema_val, table_only = parts
            elif len(parts) >= 3:
                schema_val = parts[-2]
                table_only = parts[-1]
        klass = parsed.get("klass") or ("redshift" if fmt in _REDSHIFT_FORMATS else "jdbc")
        if fmt in _REDSHIFT_FORMATS:
            klass = "redshift"
        safe_opts, opts_creds = split_credential_options(opts)
        username = safe_opts.get("user") or safe_opts.get("username") or parsed.get("username")
        extras: dict[str, str] = {}
        if safe_opts.get("driver"):
            extras["driver"] = str(safe_opts["driver"])
        if dbtable:
            extras["dbtable"] = str(dbtable)
        # Only the safe URL ever lands in the graph.
        if url:
            extras["url"] = url
        server = str(parsed.get("server") or "") or None
        port = parsed.get("port") if isinstance(parsed.get("port"), int) else None
        dbname = parsed.get("dbname") or None
        cid = connection_id(
            klass=str(klass), server=server or "", dbname=dbname or "", port=port,
        )
        return ConnectionIR(
            id=cid, klass=str(klass), server=server, port=port,
            dbname=dbname, schema=schema_val,
            username=str(username) if username else None,
            options=extras,
            has_credentials=bool(parsed.get("has_credentials")) or url_creds or opts_creds,
        )

    # --- Kafka -------------------------------------------------------------
    if fmt in _KAFKA_FORMATS:
        servers = opts.get("kafka.bootstrap.servers") or opts.get("bootstrap.servers") or ""
        topic = opts.get("subscribe") or opts.get("topic") or opts.get("assign") or ""
        host_list = [h.strip() for h in str(servers).split(",") if h.strip()]
        # Sort multi-host so different orderings dedup.
        sorted_hosts = sort_host_list(host_list)
        first = sorted_hosts[0] if sorted_hosts else ""
        host_only, _, port_str = first.partition(":")
        host_only = canonical_host(host_only)
        port = int(port_str) if port_str.isdigit() else 9092
        extras = {}
        if topic:
            extras["topic"] = str(topic)
        if sorted_hosts and len(sorted_hosts) > 1:
            extras["brokers"] = ",".join(sorted_hosts)
        cid = connection_id(klass="kafka", server=host_only or "", dbname="", port=port)
        return ConnectionIR(
            id=cid, klass="kafka",
            server=host_only or None, port=port, options=extras,
        )

    # --- Snowflake ---------------------------------------------------------
    if fmt in _SNOWFLAKE_FORMATS:
        url = opts.get("sfUrl") or opts.get("sfurl") or ""
        url, sf_url_creds = strip_url_credentials(str(url))
        host = ""
        if url:
            parsed_url = urlparse(url if "://" in url else f"https://{url}")
            host = canonical_host(parsed_url.hostname or "")
        db = opts.get("sfDatabase") or opts.get("sfdatabase")
        sch = opts.get("sfSchema") or opts.get("sfschema")
        safe_opts, sf_creds = split_credential_options(opts)
        user = safe_opts.get("sfUser") or safe_opts.get("sfuser") or safe_opts.get("user")
        extras = {}
        wh = safe_opts.get("sfWarehouse") or safe_opts.get("sfwarehouse")
        if wh:
            extras["warehouse"] = str(wh)
        role = safe_opts.get("sfRole") or safe_opts.get("sfrole")
        if role:
            extras["role"] = str(role)
        dbtable = safe_opts.get("dbtable")
        if dbtable:
            extras["dbtable"] = str(dbtable)
        cid = connection_id(klass="snowflake", server=host, dbname=db or "", port=443)
        return ConnectionIR(
            id=cid, klass="snowflake", server=host or None, port=443 if host else None,
            dbname=db, schema=sch,
            username=str(user) if user else None,
            options=extras,
            has_credentials=sf_url_creds or sf_creds,
        )

    # --- BigQuery ----------------------------------------------------------
    if fmt in _BIGQUERY_FORMATS:
        table = opts.get("table") or table_arg or ""
        project = dataset = None
        if table and "." in table:
            parts = table.split(".")
            if len(parts) == 3:
                project, dataset, _ = parts
            elif len(parts) == 2:
                dataset, _ = parts
        project = project or opts.get("project") or opts.get("parentProject")
        _safe_opts, bq_creds = split_credential_options(opts)
        extras = {}
        if opts.get("credentialsFile"):
            # Keep the path but flag credentials present.
            extras["credentialsFile"] = str(opts["credentialsFile"])
            bq_creds = True
        if table:
            extras["table"] = str(table)
        cid = connection_id(klass="bigquery", server="bigquery.googleapis.com", dbname=project or "", port=443)
        return ConnectionIR(
            id=cid, klass="bigquery", server="bigquery.googleapis.com", port=443,
            dbname=project, schema=dataset, options=extras,
            has_credentials=bq_creds,
        )

    # --- Iceberg / Hudi ----------------------------------------------------
    if fmt in _ICEBERG_FORMATS or fmt in _HUDI_FORMATS:
        loc = location or opts.get("path") or table_arg or ""
        loc_masked, ih_url_creds = strip_url_credentials(str(loc))
        store = _parse_object_store_uri(loc_masked)
        klass = "iceberg" if fmt in _ICEBERG_FORMATS else "hudi"
        extras = {}
        if store.get("klass"):
            extras["backing_store"] = str(store["klass"])
        if loc_masked:
            extras["path"] = loc_masked
        server = str(store.get("server") or "") or None
        port_v = store.get("port") if isinstance(store.get("port"), int) else None
        cid = connection_id(klass=klass, server=server or "", dbname="", port=port_v)
        return ConnectionIR(
            id=cid, klass=klass, server=server, port=port_v, options=extras,
            has_credentials=ih_url_creds,
        )

    # --- MongoDB / Cassandra / Elasticsearch -------------------------------
    if fmt in {"mongo", "mongodb", "com.mongodb.spark.sql.defaultsource"}:
        raw_uri = (
            opts.get("uri")
            or opts.get("spark.mongodb.input.uri")
            or opts.get("spark.mongodb.output.uri")
            or opts.get("spark.mongodb.read.connection.uri")
            or opts.get("spark.mongodb.write.connection.uri")
            or opts.get("connection.uri")
            or ""
        )
        uri, mongo_url_creds = strip_url_credentials(str(raw_uri))
        host: str | None = None
        port: int | None = None
        db: str | None = opts.get("database")
        coll = opts.get("collection")
        user: str | None = None
        if uri:
            try:
                p = urlparse(uri)
                host = canonical_host(p.hostname or "") or None
                port = p.port
                user = p.username or None
                if p.path and not db:
                    db = p.path.lstrip("/") or None
            except ValueError:
                pass
        port = port or 27017
        _safe_opts, mongo_opts_creds = split_credential_options(opts)
        extras = {}
        if coll:
            extras["collection"] = str(coll)
        cid = connection_id(klass="mongodb", server=host or "", dbname=db or "", port=port)
        return ConnectionIR(
            id=cid, klass="mongodb", server=host, port=port,
            dbname=db, username=user, options=extras,
            has_credentials=mongo_url_creds or mongo_opts_creds,
        )

    if fmt in {"cassandra", "org.apache.spark.sql.cassandra"}:
        host_raw = opts.get("spark.cassandra.connection.host") or opts.get("host") or ""
        # ``host`` may be comma-separated — multi-node Cassandra clusters.
        host_list = sort_host_list(h.strip() for h in str(host_raw).split(","))
        host = canonical_host(host_list[0] if host_list else "")
        port_str = opts.get("spark.cassandra.connection.port") or opts.get("port")
        port = int(port_str) if (port_str and str(port_str).isdigit()) else 9042
        keyspace = opts.get("keyspace")
        table = opts.get("table")
        _safe_opts, cass_opts_creds = split_credential_options(opts)
        extras = {}
        if table:
            extras["table"] = str(table)
        if len(host_list) > 1:
            extras["hosts"] = ",".join(host_list)
        cid = connection_id(klass="cassandra", server=host or "", dbname=keyspace or "", port=port)
        return ConnectionIR(
            id=cid, klass="cassandra", server=host or None, port=port,
            dbname=keyspace, options=extras,
            has_credentials=cass_opts_creds,
        )

    if fmt in {"elasticsearch", "es", "org.elasticsearch.spark.sql"}:
        nodes = opts.get("es.nodes") or opts.get("nodes") or ""
        port_str = opts.get("es.port") or opts.get("port")
        port = int(port_str) if (port_str and str(port_str).isdigit()) else None
        node_list = sort_host_list(h.strip() for h in str(nodes).split(","))
        first = node_list[0] if node_list else ""
        host, _, inline_port = first.partition(":")
        host = canonical_host(host)
        if inline_port.isdigit() and port is None:
            port = int(inline_port)
        port = port or 9200
        index = opts.get("es.resource") or opts.get("resource") or table_arg or location
        _safe_opts, es_opts_creds = split_credential_options(opts)
        extras = {}
        if index:
            extras["index"] = str(index)
        if len(node_list) > 1:
            extras["nodes"] = ",".join(node_list)
        cid = connection_id(klass="elasticsearch", server=host or "", dbname=str(index or ""), port=port)
        return ConnectionIR(
            id=cid, klass="elasticsearch", server=host or None, port=port,
            options=extras,
            has_credentials=es_opts_creds,
        )

    # --- Hive catalog (spark.read.table, saveAsTable without format) -------
    if fmt in {"hive", ""} and (table_arg or database):
        extras = {}
        if table_arg:
            extras["table"] = str(table_arg)
        cid = connection_id(klass="hive", server="metastore", dbname=database or "")
        return ConnectionIR(
            id=cid, klass="hive", server="metastore",
            dbname=database, options=extras,
        )

    # --- Catalog table (saveAsTable / insertInto / spark.read.table) -----
    # When a dotted, scheme-less ``table_arg`` is given even alongside a
    # storage format like ``delta`` or ``parquet`` (i.e. the format goes on
    # the file, the connection points at the metastore that owns it), emit a
    # Hive/Unity-catalog connection. The :Table itself keeps the explicit
    # storage_format so the distinction isn't lost.
    if table_arg and "://" not in str(table_arg) and not str(table_arg).startswith("/"):
        if "." in str(table_arg) or database:
            extras = {"table": str(table_arg)}
            if fmt:
                extras["storage_format"] = fmt
            cid = connection_id(klass="hive", server="metastore", dbname=database or "")
            return ConnectionIR(
                id=cid, klass="hive", server="metastore",
                dbname=database, options=extras,
            )

    # --- Path-backed file formats (parquet, csv, json, delta, orc, avro,
    #     text, xml) on s3 / gcs / adls / wasb / hdfs / dbfs / file
    #     PLUS any unknown scheme (mints klass=unknown:<scheme>). ---
    loc = location or opts.get("path") or ""
    if loc:
        loc_masked, path_url_creds = strip_url_credentials(str(loc))
        store = _parse_object_store_uri(loc_masked)
        if store.get("klass"):
            file_fmt = _FORMAT_TO_KLASS.get(fmt) or fmt or None
            extras = {}
            if file_fmt:
                extras["format"] = str(file_fmt)
            if store.get("path"):
                extras["path"] = str(store["path"])
            klass = str(store["klass"])
            server = str(store.get("server") or "") or None
            port_v = store.get("port") if isinstance(store.get("port"), int) else None
            dbname = str(store.get("dbname") or "") or None
            cid = connection_id(
                klass=klass, server=server or "", dbname=dbname or "", port=port_v,
            )
            is_unknown_scheme = klass.startswith("unknown:")
            return ConnectionIR(
                id=cid, klass=klass, server=server, port=port_v,
                dbname=dbname, options=extras,
                has_credentials=path_url_creds,
                # Unknown URI scheme — node still exists, just flagged.
                resolved=not is_unknown_scheme,
                source="unknown_scheme" if is_unknown_scheme else None,
                detail=klass.split(":", 1)[1] if is_unknown_scheme else None,
            )

    # --- Unknown format with no location info — last-chance node so an I/O
    # site is never dropped silently (per connections.md §1 prime directive).
    if fmt:
        klass = f"unknown:{fmt}"
        extras = {k: str(v) for k, v in opts.items() if not is_credential_option_key(k)}
        had_creds_unknown = any(is_credential_option_key(k) for k in opts)
        cid = connection_id(klass=klass, server="", dbname="")
        return ConnectionIR(
            id=cid, klass=klass, options=extras,
            resolved=False, has_credentials=had_creds_unknown,
            source="unknown_format", detail=fmt,
        )

    # No format AND no location AND no table — really nothing to extract.
    return None
