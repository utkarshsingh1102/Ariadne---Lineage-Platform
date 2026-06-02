"""Deterministic node-ID derivation.

Implements the rules in `lineage-contracts/schema/node-id-rules.md`. Every parser
ships a byte-for-byte identical version of `make_id`; cross-parser lineage
threading depends on it.

The canonical string is always lowercased, so identifiers that differ only in
case hash to the same ID. The `fully_qualified_name` string property stored on
:Table nodes is **uppercased** so different writers normalize to identical
strings (the FQN is the MERGE key in the writer).
"""

from __future__ import annotations

import hashlib


def make_id(*parts: str) -> str:
    """sha256(canonical_string)[:16], lowercased, '::'-joined."""
    canonical = "::".join((p or "").strip().lower() for p in parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# --- Tables -------------------------------------------------------------------

def _canonical_table_string(database: str, schema: str, name: str) -> str:
    """The string that gets hashed into a :Table id. Lowercased per contract."""
    return f"table::{database}.{schema}.{name}".strip().lower()


def table_fqn(database: str, schema: str, name: str) -> str:
    """The `fully_qualified_name` property stored on :Table nodes.

    Uppercased so heterogeneous writers converge on the same MERGE key.
    """
    return f"{database}.{schema}.{name}".strip().upper()


def table_id(database: str, schema: str, name: str) -> str:
    return hashlib.sha256(
        _canonical_table_string(database, schema, name).encode("utf-8")
    ).hexdigest()[:16]


# --- Attributes ---------------------------------------------------------------

def attribute_id_physical(table_fqn: str, column: str) -> str:
    """ID for a column hanging off a physical :Table.

    `table_fqn` should be the value returned by `table_fqn(db, schema, name)`
    (uppercased), but the function lowercases internally so callers may pass any
    case.
    """
    return make_id("attribute", table_fqn, column)


def attribute_id_calculated(datasource_id: str, field_name: str) -> str:
    """ID for a calculated field scoped to its owning Tableau datasource."""
    return make_id("attribute", datasource_id, field_name)


# --- Connections / workbooks / sheets / dashboards / parameters ---------------

def connection_id(klass: str, server: str, dbname: str) -> str:
    return make_id("connection", klass, server, dbname)


def workbook_id(absolute_file_path: str) -> str:
    return make_id("tableau_workbook", absolute_file_path)


def datasource_id(workbook_id_str: str, datasource_name: str) -> str:
    return make_id("tableau_datasource", workbook_id_str, datasource_name)


def worksheet_id(workbook_id_str: str, worksheet_name: str) -> str:
    return make_id("worksheet", workbook_id_str, worksheet_name)


def dashboard_id(workbook_id_str: str, dashboard_name: str) -> str:
    return make_id("dashboard", workbook_id_str, dashboard_name)


def parameter_id(workbook_id_str: str, parameter_name: str) -> str:
    return make_id("parameter", workbook_id_str, parameter_name)


def dashboard_zone_id(dashboard_id_str: str, zone_index: int, kind: str) -> str:
    """A zone has no persistent name in Tableau XML — keyed by index+kind
    so reparses produce stable ids for the same XML."""
    return make_id("dashboard_zone", dashboard_id_str, str(zone_index), kind)


def dashboard_action_id(
    dashboard_id_str: str, action_index: int, kind: str, name: str = ""
) -> str:
    return make_id("dashboard_action", dashboard_id_str, str(action_index), kind, name)


def group_id(datasource_id_str: str, group_name: str) -> str:
    return make_id("tableau_group", datasource_id_str, group_name)


def set_id(datasource_id_str: str, set_name: str) -> str:
    return make_id("tableau_set", datasource_id_str, set_name)


def bin_id(datasource_id_str: str, bin_name: str) -> str:
    return make_id("tableau_bin", datasource_id_str, bin_name)


def hierarchy_id(datasource_id_str: str, hierarchy_name: str) -> str:
    return make_id("tableau_hierarchy", datasource_id_str, hierarchy_name)


def parameter_scope_id(workbook_id_str: str, scope_name: str = "Parameters") -> str:
    """Improvement-v2 §4 — :TableauParameterScope is a synthetic node that
    represents the workbook's ``Parameters`` datasource without claiming
    the ``:TableauDatasource`` label. Keyed by (workbook, scope_name) so
    multiple Parameters blocks (rare but legal) are distinguishable."""
    return make_id("tableau_parameter_scope", workbook_id_str, scope_name)


def worksheet_blend_id(
    worksheet_id_str: str, primary: str, secondary: str,
) -> str:
    """Improvement-v2 §9 — one ``<datasource-relationship>`` per worksheet
    pairs a primary and a secondary datasource. The id is stable across
    reparses of the same XML."""
    return make_id("worksheet_blend", worksheet_id_str, primary, secondary)


def cross_ds_ref_id(
    target_field_id: str, source_field_id: str, char_start: int,
) -> str:
    """Improvement-v2 §6 — a resolved cross-datasource calc-field reference.
    Indexed by (target, source, span) so multiple refs from the same calc
    to the same foreign field don't collide."""
    return make_id(
        "cross_ds_ref", target_field_id, source_field_id, str(char_start),
    )
