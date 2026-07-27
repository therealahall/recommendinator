# Markdown Import

Imports content items from a single Markdown file using a prescriptive list-per-section format.

## Content types
- `book`, `movie`, `tv_show`, `video_game` (one type per import — set via `content_type` config field)

## Requirements
- A `.md` file with `## Status` section headings and `- **Title** by Creator | metadata` list items.

## Configuration

```bash
python3.11 -m src.cli source create my_md markdown_import
python3.11 -m src.cli source set my_md path /path/to/library.md
python3.11 -m src.cli source set my_md content_type book   # or movie, tv_show, video_game
```

Or add it from the **Data** tab with **+ Add source**. The id (`my_md` above) is
yours to choose, so you can have several Markdown sources for different files.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | str | yes | Path to the Markdown file. |
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
