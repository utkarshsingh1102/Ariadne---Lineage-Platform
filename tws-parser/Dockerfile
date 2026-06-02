# Multi-stage build for the TWS parser cloud plugin.
# Stage 1 runs ANTLR codegen (needs Java). Stage 2 ships only Python runtime.

# ---- Stage 1: ANTLR codegen ----
FROM eclipse-temurin:17-jre AS antlr-build
WORKDIR /build
ARG ANTLR_VERSION=4.13.1
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL -o /tmp/antlr.jar \
        https://www.antlr.org/download/antlr-${ANTLR_VERSION}-complete.jar
COPY grammar/ grammar/
RUN mkdir -p generated && \
    cd grammar && \
    java -jar /tmp/antlr.jar \
        -Dlanguage=Python3 -visitor -listener -no-listener \
        -o ../generated \
        TWSComposerLexer.g4 TWSComposerParser.g4 && \
    touch ../generated/__init__.py

# ---- Stage 2: Python runtime ----
FROM python:3.11-slim
RUN groupadd -r app && useradd -r -g app app \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl libpq5 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/
COPY alembic.ini ./

# Drop the ANTLR-generated lexer/parser/visitor files into the source tree
# BEFORE pip install, so setuptools packages them into the installed wheel
# alongside the rest of tws_parser. Previously this COPY ran AFTER pip
# install, which put the files at /app/src/... while the package lived in
# site-packages — Python imports from site-packages, so the lexer wasn't
# findable and the service crashed on startup with ModuleNotFoundError.
COPY --from=antlr-build /build/generated/ ./src/tws_parser/generated/

RUN pip install --no-cache-dir --upgrade pip build \
    && pip install --no-cache-dir .

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "tws_parser.main:app", "--host", "0.0.0.0", "--port", "8000"]
