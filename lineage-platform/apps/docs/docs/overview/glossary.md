---
title: Glossary
sidebar_label: Glossary
---

# Glossary

Per-system vocabulary mapped to the graph labels they produce.

| Term | System | Graph label | Meaning |
|---|---|---|---|
| **Workbook** | Tableau | `:Workbook` | The `.twb` / `.twbx` artefact. |
| **Datasource** | Tableau | `:Datasource` | A connection-plus-schema unit referenced by worksheets. |
| **Worksheet** | Tableau | `:Worksheet` | One chart / view bound to a datasource. |
| **Dashboard** | Tableau | `:Dashboard` | Container of worksheets + actions. |
| **Parameter** | Tableau | `:Parameter` | User-input control. Scoped via `:ParameterScope`. |
| **Composer file** | TWS | `:TwsFile` | An uploaded `.txt` / `.xml` containing N schedule definitions. |
| **Schedule** | TWS | `:Schedule` | A named, time-boxed group of jobs. |
| **JobStream** | TWS | `:JobStream` | v0.2 wrapper around a Schedule for the topology layer. |
| **Job** | TWS | `:Job` | A single executable step on a workstation. |
| **Workstation** | TWS | `:Workstation` | An agent / CPU / FTA. |
| **Calendar** | TWS | `:Calendar` | Named set of dates for run-cycle / NOTON rules. |
| **Prompt** | TWS | `:Prompt` | A manual gate that pauses a job until acknowledged. |
| **EventRule** | TWS | `:EventRule` | A rule that triggers a job stream on an event. |
| **Resource** | TWS | `:Resource` | A capacity slot a job NEEDS. |
| **FileWatcher** | TWS | `:FileWatcher` | A path/glob a job OPENS / waits on. |
| **App** | QlikView | `:QlikScript` (v0.1) / `:QlikApp` (v0.2) | A QlikView document. |
| **DataPlatform** | QlikView | `:DataPlatform` | The underlying database vendor (snowflake, oracle, …). |
| **DataConnection** | QlikView | `:DataConnection` | A named connection (with secret_ref, never the value). |
| **PhysicalSource** | QlikView | `:PhysicalSource` | A table / view / file / endpoint a load reads from. |
| **Dataset** | QlikView | `:Dataset` | A logical table in the loaded model. |
| **SparkScript** | Spark | `:SparkScript` | A `.py` / `.sql` / `.ipynb` artefact. |
| **DataFrame** | Spark | `:DataFrame` | A node in the chain map; one per variable assignment. |
| **NotebookCell** | Spark | `:NotebookCell` | One cell of an `.ipynb`. |
| **ProjectIR** | Spark | `:Project` | A multi-file Spark project with cross-file imports. |
| **OrchestrationJob** | Spark | `:OrchestrationJob` | An Airflow / Databricks task that runs Spark code. |
| **Table** | shared | `:Table` | A physical table. **Cross-parser shared label.** |
| **Connection** | shared | `:Connection` | A connection / connection-string. **Cross-parser shared label.** |
| **Attribute** | shared | `:Attribute` | A column / field. **Cross-parser shared label.** |
| **Script** | shared | `:Script` | An executable file at an absolute path. **Cross-parser shared label.** |
