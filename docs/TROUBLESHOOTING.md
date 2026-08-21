# Troubleshooting

## Install

### `ModuleNotFoundError`

```bash
uv sync --locked          # add --extra dev for the test tooling
```

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

### `Renaming content_items rewrote the foreign keys of ...`

The upgrade that moves external ids off `content_items` refused to finish, so the
database will not open. Nothing is lost: it rolls back before dropping anything.
SQLite ignored the `legacy_alter_table` pragma the rebuild sets. Report it with
your SQLite version:

```bash
python3.11 -c "import sqlite3; print(sqlite3.sqlite_version)"
```

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

Export from Goodreads (My Books → Import/Export → Export Library) and import
that file. The CSV needs Title and Author columns.

```bash
python3.11 -m src.cli import goodreads_library_export.csv --importer goodreads_csv
```

### Steam import fails

Get a key at <https://steamcommunity.com/dev/apikey> and set your numeric Steam
ID. **Make the profile public**, or the API returns nothing.

### Duplicate items

The matcher only runs when a source first names an item, so it keeps new
duplicates out but never revisits a pair already here. `library duplicates`
lists them a work at a time, naming every copy it found, `library merge`
collapses one copy into another, and `library unmerge` undoes it. The
Duplicates page in the web UI is the same review, a block per work.

A work with more than 40 copies matching is left out of both, as is one whose
copies split more ways than a page holds, with a warning in the log naming it:
merge some of them and the rest are offered again.

## Web interface

### Blank page, or "Failed to connect"

Is the server running (`python3.11 -m src.web`), on the port you are asking for
(18473 by default)? Then check the browser console.

### I have forgotten the password

There is no email and no reset link. Run this on the machine holding the
database, then sign in again:

```bash
python3.11 -m src.cli account set-password
```

### 401 Unauthorized, or the UI returns to the login form

The session lapsed, or a password change signed that browser out. Sign in again.
Five wrong passwords lock a username out for five minutes.

### 403 "Cross-origin requests may not change anything here."

Something other than the app's own page sent a write: a script on another site,
or a second dev server on another port. `SameSite=Strict` treats every port on
localhost as one site, so the browser's `Sec-Fetch-Site` header is what tells
them apart.

Adding the origin to `web.allowed_origins` will not help. That setting reaches
only what is ungated anyway, because the session cookie is never sent
cross-origin — the request arrives signed out either way.

### Preferences reset after a refresh

The form is built from what the server returned, so a page that could not load
says so and offers **Retry** instead of showing empty defaults. If the values
really did change, click **Save Preferences** and read the error the API
returned. A 401 there is the session again.

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

`docker compose logs app`. Usually the config volume is not mounted, or `./data`
is not writable by the container user.

### `legendary auth` fails inside the container

The `legendary` CLI is not a container entrypoint, and browser OAuth cannot work
inside one. Use the web UI: **Data** tab, **+ Add source**, Epic Games, then
**Connect Epic Games**, log in, and paste the authorization code back. Steps in
the [Epic Games plugin README](../src/ingestion/sources/epic_games/README.md).

## Still stuck

Raise the log level from the **Settings** page (Advanced), then open a GitHub
issue with your Python version, OS, the full traceback and the steps to
reproduce. **Strip secrets out of any config you paste.**
