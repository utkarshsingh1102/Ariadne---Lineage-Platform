---
title: Quick start
sidebar_label: Quick start
---

# Quick start

Stand up the full platform on your laptop in five commands.

## Prerequisites

- Docker Desktop 4.20+ (or Docker Engine 24+ with `docker compose` v2)
- ~4 GB free RAM
- Ports `3000`, `3002`, `5432`, `7475`, `7688`, `8000`, `8001`-`8004`
  available

Windows users: install via [`setup-windows.ps1`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/blob/main/setup-windows.ps1)
which provisions WSL2 + Docker Desktop + Git in one shot.

## Five commands

```bash
git clone https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform.git ariadne
cd ariadne/lineage-platform
docker compose up -d --build
# ~5 minutes on first build (ANTLR codegen, Next.js standalone, pip installs)
open http://localhost:3000     # Frontend
open http://localhost:3002     # Docs (this site)
```

## Service URLs

| URL | What |
|---|---|
| `http://localhost:3000/` | Carbon-styled frontend |
| `http://localhost:3000/parse` | Upload a file |
| `http://localhost:3000/files` | Inventory of parsed files |
| `http://localhost:3000/lineage` | Upstream / downstream tracer |
| `http://localhost:3002/` | This docs site |
| `http://localhost:8000/docs` | Gateway OpenAPI (Swagger UI) |
| `http://localhost:7475/` | Neo4j Browser (`neo4j` / `lineagepass`) |

## First parse

1. Open `http://localhost:3000/parse`.
2. Drop one of the canonical fixtures from
   [`tableau-parser/tests/fixtures/`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/tree/main/tableau-parser/tests/fixtures).
3. Click **Trace upstream** when the parse completes.
4. Explore the graph.

## Common operations

```bash
# Stop everything (preserves data volumes)
docker compose down

# Stop + wipe data
docker compose down -v

# Rebuild just one service (e.g. after editing the TWS parser)
docker compose up -d --build tws-parser

# Tail logs
docker compose logs -f gateway
```

## See also

- [Deploy → AWS](/deploy/aws) for production-ish deploy on EC2.
- [System architecture](/architecture/system) for the big picture.
