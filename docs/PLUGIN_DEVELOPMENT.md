# Plugin Development Guide

How to add a data source plugin.

## The interface

Plugins subclass `SourcePlugin` in `src/ingestion/plugin_base.py`.

```python
from typing import TYPE_CHECKING, Any, Iterator

from src.ingestion.plugin_base import ConfigField, ProgressCallback, SourcePlugin
from src.models.content import ContentItem, ContentType, ConsumptionStatus

if TYPE_CHECKING:
    from src.storage.manager import StorageManager


class MyPlugin(SourcePlugin):
    @property
    def name(self) -> str:
        return "my_plugin"

    @property
    def display_name(self) -> str:
        return "My Data Source"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.BOOK]

    @property
    def requires_api_key(self) -> bool:
        return True

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="api_key",
                field_type=str,
                required=True,
                sensitive=True,
                description="API key (encrypted, never shown back)",
            ),
            ConfigField(
                name="user_id",
                field_type=str,
                required=True,
                description="User identifier (visible in the UI)",
            ),
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
        return errors

    def fetch(
        self,
        config: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> Iterator[ContentItem]:
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

`sensitive=True` stores the field encrypted and strips it from web and CLI
responses. The Add-source modal and the `source` CLI generate their forms from
this schema, so `field_type`, `required`, `description` and `sensitive` are the
user-facing UI.

**Never quote a credential into a `validate_config` message.** Those messages
are returned to the caller.

`requires_network` defaults to `requires_api_key`. Override it for a file-based
source that needs neither.

Call `progress_callback(items_processed, total_items, current_item)` during long
operations, with `total_items=None` when the total is unknown.

## Fields that reach outside the app

Non-sensitive fields are writable over `PUT /api/sync/sources/<id>/config`, so
two kinds need declaring.

**A field naming a filesystem path** takes `reads_path=True`. The flag is
declarative — it is what `tests/test_source_paths.py` sweeps, and nothing more —
so the plugin must itself call `src.ingestion.paths.resolve_source_path`, in
`validate_config` *and* in `fetch`, or the path is never contained (see
[SECURITY.md](SECURITY.md#where-file-imports-may-read)):

```python
try:
    path = resolve_source_path(str(config["path"]))
except PathNotAllowed as error:
    errors.append(str(error))     # and re-raise as SourceError from fetch
```

**A field the stored credentials are bound to** — a base `url`, or a switch like
Calibre-Web's `verify_ssl` that decides how the credential travels — takes
`credential_bound=True`, which clears the source's stored secrets when it
changes. Validate the URL's shape with `src.ingestion.urls.source_url_error` in
both `validate_config` and `fetch`: a sync of *every* source never calls
`validate_config`.

## ContentItem

```python
ContentItem(
    id="unique-external-id",             # required, unique within content type
    title="Item Title",                  # required
    content_type=ContentType.BOOK,       # BOOK, MOVIE, TV_SHOW, VIDEO_GAME
    status=ConsumptionStatus.COMPLETED,  # COMPLETED, CURRENTLY_CONSUMING, UNREAD
    rating=4,                            # 1-5
    review="My review text",
    author="Author/Director",
    ignored=None,                        # True/False states it, None says nothing
    metadata={},
    source="my_plugin",                  # set for you, to the user-defined source id
)
```

Map source statuses onto the enum, and normalize ratings to 1-5:

```python
STATUS_MAP = {
    "read": ConsumptionStatus.COMPLETED,
    "reading": ConsumptionStatus.CURRENTLY_CONSUMING,
    "to-read": ConsumptionStatus.UNREAD,
}


def normalize_rating(source_rating: int, max_rating: int = 10) -> int | None:
    if source_rating <= 0:
        return None
    return max(1, min(5, round(source_rating * 5 / max_rating)))
