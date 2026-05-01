# Plugin Development Guide

This guide explains how to create new data source plugins for Recommendinator.

## Overview

Plugins are Python classes that fetch content items from external sources (APIs, files, databases). The system uses a plugin architecture to support multiple data sources without modifying core code.

## Plugin Interface

All plugins inherit from `SourcePlugin` in `src/ingestion/plugin_base.py`:

```python
from typing import Any, Iterator

from src.ingestion.plugin_base import ConfigField, ProgressCallback, SourcePlugin
from src.models.content import ContentItem, ContentType, ConsumptionStatus

class MyPlugin(SourcePlugin):
    @property
    def name(self) -> str:
        return "my_plugin"
    
    @property
    def display_name(self) -> str:
        return "My Data Source"
    
    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.BOOK]  # Types this plugin provides
    
    @property
    def requires_api_key(self) -> bool:
        return True  # Set based on your source
    
    @property
    def requires_network(self) -> bool:
        return True  # Set based on your source
    
    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(name="api_key", field_type=str, required=True),
            ConfigField(name="user_id", field_type=str, required=True),
        ]

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        """Return list of validation error messages."""
        errors = []
        if not config.get("api_key"):
            errors.append("API key is required")
        return errors
    
    def fetch(
        self,
        config: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> Iterator[ContentItem]:
        """Yield ContentItem objects from the source.

        Call progress_callback(items_processed, total_items, current_item)
        during long-running operations so callers can report progress.
        Use total_items=None when the total is unknown.
        """
        # Your implementation here
        yield ContentItem(
            id="external-id-123",
            title="Example Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            author="Author Name",
            metadata={"pages": 300, "genre": "Fiction"},
        )
```

## Key Concepts

### Content Items

Each item you yield must be a `ContentItem`:

```python
ContentItem(
    id="unique-external-id",      # Required: unique ID from source
    title="Item Title",            # Required
    content_type=ContentType.BOOK, # Required: BOOK, MOVIE, TV_SHOW, VIDEO_GAME
    status=ConsumptionStatus.COMPLETED,  # Required: COMPLETED, CURRENTLY_CONSUMING, UNREAD
    rating=4,                      # Optional: 1-5 scale
    review="My review text",       # Optional
    author="Author/Director",      # Optional
    ignored=False,                 # Optional: exclude from recommendations
    metadata={},                   # Optional: source-specific data
    source="my_plugin",            # Set automatically to the user-defined config key name
)
```

### Status Mapping

Map source statuses to our standard values:

```python
STATUS_MAP = {
    "read": ConsumptionStatus.COMPLETED,
    "reading": ConsumptionStatus.CURRENTLY_CONSUMING,
    "to-read": ConsumptionStatus.UNREAD,
}
```

### Rating Normalization

If your source uses a different scale, normalize to 1-5:

```python
def normalize_rating(source_rating: int, max_rating: int = 10) -> int | None:
    if source_rating <= 0:
        return None
    return max(1, min(5, round(source_rating * 5 / max_rating)))
```

## Example: File-Based Plugin

```python
import csv
from pathlib import Path
from typing import Any, Iterator

from src.ingestion.plugin_base import ConfigField, ProgressCallback, SourcePlugin
from src.models.content import ContentItem, ContentType, ConsumptionStatus

class CsvBookPlugin(SourcePlugin):
    @property
    def name(self) -> str:
        return "csv_books"
    
    @property
    def display_name(self) -> str:
        return "CSV Book Import"
    
    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.BOOK]
    
    @property
    def requires_api_key(self) -> bool:
        return False
    
    @property
    def requires_network(self) -> bool:
        return False
    
    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(name="path", field_type=str, required=True),
        ]

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors = []
        file_path = config.get("path", "")
        if not file_path:
            errors.append("File path is required")
        elif not Path(file_path).exists():
            errors.append(f"File not found: {file_path}")
        return errors

    def fetch(self, config: dict[str, Any], progress_callback: ProgressCallback | None = None) -> Iterator[ContentItem]:
        csv_path = Path(config["path"])
        
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if not row.get("title"):
                    continue
                
                yield ContentItem(
                    id=row.get("isbn") or row["title"],
                    title=row["title"],
                    content_type=ContentType.BOOK,
                    status=self._map_status(row.get("status", "")),
                    rating=self._parse_rating(row.get("rating")),
                    author=row.get("author"),
                    metadata={
                        "pages": int(row["pages"]) if row.get("pages") else None,
                        "genre": row.get("genre"),
                    },
                )
    
    def _map_status(self, status: str) -> ConsumptionStatus:
        status_map = {
            "read": ConsumptionStatus.COMPLETED,
            "reading": ConsumptionStatus.CURRENTLY_CONSUMING,
            "to-read": ConsumptionStatus.UNREAD,
        }
        return status_map.get(status.lower(), ConsumptionStatus.UNREAD)
    
    def _parse_rating(self, rating: str | None) -> int | None:
        if not rating:
            return None
        try:
            return max(1, min(5, int(rating)))
        except ValueError:
            return None
```

## Example: API-Based Plugin

