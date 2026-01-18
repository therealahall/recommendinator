# Contributing Guidelines

This document outlines the development standards and practices for the Personal Recommendations project.

## Code Standards

### Python Version
- Minimum Python version: 3.11+
- Current development version: 3.14.2

### Code Formatting

We use **Black** for code formatting with default settings:
```bash
black src/ tests/
```

### Type Checking

We use **MyPy** for static type checking:
```bash
mypy src/
```

### Linting

We use **Ruff** (or Flake8) for linting:
```bash
ruff check src/ tests/
```

### Pre-commit Checks

Before committing, ensure:
1. All tests pass: `pytest`
2. Code is formatted: `black --check src/ tests/`
3. Type checking passes: `mypy src/`
4. Linting passes: `ruff check src/ tests/`

## Testing

### Test Requirements
- **All code must have tests**
- Aim for high test coverage (target: 80%+)
- Use pytest as the testing framework
- Place tests in `tests/` directory mirroring `src/` structure

### Running Tests
```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src --cov-report=html

# Run specific test file
pytest tests/test_ingestion.py
```

### Test Structure
- Test files should be named `test_*.py`
- Test functions should be named `test_*`
- Use fixtures for common setup
- Mock external dependencies (Ollama API, file I/O, etc.)

## Commit Messages

We follow **Conventional Commits** specification:

```
<type>(<scope>): <subject>

<body>

<footer>
```

### Types
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting, etc.)
- `refactor`: Code refactoring
- `test`: Adding or updating tests
- `chore`: Maintenance tasks

### Examples
```
feat(ingestion): add Goodreads CSV parser

Add support for parsing Goodreads library exports with proper
handling of rating, shelf status, and review fields.

fix(llm): handle empty response from Ollama API

test(storage): add tests for vector database operations
```

## Project Structure

### Module Organization
- Keep modules focused and single-purpose
- Use clear, descriptive names
- Group related functionality together

### Import Organization
1. Standard library imports
2. Third-party imports
3. Local application imports

### Documentation
- Use docstrings for all public functions, classes, and modules
- Follow Google-style docstrings
- Include type hints in function signatures

## Architecture Principles

1. **Separation of Concerns**: Keep ingestion, LLM interaction, and storage separate
2. **Testability**: Design code to be easily testable
3. **Extensibility**: Make it easy to add new data sources and content types
4. **Configuration**: Use configuration files for settings, not hardcoded values
5. **Error Handling**: Handle errors gracefully with clear messages

## Adding New Features

1. Create a feature branch: `git checkout -b feat/feature-name`
2. Write tests first (TDD approach recommended)
3. Implement the feature
4. Ensure all tests pass
5. Update documentation if needed
6. Submit changes following commit message conventions

## Code Review Checklist

- [ ] Code follows style guidelines
- [ ] All tests pass
- [ ] New code has tests
- [ ] Documentation is updated
- [ ] No hardcoded values
- [ ] Error handling is appropriate
- [ ] Type hints are used

## Questions?

If you have questions about these guidelines, please open an issue or discuss in the project.
