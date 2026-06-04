---
title: Tableau tests
sidebar_label: Tableau tests
---

# Tableau tests

32 `test_*.py` files, 14 fixtures.

## Fixture catalogue

Canonical fixtures under
[`tableau-parser/tests/fixtures/`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/tree/main/tableau-parser/tests/fixtures):

| Fixture | Exercises |
|---|---|
| `01_simple_single_datasource.twb` | Bare-minimum workbook, one datasource, one worksheet. |
| `02_calculated_fields.twb` | Calculated-field expressions → `derives_from` edges. |
| `03_federated_join.twb` | Federated datasource with multi-table join. |
| `04_custom_sql.twb` | Custom-SQL relation → sqlglot CTE extraction. |
| `05_dashboard_with_multiple_sheets.twb` | Dashboard binding multiple worksheets. |
| `06_packaged_workbook.twbx` | `.twbx` (zipped) container handling. |
| `07_parameters.twb` | `:Parameter` + `:ParameterScope` IRs. |
| `08_realistic_dashboard.twb` | Production-shaped dashboard. |
| `09_full_reference.twb` | Stress test covering every supported feature. |

## Test categories

- **Unit** — `tests/unit/test_*.py` (pure IR + writer logic)
- **Integration** — `tests/integration/test_*.py` (testcontainers-Neo4j round-trip)

## See also

- [Parser overview](/parsers/tableau).
- [Simulator — simple single datasource](/parsers/tableau#simulator--simple-single-datasource).
