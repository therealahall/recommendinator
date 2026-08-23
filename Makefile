.PHONY: help install install-dev lock test lint format format-check
.PHONY: type-check clean run install-frontend build-frontend check-frontend check
.PHONY: check-private

# CI overrides this with the interpreter uv provisioned into .venv; locally the
# project's pinned version is on PATH.
PYTHON ?= python3.11

# private/ is gitignored, and ruff and black both honour that while walking a
# directory — so ruff is told not to, and black is handed the files by name.
# Empty on a clone without the directory, which is what makes check-private a
# no-op there.
PRIVATE_SOURCES := $(shell find private -name '*.py' 2>/dev/null)

help:
	@echo "Available commands:"
	@echo "  make install           - Install runtime dependencies"
	@echo "  make install-dev       - Install runtime + dev dependencies"
	@echo "  make install-frontend  - Install frontend dependencies (Node.js 18+ required)"
	@echo "  make lock              - Regenerate uv.lock from pyproject.toml"
	@echo "  make test              - Run Python tests"
	@echo "  make lint              - Run linters"
	@echo "  make format            - Format code with black"
	@echo "  make format-check      - Check formatting without rewriting"
	@echo "  make type-check        - Run type checker (mypy)"
	@echo "  make build-frontend    - Build Vue frontend (Vite + vue-tsc)"
	@echo "  make check-frontend    - Run frontend type-check and tests"
	@echo "  make check-private     - Run the Python checks over private/, if present"
	@echo "  make check             - Run all checks (Python + frontend)"
	@echo "  make clean             - Clean build artifacts"
	@echo "  make run               - Run the application"

install:
	uv sync --locked

install-dev:
	uv sync --locked --extra dev

install-frontend: node_modules/.make-install

# A real target, not a phony one: node_modules is gitignored, so a fresh clone
# has none and the frontend checks would fail naming a missing vue-tsc rather
# than the cause. A stamp and not the directory, because pnpm creates the
# directory before it can fail — an interrupted install would otherwise look
# newer than package.json, and .DELETE_ON_ERROR cannot unlink a directory.
node_modules/.make-install: package.json pnpm-lock.yaml
	pnpm install --frozen-lockfile
	@touch $@

lock:
	uv lock

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check src/ tests/ conftest.py

format:
	$(PYTHON) -m black src/ tests/ conftest.py $(PRIVATE_SOURCES)

format-check:
	$(PYTHON) -m black --check src/ tests/ conftest.py

type-check:
	$(PYTHON) -m mypy src/ conftest.py

build-frontend: node_modules/.make-install
	pnpm build

check-frontend: node_modules/.make-install
	pnpm vue-tsc --noEmit
	pnpm vitest run

check-private:
ifneq ($(PRIVATE_SOURCES),)
	$(PYTHON) -m ruff check --no-respect-gitignore private/
	$(PYTHON) -m black --check $(PRIVATE_SOURCES)
	$(PYTHON) -m mypy private/
# Exit 5 is "no tests collected", which a plugin tree without tests yet is
# entitled to be; every other failure is real.
	$(PYTHON) -m pytest private/ || [ $$? -eq 5 ]
endif

check: format-check lint type-check test check-private check-frontend

clean:
	find . -type d -name __pycache__ -exec rm -r {} +
	find . -type f -name "*.pyc" -delete
	find . -type d -name "*.egg-info" -exec rm -r {} +
	find . -type d -name ".pytest_cache" -exec rm -r {} +
	find . -type d -name ".mypy_cache" -exec rm -r {} +
	rm -rf build/ dist/ .coverage htmlcov/ src/web/static/dist/

run:
	@echo "Application not yet implemented"
