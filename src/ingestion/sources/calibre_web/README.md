# Calibre-Web

Imports books from a [Calibre-Web](https://github.com/janeczku/calibre-web) instance via its OPDS Atom catalog.

## Content type
- `book`

## Requirements
- A running Calibre-Web instance reachable over the network.
- A Calibre-Web login (username + password). The password is stored in the encrypted credential database — not in `config.yaml`.
- OPDS must be enabled for the account (it is on by default in Calibre-Web).

## Configuration

```yaml
inputs:
  calibre_web:
    plugin: calibre_web
    enabled: true
    url: "http://localhost:8083"
    username: "reader"
    # password is set via the web UI / CLI and stored encrypted
    verify_ssl: true
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | str | yes | Base URL of the Calibre-Web instance (e.g. `http://localhost:8083`). |
| `username` | str | yes | Calibre-Web login username. |
| `password` | str | yes | Calibre-Web login password. Sensitive — stored encrypted, not in YAML. |
| `verify_ssl` | bool | no | Verify the TLS certificate (default `true`; set `false` for self-signed instances). |

Set the password when you create the source in the web UI **Data** tab — enter it
directly in the **+ Add source** modal (it renders as a password field and is
stored encrypted). To set or rotate it later, use the Replace action on the
source's panel or the CLI:

```
python3.11 -m src.cli source set-secret calibre_web password
```

## How it works

- **Authentication** uses HTTP basic auth with the Calibre-Web username/password, a 30s timeout, and honors `verify_ssl`.
- **Catalog** is read from the OPDS acquisition feed at `/opds/new`. The plugin follows OPDS `rel="next"` links page by page until the feed is exhausted, so the whole library is imported regardless of size.
- **Status**: the entire library is imported as backlog (`unread`). Books on Calibre-Web's "Read Books" shelf (`/opds/readbooks`) are marked `completed`. If that shelf is unavailable on a given instance, all books default to `unread` (read status is never guessed). Status only ever moves forward on re-sync — a re-import as `unread` will not revert a book you previously completed.
- **Rating**: not imported. Calibre's star rating is a community average written by its "download metadata" feature, not your own rating, so syncing it would pollute recommendations. Ratings are left for you to set in Recommendinator. A rating-scheme `<category>` is still recognised so its star label is kept out of the book's tags; bare numeric category labels with no rating scheme (e.g. a publication year like `2008`) are preserved as tags.
- **Series**: read from the schema.org `<schema:Series schema:name="..." schema:position="..."/>` element Calibre-Web emits (position may also appear as a `<schema:position>` child). Bare `<series>` / `<series_index>` children are read as a fallback for non-standard feeds.
- **External id**: derived from the OPDS entry `<id>` (a `urn:uuid:` or `urn:calibre:` value), stripped of its `urn:` prefix and namespaced as `calibre:<id>` so it is stable across syncs and unique to this source.
- **Metadata**: populated from whatever the OPDS entry provides — isbn, series + series index, tags/categories, language, publisher, published date, cover/thumbnail URL, and summary.

## Development
- Implementation: [`calibre_web.py`](calibre_web.py)
- Tests: [`test_calibre_web.py`](test_calibre_web.py)
- Plugin class: `CalibreWebPlugin`
