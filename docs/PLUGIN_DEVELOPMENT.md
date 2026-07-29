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
            # Mark API keys / OAuth tokens with sensitive=True so they get
            # stored encrypted and stripped from web/CLI responses. The web
            # UI's data accordion and the `python3.11 -m src.cli source`
            # CLI commands auto-generate forms from this schema, so accurate
            # `field_type`, `required`, `description`, and `sensitive` flags
            # directly drive the user-facing UI.
            ConfigField(
                name="api_key",
                field_type=str,
                required=True,
                sensitive=True,
                description="API key (kept in the encrypted credentials table)",
            ),
            ConfigField(
                name="user_id",
                field_type=str,
                required=True,
                description="User identifier (visible in the UI)",
            ),
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
    source="my_plugin",            # get_source_identifier(config): the user-defined
                                   # source key, or the plugin name for a file import

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

## Example: File-Import Plugin

A plugin that parses one file the user hands over — an export from another
service — is a **one-shot file import**, not a syncable source. That is the
shape almost every file-based plugin wants; a plugin that scans a directory on
a schedule is the rare exception, and `roms` is the only one in the repo. See
[One-shot file-import plugins](#one-shot-file-import-plugins) below for the
rules this example follows.

```python
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.ingestion.file_reading import read_csv_rows
from src.ingestion.plugin_base import ConfigField, ProgressCallback, SourcePlugin
from src.models.content import ContentItem, ContentType, ConsumptionStatus

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

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

    # Routes the plugin through the one-shot import service rather than the
    # syncable-source list, and tells the upload form which files to offer.
    @property
    def is_file_import(self) -> bool:
        return True

    @property
    def accepted_extensions(self) -> list[str]:
        return [".csv"]

    # No `path` field. The import service injects the file the user supplied;
    # everything declared here is settable by whoever can reach the port.
    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="shelf",
                field_type=str,
                required=False,
                default="",
                description="Only import rows on this shelf",
            ),
        ]

    def validate_config(
        self,
        config: dict[str, Any],
        storage: StorageManager | None = None,
        user_id: int = 1,
    ) -> list[str]:
        # These messages reach the caller verbatim, so they describe the option
        # that was just filled in — never a path or other runtime state.
        if config.get("shelf", "").startswith("#"):
            return ["'shelf' must be a shelf name, not a comment"]
        return []

    def fetch(self, config: dict[str, Any], progress_callback: ProgressCallback | None = None) -> Iterator[ContentItem]:
        source = self.get_source_identifier(config)
        shelf = config.get("shelf", "")
        # read_csv_rows, never open(): it turns a Latin-1 export, a directory,
        # or an unreadable file into a SourceError the import service renders
        # as a 4xx instead of letting it escape as a 500.
        rows = read_csv_rows(
            self.name,
            Path(config["path"]),
            required_columns=["title"],
        )

        for index, row in enumerate(rows):
            title = (row.get("title") or "").strip()
            if not title:
                continue
            if shelf and (row.get("shelf") or "").strip() != shelf:
                continue

            if progress_callback:
                progress_callback(index, len(rows), title)

            yield ContentItem(
                id=row.get("isbn") or title,
                title=title,
                content_type=ContentType.BOOK,
                status=self._map_status(row.get("status", "")),
                rating=self._parse_rating(row.get("rating")),
                author=row.get("author"),
                source=source,
                metadata={"genre": row.get("genre")},
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
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from src.ingestion.plugin_base import ConfigField, ProgressCallback, SourceError, SourcePlugin
from src.models.content import ContentItem, ContentType, ConsumptionStatus
from src.utils.request_errors import scrub_request_error

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

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

    def validate_config(
        self,
        config: dict[str, Any],
        storage: StorageManager | None = None,
        user_id: int = 1,
    ) -> list[str]:
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
            raise SourceError("movie_api", f"API request failed: {scrub_request_error(e)}") from e
        
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

Plugins are **auto-discovered** by `PluginRegistry` from `src/ingestion/sources/`. Each plugin lives in its own folder, which the registry treats as a Python subpackage:

```
src/ingestion/sources/<plugin>/
├── __init__.py        # re-exports everything from <plugin>.py for discovery
├── <plugin>.py        # SourcePlugin subclass implementation
├── README.md          # plugin-specific usage and configuration
└── test_<plugin>.py   # tests live next to the plugin
```

The minimal `__init__.py` is one line:

```python
"""<plugin> plugin package."""
from src.ingestion.sources.<plugin>.<plugin> import *  # noqa: F401, F403
```

To verify your plugin is discovered:

```bash
python3.11 -m src.cli source plugins   # syncable plugins
python3.11 -m src.cli import --source list   # file-import plugins
```

## Configuration Format

Sources use a **named instance** model: each source has a user-defined id plus a plugin name, so the same plugin can back several sources. Sources live in the `source_configs` table and are created from the Data tab or the `source` CLI; the YAML below is the legacy bootstrap form of the same shape, shown here because it is the most compact way to see the model:

```yaml
inputs:
  # User-defined name "my_shelves" using the goodreads_rss plugin
  my_shelves:
    plugin: goodreads_rss
    user_id: "12345678"
    enabled: true

  # A second instance of the sonarr plugin, pointed at another server
  living_room:
    plugin: sonarr
    url: "http://localhost:8989"
    enabled: true
```

**A plugin that parses a single user-supplied file must not declare a filesystem path field.** Source config is settable over the network by anyone who can reach the port (and `create_source` stores a schema-declared value without calling `validate_config`), so a caller-settable path is a way to make the app read arbitrary files. Such plugins are one-shot file imports instead — see below — and receive the path from the import service. A plugin that genuinely needs to scan a directory repeatedly (`roms` is the only one) must resolve every configured path and refuse anything outside an allowed root.

When your plugin's `fetch()` method is called, the config dict includes a `_source_id` key containing the user-defined name. The base class method `get_source_identifier(config)` returns this value, which is stored in `ContentItem.source`. This means items are tracked by user-defined name, not plugin name. A file import has no user-defined name — `import_file` refuses `_source_id` along with every other undeclared option — so `get_source_identifier` falls back to the plugin name there.

### One-shot file-import plugins

A plugin that imports a single user-supplied file, rather than syncing a source that persists, overrides `is_file_import` to return `True` and `accepted_extensions` to return the extensions it reads (e.g. `[".csv"]`). Such a plugin is deliberately not a source: `list_available_plugins` leaves it out of the plugin picker, `create_source` rejects it, and `resolve_inputs` skips any leftover source row or YAML block naming it (that leftover is still *listed*, flagged `is_file_import`, so the user can find and remove it — and `get_available_sync_sources`, the enumeration behind that listing, is what logs the warning telling them to). The plugin is listed by `GET /api/import/sources` / `import --source list` and run by `POST /api/import` / the CLI `import` command instead. `accepted_extensions` rides that listing and drives the upload form's file picker, so a new format does not need a frontend change; it is advisory, since the parser is what decides whether a file is usable.

The file arrives at invocation time (a web upload or the CLI `--file` flag) and `src/ingestion/import_service.py` runs it through the ingestion pipeline once. The import service injects the file path as the config `path` key, so `fetch()` still reads `config["path"]` — but `path` must not be in `get_config_schema()`, or a caller could set it. Declare any other per-import option (e.g. `content_type`) in `get_config_schema()` and both the upload form and the CLI will collect it. `import_file` itself refuses any key that schema does not declare, so both interfaces reject the same thing with the same message and a third caller cannot bypass the rule. The bundled `goodreads_csv`, `storygraph_csv`, `csv_import`, `json_import`, and `markdown_import` plugins work this way.

Read the file through `src/ingestion/file_reading.py` (`read_import_text` for text, `read_csv_rows` for CSV) rather than `open()` or `read_text()` directly. A user-supplied file can be a Latin-1 export, a directory, or unreadable; those helpers turn each of those into a `SourceError` with an actionable message, which the import service renders as a 4xx instead of letting a `UnicodeDecodeError` escape as a 500. This is load-bearing, not a convenience: the import service does *not* wrap `OSError`, precisely so a full disk or a permission fault during the storage write stays a 500 rather than telling the user to check their file. `read_csv_rows` also takes `required_columns` and `known_columns`, so header validation lives with the read instead of in each plugin.

A `SourceError` your plugin raises reaches the log and the CLI but never an HTTP response: `FileImportError` carries a separate, path-free `client_detail` for that, since plugin messages routinely embed the (server-side) file path. The one thing that *is* surfaced verbatim is what `validate_config` returns, because those messages describe the option schema the caller just filled in — so keep paths and runtime state out of them.

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
12. **Populate `seasons_watched_dates` when you have per-season timestamps** - For TV shows, the optional `seasons_watched_dates` metadata field maps `{season_number_str: iso_timestamp}` (e.g., `{"1": "2026-05-01T00:00:00+00:00"}`), keyed by the same season numbers as `seasons_watched`. This is how a finished season of an in-progress show gets correct recency on the recommendation engine's variety ladder (see [SCORING.md](SCORING.md#variety-after-completion)); a source that omits it still gets the season admitted to the ladder, but it sorts to the weakest/undated rung instead of by actual watch date.
13. **Scrub `requests` errors that may carry secrets** - If your plugin passes a secret in the URL or query params (an `?api_key=` / `?key=` style API), the default `str()` of a `requests` exception embeds the full request URL and leaks that credential into raised errors and logs. Pass the exception through `scrub_request_error()` from `src.utils.request_errors` before interpolating it — it returns only `HTTP <status>` (or the bare exception class name), never the URL. The TMDB and RAWG enrichment providers and the Steam source all do this.

## Thread Safety

When the user enables parallel sync (`config.sync.max_workers > 1`), each
enabled source runs on its own worker thread inside
`execute_multi_source_sync`. To stay safe under that model:

- **Plugin instances are independent.** The registry instantiates a
  separate plugin object per source entry, so per-instance state is fine.
- **Avoid mutable class-level state.** Class attributes are shared across
  instances and across threads — keep state on `self`, not on the class.
- **Per-source rate limiting is your responsibility.** Sleep / token-bucket
  inside `fetch()` for the source you're talking to. Cross-source
  parallelism is what the framework adds; intra-source pacing must remain.
- **Storage writes are already serialised.** `StorageManager` takes a
  lock around `save_content_item` and `save_credential`, so there is
  nothing for plugins to coordinate on the persistence side.
- **`progress_callback` is called from the worker thread.** The framework
  guarantees the callback itself is thread-safe; plugins just call it as
  documented.

Stateless plugins (the existing CSV / Goodreads CSV / Steam / Sonarr / Radarr
implementations) need no changes for parallel sync.

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

See `src/ingestion/sources/gog/gog.py` and `src/ingestion/sources/epic_games/epic_games.py`
for complete examples.

## Enrichment Providers

In addition to data source plugins, you can create custom **enrichment providers** that fetch metadata from external APIs. Built-in enrichment providers use the folder-based auto-discovery pattern: place your provider at `src/enrichment/providers/<name>/<name>.py` with a one-line `__init__.py` that re-exports it (same `from src.enrichment.providers.<name>.<name> import *` shim used by source plugins), plus a `README.md` and a `test_<name>.py` alongside it.

Private enrichment providers (under `plugins/private/enrichment/`) and private source plugins (under `private/plugins/`) currently remain **flat single-file modules** — the private discovery code globs `*.py` rather than walking subpackages, so a private provider folder would be silently skipped. If you need a private provider, drop a single `<name>.py` into the private directory.

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

- `src/enrichment/providers/tmdb/tmdb.py` — Movies and TV shows (API key required)
- `src/enrichment/providers/openlibrary/openlibrary.py` — Books (no API key)
- `src/enrichment/providers/rawg/rawg.py` — Video games (API key required)

## Existing Plugins to Reference

- `src/ingestion/sources/goodreads_csv/goodreads_csv.py` - File-based CSV parser
- `src/ingestion/sources/goodreads_rss/goodreads_rss.py` - Simple GET + pagination, no auth
- `src/ingestion/sources/steam/steam.py` - API-based with rate limiting
- `src/ingestion/sources/gog/gog.py` - OAuth-based API with token refresh
- `src/ingestion/sources/epic_games/epic_games.py` - OAuth-based API via Legendary
- `src/ingestion/sources/radarr/radarr.py` - API-based movie library
- `src/ingestion/sources/sonarr/sonarr.py` - API-based TV library
- `src/ingestion/sources/generic_csv/generic_csv.py` - Flexible CSV importer
- `src/ingestion/sources/generic_json/generic_json.py` - Flexible JSON importer
- `src/ingestion/sources/markdown/markdown.py` - Flexible Markdown importer
- `src/ingestion/sources/roms/roms.py` - ROM Library scanner with curated extension defaults and built-in No-Intro/Redump/TOSEC title cleaner (`src/ingestion/sources/roms/_rom_title.py`)
