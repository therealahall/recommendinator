# Enrichment Setup Guide

Enrichment is **disabled by default**. Enable it and configure providers
immediately after your first import.

Enrichment settings live in the database and are managed from the web
**Settings** page or the `settings` CLI, not `config.yaml`. Provider API keys
are stored encrypted in the `credentials` table via `settings set-secret`, never
in plaintext.

## Why it matters

Enrichment fills in the genres, tags and descriptions the scoring pipeline runs
on. A few sources arrive rich, Sonarr and Radarr carry genres, but most do not:
a Goodreads CSV gives you titles and authors, Steam gives names and playtime.
Genre matching, tag overlap, series affinity and creator matching all need that
metadata, so a library half without it produces poor or seemingly random
recommendations.

## Providers

Enable all three for full coverage.

| Provider | Content | API key | Rate limit |
|----------|---------|---------|------------|
| OpenLibrary | Books | None | 1 request/second |
| TMDB | Movies, TV shows | Free, v3 auth | 40 requests/second |
| RAWG | Video games | Free | 5 requests/second |

**OpenLibrary** matches by ISBN when your source supplies one, otherwise by
title and author search. It fills genres, description, page count, publisher and
publish year. No account needed.

**TMDB** fills genres, tags from keywords, the overview, runtime for movies or
season and episode counts for TV, ratings, release dates, studio or network, up
to three directors or creators, and collection info with series position. Create
a free account at [themoviedb.org](https://www.themoviedb.org/), go to
**Settings > API**, request a Developer key, and copy the **API Key (v3 auth)**.
Two optional fields:

```bash
# ISO 639-1, optionally with a region (default en-US)
uv run python -m src.cli settings set enrichment.providers.tmdb.language de-DE
# keywords as tags, default true, costs one extra call per item
uv run python -m src.cli settings set enrichment.providers.tmdb.include_keywords false
```

**RAWG** fills genres, up to 20 tags, description, developer and publisher,
platforms, RAWG and Metacritic scores, ESRB rating, playtime estimates, and
franchise info ordered by release. It strips edition suffixes, trademark symbols
and DLC indicators from a title before searching, so it copes with messy names.
Get a key from [rawg.io/apidocs](https://rawg.io/apidocs).

## Full setup

```bash
uv run python -m src.cli settings set enrichment.enabled true
uv run python -m src.cli settings set enrichment.auto_enrich_on_sync true   # recommended
uv run python -m src.cli settings set enrichment.batch_size 50              # items per batch

uv run python -m src.cli settings set enrichment.providers.openlibrary.enabled true

uv run python -m src.cli settings set enrichment.providers.tmdb.enabled true
uv run python -m src.cli settings set-secret enrichment.providers.tmdb.api_key

uv run python -m src.cli settings set enrichment.providers.rawg.enabled true
uv run python -m src.cli settings set-secret enrichment.providers.rawg.api_key
```

`set-secret` prompts with hidden input, or reads `RECOMMENDINATOR_SECRET_VALUE`,
and stores the value encrypted. The same controls are on the **Settings** page
(Enrichment section) with masked key fields, and
`settings list --section enrichment` shows the current state.

Set this up *before* your first import and leave `auto_enrich_on_sync` on. Then
every sync enriches straight afterwards.

## Running enrichment

```bash
uv run python -m src.cli enrichment start
uv run python -m src.cli enrichment start --type movie   # or tv_show, book, video_game
uv run python -m src.cli enrichment status
```

The **Data** page's **Metadata Enrichment** section shows coverage broken down
by provider and starts a run. To find items still missing metadata, filter the
**Library** page instead.

## Manual enrichment editing

Some items never match a provider, being niche, very new or oddly titled. Fill
those in yourself. A manual edit marks the item enriched, so it leaves the
unenriched set and is skipped by automatic enrichment until you hand it back.

**Finding them.** An item counts as enriched only when a provider matched it
cleanly: a real provider, no error, not marked "not found", and not pending
re-enrichment. Everything else is unenriched.

- Web: the **Library** page's **Enrichment** filter, set to **Not enriched** or
  **Enriched**. Unenriched items also carry a "Not enriched" badge.
- CLI: `library list --enrichment not_enriched`. The table has an **Enriched**
  column, and `library show --id <id>` prints the state alongside genres, tags
  and description.

**Editing.** The web edit modal's **Enrichment metadata** section takes genres,
tags and a description. `library edit` takes the same three:

```bash
uv run python -m src.cli library edit --id 42 \
  --genre Action --genre RPG --tag co-op --description "A grand adventure."
```

Repeated `--genre` and `--tag` replace the existing lists rather than appending,
and `--description` replaces the description. `--clear-genres`, `--clear-tags`
and `--description ""` empty them.

Manual values **overwrite** the stored detail, unlike the gap-filling merge that
sync and automatic enrichment use. Supplying any of the three marks the item
enriched with the provider `"manual"`, which drops it from the `not_enriched`
filter and keeps automatic enrichment off it, so your values survive later runs.
To undo that, run `enrichment reset --id <id>` or press **Restore automatic
enrichment** in the edit modal.

## Troubleshooting

### "No providers for [content type]"

No provider is enabled for that type. Check
`settings list --section enrichment`: books need `openlibrary.enabled`, movies
and TV shows need `tmdb.enabled` plus a stored key, video games need
`rawg.enabled` plus a stored key.

### Items showing as "not found"

A settled answer: the provider replied with nothing, or with nothing whose title
and release year are close enough to be the same work, which is normal for niche
or very new content and for anything oddly titled. Ordinary runs skip the item from then on. Retry later,
once the data may have been added upstream, with
`uv run python -m src.cli enrichment start --retry-not-found`.

### Items showing as "failed"

Different from "not found". The provider never answered, having timed out, been
unreachable, throttled you or returned a server error, so nothing is known about
the item yet. Failed items stay queued and the next run picks them up. There is
nothing to do but run enrichment again once the provider is healthy.
`enrichment status` counts each item once, so a failed item reports under
**Failed** rather than **Pending** even though it is queued.

**Keeping a failure queued is new.** Before, *any* provider error settled the
item as "not found", so a library enriched under an older version can hold items
that were never really missing, only skipped because a provider was down or
throttling at the time. Ordinary runs keep skipping them, because "not found" is
settled. Run `enrichment start --retry-not-found` once after upgrading to sweep
them back in.

A rejected request — usually an invalid, revoked or expired API key returning
401 or 403 — settles that item as "not found", since the next run would be
rejected the same way. After five rejections in a row the provider is dropped
for the rest of the run.

Items the dropped provider never reached keep the status they had, so the next
run picks them up. Fix the key, run enrichment again, then `--retry-not-found`
for the few that already settled.

### API key errors

- **TMDB**: verify at
  [themoviedb.org/settings/api](https://www.themoviedb.org/settings/api). Use the
  v3 API key, not the v4 access token.
- **RAWG**: verify at [rawg.io/apidocs](https://rawg.io/apidocs). Free tier keys
  work fine.

### Enrichment seems slow

OpenLibrary is held to 1 request per second to stay a polite consumer, so
hundreds of books take a few minutes. TMDB and RAWG are much faster. A run
processes new unenriched items plus anything a previous run failed on, and skips
items already matched or settled as not found.
