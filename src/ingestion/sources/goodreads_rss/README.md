# Goodreads (Public Shelves via RSS)

Syncs books from a **public** Goodreads profile via the per-shelf RSS feeds.
No CSV export is required — the plugin reads your shelves directly over the
network.

## Content type
- `book`

## Requirements
- A **public** Goodreads profile. If your profile is private, Goodreads returns
  an empty feed and nothing is imported.
- Your Goodreads numeric user ID (see below). No API key or login is needed.

## Configuration

```yaml
inputs:
  goodreads_rss:
    plugin: goodreads_rss
    user_id: "12345"          # numeric ID or full profile URL
    shelves:
      - "read"
      - "currently-reading"
      - "to-read"
    content_type: "book"
    enabled: false
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `user_id` | str | yes | Goodreads numeric user ID or public profile URL. |
| `shelves` | list | no | Shelves to sync (default: `read`, `currently-reading`, `to-read`). |

## Finding your user ID
Open your Goodreads profile in a browser. The URL looks like
`https://www.goodreads.com/user/show/12345-your-name` — the number (`12345`) is
your user ID. You can paste either the bare number or the whole URL into
`user_id`; a `https://www.goodreads.com/review/list/12345` URL works too.

## Notes
- Reads `title`, `author_name`, `isbn`, `book_id`, `num_pages` (also the nested
  `<book><num_pages>`), `user_rating`, `average_rating`, `book_published`,
  `user_read_at`, and `book_description`. Missing or empty fields are tolerated.
- Metadata keys: `book_id`, `isbn`, `pages`, and `year_published` are shared
  with the [goodreads_csv](../goodreads_csv/README.md) plugin; this plugin also
  carries `average_rating`, `description`, and `shelf` (the shelf the item was
  found on). `isbn13` and `publisher` are **not** provided — Goodreads RSS does
  not expose them, so those keys are absent rather than empty.
- Status mapping: `read` → completed, `currently-reading` → currently consuming,
  `to-read` and any custom shelf → unread.
- Rating: `user_rating` of `0` means unrated (stored as no rating); `1`–`5` are
  kept as-is.
- Deduplication: the three default shelves are mutually exclusive, but custom
  shelves can overlap them. A book appearing on more than one requested shelf is
  imported once with the strongest status
  (completed > currently consuming > unread).
- Each shelf feed is paginated (`per_page=100`); the plugin walks every page
  until one comes back empty, so large shelves import in full.

## Development
- Implementation: [`goodreads_rss.py`](goodreads_rss.py)
- Tests: [`test_goodreads_rss.py`](test_goodreads_rss.py)
- Plugin class: `GoodreadsRssPlugin`
