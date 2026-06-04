---
title: QlikView tests
sidebar_label: QlikView tests
---

# QlikView tests

32 `test_*.py` files, 13 fixtures.

## Fixture catalogue

Canonical fixtures under
[`qlikview-parser/tests/fixtures/`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/tree/main/qlikview-parser/tests/fixtures):

| Fixture | Exercises |
|---|---|
| `01_simple_sql_load.qvs` | `LOAD … SQL SELECT`. |
| `02_resident_load.qvs` | `LOAD … RESIDENT`. |
| `03_left_join.qvs` | `LEFT JOIN` two tables. |
| `04_concatenate.qvs` | `CONCATENATE`. |
| `05_file_load.qvs` | `LOAD … FROM file.qvd`. |
| `06_variables_and_includes.qvs` | `SET` / `LET` + `$(include=…)`. |
| `07_subroutines.qvs` | `SUB` / `CALL`. |
| `08_realistic_dashboard.qvs` | Production-shaped composite. |
| `09_comments_and_edge_cases.qvs` | Whitespace + comment robustness. |
| `10_qvd_load.qvs` | QVD-header signal for `unique` constraints. |

## Status

Per [`qlikview-parser/TEST_REPORT.md`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/blob/main/qlikview-parser/TEST_REPORT.md):

| State | Count | Share |
|---|---:|---:|
| PASSED | 33 | 41% |
| XFAIL (known gaps) | 35 | 43% |
| XPASS | 4 | 5% |
| SKIPPED (Neo4j-gated) | 9 | 11% |

The XFAIL set is the **explicit remediation roadmap** for v0.3 — see
[`qlikview-parser/REVIEW.md`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/blob/main/qlikview-parser/REVIEW.md).

## See also

- [Parser overview](/parsers/qlikview).
- [Simulator — simple SQL load](/parsers/qlikview#simulator--simple-sql-load).
