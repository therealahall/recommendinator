# Troubleshooting Guide

Common issues and solutions for Recommendinator.

## Installation Issues

### ChromaDB Installation Fails

**Error:** `Failed to build hnswlib` or similar

**Solution:** ChromaDB requires Python 3.11 or 3.12. If you're using 3.13+, either:
1. Use Python 3.11: `uv sync --locked --extra ai`
2. Run without AI features (ChromaDB is optional)

### Missing Dependencies

**Error:** `ModuleNotFoundError: No module named 'xxx'`

**Solution:**
```bash
uv sync --locked --extra ai
# or for development:
uv sync --locked --extra ai --extra dev
```

## Ollama Issues

### Ollama Not Running

**Error:** `Connection refused` or `Failed to connect to Ollama`

**Solution:**
1. Start Ollama: `ollama serve`
2. Check it's running: `curl http://localhost:11434/api/tags`
3. Verify the URL in your config matches

### Model Not Found

**Error:** `Model 'xxx' not found`

**Solution:**
```bash
# Pull the required models
ollama pull llama3.2
ollama pull nomic-embed-text

# List available models
ollama list
```

### Slow Embeddings

**Symptom:** Ingestion takes a very long time

**Solutions:**
1. Use a faster embedding model (e.g., `nomic-embed-text`)
2. Reduce batch size in config
3. Run without AI features for initial import, add embeddings later

## Database Issues

### Database Locked

**Error:** `database is locked`

**Solution:**
1. Ensure only one instance is running
2. Close any SQLite browsers (DB Browser, etc.)
3. Check for zombie processes: `ps aux | grep python`

### Schema Migration Failed

**Error:** `Schema version mismatch`

**Solution:**
1. Backup your data: `cp data/recommendations.db data/recommendations.db.backup`
2. Delete and recreate: `rm data/recommendations.db`
3. Re-import your data

### Data Not Persisting

**Symptom:** Items disappear after restart

**Solution:**
1. Check the database path in config
2. Ensure the `data/` directory exists and is writable
3. Verify you're using the same config file

## Recommendation Issues

### No Recommendations Generated

**Symptom:** "No recommendations available"

**Causes & Solutions:**

Recommendations are based on items you **haven't consumed yet**. If all your items are marked as completed, there's nothing left to recommend.

1. **All items completed:** Add new items to your wishlist/library that you haven't consumed yet
2. **No consumed items:** You need some completed items so the engine can learn your preferences
3. **Wrong content type:** Ensure you have unconsumed items of the requested type
4. **Series filtering:** If all items are excluded by series rules, check series order settings

### Poor Quality Recommendations

**Symptom:** Recommendations don't match preferences

**Solutions:**
1. **Enable enrichment first** — This is the most common cause. Without enrichment, most items lack the genres, tags, and descriptions the scoring pipeline depends on. See [ENRICHMENT_SETUP.md](ENRICHMENT_SETUP.md) to set up TMDB, OpenLibrary, and RAWG.
2. **Check enrichment coverage** — In the web UI Data page, check the enrichment percentage. Aim for 90%+ coverage.
3. Rate more items (need variety for good preferences)
4. Adjust scorer weights in preferences
5. Add custom rules for specific preferences
6. Check if AI features are enabled for better similarity

### Custom Rules Not Working

**Symptom:** Added rules but no change in recommendations

**Solutions:**
1. Verify rule was saved: `python3.11 -m src.cli preferences custom-rules list`
2. Test interpretation: `python3.11 -m src.cli preferences custom-rules interpret "your rule"`
3. Click "Save Preferences" in web UI
4. Rules influence but don't completely override scoring

## Import Issues

### Goodreads Import Fails

**Error:** `File not found` or `Invalid CSV format`

**Solution:**
1. Export from Goodreads: My Books → Import/Export → Export Library
2. Import the file from the **Data** tab's **Import from file** button, or run
   `python3.11 -m src.cli import --source goodreads_csv --file <path>`
3. Check the path you passed, the CLI reads it directly off disk
4. Ensure CSV has required columns (Title, Author, etc.)

**Error:** HTTP 400 on a web upload, or "Check that the file is the export that importer expects, and that it is UTF-8 text"

