---
title: TWS tests
sidebar_label: TWS tests
---

# TWS tests

23 `test_*.py` files, 14 fixtures.

## Fixture catalogue

Canonical fixtures under
[`tws-parser/tests/fixtures/`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/tree/main/tws-parser/tests/fixtures):

| Fixture | Exercises |
|---|---|
| `01_single_schedule_single_job.txt` | The minimum useful input. |
| `02_multi_job_with_follows.txt` | Job-to-job FOLLOWS edges within a schedule. |
| `03_schedule_level_dependency.txt` | Cross-stream `FOLLOWS WS#STREAM.@`. |
| `04_resource_and_file_deps.txt` | `NEEDS` resources + `OPENS` file watchers. |
| `05_complex_run_cycles.txt` | RRULE / `MONTHSTART` / `WORKDAYS` normalisation. |
| `06_realistic_dump_many_schedules.txt` | Production-shaped multi-schedule dump. |
| `07_xml_export_single.xml` | XML export path. |
| `08_xml_export_full.xml` | Full XML topology. |
| `09_lineage_stress.txt` | High-cardinality edges across streams. |
| `10_malformed.txt` | Parse-error surface for `parse_errors`. |

## v0.3 features under test

- `:TwsFile` wrapper + `CONTAINS_SCHEDULE` edge
- `days_of_week`, `days_of_month`, `frequency`, `on_until`, `every`
- Schedule-level `NEEDS` synthesizing implicit `:Resource` nodes
- RRULE `BYMONTHDAY`/`BYDAY` extraction
- `ON … RC=N` recovery branches → `RECOVERS_WITH` edges
- `MONTHSTART` / `WORKDAYS` run-cycle normalisation

## See also

- [Parser overview](/parsers/tws).
- [Simulator — realistic dump](/parsers/tws#simulator--realistic-dump-with-three-schedules).
