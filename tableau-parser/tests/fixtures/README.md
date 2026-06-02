# Tableau Test Fixtures

| File | Plan section | What it covers |
|---|---|---|
| `01_simple_single_datasource.twb` | §2.1, §6 | One connection, one table, three columns, one worksheet — smoke test |
| `02_calculated_fields.twb` | §2.3, §6 step 4.4–4.5 | Simple, nested, LOD, and CASE-WHEN calculated fields |
| `03_federated_join.twb` | §2.1, §6 step 4.3 | Cross-database INNER JOIN with `<named-connections>` |
| `04_custom_sql.twb` | §6 step 4.3 | `<relation type="text">` with sqlglot extraction across 3 schemas |
| `05_dashboard_with_multiple_sheets.twb` | §6 step 6 | Dashboard zone references, including a repeated worksheet (deduplication test) |
| `06_packaged_workbook_source.twb` + `make_twbx.py` | §2.2, §6 step 1 + 7 | `.twbx` archive with embedded `.hyper` extract marker |
| `07_parameters.twb` | §6 step 8 | The special `Parameters` datasource |
| `08_realistic_dashboard.twb` | all of §2 + §6 | Kitchen sink: 4 datasources, federated join, LOD calc, custom SQL, shared worksheets, Unicode |

## Generating the `.twbx`

```bash
python3 make_twbx.py
```

This zips `06_packaged_workbook_source.twb` into `06_packaged_workbook.twbx` and adds a placeholder `Data/Datasources/packaged_sales.hyper` so `has_extract` detection has something to find. The output is gitignored.

## Provenance

All fixtures are hand-written, minimised XML. None contain real customer data. When realistic anonymised Tableau Public workbooks are sourced (plan §10), add as `09_*.twb` onward and update this table.