```

## Metadata keys

`metadata` is free-form, but storage recognises a fixed set of keys per content
type and lifts those into the type's detail table (`book_details`,
`movie_details`, `tv_show_details`, `video_game_details`). Anything else is kept
verbatim in the detail row's free-form blob. **A misspelled key is not an error
anywhere**, so spell a recognised one correctly: the value lands in the blob and
never reaches the column the rest of the app queries. Both tables below come
from `src/models/detail_fields.py`, which declares every field once.

| Content type | Recognised keys |
|---|---|
| `book` | `author`, `pages`, `isbn`, `isbn13`, `publisher`, `year_published`, `genres`, `tags`, `description` |
| `movie` | `director`, `runtime`, `release_year`, `genres`, `studio`, `tags`, `description` |
| `tv_show` | `creators`, `seasons`, `episodes`, `network`, `release_year`, `genres`, `tags`, `description` |
| `video_game` | `developer`, `publisher`, `platforms`, `genres`, `release_year`, `tags`, `description` |

The first key of each row above is that type's creator, and it is the one
exception to metadata in and metadata out: storage reads it back on
`ContentItem.author`, not in `metadata`. Setting `author` on the item works
just as well, and wins if you do both.

Eight columns accept a second spelling, and reach the same column either way:

| Column | Also accepted as | Where |
|---|---|---|
| `genres` | `genre` | every content type |
| `platforms` | `platform` | `video_game` |
| `seasons` | `total_seasons` | `tv_show` |
| `runtime` | `runtime_minutes` | `movie` |
| `release_year` | `year` | `movie`, `tv_show` (**not** `video_game`, which takes `release_year` only) |
| `creators` | `creator` | `tv_show` |
| `developer` | `developers` | `video_game` |
| `publisher` | `publishers` | `video_game` |

A text column takes a string, a number, or a list of either, joined with commas
— so the plural spellings above may be lists. An object is refused rather than
stored as its Python repr, and the refusal fails the whole item's save: sync
reports only `Failed to process '<title>'`, naming no field, while the log line
beside it names the key. So `metadata["publisher"] = {"name": "X"}` loses the
item, not just the publisher. Reduce the shape to names before handing it over.

The blob is a shared namespace rather than scratch space. First-party code reads
these keys out of it, and none of them is a recognised key:

| Read by | Keys |
|---|---|
| [Length scorer](SCORING.md#content-length-preferences), `src/recommendations/content_length.py` | **book** `num_pages`, `number_of_pages`. **TV show** `number_of_seasons`. **video game** `average_playtime_hours` |
| Series ordering, `src/utils/series.py` | Series name: `series_name`, `series`, `series_title`, `franchise`. Position: `series_position`, `series_number`, `series_num`, `book_number`, `book_num`, `season`, `season_number`, `season_num`, `part`, `part_number`, `episode`, `episode_number`, `movie_number`. Expanding a show into seasons: `number_of_seasons` |
| Season checklist and the [variety ladder](SCORING.md#variety-after-completion), `src/utils/series.py` | `seasons_watched`, `seasons_watched_dates` |
| Library export, `src/web/export.py` | `notes` on every type. **TV show** `seasons_watched`. **video game** `playtime_hours` |

That list grows whenever a reader gains another fallback spelling, so check those
files. Beyond it and the recognised keys, the blob is yours.

**Taking one of those keys for your own bookkeeping changes behaviour, with no
warning and no error at any layer.** Your own `number_of_seasons` re-classifies
the show's length. Your own `franchise` re-orders a series. Your own
`average_playtime_hours` re-classifies a game's length, where `playtime_hours`
does not: that key holds the user's own hours, and nothing scores it.

Enrichment writes into the blob too. RAWG writes `average_playtime_hours`,
`franchise` and `series_position`, TMDB writes `series_name`, `series_position`
and `tmdb_collection_id`. `merge_enrichment` (`src/enrichment/manager.py`) fills
each only where the key is missing or empty, so it never overwrites you. See
[ARCHITECTURE.md](../ARCHITECTURE.md).

### Shape rules

**`genres`, `tags` and `platforms` are lists of names.** A list is stored as
given. Anything else, a bare string or a dict included, is wrapped into a
one-element list rather than rejected, so a wrong shape survives all the way out
to the library export and back in as a literal string. Write
`["Windows", "Linux"]`.

**Get the shape right the first time, because fixing the plugin does not repair
what it already stored.** `platforms` is fill-only, so a stored value wins on
every later sync, and the user has no way to undo it. Removing the source drops
its config and secrets but keeps its items, so re-adding syncs into the same rows
and the stored value still wins. Nothing in the web API or the CLI deletes a
library item, either. Your fix reaches only items nobody has synced yet, and that
holds for every fill-only column.

**`genres` and `tags` merge additively, and `seasons` and `episodes` only ever
increase.** Every other detail column is fill-only, written while the stored
value is empty and left alone afterwards, because enrichment and the user's own
edits outrank a re-sync.

## Example: file-based plugin

The properties follow the interface above, with `requires_api_key` and
`requires_network` returning `False`, and `validate_config` checking that the
configured `path` exists.

```python
def fetch(
    self,
    config: dict[str, Any],
    progress_callback: ProgressCallback | None = None,
) -> Iterator[ContentItem]:
    with open(Path(config["path"]), newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
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
```

## Example: API-based plugin

```python
def fetch(
    self,
    config: dict[str, Any],
    progress_callback: ProgressCallback | None = None,
) -> Iterator[ContentItem]:
    try:
        response = requests.get(
            f"{self.API_BASE}/users/{config['username']}/movies",
            headers={"Authorization": f"Bearer {config['api_key']}"},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as error:
        raise SourceError(
            "movie_api", f"API request failed: {scrub_request_error(error)}"
        ) from error

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
```

`SourceError` and `scrub_request_error` come from `src.ingestion.plugin_base` and
`src.utils.request_errors`.

## Registration

`PluginRegistry` auto-discovers plugins from `src/ingestion/sources/`. Each lives
in its own folder, which the registry treats as a Python subpackage:

```
src/ingestion/sources/<plugin>/
├── __init__.py        # re-exports <plugin>.py for discovery
├── <plugin>.py        # the SourcePlugin subclass
├── README.md          # usage and configuration
└── test_<plugin>.py   # tests live next to the plugin
```

The `__init__.py` is one line:

```python
"""<plugin> plugin package."""
from src.ingestion.sources.<plugin>.<plugin> import *  # noqa: F401, F403
```

Confirm discovery with `python3.11 -m src.cli update --help`, which lists your
source.

## Configuration

Sources are **named instances**: a user-defined id plus a plugin name, so one
plugin can back several sources. They live in the `source_configs` table and are
created from the Data tab or the `source` CLI. The legacy YAML bootstrap shows
the same shape most compactly:

```yaml
inputs:
  my_books:
    plugin: csv_import
    path: "inputs/books.csv"
    content_type: "book"
    enabled: true

  classic_movies:
    plugin: csv_import
    path: "inputs/classic_movies.csv"
    content_type: "movie"
    enabled: true
```

File-based plugins use `path`, never `csv_path`, `json_path` or `markdown_path`.

`fetch()` receives a `_source_id` key holding the user-defined name.
`get_source_identifier(config)` returns it, and it is what lands in
`ContentItem.source`, so items are tracked by source id rather than plugin name.

## Testing

Tests live at `src/ingestion/sources/<plugin>/test_<plugin>.py`. `pytest` collects
them alongside `tests/`, and the root `conftest.py` gives them the same autouse
isolation every other test gets: the credential key is redirected into `tmp_path`,
production logging is neutralised, and the process timezone is pinned to UTC.
Request the `host_timezone` fixture and call it to exercise another zone. Private
plugins run under the same conftest. `src/ingestion/sources/_isolation/` holds the
test proving it, and its leading underscore keeps the registry from importing it.

```python
# src/ingestion/sources/my_plugin/test_my_plugin.py
class TestMyPlugin:
    @pytest.fixture
    def plugin(self):
        return MyPlugin()

    def test_validate_config_missing_key(self, plugin):
        assert "API key is required" in plugin.validate_config({"user_id": "user1"})

    @patch("requests.get")
    def test_fetch_returns_items(self, mock_get, plugin):
        mock_get.return_value.json.return_value = {
            "items": [{"id": "1", "title": "Test", "status": "completed"}]
        }
        mock_get.return_value.raise_for_status = Mock()

        items = list(plugin.fetch({"api_key": "key", "user_id": "user"}))

        assert len(items) == 1
        assert items[0].title == "Test"
```

Mock every network call. Never make a real one.

## Best practices

The traps that bite hardest come first.

**Say nothing about `ignored` unless your source really says something.**
`ignored` is user-owned, and storage writes it whenever your plugin states a
value: `True` ignores the item, `False` *un-ignores* one the user ignored in the
app. `None`, the default, means this source has no opinion, and the stored flag
is left alone. Storage receives a boolean or nothing, so it cannot tell a file's
explicit `false` from a `False` you defaulted to, and **a defaulted `False`
silently clears the user's ignore list on every sync**. If your source carries
the flag, read it with `parse_ignored_field()` from `generic_csv`: it returns
`None` for an absent key, a blank CSV cell or a JSON `null`, and a boolean only
when a value was stated. Do not reach for the lower-level
`parse_boolean_field()`, which returns `False` for a missing value.

The library exporter (`src/web/export.py`) is the one deliberate exception, not a
precedent. It states `ignored` on every row, because re-importing an edited
export is the supported bulk un-ignore. That is why a re-imported export replaces
the whole ignore list with its state at export time, and why
[DATA_SOURCES.md](DATA_SOURCES.md#library-export) warns against leaving one
configured as a standing source.

**`seasons_watched` is a list of season numbers**, `[1, 2, 5, 6]`, not a count.
Use `parse_seasons_watched()` from `generic_csv` to convert string input. A bare
integer is read as a count for backward compatibility, so `5` becomes
`[1, 2, 3, 4, 5]`.

**Populate `seasons_watched_dates` when you have per-season timestamps.** It maps
`{season_number_str: iso_timestamp}`, keyed by the same numbers as
`seasons_watched`, and it is how a finished season of an in-progress show gets
correct recency on the [variety ladder](SCORING.md#variety-after-completion).
Omit it and the season still reaches the ladder, on the undated bottom rung.

**Scrub `requests` errors that may carry secrets.** If your plugin passes a
secret in the URL or query string, the default `str()` of a `requests` exception
embeds the full request URL and leaks that credential into raised errors and
logs. Pass it through `scrub_request_error()` from `src.utils.request_errors`,
which returns only `HTTP <status>` or the bare exception class name. The TMDB and
RAWG enrichment providers and the Steam source all do this.

Beyond those: raise `SourceError` for recoverable failures, validate every
required config field, skip items missing required fields rather than yielding
them, and keep ids unique within a content type.

## Thread safety

With `sync.max_workers > 1`, each enabled source runs on its own worker thread
inside `execute_multi_source_sync`.

- **Plugin instances are independent.** The registry instantiates one plugin
  object per source entry, so per-instance state is fine.
- **Avoid mutable class-level state.** Class attributes are shared across
  instances and threads. Keep state on `self`.
- **Per-source rate limiting is yours.** Sleep or token-bucket inside `fetch()`.
  The framework adds cross-source parallelism, not intra-source pacing.
- **Storage writes are already serialised.** `StorageManager` locks around
  `save_content_item`, `complete_content_item`, `save_credential` and
  `merge_user_preference_config`.
- **`progress_callback` is called from the worker thread**, and is thread-safe.

Stateless plugins need no changes for parallel sync.

## OAuth token rotation

A server may rotate a refresh token mid-sync. Persist it through the
`_on_credential_rotated` callback `execute_sync` injects into the config, or the
user has to re-authenticate:

```python
from src.ingestion.plugin_base import CredentialUpdateCallback

on_credential_rotated: CredentialUpdateCallback | None = (
    config.get("_on_credential_rotated")
    if callable(config.get("_on_credential_rotated"))
    else None
)

new_refresh_token = token_response.get("refresh_token")
if new_refresh_token and new_refresh_token != original_refresh_token:
    if on_credential_rotated:
        on_credential_rotated("refresh_token", new_refresh_token)
```

Worked examples: `src/ingestion/sources/gog/gog.py` and
`src/ingestion/sources/epic_games/epic_games.py`.

## Enrichment providers

Providers fill metadata gaps from external APIs. They use the same folder layout
as source plugins, under `src/enrichment/providers/<name>/`, and subclass
`EnrichmentProvider` from `src/enrichment/provider_base.py`.

Private providers (`plugins/private/enrichment/`) and private source plugins
(`private/plugins/`) stay **flat single-file modules**. The private discovery
code globs `*.py` rather than walking subpackages, so a private provider folder
is silently skipped.

`name`, `display_name`, `content_types`, `requires_api_key`,
`get_config_schema` and `validate_config` work as they do on a source plugin. The
rest is what differs:

```python
class MyEnrichmentProvider(EnrichmentProvider):
    @property
    def rate_limit_requests_per_second(self) -> float:
        return 5.0  # default is 1.0

    def enrich(
        self, item: ContentItem, config: dict[str, Any]
    ) -> EnrichmentResult | None:
        # Return None when the item cannot be found.
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

You return one `EnrichmentResult` instead of yielding `ContentItem`s, the manager
throttles you from `rate_limit_requests_per_second`, the merge is gap-filling
only, and config lives under `enrichment.providers.<name>` rather than `inputs`.

```yaml
enrichment:
  enabled: true
  providers:
    my_api:
      api_key: "your-key"
      enabled: true
```

## Plugins to read

| Plugin | Pattern |
|---|---|
| `goodreads_csv` | File-based CSV parser |
| `goodreads_rss` | Simple GET plus pagination, no auth |
| `steam` | API-based with rate limiting |
| `gog` | OAuth API with token refresh |
| `epic_games` | OAuth API via Legendary |
| `radarr`, `sonarr` | API-based movie and TV libraries |
| `generic_csv`, `generic_json`, `markdown` | Flexible file importers |
| `roms` | Directory scanner, with a No-Intro/Redump/TOSEC title cleaner in `_rom_title.py` |

Each lives at `src/ingestion/sources/<name>/<name>.py`. Enrichment providers:
`tmdb` (movies and TV), `openlibrary` (books, no API key), `rawg` (video games),
under `src/enrichment/providers/<name>/<name>.py`.
