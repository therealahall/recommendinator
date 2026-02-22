.PHONY: help install install-ai install-dev test lint format type-check clean run

help:
	@echo "Available commands:"
	@echo "  make install       - Install base dependencies (no AI)"
	@echo "  make install-ai    - Install base + AI dependencies (ollama, chromadb)"
	@echo "  make install-dev   - Install all dependencies in editable mode (AI + dev tools)"
	@echo "  make test          - Run tests"
	@echo "  make lint          - Run linters"
	@echo "  make format        - Format code with black"
	@echo "  make type-check    - Run type checker (mypy)"
	@echo "  make check         - Run all checks (lint, format-check, type-check, test)"
	@echo "  make clean         - Clean build artifacts"
	@echo "  make run           - Run the application"

install:
	python3.11 -m pip install .

install-ai:
	python3.11 -m pip install ".[ai]"

install-dev:
	python3.11 -m pip install -e ".[ai,dev]"

test:
	pytest

lint:
	ruff check src/ tests/

format:
	black src/ tests/

format-check:
	black --check src/ tests/

type-check:
	mypy src/

check: format-check lint type-check test

clean:
	find . -type d -name __pycache__ -exec rm -r {} +
	find . -type f -name "*.pyc" -delete
	find . -type d -name "*.egg-info" -exec rm -r {} +
	find . -type d -name ".pytest_cache" -exec rm -r {} +
	find . -type d -name ".mypy_cache" -exec rm -r {} +
	rm -rf build/ dist/ .coverage htmlcov/

run:
	@echo "Application not yet implemented"
