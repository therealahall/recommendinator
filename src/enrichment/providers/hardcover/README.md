# Hardcover Enrichment Provider

States where a book sits in its series, using the [Hardcover](https://hardcover.app) GraphQL API. It writes no other metadata.

## Content types
- `book`

## Requirements
- A Hardcover personal access token from https://hardcover.app/account/api.

## Configuration

Set these from the **Settings** page (Enrichment section), or the CLI:

```bash
uv run python -m src.cli settings set enrichment.providers.hardcover.enabled true

# The token is a secret: hidden prompt, stored encrypted, never in a file
uv run python -m src.cli settings set-secret enrichment.providers.hardcover.api_key
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `enabled` | bool | yes | Whether the provider participates in enrichment. |
| `api_key` | str | yes (sensitive) | Hardcover personal access token. |

## Behavior
- Writes `series_position` only, at the `authored` authority, off the `position`
  Hardcover's `featured_book_series` states. The series name stays whatever the
  provider that matched the book called it. A book Hardcover holds in no series,
  or in one with no stated position, gets nothing.
- Matches on an ISBN where the item carries one, otherwise on title plus author.
  Merged duplicate records are filtered out, and a title still matching two books
  is refused rather than guessed: an `authored` position replaces a title marker,
  and nothing later corrects a wrong one.
- Held to one request a second, inside the free tier's 60 a minute.

## Development
- Implementation: [`hardcover.py`](hardcover.py)
- Tests: [`test_hardcover.py`](test_hardcover.py)
- Provider class: `HardcoverProvider`