```python
import requests
from typing import Any, Iterator

from src.ingestion.plugin_base import ConfigField, ProgressCallback, SourceError, SourcePlugin
from src.models.content import ContentItem, ContentType, ConsumptionStatus

class MovieApiPlugin(SourcePlugin):
    API_BASE = "https://api.example.com/v1"
    
    @property
    def name(self) -> str:
        return "movie_api"
    
    @property
    def display_name(self) -> str:
        return "Movie API"
    
    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.MOVIE]
    
    @property
    def requires_api_key(self) -> bool:
        return True
    
    @property
    def requires_network(self) -> bool:
        return True
    
    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(name="api_key", field_type=str, required=True),
            ConfigField(name="username", field_type=str, required=True),
        ]

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors = []
        if not config.get("api_key"):
            errors.append("API key is required")
        if not config.get("username"):
            errors.append("Username is required")
        return errors

    def fetch(self, config: dict[str, Any], progress_callback: ProgressCallback | None = None) -> Iterator[ContentItem]:
        api_key = config["api_key"]
        username = config["username"]
        
        try:
            response = requests.get(
                f"{self.API_BASE}/users/{username}/movies",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            raise SourceError("movie_api", f"API request failed: {e}") from e
        
        for movie in data.get("movies", []):
            yield ContentItem(
                id=str(movie["id"]),
                title=movie["title"],
                content_type=ContentType.MOVIE,
                status=self._map_status(movie.get("watch_status")),
                rating=self._normalize_rating(movie.get("user_rating")),
                metadata={
                    "runtime": movie.get("runtime"),
                    "director": movie.get("director"),
                    "genres": movie.get("genres", []),
                    "year": movie.get("release_year"),
                },
            )
    
    def _map_status(self, status: str | None) -> ConsumptionStatus:
        if status == "watched":
            return ConsumptionStatus.COMPLETED
        elif status == "watching":
            return ConsumptionStatus.CURRENTLY_CONSUMING
        return ConsumptionStatus.UNREAD
    
    def _normalize_rating(self, rating: float | None) -> int | None:
        if rating is None or rating <= 0:
            return None
        # Convert 10-point scale to 5-point
        return max(1, min(5, round(rating / 2)))
```

## Plugin Registration

Plugins are **auto-discovered** by `PluginRegistry` from `src/ingestion/sources/`. No manual registration is needed — just create your plugin file and it will be found automatically.

To verify your plugin is discovered:

```bash
python3.11 -m src.cli update --help  # Should show your source in the list
```

## Configuration Format

Each input source in `config.yaml` uses a **named instance** model. The config key is a user-defined name, and the `plugin:` field specifies which plugin to use. This allows multiple instances of the same plugin:

```yaml
inputs:
  # User-defined name "my_books" using the csv_import plugin
  my_books:
    plugin: csv_import
    path: "inputs/books.csv"
    content_type: "book"
    enabled: true

  # A second instance of csv_import with a different name
  classic_movies:
    plugin: csv_import
    path: "inputs/classic_movies.csv"
    content_type: "movie"
    enabled: true
```

File-based plugins use a standardized `path` field (not `csv_path`, `json_path`, or `markdown_path`).

When your plugin's `fetch()` method is called, the config dict includes a `_source_id` key containing the user-defined name. The base class method `get_source_identifier(config)` returns this value, which is stored in `ContentItem.source`. This means items are tracked by user-defined name, not plugin name.

## Testing Your Plugin

Create tests in `tests/test_my_plugin.py`:

```python
import pytest
from unittest.mock import Mock, patch

from src.ingestion.sources.my_plugin import MyPlugin
from src.models.content import ContentType, ConsumptionStatus

class TestMyPlugin:
    @pytest.fixture
    def plugin(self):
        return MyPlugin()
    
    def test_name(self, plugin):
        assert plugin.name == "my_plugin"
    
    def test_validate_config_valid(self, plugin):
        config = {"api_key": "key123", "user_id": "user1"}
        errors = plugin.validate_config(config)
        assert errors == []
    
    def test_validate_config_missing_key(self, plugin):
        config = {"user_id": "user1"}
        errors = plugin.validate_config(config)
        assert "API key is required" in errors
    
    @patch("requests.get")
    def test_fetch_returns_items(self, mock_get, plugin):
        mock_get.return_value.json.return_value = {
            "items": [{"id": "1", "title": "Test", "status": "completed"}]
        }
        mock_get.return_value.raise_for_status = Mock()
        
        config = {"api_key": "key", "user_id": "user"}
        items = list(plugin.fetch(config))
        
        assert len(items) == 1
        assert items[0].title == "Test"
```

## Best Practices

1. **Always mock network calls in tests** - Never make real API calls
2. **Handle errors gracefully** - Raise `SourceError` for recoverable errors
3. **Validate config thoroughly** - Check all required fields
4. **Normalize data** - Convert ratings to 1-5, statuses to standard values
5. **Include metadata** - Store source-specific data for reference
6. **Use unique IDs** - Ensure `id` is unique within content type
7. **Skip invalid items** - Don't yield items with missing required fields
8. **Log useful info** - Help users debug issues
9. **Support progress reporting** - Accept `progress_callback` in `fetch()` and
   call it during long operations: `progress_callback(items_processed,
   total_items, current_item)`. Use `total_items=None` when unknown.
