# Recommendinator - Docker Image
# Multi-stage build with separate targets for base and AI variants.
#
# Targets:
#   default - Base app without AI dependencies (smaller image)
#   ai      - Full app with AI dependencies (ollama, chromadb)

# =============================================================================
# Frontend builder (Vue 3 + Vite)
# =============================================================================
FROM node:20-slim AS frontend-builder

RUN corepack enable && corepack prepare pnpm@9.7.0 --activate

WORKDIR /app

# Copy dependency files first for layer caching
COPY package.json pnpm-lock.yaml ./

# Install dependencies using locked versions
RUN pnpm install --frozen-lockfile

# Copy frontend source files
COPY index.html vite.config.ts tsconfig.json env.d.ts ./
COPY resources/ ./resources/

# Build the frontend (vue-tsc + vite build -> src/web/static/dist/)
RUN pnpm build

# =============================================================================
# Shared build base
# =============================================================================
FROM python:3.11-slim AS builder-base

COPY --from=ghcr.io/astral-sh/uv:0.10.7 /uv /bin/uv

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock .python-version ./

# =============================================================================
# Builder: default (no AI)
# =============================================================================
FROM builder-base AS builder-default

RUN uv sync --locked --no-install-project

COPY src/ ./src/
RUN uv sync --locked

# =============================================================================
# Builder: AI (includes ollama + chromadb)
# =============================================================================
FROM builder-base AS builder-ai

RUN uv sync --locked --extra ai --no-install-project

COPY src/ ./src/
RUN uv sync --locked --extra ai

# =============================================================================
# Runtime base (shared between both targets)
# =============================================================================
FROM python:3.11-slim AS runtime-base

WORKDIR /app

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash appuser

# Copy application code
COPY --chown=appuser:appuser src/ ./src/
COPY --chown=appuser:appuser templates/ ./templates/
COPY --chown=appuser:appuser config/example.yaml ./config/example.yaml

# Copy built frontend assets from frontend builder
COPY --from=frontend-builder --chown=appuser:appuser /app/src/web/static/dist/ ./src/web/static/dist/

# Create directories for data and inputs
RUN mkdir -p data inputs config logs && \
    chown -R appuser:appuser data inputs config logs

# =============================================================================
# Target: default (no AI dependencies)
# =============================================================================
FROM runtime-base AS default

COPY --from=builder-default --chown=appuser:appuser /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/status')" || exit 1
CMD ["python", "-m", "src.web", "--host", "0.0.0.0", "--port", "8000"]

# =============================================================================
# Target: ai (with AI dependencies)
# =============================================================================
FROM runtime-base AS ai

COPY --from=builder-ai --chown=appuser:appuser /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/status')" || exit 1
CMD ["python", "-m", "src.web", "--host", "0.0.0.0", "--port", "8000"]
