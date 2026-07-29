# Markdown Import

Imports content items from a single Markdown file using a prescriptive list-per-section format. This is a **one-shot file import**, not a source: the file is read once and nothing about it is stored, so import it again whenever you update it.

## Content types
- `book`, `movie`, `tv_show`, `video_game` (one type per import, set via the `content_type` option)

## Requirements
- A `.md` file with `## Status` section headings and `- **Title** by Creator | metadata` list items.
- UTF-8 encoded. A file saved with a byte-order mark works as is.

## Import

Open the **Data** tab, click **Import from file**, pick **Markdown Import**, choose
the file, and select a content type. Or from the CLI:

```bash
python3.11 -m src.cli import --source markdown_import --file /path/to/library.md --content-type book
```

Import one file per content type. Web uploads are capped at 50 MB, and the CLI has
no cap.

| Option | Type | Required | Description |
|--------|------|----------|-------------|
| `content_type` | str | yes | One of: `book`, `movie`, `tv_show`, `video_game`. |

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
