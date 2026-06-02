"""Build ConnectionIR(s) from a single Tableau `<datasource>` element."""

from __future__ import annotations

from lxml import etree

from tableau_parser.models.domain import ConnectionIR
from tableau_parser.utils.ids import connection_id
from tableau_parser.utils.lines import first_sourceline


def parse_connections(datasource_el: etree._Element) -> list[ConnectionIR]:
    """Return one ConnectionIR per named-connection, or the single non-federated connection."""
    out: list[ConnectionIR] = []

    named = datasource_el.findall(".//named-connection/connection")
    if named:
        for c in named:
            ir = _build(c)
            if ir is not None:
                out.append(ir)
        return out

    c = datasource_el.find("./connection")
    if c is not None:
        # Skip class='federated' wrapper without named-connections (no real connection).
        if c.get("class", "") != "federated":
            ir = _build(c)
            if ir is not None:
                out.append(ir)
    return out


def _build(conn_el: etree._Element) -> ConnectionIR | None:
    klass = conn_el.get("class", "")
    if not klass or klass == "federated":
        return None
    server = conn_el.get("server", "") or conn_el.get("dbname", "") or ""
    dbname = conn_el.get("dbname", "")
    schema = conn_el.get("schema", "")
    port = conn_el.get("port", "")
    username = conn_el.get("username", "")
    return ConnectionIR(
        id=connection_id(klass=klass, server=server, dbname=dbname),
        klass=klass,
        server=server,
        dbname=dbname,
        schema=schema,
        port=port,
        username=username,
        line=first_sourceline(conn_el),
    )
