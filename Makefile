.PHONY: help install install-dev test lint format type-check clean run

help:
	@echo "Available commands:"
	@echo "  make install       - Install production dependencies"
	@echo "  make install-dev   - Install development dependencies"
	@echo "  make test          - Run tests"
	@echo "  make lint          - Run linters"
	@echo "  make format        - Format code with black"
	@echo "  make type-check    - Run type checker (mypy)"
	@echo "  make check         - Run all checks (lint, format-check, type-check, test)"
	@echo "  make clean         - Clean build artifacts"
	@echo "  make run           - Run the application"

install:
	pip install -r requirements.txt

install-dev:
	pip install -r requirements-dev.txt

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
