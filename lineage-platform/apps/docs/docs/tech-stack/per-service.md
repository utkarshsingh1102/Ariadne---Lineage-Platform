---
title: Per service
sidebar_label: Per service
---

# Per service

| Service | Language | Key deps |
|---|---|---|
| **tableau-parser** | Python 3.11 | FastAPI, Pydantic v2, lxml, sqlglot, neo4j |
| **tws-parser** | Python 3.11 | FastAPI, Pydantic v2, antlr4-python3-runtime, neo4j, asyncpg, openpyxl |
| **qlikview-parser** | Python 3.11 | FastAPI, Pydantic v2, antlr4-python3-runtime, sqlglot, neo4j, olefile |
| **spark-parser** | Python 3.11 | FastAPI, Pydantic v2, ast (stdlib), sqlglot, neo4j |
| **gateway** | Python 3.11 | FastAPI, Pydantic v2, neo4j, asyncpg, httpx |
| **frontend** | TypeScript 5 | Next.js 14, React 18, @carbon/react, cytoscape, cytoscape-elk |
| **docs** | TypeScript 5 | Docusaurus 3, MDX, @docusaurus/theme-mermaid, cytoscape |

## Codegen step

Two parsers run an ANTLR4 codegen step at container-image build time:

- `tws-parser/Makefile` → `src/tws_parser/generated/`
- `qlikview-parser/Makefile` → `src/qlikview_parser/generated/`

Both invoke `java -jar tools/antlr-4.13.x-complete.jar -Dlanguage=Python3 -visitor`
on the `.g4` files. The generated `.py` files are NOT checked into git
— the container build re-runs codegen, so the Dockerfile order is:

```dockerfile
FROM eclipse-temurin:17-jre AS antlr-build
COPY grammar/ /build/grammar/
COPY tools/   /build/tools/
RUN java -jar /build/tools/antlr-*-complete.jar …

FROM python:3.11-slim AS runtime
COPY --from=antlr-build /build/generated/ src/<parser>_parser/generated/
COPY pyproject.toml ./
RUN pip install -e .
```

## See also

- [Tech stack](/tech-stack/) — grouped by responsibility.
- [Versions](/tech-stack/versions) — exact pin matrix.
