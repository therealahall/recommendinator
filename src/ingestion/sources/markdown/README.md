# Markdown Import

Imports content items from a single Markdown file using a prescriptive list-per-section format.

## Content types
- `book`, `movie`, `tv_show`, `video_game` (one type per import — set via `content_type` config field)

## Requirements
- A `.md` file with `## Status` section headings and `- **Title** by Creator | metadata` list items.

## Importing

This is a one-shot file import, not a syncable `inputs:` source. Upload the file
from the web **Data** tab (**Import from file**) or run:

```bash
python3.11 -m src.cli import --source markdown_import --file /path/to/library.md --content-type book
```

| Option | Required | Description |
|--------|----------|-------------|
| `content_type` | yes | One of: `book`, `movie`, `tv_show`, `video_game`. |

## File format

```markdown
## Completed
- **Project Hail Mary** by Andy Weir | Rating: 5 | Date: 2024-06-15
- **Dune** by Frank Herbert | Rating: 5

## In Progress
- **The Three-Body Problem** by Liu Cixin

## To Read
- **Hyperion** by Dan Simmons
```

Recognized section headings (case-insensitive): `Completed`, `In Progress`, `Currently Reading`/`Watching`/`Playing`, `To Read`/`Watch`/`Play`, `Wishlist`, `Backlog`. The metadata tail after `|` accepts `key: value` pairs (e.g. `Rating: 5`, `Date: 2024-06-15`).

## Development
- Implementation: [`markdown.py`](markdown.py)
- Tests: [`test_markdown.py`](test_markdown.py)
- Plugin class: `MarkdownImportPlugin`
