# TWS Test Fixtures

| File | Format | Plan section | Covers |
|---|---|---|---|
| `01_single_schedule_single_job.txt` | composer text | §2.1 | One schedule + one job — smoke test |
| `02_multi_job_with_follows.txt` | composer text | §2.1, §5.2 | Multiple jobs in one schedule with FOLLOWS chain |
| `03_schedule_level_dependency.txt` | composer text | §2.1, §6 step 7 | Schedule-to-schedule FOLLOWS with `.@` wildcard |
| `04_resource_and_file_deps.txt` | composer text | §5.2 | NEEDS resource + OPENS file dependency |
| `05_complex_run_cycles.txt` | composer text | §10.4 | EVERY_WEEKDAY, MONTHLY, HOURLY, custom calendars |
| `06_realistic_dump_many_schedules.txt` | composer text | §11 | Kitchen sink: 3 streams, mixed script types, cross-workstation, comments |
| `07_xml_export_single.xml` | XML | §2.2 | Equivalent of 02 in XML form |
| `08_xml_export_full.xml` | XML | §2.2, §10.5 | Equivalent of 06 in XML form (IR convergence test) |

## Format coverage

- Six composer-text fixtures cover the ANTLR grammar path.
- Two XML fixtures cover the lxml path.
- Fixtures `02` ↔ `07` and `06` ↔ `08` are designed as **input pairs** so the integration tests can assert both formats converge on the same `ScheduleIR` shape (plan §10.5).

## Provenance

Hand-written, minimised. No real production data. When an anonymised Barclays dump is available (plan §11), add as `09_*.txt` and document any redactions here.
