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

### Regression Testing

**When fixing bugs, always write regression tests to prevent the bug from reoccurring.**

#### Guidelines for Regression Tests

1. **Write tests that reproduce the bug**
   - The test should fail before the fix and pass after the fix
   - Name the test clearly to indicate it's a regression test (e.g., `test_enum_handling_regression`)

2. **Test the specific failure scenario**
   - Focus on the exact conditions that caused the bug
   - Test edge cases and boundary conditions related to the bug

3. **Document the bug in the test**
   - Include a docstring explaining what bug the test covers
   - Reference the error message or issue that was fixed

4. **Place regression tests appropriately**
   - If the bug affects a specific module, add tests to that module's test file
   - For complex bugs affecting multiple modules, create a dedicated regression test file (e.g., `test_<bug_name>_regression.py`)

#### Example

```python
"""Regression tests for enum handling with Pydantic use_enum_values=True.

This test suite covers the fix for the bug where code was trying to access
.value on enum fields that were already converted to strings by Pydantic's
use_enum_values=True configuration.

Bug: 'str' object has no attribute 'value'
Fixed in: src/llm/prompts.py, src/storage/manager.py
"""

def test_build_content_description_with_string_enum():
    """Test that build_content_description works with ContentItem having string enums.
    
    Regression test for: 'str' object has no attribute 'value' when accessing
    item.content_type.value in build_content_description.
    """
    # Test implementation...
```

#### Benefits

- Prevents bugs from being reintroduced
- Documents the bug and fix for future developers
- Provides confidence when refactoring related code
- Helps identify similar bugs in other parts of the codebase

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

### Commit Strategy

**Break changes into logical, atomic commits** rather than large mega-commits. This makes:
- Code review easier
- History more readable
- Rollbacks more precise
- Debugging simpler

#### Guidelines for Breaking Up Commits

1. **One logical change per commit**
   - Each commit should represent a complete, working change
   - Related changes can be grouped, but keep them focused

2. **Separate concerns**
   - Schema changes → separate commit
   - Implementation → separate commit
   - Tests → separate commit
   - Documentation → separate commit

3. **Order matters**
   - Schema before implementation
   - Implementation before tests
   - Tests should pass after each commit

4. **Examples of good commit breakdowns:**

   **Schema v2 Implementation:**
   ```
   feat(storage): add schema v2 with type-specific tables
   feat(storage): add migration from v1 to v2
   feat(storage): update SQLiteDB for schema v2
   test(storage): update tests for schema v2
   ```

   **New Feature:**
   ```
   feat(models): add new content type model
   feat(ingestion): add parser for new source
   test(ingestion): add tests for new parser
   docs: update architecture for new content type
   ```

5. **When to combine:**
   - Trivial changes (typos, formatting) can be combined
   - Very small related changes (e.g., adding a field and its test)

6. **When to separate:**
   - Different modules (storage vs ingestion)
   - Different concerns (implementation vs tests)
   - Breaking changes vs non-breaking changes

### Examples
```
feat(ingestion): add Goodreads CSV parser

Add support for parsing Goodreads library exports with proper
handling of rating, shelf status, and review fields.

fix(llm): handle empty response from Ollama API

test(storage): add tests for vector database operations

feat(storage): add schema v2 with type-specific tables

Implement hybrid schema approach with base content_items table
and type-specific detail tables (book_details, movie_details, etc.)
for better type safety and query efficiency.
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
