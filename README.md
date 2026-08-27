# Recommendinator

A privacy-focused recommendation engine that learns from your ratings and reviews
across books, movies, TV shows, and video games.

- **Runs locally**. Your data never leaves your machine
- **No model, no service**. Scoring is arithmetic over your own library, and
  every score can be read back
- **You own your data**. A SQLite database you can query, back up, or delete

It imports from sources you already use, enriches items with metadata, and ranks
recommendations through a transparent scoring pipeline. Your love of sci-fi books
can influence game and movie suggestions through genre clusters. Browse and tune
everything from a themeable web UI or the CLI, which are
[interchangeable interfaces](ARCHITECTURE.md#5-interfaces) to the same engine.

## 30-second start

```bash
mkdir -p recommendinator/{config,data,inputs} && cd recommendinator

docker run -d \
  --name recommendinator \
  -p 127.0.0.1:18473:8000 \
  -v "$(pwd)/config:/app/config" \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/inputs:/app/inputs:ro" \
  --restart unless-stopped \
  ghcr.io/therealahall/recommendinator:latest
```

The container writes a starter `config/config.yaml` and starts serving. Open
**http://localhost:18473**: a new instance has no account, so it opens on a setup
screen asking for a username, a display name and a password of at least 12
characters, and signs you in.
Then, in order:

1. **[Set up enrichment](docs/ENRICHMENT_SETUP.md) first.** It fills in the
   genres, tags, and descriptions the scoring pipeline depends on. Skipping it
   produces poor recommendations.
2. **Connect a data source**, from the table below.
3. **Get recommendations**, in the web UI or with
   `uv run python -m src.cli recommend --type book --count 5`.

> Running from source instead of Docker? See the [Quick Start guide](QUICKSTART.md).
> For reverse proxies and the full deployment reference, see
> [docs/DOCKER.md](docs/DOCKER.md).

## Security notice

The web UI signs in to one account, created on the first visit and held by a
session cookie. **Until someone creates it, whoever reaches the instance first
can**, which is why it binds to `127.0.0.1` by default and Docker publishes on
`127.0.0.1` too. The app **never serves TLS**, so reaching it from another
machine means a reverse proxy terminating HTTPS. See
[docs/SECURITY.md](docs/SECURITY.md).

## Data sources

Each source has its own setup guide. Pick the ones you use.

| Source | Type | Setup |
|--------|------|-------|
| **Goodreads (public shelves via RSS)** | Books | [goodreads_rss](src/ingestion/sources/goodreads_rss/README.md) |
| **Calibre-Web** | Books | [calibre_web](src/ingestion/sources/calibre_web/README.md) |
| **Steam** | Games | [steam](src/ingestion/sources/steam/README.md) |
| **GOG** | Games | [gog](src/ingestion/sources/gog/README.md) |
| **Epic Games** | Games | [epic_games](src/ingestion/sources/epic_games/README.md) |
| **Sonarr** | TV Shows | [sonarr](src/ingestion/sources/sonarr/README.md) |
| **Radarr** | Movies | [radarr](src/ingestion/sources/radarr/README.md) |
| **Trakt** | TV Shows / Movies | [trakt](src/ingestion/sources/trakt/README.md) |
| **ROM Library** | Games | [roms](src/ingestion/sources/roms/README.md) |

A one-off export — a Goodreads or StoryGraph CSV, or a CSV, JSON or Markdown
file of your own — is imported once instead. For that, managing sources,
parallel sync and library export, see **[docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)**.

## Features

- Multi-source ingestion, with cross-content recommendations through genre
  clusters
- A transparent scoring pipeline over genre, creator, series order, tag overlap
  and rating patterns ([how it works](docs/SCORING.md))
- Natural-language [custom rules](docs/CUSTOM_RULES.md) like "avoid horror"
- [Metadata enrichment](docs/ENRICHMENT_SETUP.md) from TMDB, OpenLibrary and
  RAWG, automatic or edited by hand
- Automated syncing: each source carries its own cadence, set from the CLI or
  the Data page, and the web server runs it while it is up
- Content-length filtering, multi-user support, typo-tolerant library search,
  themes

## Configuration

Copy `config/example.yaml` to `config/config.yaml`. It holds only what is needed
to stand the app up: where the server binds, and where the database lives.

```yaml
web:
  host: "127.0.0.1"
  port: 18473

storage:
  database_path: "data/recommendations.db"
```

Under Docker `host` and `port` are inert, because the image passes `--host` and
`--port` on the command line and CLI flags beat `config.yaml`. Publish a
different port with `APP_PORT` instead. See [docs/DOCKER.md](docs/DOCKER.md).

`security.allowed_source_roots` optionally belongs here too — the directories a
scanning source may read, `inputs/` by default, and deliberately unreachable
from the Settings API. See
[docs/SECURITY.md](docs/SECURITY.md#where-a-source-may-read).

Everything else lives in the database and is set from the app. Data sources come
from the **Data** tab or the `source` CLI. Global settings, from scorer weights
to enrichment to logging, come from the **Settings** page or the `settings` CLI.
Enrichment can also be switched on from the **Data** tab, beside the controls
that need it.

```bash
uv run python -m src.cli settings list
uv run python -m src.cli settings set recommendations.default_count 10
uv run python -m src.cli settings set-secret enrichment.providers.tmdb.api_key
```

API keys and OAuth tokens are stored encrypted, so enter them from the Settings
page or `settings set-secret`, never `config.yaml`. See
[docs/SCORING.md](docs/SCORING.md) for what the weights do and
[ARCHITECTURE.md](ARCHITECTURE.md#global-configuration-precedence) for
precedence.

### Your ratings are yours

A sync fills in around what you have said, it does not talk over you. Ratings and
reviews are only ever written into an empty field, completion dates give way only
to later ones, and status moves forward, never back. The single exception is a
completed TV show whose season checklist you have filled in, which returns to
in-progress when a sync brings new seasons.
Your own edits win outright, and any field you do not mention is left alone. The
[full rules](ARCHITECTURE.md#user-owned-fields) cover every case.

**An export is a snapshot, not a patch.** Every row it writes states whether that
item is ignored, so re-importing one replaces your whole ignore list with the one
you had on the day you exported. That is how you un-ignore things in bulk.

### Upgrading

**From 0.36.0 or earlier.** The CSV, JSON, Markdown, Goodreads and StoryGraph
plugins are gone: those files are imported once now. Sources on them are deleted
on first boot and named in the log, and an `inputs:` block naming one is
ignored — delete it.

**From 0.43.0 or earlier.** Your book titles change once, on first open:
`Leviathan Wakes (The Expanse, #1)` becomes `Leviathan Wakes`, with the series
and its number stored beside it.

## CLI usage

```bash
uv run python -m src.cli update --source all
uv run python -m src.cli recommend --type book --count 10
uv run python -m src.cli library list --type book --status completed --sort rating
uv run python -m src.cli library list --search "die hard"
```

Full command reference: **[docs/CLI.md](docs/CLI.md)**.

## Documentation

| Document | Description |
|----------|-------------|
| [QUICKSTART.md](QUICKSTART.md) | Getting started guide (Docker and from-source) |
| [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) | Managing sources, parallel sync, export |
| [docs/CLI.md](docs/CLI.md) | Full CLI command reference |
| [docs/SCORING.md](docs/SCORING.md) | How the recommendation engine scores |
| [docs/ENRICHMENT_SETUP.md](docs/ENRICHMENT_SETUP.md) | Metadata enrichment setup (critical) |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System design and components |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Contributing guidelines |
| [docs/DOCKER.md](docs/DOCKER.md) | Docker deployment and reverse proxy |
| [docs/CUSTOM_RULES.md](docs/CUSTOM_RULES.md) | Custom preference rules |
| [docs/PLUGIN_DEVELOPMENT.md](docs/PLUGIN_DEVELOPMENT.md) | Adding new data sources |
| [docs/THEME_DEVELOPMENT.md](docs/THEME_DEVELOPMENT.md) | Creating custom web UI themes |
| [docs/SECURITY.md](docs/SECURITY.md) | Security considerations |
| [docs/PYTHON_VERSION.md](docs/PYTHON_VERSION.md) | Python version requirements |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Common issues and solutions |

## Requirements

Python 3.11 through 3.14 (see [docs/PYTHON_VERSION.md](docs/PYTHON_VERSION.md))
and SQLite.

## License

[PolyForm Noncommercial 1.0.0](LICENSE), free for personal and noncommercial use.