10. **Respect the `ignored` field** - If your source provides a way to mark items as excluded, set `ignored=True` on the `ContentItem`. Use `parse_boolean_field()` from `generic_csv` for flexible boolean parsing.
11. **Use list format for `seasons_watched`** - For TV shows, store `seasons_watched` as a list of specific season numbers (e.g., `[1, 2, 5, 6]`) in metadata. Use `parse_seasons_watched()` from `generic_csv` if converting from string input. A single integer is treated as a count for backward compatibility (e.g., `5` → `[1, 2, 3, 4, 5]`).

## Handling Token Rotation (OAuth Plugins)

If your plugin uses OAuth refresh tokens, the token may be rotated by the
server during a sync operation. To persist the new token so the user doesn't
need to re-authenticate, use the `_on_credential_rotated` callback that
`execute_sync` injects into the plugin config:

```python
from src.ingestion.plugin_base import CredentialUpdateCallback

# Inside your internal fetch function:
on_credential_rotated: CredentialUpdateCallback | None = (
    config.get("_on_credential_rotated")
    if callable(config.get("_on_credential_rotated"))
    else None
)

# After obtaining new tokens:
new_refresh_token = token_response.get("refresh_token")
if new_refresh_token and new_refresh_token != original_refresh_token:
    if on_credential_rotated:
        on_credential_rotated("refresh_token", new_refresh_token)
```

See `src/ingestion/sources/gog.py` and `src/ingestion/sources/epic_games.py`
for complete examples.

## Enrichment Providers

In addition to data source plugins, you can create custom **enrichment providers** that fetch metadata from external APIs. Enrichment providers use the same auto-discovery pattern as source plugins — place your provider in `src/enrichment/providers/` (or `plugins/private/enrichment/` for private providers) and it will be discovered automatically.

All enrichment providers inherit from `EnrichmentProvider` in `src/enrichment/provider_base.py`:

```python
from typing import Any

from src.enrichment.provider_base import (
    ConfigField,
    EnrichmentProvider,
    EnrichmentResult,
    ProviderError,
)
from src.models.content import ContentItem, ContentType


class MyEnrichmentProvider(EnrichmentProvider):
    @property
    def name(self) -> str:
        return "my_api"

    @property
    def display_name(self) -> str:
        return "My Metadata API"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.MOVIE]  # Types this provider enriches

    @property
    def requires_api_key(self) -> bool:
        return True

    @property
    def rate_limit_requests_per_second(self) -> float:
        return 5.0  # Default is 1.0 (conservative)

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="api_key",
                field_type=str,
                required=True,
                description="API key for My API",
                sensitive=True,
            ),
        ]

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors = []
        if not config.get("api_key"):
            errors.append("'api_key' is required")
        return errors

    def enrich(
        self, item: ContentItem, config: dict[str, Any]
    ) -> EnrichmentResult | None:
        # Search for the item in your API, fetch metadata
        # Return None if the item can't be found
        return EnrichmentResult(
            external_id="myapi:12345",
            genres=["Action", "Adventure"],
            tags=["open-world", "rpg"],
            description="A description from the API",
            extra_metadata={"runtime": 120, "release_year": 2024},
            match_quality="high",  # "high", "medium", or "not_found"
            provider=self.name,
        )
```

### Key Differences from Source Plugins

- **Return `EnrichmentResult`** instead of yielding `ContentItem` objects
- **Gap-filling only** — the enrichment manager only fills in missing metadata, never overwrites existing data
- **Rate limiting is built-in** — set `rate_limit_requests_per_second` and the manager handles throttling
- **Configuration** lives under `enrichment.providers.<name>` in config (not under `inputs`)

### Configuration

Add your provider to `config.yaml`:

```yaml
enrichment:
  enabled: true
  providers:
    my_api:
      api_key: "your-key"
      enabled: true
```

### Existing Enrichment Providers to Reference

- `src/enrichment/providers/tmdb.py` — Movies and TV shows (API key required)
- `src/enrichment/providers/openlibrary.py` — Books (no API key)
- `src/enrichment/providers/rawg.py` — Video games (API key required)

## Existing Plugins to Reference

- `src/ingestion/sources/goodreads.py` - File-based CSV parser
- `src/ingestion/sources/steam.py` - API-based with rate limiting
- `src/ingestion/sources/gog.py` - OAuth-based API with token refresh
- `src/ingestion/sources/epic_games.py` - OAuth-based API via Legendary
- `src/ingestion/sources/radarr.py` - API-based movie library
- `src/ingestion/sources/sonarr.py` - API-based TV library
- `src/ingestion/sources/generic_csv.py` - Flexible CSV importer
- `src/ingestion/sources/generic_json.py` - Flexible JSON importer
- `src/ingestion/sources/markdown.py` - Flexible Markdown importer
- `src/ingestion/sources/roms.py` - ROM Library scanner with curated extension defaults and built-in No-Intro/Redump/TOSEC title cleaner (`_rom_title.py`)
