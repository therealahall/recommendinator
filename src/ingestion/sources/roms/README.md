# ROM Library

Imports a video game library from a directory of ROM files (e.g. an EmulationStation or RetroArch library).

## Content type
- `video_game`

## Requirements
- A local directory containing ROM files and/or per-game folders.

## Configuration

Add it from the **Data** tab with **+ Add source**, or create it from the CLI:

```bash
python3.11 -m src.cli source create roms roms
```

Then set the scan paths and the optional filters from the source's panel in the
**Data** tab. All but `paths` are list fields that refine the built-in defaults —
see the table below for what each one does.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `paths` | list[str] | yes | One or more directories to scan, each under an allowed source root. Each direct child (folder, or file with a matching extension) becomes one game. |
| `include_extensions` | list[str] | no | Extensions added to the built-in ROM extension list. Leading dot optional; case-insensitive. |
| `exclude_extensions` | list[str] | no | Extensions removed from the built-in list. |
| `exclude_names` | list[str] | no | Glob patterns matched against file or folder names to skip. Hidden dotfiles are always skipped. |
| `extra_strip_patterns` | list[str] | no | Extra Python regex patterns appended to the title cleaner. Avoid unbounded repetition that could backtrack catastrophically. |

Allowed roots default to `inputs/` and are set in `config.yaml` — see
[SECURITY.md](../../../../docs/SECURITY.md#where-file-imports-may-read).

## Notes
- Title cleanup (`Game (USA) [!].zip` → `Game`) is performed by the [`_rom_title`](_rom_title.py) helper.
- Both top-level ROM files and per-game subdirectories are recognized.
- Items are imported as `unread`.

## Development
- Implementation: [`roms.py`](roms.py) (with [`_rom_title.py`](_rom_title.py) helper)
- Tests: [`test_roms.py`](test_roms.py), [`test_rom_title.py`](test_rom_title.py)
- Plugin class: `RomScannerPlugin`
