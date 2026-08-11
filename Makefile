.PHONY: help install install-ai install-dev lock test lint format format-check
.PHONY: type-check clean run install-frontend build-frontend check-frontend check-agents check

# CI overrides this with the interpreter uv provisioned into .venv; locally the
# project's pinned version is on PATH.
PYTHON ?= python3.11

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
	@echo "  make format-check      - Check formatting without rewriting"
	@echo "  make type-check        - Run type checker (mypy)"
	@echo "  make build-frontend    - Build Vue frontend (Vite + vue-tsc)"
	@echo "  make check-frontend    - Run frontend type-check and tests"
	@echo "  make check             - Run all checks (Python + frontend + agents)"
	@echo "  make check-agents      - Verify every mandated review agent can be launched"
	@echo "  make clean             - Clean build artifacts"
	@echo "  make run               - Run the application"

install:
	uv sync --locked

install-ai:
	uv sync --locked --extra ai

install-dev:
	uv sync --locked --extra ai --extra dev

install-frontend: node_modules

# A real target, not a phony one. node_modules is gitignored, so a fresh clone
# or git worktree has none and the frontend checks would otherwise fail naming a
# missing vue-tsc binary rather than the cause. A warm tree costs two stats.
node_modules: package.json pnpm-lock.yaml
	pnpm install --frozen-lockfile
	@touch node_modules

lock:
	uv lock

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check src/ tests/ scripts/ conftest.py

format:
	$(PYTHON) -m black src/ tests/ scripts/ conftest.py

format-check:
	$(PYTHON) -m black --check src/ tests/ scripts/ conftest.py

type-check:
	$(PYTHON) -m mypy src/ scripts/ conftest.py

build-frontend: node_modules
	pnpm build

check-frontend: node_modules
	pnpm vue-tsc --noEmit
	pnpm vitest run

# check-agents runs first deliberately: it finishes in under a second, and a
# review gate that cannot resolve its agents should not cost a full test run to
# discover.
check: check-agents format-check lint type-check test check-frontend

# Verifies every review agent CLAUDE.md mandates is committed, loadable, and
# declares the name it is launched by. An agent that never loads reviews nothing
# and says nothing, so the gate would otherwise report success without it.
check-agents:
	$(PYTHON) scripts/check_review_agents.py

clean:
	find . -type d -name __pycache__ -exec rm -r {} +
	find . -type f -name "*.pyc" -delete
	find . -type d -name "*.egg-info" -exec rm -r {} +
	find . -type d -name ".pytest_cache" -exec rm -r {} +
	find . -type d -name ".mypy_cache" -exec rm -r {} +
	rm -rf build/ dist/ .coverage htmlcov/ src/web/static/dist/

run:
	@echo "Application not yet implemented"
