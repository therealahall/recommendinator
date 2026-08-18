# Markdown Import

Reads a Markdown file of one content type, which you pick when you upload it.

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

Recognized section headings, matched case-insensitively on the keyword:
`Completed`, `In Progress`, `Currently Reading`/`Watching`/`Playing`, `To
Read`/`Watch`/`Play`, `Wishlist`, `Backlog`. An unrecognized heading leaves the
running status alone. The metadata tail after `|` takes `key: value` pairs;
`Rating` and `Date` are read, and the rest is stored as metadata.

## Skipped lines

Reported with the line they were on. Prose, headings and blank lines are not
rows and are not reported — only a line that starts as a list item:

| Reason | What it means |
|---|---|
| `list item has no **Title**` | The bullet does not match `- **Title** by Creator`. |
| `no title` | The bold section is empty. |

## Development

- Implementation: [`markdown.py`](markdown.py)
- Tests: [`test_markdown.py`](test_markdown.py)
- Importer class: `MarkdownImporter`, named `markdown_import`
