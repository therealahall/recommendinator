# Python Version Setup

## Recommended Python Version

**Use Python 3.11** for this project to ensure full compatibility with all dependencies, especially ChromaDB.

The project includes a `.python-version` file that pins Python 3.11, which uv reads automatically.

## Quick Setup

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install all dependencies (AI + dev tools)
uv sync --locked --extra ai --extra dev

# Run tests
python3.11 -m pytest tests/ -v

# Run other commands
python3.11 -m src.cli ...
```

## Why Python 3.11?

- **ChromaDB compatibility**: ChromaDB works best with Python 3.11 and 3.12
- **All dependencies tested**: Production dependencies are tested on Python 3.11
- **Stable**: Python 3.11 is a stable, well-supported version


## Using Python 3.11

uv automatically selects Python 3.11 based on the `.python-version` file. For direct commands, use `python3.11` explicitly:

```bash
# Instead of: python3 script.py
# Use: python3.11 script.py

# Instead of: python -m pytest
# Use: python3.11 -m pytest
```

## Virtual Environment

uv manages the virtual environment automatically at `.venv/`. To install dependencies:

```bash
uv sync --locked --extra ai --extra dev
```