Every importer reads UTF-8 (a byte-order mark, which is what Excel writes, is
fine). A file saved as Latin-1 or UTF-16 is refused. Re-save it as UTF-8 — in
Excel, "CSV UTF-8 (Comma delimited)" — and import again. The server log carries
the full detail for any import failure; the response keeps the file path out of
it deliberately. When a 400 arrives with no message of its own, the modal says
"We couldn't read that file. Check that it matches the selected format and try
again." instead, which means the same thing.

**Error:** HTTP 409 on a web upload, or "An import from this source is already running"

The server refuses a second import from the same source while the first one is
still going. Wait for it to finish and try again. The Data tab's job list shows
what is in flight. The modal is stricter than the server here: it greys out the
Import button while any sync or import job is running, not just one from the
same source.

**Error:** HTTP 413 on a web upload, or "That file is larger than the 50 MB limit"

The upload endpoint caps files at 50 MB, and the Import modal refuses a larger
file before uploading it. Split the export, or import the file with the CLI
instead, which has no cap.

**Error:** HTTP 422 on a web upload, or "That import source isn't available"

The source named in the request is not one of the installed file-import plugins.
In the browser that usually means the Source dropdown is stale, so reload the
Data tab and pick the format again. A non-browser client gets the same status for
a request that leaves out the `source` or `file` field. `GET /api/import/sources`
lists every source the endpoint accepts.

**Error:** HTTP 429 on a web upload, or "Too many imports are already running"

The endpoint accepts two imports at once, because each one costs a spooled copy
of the body plus a temp file plus a full ingestion run. Wait for a running import
to finish and retry. The Data tab's job list shows what is in flight.

**Error:** HTTP 503 on a web upload, or "Imports are unavailable: the server's storage or configuration didn't load"

The server is answering requests but its storage or its configuration never
initialised, so an import has nowhere to write. Retrying will not help, and
neither will a different file: this one is on the server, not on you. Check the
server's startup log, fix what it reports, and restart. Anything else that needs
the database is failing at the same time.

**Error:** "Something went wrong during the import. Please try again."

The modal's fallback for a status it has no specific wording for, which in
practice means the server hit an unexpected error (HTTP 500). The failure is not
about the file you picked, so retrying the same upload usually gives the same
result. The server log carries the detail.

**Error:** "Couldn't load import sources"

The modal asks the server which formats it can import when it opens, and that
request failed, so the Source dropdown is empty. Check the server is still
running, then close and reopen the modal.

**Warning:** "No items were found in the file."

The file parsed cleanly but produced nothing. Check that you uploaded the export
you meant to and that it is not empty. This is a warning, not an error, so the
import itself succeeded.

### A ROM Library source finds nothing, or refuses its path

**Error:** "Scan path is not an allowed ROM directory"

Scan paths are contained to an allow-list: your home directory, the directory the
app runs from, and the usual media mounts (`/mnt`, `/media`, `/run/media`,
`/srv`, `/data`, `/games`, `/roms`, `/Volumes`). Hidden directories under those
roots are refused, so a library at `~/.local/share/roms` is not scannable by
default — `~/.ssh` is refused the same way, which is the point. Name the hidden
directory itself as a root and it works, since a root is something you set.

If your library is elsewhere, set `RECOMMENDINATOR_SCAN_ROOTS` to the directories
it lives under, separated by `:`, and restart. It **replaces** the defaults
rather than adding to them, so list every root you need — naming only the new one
stops every other ROM source, including one under your home directory, from
scanning:

```bash
RECOMMENDINATOR_SCAN_ROOTS=/storage/roms:"$HOME" python3.11 -m src.web
```

