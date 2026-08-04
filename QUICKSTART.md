# Quick Start

Up and running in under five minutes.

## Prerequisites

Docker, or Python 3.11 to run from source. Plus your data: a Goodreads export, a
Steam account, whatever you already have. No AI and no external services needed.

## Install

### Docker

No clone needed. Pull a published image and mount your directories:

```bash
mkdir -p recommendinator/{config,data,inputs} && cd recommendinator

docker run -d \
  --name recommendinator \
  -p 18473:8000 \
  -v "$(pwd)/config:/app/config" \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/inputs:/app/inputs:ro" \
  --restart unless-stopped \
  ghcr.io/therealahall/recommendinator:latest
```

The container writes a starter `config/config.yaml` on first run. You do not need
to edit it. Under Docker the bind comes from the image's `--host` and `--port`,
which beat `config.yaml`, so **publish a different port with the `-p` mapping**
(or `APP_PORT` under Compose) rather than `web.port`. Sources, settings and API
keys live in the database and are managed from the app.

For AI features, with an Ollama sidecar that downloads models for you, use
Compose:

```bash
curl -L https://github.com/therealahall/recommendinator/releases/latest/download/docker-compose.yml \
  -o docker-compose.yml
docker compose --profile ai up -d app-ai
```

**Naming `app-ai` is required.** The default `app` service has no profile, so
otherwise it starts too and both fight over the same host port.

[docs/DOCKER.md](docs/DOCKER.md) covers parameters, GPU setup and reverse
proxies.

### From source

```bash
git clone https://github.com/therealahall/recommendinator.git
cd recommendinator

curl -LsSf https://astral.sh/uv/install.sh | sh   # if you do not have uv
uv sync --locked                                  # add --extra ai for AI features

corepack enable                                   # pnpm, needs Node.js 18+
pnpm install --frozen-lockfile
pnpm build

cp config/example.yaml config/config.yaml
```

Node.js is only needed to build the web UI. CLI-only users can skip those three
lines. Start the server with `python3.11 -m src.web` and open
<http://localhost:18473>.

## Set up enrichment first

