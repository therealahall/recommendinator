# Python Version Setup

## Recommended Python Version

**Use Python 3.11** for this project to ensure full compatibility with all dependencies, especially ChromaDB.

## Quick Setup

```bash
# Verify Python 3.11 is available
python3.11 --version

# Install all dependencies (AI + dev tools, editable mode)
python3.11 -m pip install -e ".[ai,dev]"

# Run tests
python3.11 -m pytest tests/ -v

# Run other commands
python3.11 -m src.cli ...
```

## Why Python 3.11?

- **ChromaDB compatibility**: ChromaDB works best with Python 3.11 and 3.12
- **All dependencies tested**: Production dependencies are tested on Python 3.11
- **Stable**: Python 3.11 is a stable, well-supported version

## Note on Python 3.14

While Python 3.14.2 is available on your system, ChromaDB doesn't have full support for it yet. You can use Python 3.14 for development, but ChromaDB features won't work until ChromaDB adds Python 3.14 support.

## Using Python 3.11

Since `python3` is aliased to Python 3.14.2, use `python3.11` explicitly:

```bash
# Instead of: python3 script.py
# Use: python3.11 script.py

# Instead of: python -m pytest
# Use: python3.11 -m pytest
```

## Virtual Environment (Optional)

If you want to isolate dependencies:

```bash
python3.11 -m venv venv
source venv/bin/activate  # On Linux/Mac
pip install -e ".[ai,dev]"
```
