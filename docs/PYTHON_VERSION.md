# Python Version Setup

**Use Python 3.11.** ChromaDB ships wheels for 3.11 and 3.12, and the project
targets 3.11. `.python-version` pins it and uv reads that file automatically.

## Setup

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # if you do not have uv
uv sync --locked --extra ai --extra dev
```

uv creates and manages the virtual environment at `.venv/`.

## Running commands

Outside uv, name the interpreter explicitly. Bare `python3` is whatever your
system ships, which is usually not 3.11:

```bash
python3.11 -m pytest tests/
python3.11 -m src.cli status
```
