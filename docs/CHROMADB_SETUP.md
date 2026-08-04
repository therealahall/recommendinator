# ChromaDB Setup

ChromaDB holds the vector embeddings. It is only used when AI features are on
(`features.ai_enabled`), and everything else works without it.

## Install

```bash
uv sync --locked --extra ai
```

Use Python 3.11 or 3.12. Newer versions may have no ChromaDB wheel, and the
source build fails on `hnswlib`. See [PYTHON_VERSION.md](PYTHON_VERSION.md).

Verify:

```bash
python3.11 -c "import chromadb; print(chromadb.__version__)"
python3.11 -m pytest tests/test_vector_db.py tests/test_storage_manager.py
```

## Storage location

`storage.vector_db_path` in `config/config.yaml`, default `data/chroma_db/`. It
holds `chroma.sqlite3` plus a directory of HNSW index files per collection.

## Usage

`StorageManager` drives ChromaDB for you. Reach for it directly only when
debugging:

```python
from pathlib import Path
from src.storage.manager import StorageManager

storage = StorageManager(
    sqlite_path=Path("data/recommendations.db"),
    vector_db_path=Path("data/chroma_db"),
    ai_enabled=True,
)
storage.save_content_item(item, embedding)
```

## `No module named 'chromadb'`

You installed without the `ai` extra, or on a Python with no wheel. Check the
version, then reinstall the package:

```bash
uv sync --locked --extra ai --reinstall-package chromadb
```
