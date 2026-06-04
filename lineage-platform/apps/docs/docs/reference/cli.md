---
title: CLI
sidebar_label: CLI
---

# CLI reference

## Per-parser entry points

Each parser ships its FastAPI app under
`<parser>_parser.main` (or `core` for QlikView). When running inside
the container you typically interact with it via HTTP, but for local
debugging:

```bash
# Tableau — direct parse, prints JSON IR
cd tableau-parser && .venv/bin/python -c "
from tableau_parser.parser.workbook import parse_workbook
print(parse_workbook('tests/fixtures/01_simple_single_datasource.twb'))
"

# TWS — orchestrator with parse-errors + dependencies
cd tws-parser && .venv/bin/python -c "
from tws_parser.parser.orchestrator import parse_full_with_errors
from tws_parser.parser.dependencies import resolve_full
unit, errs = parse_full_with_errors('tests/fixtures/01_single_schedule_single_job.txt')
deps = resolve_full(unit)
print('schedules:', len(unit.schedules), 'follows:', len(deps.follows_edges))
"
```

## `dump_stages` (simulator backend)

Every parser has a `cli.dump_stages` module that writes per-stage JSON
files — the data the [parser simulators](/tutorials/see-the-parser-work)
read:

```bash
# Run inside the container so all deps are present
docker exec lineage-tws-parser \
  python -m tws_parser.cli.dump_stages \
  /data/inputs/01_single_schedule_single_job.txt \
  --out /tmp/sim-out

docker cp lineage-tws-parser:/tmp/sim-out ./my-snapshot
```

Outputs:

| File | Contents |
|---|---|
| `input.<ext>` | Verbatim source |
| `tokens.json` | ANTLR token stream (ANTLR-based parsers) |
| `tree.json` | ANTLR parse tree (ANTLR-based parsers) |
| `dom.json` | lxml DOM (Tableau) |
| `ast.json` | Python AST summary (Spark) |
| `ir.json` | Domain IR as `dataclasses.asdict` |
| `cypher.cypher` | MERGE templates the writer would execute |
| `graph.json` | Cytoscape `{nodes, edges}` |
| `meta.json` | Stats + warnings |

## Docs-site build scripts

```bash
cd lineage-platform/apps/docs

npm run build:api-ref       # regenerate per-service openapi + master api-catalogue
npm run build:test-matrix   # walk tests/ trees, refresh docs/tests/_matrix.json
npm run build:simulations   # regenerate all simulator snapshots (needs parser containers up)
npm run build               # runs all three then docusaurus build
npm run start               # dev server on :3002
npm run serve               # serve the production build on :3002
```
