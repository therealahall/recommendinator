.PHONY: help install install-ai install-dev lock test lint format type-check clean run
.PHONY: install-frontend build-frontend check-frontend

help:
	@echo "Available commands:"
	@echo "  make install           - Install base dependencies (no AI)"
	@echo "  make install-ai        - Install base + AI dependencies (ollama, chromadb)"
	@echo "  make install-dev       - Install all dependencies (AI + dev tools)"
	@echo "  make install-frontend  - Install frontend dependencies (Node.js 18+ required)"
	@echo "  make lock              - Regenerate uv.lock from pyproject.toml"
	@echo "  make test              - Run Python tests"
	@echo "  make lint              - Run linters"
	@echo "  make format            - Format code with black"
	@echo "  make type-check        - Run type checker (mypy)"
	@echo "  make build-frontend    - Build Vue frontend (Vite + vue-tsc)"
	@echo "  make check-frontend    - Run frontend type-check and tests (requires install-frontend)"
	@echo "  make check             - Run all checks (Python + frontend; requires install-frontend)"
	@echo "  make clean             - Clean build artifacts"
	@echo "  make run               - Run the application"

install:
	uv sync --locked

install-ai:
	uv sync --locked --extra ai

install-dev:
	uv sync --locked --extra ai --extra dev

install-frontend:
	pnpm install --frozen-lockfile

lock:
	uv lock

test:
	python3.11 -m pytest

lint:
	python3.11 -m ruff check src/ tests/

format:
	python3.11 -m black src/ tests/

format-check:
	python3.11 -m black --check src/ tests/

type-check:
	python3.11 -m mypy src/

build-frontend:
	pnpm build

check-frontend:
	pnpm vue-tsc --noEmit
	pnpm vitest run

check: format-check lint type-check test check-frontend

clean:
	find . -type d -name __pycache__ -exec rm -r {} +
	find . -type f -name "*.pyc" -delete
	find . -type d -name "*.egg-info" -exec rm -r {} +
	find . -type d -name ".pytest_cache" -exec rm -r {} +
	find . -type d -name ".mypy_cache" -exec rm -r {} +
	rm -rf build/ dist/ .coverage htmlcov/ src/web/static/dist/

run:
	@echo "Application not yet implemented"