See [the roms guide](../src/ingestion/sources/roms/README.md#where-a-library-may-live).

**Symptom:** a ROM sync hangs after an `extra_strip_patterns` entry was added

Patterns are capped at 10 entries of 200 characters, but nothing bounds how long
one takes to run — Python's `re` has no timeout, and a nested quantifier such as
`(a+)+` (or a chain like `.*.*.*x`) backtracks exponentially on a title that does
not match. Remove the pattern and rewrite it without the nesting: usually
`(?:...)+` over a fixed-length body, or a plain character class, does the same
job.

### Steam Import Fails

**Error:** `Steam API error` or `Invalid API key`

**Solutions:**
1. Get API key: https://steamcommunity.com/dev/apikey
2. Find Steam ID: Profile → Edit Profile → Custom URL or use numeric ID
3. Set profile to public (required for API access)
4. Check rate limits (wait a few minutes if hitting limits)

### Duplicate Items

**Symptom:** Same items appear multiple times

**Cause:** The system automatically deduplicates items from different sources using normalized title matching (strips punctuation, articles, edition suffixes, etc.). Duplicates should not occur under normal operation.

**If duplicates still appear:**
1. The items may have titles that don't match after normalization (try renaming one)
2. Items imported before the dedup feature may exist as separate rows — re-running a sync will merge them
3. Schema upgrades automatically re-normalize all titles and merge exposed duplicates

## Web Interface Issues

### Page Won't Load

**Error:** Blank page or "Failed to connect"

**Solutions:**
1. Check server is running: `python3.11 -m src.web`
2. Verify port (default: 18473)
3. Check browser console for errors
4. Try different browser or incognito mode

### Changes Not Saving

**Symptom:** Preferences reset after refresh

**Solutions:**
1. Click "Save Preferences" button
2. Check browser network tab for errors
3. Verify API is responding: `curl http://localhost:18473/api/status`

## CLI Issues

### Command Not Found

**Error:** `No such command 'xxx'`

**Solution:**
```bash
# Run through module
python3.11 -m src.cli --help

# Or check if it's a subcommand
python3.11 -m src.cli preferences --help
```

### Config File Not Found

**Error:** `Configuration file not found`

**Solution:**
1. Copy example config: `cp config/example.yaml config/config.yaml`
2. Specify path: `python3.11 -m src.cli --config path/to/config.yaml`

## Docker Issues

### Container Won't Start

**Error:** `Container exited with code 1`

**Solutions:**
1. Check logs: `docker compose logs app` (or `docker compose logs app-ai` if using `--profile ai`)
2. Verify config file is mounted
3. Ensure data directory permissions are correct

### Ollama Sidecar Issues

**Error:** `Connection refused to ollama:11434`

**Solutions:**
1. Wait for Ollama to be ready (check health status)
2. Verify network configuration in docker compose config
3. Check Ollama logs: `docker compose logs ollama`

### AI Features Disabled Despite Being Enabled in Config

**Symptom:** `features.ai_enabled: true` is set but AI features don't work

**Solutions:**
1. Check logs for warnings about missing packages (`chromadb is not installed` or `ollama is not installed`)
2. Install AI packages: `pip install recommendinator[ai]`
3. For Docker: run the AI variant with `docker compose --profile ai up -d app-ai` (the explicit `app-ai` service name is required so the default no-AI `app` service does not also start)

The application gracefully degrades when AI packages are missing — it logs a warning and continues with AI features disabled rather than crashing.

### GPU Not Working

**Symptom:** Ollama using CPU instead of GPU

**Solution:** Uncomment the `deploy` section under the `ollama` service in docker-compose.yml:
```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: all
          capabilities: [gpu]
```

### Epic Games Authentication in Docker

**Error:** `legendary auth` returns "unknown command" or can't open a browser inside a Docker container

**Cause:** The `legendary` CLI is not exposed as a container entrypoint, and browser-based OAuth cannot work inside a container.

**Solution:** Use the **web UI OAuth flow** instead — it works in Docker without any host-side tools:

1. Open the web UI → Data tab → **+ Add source** → Epic Games
2. In the Epic Games source panel, click **"Connect Epic Games"**
3. Log into Epic in the new tab, copy the authorization code from the JSON response
4. Paste the code back into the web UI and click **Connect**

See the [Epic Games Setup](../README.md#epic-games-setup) section in the README for full details.

## Performance Issues

### Slow Startup

**Symptom:** App takes long to start

**Solutions:**
1. Disable AI features if not needed
2. Use smaller embedding model
3. Pre-download Ollama models before first run

### High Memory Usage

**Symptom:** App uses excessive RAM

**Solutions:**
1. Limit vector DB cache size
2. Use pagination for large libraries
3. Consider running Ollama on separate machine

## Getting Help

If you can't resolve an issue:

1. Check existing GitHub issues
2. Include in your report:
   - Python version: `python3.11 --version`
   - OS and version
   - Error message (full traceback)
   - Steps to reproduce
   - Config file (remove secrets!)
3. Enable debug logging for more details
