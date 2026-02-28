# Personal Recommendations - Docker Image
# Multi-stage build for smaller final image

# Build stage
FROM python:3.11-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.10.7 /uv /bin/uv

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock .python-version ./

# Install dependencies (without the project itself) for caching
RUN uv sync --locked --extra ai --no-install-project

# Copy source and install the project
COPY src/ ./src/
RUN uv sync --locked --extra ai

# Runtime stage
FROM python:3.11-slim

WORKDIR /app

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash appuser

# Copy virtual environment from builder
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv

# Use the virtual environment's Python
ENV PATH="/app/.venv/bin:$PATH"

# Copy application code
COPY --chown=appuser:appuser src/ ./src/
COPY --chown=appuser:appuser templates/ ./templates/
COPY --chown=appuser:appuser config/example.yaml ./config/example.yaml

# Create directories for data and inputs
RUN mkdir -p data inputs config logs && \
    chown -R appuser:appuser data inputs config logs

# Switch to non-root user
USER appuser

# Expose web interface port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/status')" || exit 1

# Default command: run web interface
CMD ["python", "-m", "src.web", "--host", "0.0.0.0", "--port", "8000"]
