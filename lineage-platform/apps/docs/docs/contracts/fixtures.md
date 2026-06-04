---
title: Fixtures
sidebar_label: Fixtures
---

# Fixtures

The cross-parser fixture catalogue lives at
[`lineage-contracts/fixtures-index.md`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/blob/main/lineage-contracts/fixtures-index.md)
and is the source of truth for the canonical inputs each parser ships.

## Per-parser fixture lists

Each parser also keeps its own working fixture set:

- [Tableau](/tests/tableau) — `tableau-parser/tests/fixtures/`
- [TWS](/tests/tws) — `tws-parser/tests/fixtures/`
- [QlikView](/tests/qlikview) — `qlikview-parser/tests/fixtures/`
- [Spark](/tests/spark) — `spark-parser/fixtures/`

## Where they appear in this site

Several fixtures power the [Parser simulators](/tutorials/see-the-parser-work):

- `01_single_schedule_single_job.txt` → [TWS simulator](/parsers/tws#simulator--single-schedule-single-job)
- `06_realistic_dump_many_schedules.txt` → [TWS simulator](/parsers/tws#simulator--realistic-dump-with-three-schedules)
- `01_simple_single_datasource.twb` → [Tableau simulator](/parsers/tableau#simulator--simple-single-datasource)
- `02_calculated_fields.twb` → [Tableau simulator](/parsers/tableau#simulator--calculated-fields)
- `01_simple_sql_load.qvs` → [QlikView simulator](/parsers/qlikview#simulator--simple-sql-load)
- `03_left_join.qvs` → [QlikView simulator](/parsers/qlikview#simulator--left-join)
- `01_simple_read_write.py` → [Spark simulator](/parsers/spark#simulator--simple-read--write)
- `02_join_and_select.py` → [Spark simulator](/parsers/spark#simulator--join-and-select)
