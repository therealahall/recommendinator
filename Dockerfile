# Recommendinator - Docker Image
#
# Bases are pinned tag@digest so two builds of one commit are one image. Use the
# index digest from `docker buildx imagetools inspect` — a per-platform digest
# breaks the arm64 build.

# =============================================================================
# Frontend builder (Vue 3 + Vite)
# =============================================================================
FROM node:25-slim@sha256:81db02c4b671288a03915da9534dbd54f96d0e7c24d80ccc54f5b36b2e684370 AS frontend-builder

RUN corepack enable && corepack prepare pnpm@9.7.0 --activate

WORKDIR /app

# Copy dependency files first for layer caching
COPY package.json pnpm-lock.yaml ./

# Install dependencies using locked versions
RUN pnpm install --frozen-lockfile

# Copy frontend source files
COPY index.html vite.config.ts tsconfig.json env.d.ts pyproject.toml ./
COPY resources/ ./resources/

# Build the frontend (vue-tsc + vite build -> src/web/static/dist/)
RUN pnpm build

# =============================================================================
# Builder
# =============================================================================
FROM python:3.11-slim@sha256:90744cff8f32887f075c47d747a173ff333e9e98801667af93c357fa9f5e28ff AS builder

COPY --from=ghcr.io/astral-sh/uv:0.10.7@sha256:edd1fd89f3e5b005814cc8f777610445d7b7e3ed05361f9ddfae67bebfe8456a /uv /bin/uv

WORKDIR /app

# Install build dependencies. build-essential is intentionally unpinned —
# pinning apt package versions across Debian base image patch updates is
# fragile and forces a Dockerfile change every minor base-image bump.
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock .python-version ./

RUN uv sync --locked --no-install-project

COPY src/ ./src/
RUN uv sync --locked

# =============================================================================
# Runtime
# =============================================================================
# Same digest as the builder: the venv copied in below was built against that
# interpreter and its shared libraries.
FROM python:3.11-slim@sha256:90744cff8f32887f075c47d747a173ff333e9e98801667af93c357fa9f5e28ff AS runtime

WORKDIR /app

# The IANA zone database. Completion dates are narrowed to the calendar day of
# the zone the process runs in, and TZ names a zone that glibc looks up in
# /usr/share/zoneinfo — Python's tzdata wheel does not serve that lookup. Slim
# base images may or may not carry it; installing it here makes the TZ override
# documented in docs/DOCKER.md work regardless of what the base image ships.
# tzdata is intentionally unpinned, for the same reason build-essential is.
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash appuser

# Copy application code
COPY --chown=appuser:appuser src/ ./src/
COPY --chown=appuser:appuser templates/ ./templates/
# Deliberately not under ./config: compose bind-mounts the host's ./config over
# that directory, and a seed the mount hides is one the entrypoint cannot read
# on the only run that needs it.
COPY --chown=appuser:appuser config/example.yaml ./example.yaml

# Copy entrypoint that bootstraps config.yaml on first run
COPY --chown=appuser:appuser docker/entrypoint.sh /app/docker/entrypoint.sh
RUN chmod +x /app/docker/entrypoint.sh

# Copy built frontend assets from frontend builder
COPY --from=frontend-builder --chown=appuser:appuser /app/src/web/static/dist/ ./src/web/static/dist/

# Create directories for data and inputs
RUN mkdir -p data inputs config && \
    chown -R appuser:appuser data inputs config

COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# The name is the point: every COPY above chowns to appuser:appuser, so pinning
# a uid here would mean pinning it in useradd and repeating the number at each
# of those. A host resolving the name is not something this image needs.
# hadolint ignore=DL3066
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD ["python", "-m", "src.web.healthcheck"]
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["python", "-m", "src.web", "--host", "0.0.0.0", "--port", "8000"]
