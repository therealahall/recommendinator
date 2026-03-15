# Enrichment Setup Guide

**Note:** Enrichment is **disabled by default**. You must explicitly enable it and configure providers in your `config.yaml` before enrichment will run. Do this immediately after your first data import.

## Why Enrichment is Critical

Enrichment is **paramount** to making sure that the Recommendinator's recommendations are of any value. While some ingestion sources provide rich metadata out of the gate (e.g., Sonarr and Radarr include genres), others are extremely limited or even non-existent. A Goodreads CSV export gives you titles and authors but no genres, tags, or descriptions. Steam provides game names and playtime but limited categorization.

Without enrichment, the recommendation engine is working with incomplete data. The scorers that drive recommendations — genre matching, tag overlap, series affinity, creator matching — all depend on having rich metadata for every item in your library. If half your library has no genres or tags, those scorers can't do their job, and you'll get poor or seemingly random recommendations.

**Bottom line:** If you skip enrichment, the Recommendinator is flying blind. Set up your enrichment providers before expecting useful recommendations.

## How Enrichment Works

The enrichment system compares your library items against known metadata databases — TMDB for movies and TV shows, RAWG for video games, and OpenLibrary for books. It uses a **gap-filling strategy**: it only adds metadata that's missing, never overwriting data you already have from your ingestion sources.

The process:

1. **Sync your data** from ingestion sources (Goodreads, Steam, Sonarr, etc.)
2. **Run enrichment** — it processes unenriched items in batches, querying the appropriate provider for each content type
3. **Metadata is merged** — genres, tags, descriptions, and additional metadata (runtime, page count, developer, series position, etc.) are filled in

You can run enrichment from the CLI or the web interface, and optionally enable auto-enrichment to run automatically after every sync.

## Provider Setup

There are three enrichment providers, one for each content type category. **All three should be enabled** to get full coverage across your library.

### OpenLibrary (Books)

**Content types:** Books
**API key required:** No
**Rate limit:** 1 request per second (polite limit)
**Cost:** Free

OpenLibrary is the easiest provider to set up — no API key needed. It matches books by ISBN (if available from your ingestion source) or by title and author search. It provides:

- Genres (filtered from library subject headings)
- Descriptions
- Page count
- Publisher
- Publish year

**Setup:**

Add to your `config.yaml` under the `enrichment` section:

```yaml
enrichment:
  enabled: true
  providers:
    openlibrary:
      enabled: true
```

That's it. No API key, no account creation.

### TMDB — The Movie Database (Movies & TV Shows)

**Content types:** Movies, TV Shows
**API key required:** Yes (free)
**Rate limit:** 40 requests per second
**Cost:** Free

TMDB provides comprehensive metadata for movies and TV shows. It's the backbone of enrichment for visual media. It provides:

- Genres
- Tags (derived from TMDB keywords)
- Description (overview)
- Runtime (movies) / season and episode counts (TV)
- Ratings
- Release dates
- Studio/network information
- Creator credits (TV shows)
- Collection/franchise info with series position ordering (e.g., knowing that *The Dark Knight* is the 2nd film in the Dark Knight trilogy)

**Getting your API key:**

1. Go to [themoviedb.org](https://www.themoviedb.org/) and create a free account
2. Navigate to **Settings** > **API** (or go directly to [themoviedb.org/settings/api](https://www.themoviedb.org/settings/api))
3. Request an API key — select "Developer" for the use type
4. Fill out the short application form (personal/hobby use is fine)
5. Copy the **API Key (v3 auth)** value

**Setup:**

```yaml
enrichment:
  enabled: true
  providers:
    tmdb:
      api_key: "your-tmdb-api-key-here"
      enabled: true
```

**Optional settings:**

```yaml
    tmdb:
      api_key: "your-tmdb-api-key-here"
      enabled: true
      language: "en-US"          # Language for results (default: en-US)
      include_keywords: true     # Fetch keywords as tags (default: true, costs 1 extra API call per item)
```

### RAWG — Video Game Database (Video Games)

**Content types:** Video Games
**API key required:** Yes (free)
**Rate limit:** 5 requests per second
**Cost:** Free

RAWG provides detailed video game metadata. It's particularly good at matching games even with messy titles (edition suffixes, trademark symbols, and DLC indicators are automatically cleaned before searching). It provides:

- Genres
- Tags (up to 20 per game)
- Description
- Developer and publisher
- Platforms
- RAWG rating and Metacritic score
- ESRB rating
- Playtime estimates
- Franchise/series info with release-order positioning

**Getting your API key:**

1. Go to [rawg.io/apidocs](https://rawg.io/apidocs)
2. Click **Get API Key**
3. Create a free account
4. Your API key will be displayed on your dashboard

**Setup:**

```yaml
enrichment:
  enabled: true
  providers:
    rawg:
      api_key: "your-rawg-api-key-here"
      enabled: true
```

## Full Configuration Example

Here's a complete enrichment configuration with all three providers enabled:

```yaml
enrichment:
  enabled: true
  auto_enrich_on_sync: true    # Recommended: auto-enrich after every sync
  batch_size: 50               # Items processed per batch

  providers:
    tmdb:
      api_key: "your-tmdb-api-key-here"
      enabled: true

    openlibrary:
      enabled: true

    rawg:
      api_key: "your-rawg-api-key-here"
      enabled: true
```

**Tip:** Set up enrichment *before* your first data import and enable `auto_enrich_on_sync`. That way, every time you sync a data source, enrichment runs automatically — no extra step needed.

## Running Enrichment

### From the CLI

```bash
# Enrich all unenriched items
python3.11 -m src.cli enrichment start

# Enrich only a specific content type
python3.11 -m src.cli enrichment start --type movie
python3.11 -m src.cli enrichment start --type tv_show
python3.11 -m src.cli enrichment start --type book
python3.11 -m src.cli enrichment start --type video_game

# Check enrichment progress
python3.11 -m src.cli enrichment status
```

### From the Web Interface

On the **Data** page, the **Metadata Enrichment** section shows your current enrichment coverage with a breakdown by provider. Click the enrichment button to start a new enrichment run.

### Auto-Enrichment

Set `auto_enrich_on_sync: true` in your config to automatically queue enrichment after every data sync. This is convenient if you want a hands-off workflow — sync your sources and enrichment runs immediately after.

## Troubleshooting

### "No providers for [content type]"

This means no enrichment provider is enabled for that content type. Check your config:

- Books need `openlibrary.enabled: true`
- Movies and TV shows need `tmdb.enabled: true` (with a valid API key)
- Video games need `rawg.enabled: true` (with a valid API key)

### Items showing as "not found"

Some items may not be found in the provider databases. This is normal — niche or very new content may not have entries yet. You can retry not-found items later (the data may have been added upstream) by running enrichment with the retry flag.

### API key errors

If you see authentication errors:

- **TMDB:** Verify your key at [themoviedb.org/settings/api](https://www.themoviedb.org/settings/api). Make sure you're using the v3 API key, not the v4 access token.
- **RAWG:** Verify your key at [rawg.io/apidocs](https://rawg.io/apidocs). Free tier keys work fine.

### Rate limiting

The enrichment system has built-in rate limiting per provider to stay within API limits. If you have a very large library (thousands of items), the initial enrichment run may take some time — this is normal. Subsequent runs only process new unenriched items.

### Enrichment seems slow

OpenLibrary is rate-limited to 1 request per second to be a polite API consumer. If you have hundreds of books, this step will naturally take a few minutes. TMDB (40 req/s) and RAWG (5 req/s) are significantly faster.