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
| `paths` | list[str] | yes | One or more directories to scan. Each direct child (folder, or file with a matching extension) becomes one game. Each path must be non-empty, must sit under an allowed root, and must reach it without descending through a hidden (dot-prefixed) directory — see below. |
| `include_extensions` | list[str] | no | Extensions added to the built-in ROM extension list. Leading dot optional; case-insensitive. |
| `exclude_extensions` | list[str] | no | Extensions removed from the built-in list. |
| `exclude_names` | list[str] | no | Glob patterns matched against file or folder names to skip. Hidden dotfiles are always skipped. |
| `extra_strip_patterns` | list[str] | no | Extra Python regex patterns appended to the title cleaner. At most 10, 200 characters each. Nothing bounds how long a pattern runs — see [What is and is not bounded](#what-is-and-is-not-bounded). |

## Where a library may live

`paths` is settable over the API, and the app has no authentication, so a scan
path is resolved (following `..` and symlinks) and then refused unless it sits
under an allowed root **and** reaches that root without descending through a
hidden (dot-prefixed) directory. Anything else is refused before the directory
is read, which keeps a request from turning `/etc` or `/root` into a list of
"games".

The hidden-directory rule is what makes the root list worth anything. The list
has to include your home directory for the plugin to be usable at all, and
without the second rule `~/.ssh` would be a perfectly legal scan root — the
scanner would list `id_rsa`, `known_hosts` and `authorized_keys` as games.
`~/.aws`, `~/.gnupg` and everything under `~/.config` are the same shape.

The rule covers the part below the root, not the root itself. A root only ever
comes from `RECOMMENDINATOR_SCAN_ROOTS`, so a dot in one is your own choice
rather than something a request can set. Name `~/.local/share/roms` there and it
scans; `~/.ssh` still will not, because that matches `$HOME` with `.ssh` below
it.

The defaults cover where libraries actually live: your home directory, the
working directory the app runs from (so a relative `inputs/roms` and the `/app`
mounts in Docker work), and `/mnt`, `/media`, `/run/media`, `/srv`, `/data`,
`/games`, `/roms`, `/Volumes`.

If your library is somewhere else, set `RECOMMENDINATOR_SCAN_ROOTS` in the
environment to the directories it lives under, separated by the platform path
separator (`:` on Linux and macOS). It replaces the defaults rather than adding
to them, so list **every** root you need — naming only the new one stops every
other `roms` source from scanning, including one under your home directory:

```bash
RECOMMENDINATOR_SCAN_ROOTS=/storage/roms:/opt/games python3.11 -m src.web
```

It is read from the environment on purpose. A setting stored in the database or
`config.yaml` would be editable by the same unauthenticated request it is meant
to contain.

## What is and is not bounded

Containment decides which directories may be opened. It does not protect the
rest of an allowed root: any plain directory under `$HOME` can still be listed
as "games", so allowing a root means accepting that.

Nor does it follow through to what the scan *records*. A symlinked child inside
an allowed root is resolved, and its resolved target path and byte size are
written into the item's `metadata` — so an entry inside an allowed root can
publish a path outside every allowed root. Containment gates the root, not the
contents.

An empty string in `paths` is rejected. `Path("").resolve()` is the working
directory, which is itself a default root, so a blank entry would otherwise pass
containment and quietly mean "scan wherever the app was started from". `"."`
still means exactly that — it just has to be spelled deliberately.

`extra_strip_patterns` is capped at 10 patterns of 200 characters. That bounds
how much regex runs against each title; it does not bound how long a pattern
takes. Python's `re` has no execution timeout, and there is no cheap check that
tells a safe pattern from one that backtracks exponentially — `(a+)+` is five
characters, and `.*.*.*.*x` has no group at all — so patterns are compiled as
written. The consequence is worse than a slow scan: the match does not end when
the scan does, and a Python thread cannot be cancelled, so the sync worker
running it is lost until the process restarts and every later sync runs with one
fewer worker. Bounding it for real would mean running the match somewhere
killable (a subprocess with a timeout) or using a backtracking-free engine such
as RE2; neither is in place today.

## Notes
- Title cleanup (`Game (USA) [!].zip` → `Game`) is performed by the [`_rom_title`](_rom_title.py) helper.
- Both top-level ROM files and per-game subdirectories are recognized.
- Items are imported as `unread`.

## Development
- Implementation: [`roms.py`](roms.py) (with [`_rom_title.py`](_rom_title.py) helper)
- Tests: [`test_roms.py`](test_roms.py), [`test_rom_title.py`](test_rom_title.py)
- Plugin class: `RomScannerPlugin`
