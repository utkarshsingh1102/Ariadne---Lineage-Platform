# Multi-stage build for the Tableau parser cloud plugin.

# ---- Stage 1: build wheels ----
FROM python:3.11-slim AS builder
WORKDIR /build
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir --upgrade pip build \
    && pip wheel --no-cache-dir --wheel-dir /wheels .

# ---- Stage 2: runtime ----
FROM python:3.11-slim
RUN groupadd -r app && useradd -r -g app app
WORKDIR /app

# libxml2 / libxslt runtime needed by lxml binary wheels (usually bundled, but safe to install)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "tableau_parser.main:app", "--host", "0.0.0.0", "--port", "8000"]