**Do this before importing anything.** Enrichment fills in the genres, tags and
descriptions the scoring pipeline runs on. Without it recommendations are poor.
All three providers are free, and only two need a key: **TMDB** for movies and TV
([themoviedb.org](https://www.themoviedb.org/settings/api)), **RAWG** for games
([rawg.io](https://rawg.io/apidocs)), and **OpenLibrary** for books, which needs
nothing.

These are database settings, from the **Settings** page or the `settings` CLI.
Keys go into the encrypted `credentials` table, never `config.yaml`:

```bash
python3.11 -m src.cli settings set enrichment.enabled true
python3.11 -m src.cli settings set enrichment.auto_enrich_on_sync true

python3.11 -m src.cli settings set enrichment.providers.tmdb.enabled true
python3.11 -m src.cli settings set-secret enrichment.providers.tmdb.api_key

python3.11 -m src.cli settings set enrichment.providers.rawg.enabled true
python3.11 -m src.cli settings set-secret enrichment.providers.rawg.api_key

python3.11 -m src.cli settings set enrichment.providers.openlibrary.enabled true
```

`auto_enrich_on_sync` runs enrichment after every sync, so there is no extra
step. Full guide: [docs/ENRICHMENT_SETUP.md](docs/ENRICHMENT_SETUP.md).

## Import your data

Plugins cover Goodreads, The StoryGraph, Calibre-Web, Steam, GOG, Epic Games,
Sonarr, Radarr, Trakt, a ROM library, and generic CSV, JSON and Markdown files.
[docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) lists them all.

### Add a source

From the **Data** tab, **+ Add source** builds the form from the plugin's own
field list and takes any secret up front. The same from the CLI:

```bash
# A file source needs no credentials
python3.11 -m src.cli source create my_books goodreads_csv
python3.11 -m src.cli source set my_books path inputs/goodreads_library_export.csv

# A secret is set separately, through a hidden prompt
python3.11 -m src.cli source create my_steam steam
python3.11 -m src.cli source set my_steam steam_id 76561198000000000
python3.11 -m src.cli source set-secret my_steam api_key
```

Sources live in the database, so there is nothing to edit by hand and no restart.
Later on, `source show`, `source set`, `source enable`, `source disable` and
`source remove` do the rest, and the **Data** tab does all of it too.

GOG, Epic Games and Trakt need OAuth. Follow that plugin's guide:
[GOG](src/ingestion/sources/gog/README.md),
[Epic Games](src/ingestion/sources/epic_games/README.md),
[Trakt](src/ingestion/sources/trakt/README.md).

### Generic CSV, JSON and Markdown

For anything without a dedicated plugin. `templates/` has a starter file per
content type:

```bash
cp templates/movies.csv inputs/my_movies.csv

python3.11 -m src.cli source create my_movies csv_import
python3.11 -m src.cli source set my_movies path inputs/my_movies.csv
python3.11 -m src.cli source set my_movies content_type movie
```

Give each source a unique id and you can run as many of the same plugin as you
like.

### Sync

```bash
python3.11 -m src.cli update --source list      # what is configured
python3.11 -m src.cli update --source my_books
python3.11 -m src.cli update --source all       # every enabled source
```

Without `auto_enrich_on_sync`, run enrichment yourself:

```bash
python3.11 -m src.cli enrichment start
python3.11 -m src.cli enrichment status
python3.11 -m src.cli enrichment start --retry-not-found   # providers drift
```

## Get recommendations

```bash
python3.11 -m src.cli recommend --type book --count 5   # or movie, tv_show, video_game
```

## Everyday commands

The full reference is [docs/CLI.md](docs/CLI.md). Most read-only commands take
`--format json`.

```bash
python3.11 -m src.cli status                    # component health and feature flags

python3.11 -m src.cli library list --type book --status completed --sort rating
python3.11 -m src.cli library show --id 42
python3.11 -m src.cli library edit --id 42 --rating 5 --status completed
python3.11 -m src.cli library edit --id 42 --seasons-watched 1,2,3   # each 1-200
python3.11 -m src.cli library ignore --id 42    # and library unignore

python3.11 -m src.cli auth connect --source gog     # or epic, trakt
python3.11 -m src.cli auth status
```

With AI on, chat and the memories behind it:

```bash
python3.11 -m src.cli chat start
python3.11 -m src.cli chat send --message "What should I read next?"

python3.11 -m src.cli memory list
python3.11 -m src.cli memory add --text "I love hard sci-fi"
python3.11 -m src.cli memory toggle --id 3      # flip active/inactive

python3.11 -m src.cli profile show              # what the engine learned
python3.11 -m src.cli profile regenerate
```

## Use the web interface

```bash
python3.11 -m src.web
```

Open <http://localhost:18473>. Browsing, syncing, recommendations, a **Settings**
page for feature flags, enrichment, scorer defaults, provider secrets and the
advanced infrastructure options, and chat when AI is on. The sidebar shows the
running version and banners a reload when a newer one appears.

Each recommendation card has two actions. **Ignore** drops the item out of future
recommendations. **Mark complete** opens an edit dialog for status, rating and
review, then saves it to your library. Both remove the card and leave the rest of
the list alone.

## Tune your preferences

```bash
python3.11 -m src.cli preferences get

python3.11 -m src.cli preferences set-weight genre_match 3.0
python3.11 -m src.cli preferences set-length book short        # or any, long
python3.11 -m src.cli preferences custom-rules add "avoid horror"
python3.11 -m src.cli preferences set-variety 4.0              # 0.0 off, 5.0 full
```

`set-variety` demotes genres you recently finished, per content type, so
recommendations stay varied. The next entry in a series you are actively reading
takes half the penalty, nudged down rather than buried. See
[docs/SCORING.md](docs/SCORING.md) and
[docs/CUSTOM_RULES.md](docs/CUSTOM_RULES.md).

## Optional: enable AI

For semantic similarity and LLM explanations:

1. Install [Ollama](https://ollama.ai).
2. `ollama pull mistral:7b` and `ollama pull nomic-embed-text`.
3. Turn the flags on, from the **Settings** page or the CLI:

   ```bash
   python3.11 -m src.cli settings set features.ai_enabled true
   python3.11 -m src.cli settings set features.embeddings_enabled true
   python3.11 -m src.cli settings set features.llm_reasoning_enabled true
   ```

4. Restart the app. All three flags need one.

Model guidance:
[docs/MODEL_RECOMMENDATIONS.md](docs/MODEL_RECOMMENDATIONS.md).

## Next steps

| Document | Covers |
|----------|--------|
| [docs/ENRICHMENT_SETUP.md](docs/ENRICHMENT_SETUP.md) | Enrichment in full. Do it first |
| [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) | Managing sources, parallel sync, export |
| [docs/CLI.md](docs/CLI.md) | Full CLI reference |
| [docs/SCORING.md](docs/SCORING.md) | How recommendations are scored |
| [docs/CONVERSATION_GUIDE.md](docs/CONVERSATION_GUIDE.md) | The chat interface |
| [docs/CUSTOM_RULES.md](docs/CUSTOM_RULES.md) | Preference rules |
| [docs/PLUGIN_DEVELOPMENT.md](docs/PLUGIN_DEVELOPMENT.md) | Writing a data source plugin |
| [ARCHITECTURE.md](ARCHITECTURE.md) | How the system works |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Common issues |
