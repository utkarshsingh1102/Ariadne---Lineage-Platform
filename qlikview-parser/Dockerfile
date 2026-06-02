# QlikView parser cloud plugin.
# Multi-stage build: Stage 1 runs ANTLR codegen (Java). Stages 2-3 are
# Python-only (no Java in the runtime image).

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
        -Dlanguage=Python3 -visitor -listener \
        -o ../generated \
        QlikViewLexer.g4 QlikViewParser.g4 && \
    touch ../generated/__init__.py

# ---- Stage 2: build the wheel (with generated files included) ----
FROM python:3.11-slim AS builder
WORKDIR /build
COPY pyproject.toml ./
COPY src/ ./src/
# Inject ANTLR-generated lexer/parser/visitor INTO the source tree BEFORE
# building the wheel — setuptools' package discovery only includes files
# that exist at build time. Without this copy, the wheel ships with an
# empty generated/ subpackage and the service crashes with
# ModuleNotFoundError: No module named 'qlikview_parser.generated.QlikViewLexer'.
COPY --from=antlr-build /build/generated/ ./src/qlikview_parser/generated/
RUN pip install --no-cache-dir --upgrade pip build \
    && pip wheel --no-cache-dir --wheel-dir /wheels .

# ---- Stage 3: runtime ----
FROM python:3.11-slim
RUN groupadd -r app && useradd -r -g app app
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "qlikview_parser.main:app", "--host", "0.0.0.0", "--port", "8000"]
