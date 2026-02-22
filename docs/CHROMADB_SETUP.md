# ChromaDB Setup Guide

This guide explains how to set up ChromaDB locally for the Personal Recommendations project.

## Installation

ChromaDB is included in the `ai` optional dependency group. To install it:

```bash
python3.11 -m pip install ".[ai]"
```

Or install ChromaDB directly:

```bash
python3.11 -m pip install chromadb
```

## Python Version Compatibility

**Note:** ChromaDB may not have full support for Python 3.14 yet. If you encounter installation issues:

1. **Check your Python version:**
   ```bash
   python3.11 --version
   ```

2. **If using Python 3.14**, you may need to:
   - Use Python 3.11 or 3.12 instead (recommended)
   - Or wait for ChromaDB to release Python 3.14 wheels

3. **Recommended Python versions:**
   - Python 3.11 ✅ (fully supported)
   - Python 3.12 ✅ (fully supported)
   - Python 3.14 ⚠️ (may have compatibility issues)

**Note:** ChromaDB is only required when AI features are enabled (`features.ai_enabled: true`). The system works fully without it.

## Verification

After installation, verify ChromaDB works:

```bash
python3.11 -c "import chromadb; print(f'ChromaDB version: {chromadb.__version__}')"
```

## Testing

Run the vector database tests:

```bash
python3.11 -m pytest tests/test_vector_db.py -v
python3.11 -m pytest tests/test_storage_manager.py -v
```

## Troubleshooting

### Import Error: No module named 'chromadb'

1. Verify installation:
   ```bash
   pip list | grep chromadb
   ```

2. Check Python version compatibility (see above)

3. Try reinstalling:
   ```bash
   pip uninstall chromadb
   pip install chromadb
   ```

### Installation fails on Python 3.14

If installation fails with Python 3.14, you have two options:

1. **Use Python 3.11 or 3.12** (recommended):
   ```bash
   # Create a virtual environment with Python 3.11
   python3.11 -m venv venv
   source venv/bin/activate  # On Linux/Mac
   pip install ".[ai]"
   ```

2. **Wait for ChromaDB Python 3.14 support** or use a workaround

## ChromaDB Storage Location

By default, ChromaDB stores data in:
- Directory specified in `config/config.yaml` → `storage.vector_db_path`
- Default: `data/chroma_db/`

The database files include:
- `chroma.sqlite3` - Metadata database
- Collection directories with HNSW index files

## Usage

The ChromaDB integration is handled automatically by `StorageManager`. You don't need to interact with ChromaDB directly unless you're debugging.

Example usage:

```python
from pathlib import Path
from src.storage.manager import StorageManager

# Initialize storage manager
storage = StorageManager(
    sqlite_path=Path("data/recommendations.db"),
    vector_db_path=Path("data/chroma_db")
)

# Save content with embedding
item = ContentItem(...)
embedding = [0.1, 0.2, 0.3, ...]  # Your embedding vector
storage.save_content_item(item, embedding)
```
