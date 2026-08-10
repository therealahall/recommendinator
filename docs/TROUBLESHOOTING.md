# Troubleshooting

## Install

### `Failed to build hnswlib`

ChromaDB needs Python 3.11 or 3.12. Install under 3.11, or run without AI and
skip ChromaDB.

### `ModuleNotFoundError`

```bash
uv sync --locked --extra ai          # add --extra dev for the test tooling
```

## Ollama

### `Connection refused` or `Failed to connect to Ollama`

Start it with `ollama serve`, confirm with
`curl http://localhost:11434/api/tags`, then check `ollama.base_url` points
there. See [OLLAMA_SETUP_GUIDE.md](OLLAMA_SETUP_GUIDE.md).

### `Model 'xxx' not found`

```bash
ollama list
ollama pull mistral:7b
ollama pull nomic-embed-text
```

The names have to match `ollama.model` and `ollama.embedding_model`.

### Ingestion crawls once embeddings are on

Use `nomic-embed-text`, not a general model. Or import with
`features.embeddings_enabled` off and re-sync later to backfill.

## Database

### `database is locked`

Only one process may write. Close SQLite browsers and check for a leftover
instance.

### `Schema version mismatch`

Back the database up first. It is the only copy of your ratings.

```bash
cp data/recommendations.db data/recommendations.db.backup
rm data/recommendations.db
```

Then re-import.

### Items disappear after a restart

The database is not where you think. Check `storage.database_path`, that `data/`
exists and is writable, and that you pass the same `--config` every time.

## Recommendations

### "No recommendations available"

Recommendations come from items you have **not** consumed, so:

- Everything is completed. Add items you have not finished.
- Nothing is completed and rated. The engine learns only from completed, rated,
  non-ignored items.
- No unconsumed items of the requested `--type`.
- Series ordering excluded the rest. Check `series_in_order`.

### They do not match your taste

- **Turn enrichment on**, much the most common cause. Without genres and tags
  the scorers have nothing to work with. See
  [ENRICHMENT_SETUP.md](ENRICHMENT_SETUP.md).
- Check coverage on the Data page. Aim for 90% or better.
- Rate more items, across more genres.
- Tune scorer weights and rules. See [SCORING.md](SCORING.md) and
  [CUSTOM_RULES.md](CUSTOM_RULES.md).

### A custom rule changes nothing

Confirm it saved with `preferences custom-rules list`, then see how it parses:

```bash
python3.11 -m src.cli preferences custom-rules interpret "your rule"
```

Rules bias scoring. They do not override it.

## Imports

### Goodreads import fails

Export from Goodreads (My Books → Import/Export → Export Library), drop the file
in `inputs/`, and point the source at it. The CSV needs Title and Author columns.

```bash
python3.11 -m src.cli source set my_books path inputs/goodreads_library_export.csv
```

### Steam import fails

Get a key at <https://steamcommunity.com/dev/apikey> and set your numeric Steam
ID. **Make the profile public**, or the API returns nothing.

### Source attribution at startup

Six plugins once stored their items under the plugin name instead of the source
id, splitting a library in two. Every startup and config reload tries to move
those rows onto the source that owns them, and logs one of four lines. Each is
said once per reason, so an edit that trades one obstacle for another says the
new line. No row is ever deleted or merged, whichever line you get.

- `Re-attributed 5 content item(s) from plugin name 'gog' to source 'my_gog'` —
  it worked, nothing to do.
- `Leaving 5 ... under plugin 'gog': a source is named after it but runs
  'steam'` — your source called `gog` runs a different plugin, so its own rows
  are spelled `gog` too. Rename it and the next startup hands all five rows to
  the source that does run `gog` — the renamed source's own rows included, since
  a rename relabels nothing. Its next sync takes back the ones still upstream.
- `Leaving 5 ... under plugin 'gog': 2 sources share it ('gog_home',
  'gog_work')` — nothing records which of them each row came from, and guessing
  would mis-attribute real data. Remove one and the next startup moves the rows
  onto the other.
- `Leaving 5 ... under plugin 'gog': the source named after it runs it` — the
  `gog` source spells its own rows `gog` by design, so nothing tells them from a
  sibling's. There is no fix, and nothing is wrong.

### Duplicate items

Items deduplicate by normalized title, so this is rare. When it happens the
titles still differ after normalization. Rename one and re-sync. Schema upgrades
re-normalize every title and merge whatever that exposes.

## Web interface

### Blank page, or "Failed to connect"

Is the server running (`python3.11 -m src.web`), on the port you are asking for
(18473 by default)? Then check the browser console.

### The server will not start: `No API token configured`

Nothing generates a token for you. Set `web.api_token` in `config/config.yaml`
to an `openssl rand -hex 32` value and start again.

### 401 Unauthorized, or the UI keeps asking for a token

The token is whatever you set `web.api_token` to in `config/config.yaml`; paste
that value when the UI asks. A rejected token is cleared and re-prompted, so a
prompt that keeps coming back means the value is wrong.

### Preferences reset after a refresh

Click **Save Preferences**, check the network tab, and confirm the API answers.
Every `/api` route needs the bearer token:

```bash
curl -H "Authorization: Bearer $(yq '.web.api_token' config/config.yaml)" \
  http://localhost:18473/api/status
```

### `503 Too many streams in progress`

Chat and recommendation streams share a budget of 8. Several tabs left open on a
streaming view is the usual cause — close the ones you are not watching and
retry. A slot comes back as soon as a stream ends or its tab disconnects.

## CLI

### `No such command`

```bash
python3.11 -m src.cli --help
python3.11 -m src.cli preferences --help    # it may be a subcommand
```

### `Configuration file not found`

```bash
cp config/example.yaml config/config.yaml
python3.11 -m src.cli --config path/to/config.yaml status
```

## Docker

[DOCKER.md](DOCKER.md) covers ports, permissions and volumes in full.

### The container exits immediately

`docker compose logs app`, or `app-ai` under the `ai` profile. Usually the config
volume is not mounted, or `./data` is not writable by the container user.

### `Connection refused to ollama:11434`

The sidecar is still pulling models. Watch `docker compose logs ollama` and wait
for its health check to pass.

### AI stays off with `features.ai_enabled` true

The AI packages are missing, so the app logs `chromadb is not installed` or
`ollama is not installed` and carries on without them. Run the AI image
(`docker compose --profile ai up -d app-ai`, naming the service), or install
locally with `uv sync --locked --extra ai`.

### Ollama uses the CPU with a GPU present

Uncomment the `deploy` block under the `ollama` service in `docker-compose.yml`:

```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: all
          capabilities: [gpu]
```

### `legendary auth` fails inside the container

The `legendary` CLI is not a container entrypoint, and browser OAuth cannot work
inside one. Use the web UI: **Data** tab, **+ Add source**, Epic Games, then
**Connect Epic Games**, log in, and paste the authorization code back. Steps in
the [Epic Games plugin README](../src/ingestion/sources/epic_games/README.md).

## Performance

### Slow startup

Turn AI off if you are not using it, and pre-pull the Ollama models.

### High memory use

Ollama holds the model in memory. Pick a smaller one, or run it on another
machine on your network and point `ollama.base_url` there — a host beyond your
own network is only settable in `config.yaml`, see
[SECURITY.md](SECURITY.md#network). Models:
[MODEL_RECOMMENDATIONS.md](MODEL_RECOMMENDATIONS.md).

## Still stuck

Raise the log level from the **Settings** page (Advanced), then open a GitHub
issue with your Python version, OS, the full traceback and the steps to
reproduce. **Strip secrets out of any config you paste.**
