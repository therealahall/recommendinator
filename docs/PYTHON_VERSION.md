# Python Version Setup

**Any of Python 3.11, 3.12, 3.13 or 3.14.** CI runs the whole gate on each of
them, and `requires-python` claims exactly that range. `.python-version` names
3.11, the floor and the minor the published image ships, so that is what uv
reaches for unless you ask for another.

## Setup

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # if you do not have uv
uv sync --locked --extra dev
```

uv creates and manages the virtual environment at `.venv/`.

## Running commands

Let uv name the interpreter. Bare `python3` is whatever your system ships, and
may be older than the floor:

```bash
uv run python -m src.cli status
uv run --python 3.14 --extra dev python -m pytest tests/   # another supported minor
```
