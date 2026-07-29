"""ROM Library scanner plugin.

Scans configurable filesystem directories at a single depth level. Each
direct child becomes one :class:`ContentItem`:

- **File** entries are included only when their extension matches the
  effective extension allow-list (``DEFAULT_EXTENSIONS`` plus
  ``include_extensions`` minus ``exclude_extensions``).
- **Folder** entries are always included (a multi-disc folder layout like
  ``Final Fantasy VII (Disc 1)/`` containing ``.bin`` + ``.cue`` is one
  game, not two), unless an ``exclude_names`` glob skips the folder name.

Titles are run through the built-in ROM title cleaner
(``_rom_title.clean_display_title``) which strips region/language/year/
revision/disc tags and bracket noise from No-Intro / Redump / TOSEC style
filenames. Users can append additional regex strips via
``extra_strip_patterns``.

Entries are deduplicated within a single fetch by both **resolved
absolute path** (so two symlinks to the same target collapse) and
**normalized title** (so multi-disc games collapse to one item once
``(Disc N)`` is stripped). The first matching entry wins (entries are
processed in case-insensitive name order per scan root, then in
scan-root order).

Item IDs are stable SHA-256 hashes of the resolved path so re-syncs
update existing rows rather than create duplicates. The storage layer's
forward-only status progression preserves any user-set status (e.g. a
ROM marked ``completed`` in the UI keeps that status across re-syncs).

``paths`` is settable over the network (the source-config API stores
whatever the schema declares), so every configured path is resolved and
then refused unless it sits under an allowed root (see
``allowed_scan_roots``) and no component of it is dot-prefixed. The second
rule is what makes the first worth anything: the allow-list has to include
the home directory, so without it ``~/.ssh`` would be an allowed scan root
and ``id_rsa``, ``known_hosts`` and ``authorized_keys`` would come back as
game titles. What containment does not do is protect the rest of an allowed
root — any plain directory under ``$HOME`` can still be listed as "games".

``extra_strip_patterns`` is capped by count and by length, which bounds how
much regex runs per title but not how long any one pattern takes; see
``compile_extra_patterns``.
"""

from __future__ import annotations

import fnmatch
import hashlib
import logging
import os
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.ingestion.plugin_base import (
    ConfigField,
    ProgressCallback,
    SourceError,
    SourcePlugin,
)
from src.ingestion.sources.roms._rom_title import (
    clean_display_title,
    compile_extra_patterns,
    normalize_title_key,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)


# Curated ROM extension list. Lowercase, leading dot. When users add or
# remove extensions via config, the comparison is also case-insensitive.
DEFAULT_EXTENSIONS: frozenset[str] = frozenset(
    {
        # Cartridge ROMs
        ".nes",
        ".unf",
        ".unif",
        ".sfc",
        ".smc",
        ".swc",
        ".fig",
        ".gb",
        ".gbc",
        ".gba",
        ".n64",
        ".z64",
        ".v64",
        ".u64",
        ".nds",
        ".3ds",
        ".cia",
        ".smd",
        ".gen",
        ".md",
        ".bin",
        ".32x",
        ".sms",
        ".gg",
        ".pce",
        ".ws",
        ".wsc",
        ".ngp",
        ".ngc",
        ".col",
        ".int",
        ".vec",
        ".a26",
        ".a78",
        ".lnx",
        ".car",
        ".crt",
        ".d64",
        ".t64",
        ".tap",
        ".prg",
        ".cdt",
        ".dsk",
        # Disc images
        ".iso",
        ".cue",
        ".chd",
        ".gdi",
        ".cdi",
        ".img",
        ".nrg",
        ".mds",
        ".mdf",
        ".gcm",
        ".rvz",
        ".wbfs",
        ".wad",
        ".nsp",
        ".xci",
        ".nro",
        ".vpk",
        ".psv",
        ".pbp",
        # Multi-disc playlists
        ".m3u",
        # Compressed
        ".zip",
        ".7z",
        ".rar",
        ".gz",
        ".tgz",
        ".xz",
        ".zst",
    }
)


# Operator-set list of directories a ROM library may live under,
# ``os.pathsep``-separated like PATH. Read from the environment rather than
# from config or the settings table on purpose: ``paths`` itself is settable
# over the unauthenticated network API, so an allow-list stored next to it
# would be settable by the same request it is supposed to contain.
SCAN_ROOTS_ENV_VAR = "RECOMMENDINATOR_SCAN_ROOTS"

# Where ROM libraries actually live when the operator has not said otherwise:
# the user's home directory, the working directory (a relative path like
# ``inputs/roms``, and the ``/app`` bind mounts the Docker image uses), and the
# conventional mount points for external and network storage. Wide enough that
# a real library is inside one, narrow enough that ``/etc``, ``/proc``,
# ``/root`` and ``/var`` are not — those are what a caller-settable scan path
# would otherwise turn into a directory listing rendered as game titles. The
# dot-component rule in ``contained_scan_path`` covers the secrets that live
# *inside* these roots (``~/.ssh``, ``~/.aws``, ``~/.gnupg``).
_DEFAULT_SCAN_ROOT_NAMES = (
    "/mnt",
    "/media",
    "/run/media",
    "/srv",
    "/data",
    "/games",
    "/roms",
    "/Volumes",
)


def allowed_scan_roots() -> list[Path]:
    """Return the directories a configured scan path must sit under.

    ``RECOMMENDINATOR_SCAN_ROOTS`` replaces the defaults outright when set, so
    an operator with a library somewhere unusual names that directory and gets
    exactly it — no widening by accident.
    """
    configured = os.environ.get(SCAN_ROOTS_ENV_VAR, "")
    if configured.strip():
        raw_roots = [entry for entry in configured.split(os.pathsep) if entry.strip()]
    else:
        raw_roots = [str(Path.home()), str(Path.cwd()), *_DEFAULT_SCAN_ROOT_NAMES]
    return [Path(root).expanduser().resolve() for root in raw_roots]


def contained_scan_path(path_str: str) -> Path | None:
    """Resolve *path_str*, returning ``None`` if it may not be scanned.

    Resolution happens before both checks so ``..`` segments and symlinks
    cannot point a permitted-looking path at somewhere else. A resolved path is
    scannable when it sits under an allowed root *and* reaches it without
    descending through a dot-prefixed directory. Hidden directories are refused
    below the root because the roots that make the plugin usable (the home and
    working directories) are also the ones holding ``.ssh``, ``.aws``,
    ``.gnupg`` and ``.config``.

    The dot rule deliberately applies only to the part *below* the matched
    root. A dot inside the root itself is operator-chosen — it arrives from
    the environment, never from a stored source config — so a library at
    ``~/.local/share/roms`` is reachable by naming it in
    ``RECOMMENDINATOR_SCAN_ROOTS``, and running the app from a dot-prefixed
    working directory keeps working. ``~/.ssh`` is still refused under the
    default roots: the match is ``$HOME``, leaving ``.ssh`` below it.

    ``_collect_entries`` skips dot-prefixed *children* while listing a root,
    which is a different job — keeping ``.DS_Store`` out of a legitimate
    library — and stays there.

    A blank entry is refused outright. ``Path("").resolve()`` is the working
    directory, which is itself a default root, so an empty string in ``paths``
    would otherwise pass containment and silently mean "scan wherever the app
    was started from". ``"."`` still means exactly that, deliberately spelled.
    """
    if not path_str.strip():
        return None
    resolved = Path(path_str).expanduser().resolve()
    for root in allowed_scan_roots():
        if resolved != root and root not in resolved.parents:
            continue
        below = resolved.relative_to(root)
        if not any(part.startswith(".") for part in below.parts):
            return resolved
    # Every matching root reached the path through a hidden directory. Keep
    # looking rather than refusing on the first one, so listing both a home
    # directory and a dot-prefixed library beneath it accepts the library.
    return None


def _containment_error(path_str: str) -> str:
    """The validation message for a scan path the plugin refuses to read."""
    return (
        f"Scan path is not an allowed ROM directory: {path_str}. It must "
        "resolve to somewhere under an allowed root without descending "
        f"through a hidden (dot-prefixed) directory. Set {SCAN_ROOTS_ENV_VAR} "
        "(os.pathsep-separated) to the directories your library lives under — "
        "naming a hidden directory there makes it scannable."
    )


def _coerce_string_list(value: Any, field_name: str) -> tuple[list[str], str | None]:
    """Coerce a YAML value into a list of strings.

    Returns ``(values, error)``. Error is non-None when *value* is not a
    list of strings.
    """
    if value is None:
        return [], None
    if isinstance(value, str):
        return [], f"'{field_name}' must be a list, got string"
    if not isinstance(value, list):
        return [], f"'{field_name}' must be a list"
    coerced: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            return [], f"'{field_name}' entries must be strings"
        coerced.append(entry)
    return coerced, None


def _normalize_extensions(raw: list[str]) -> set[str]:
    """Lowercase and ensure each extension begins with a single leading dot."""
    normalized: set[str] = set()
    for ext in raw:
        cleaned = ext.strip().lower()
        if not cleaned:
            continue
        if not cleaned.startswith("."):
            cleaned = f".{cleaned}"
        normalized.add(cleaned)
    return normalized


def _effective_extensions(include: list[str], exclude: list[str]) -> set[str]:
    """Compute the active extension set: defaults + include - exclude."""
    return (set(DEFAULT_EXTENSIONS) | _normalize_extensions(include)) - (
        _normalize_extensions(exclude)
    )


def _matches_any_glob(name: str, patterns: list[str]) -> bool:
    """True when *name* matches any glob in *patterns* (case-sensitive)."""
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def _entry_id(absolute_path: Path) -> str:
    """Build a stable, unique ContentItem ID from an absolute path."""
    digest = hashlib.sha256(str(absolute_path).encode("utf-8")).hexdigest()
    return f"rom:{digest[:16]}"


def _safe_size_bytes(path: Path) -> int | None:
    """Return ``path.stat().st_size`` or ``None`` if stat fails.

    Wrapper exists so the size lookup is independently testable and so
    flaky-mount stat failures don't abort the surrounding scan.
    """
    try:
        return path.stat().st_size
    except OSError as error:
        logger.warning(
            "Failed to read size for %s: %s; skipping size_bytes", path, error
        )
        return None


class RomScannerPlugin(SourcePlugin):
    """Scan local directories for emulator ROMs and game files.

    Each direct child (file matching the active extension set, or any
    directory) becomes one :class:`ContentItem`. Titles are cleaned with
    a built-in ROM title cleaner; users can extend the cleanup with
    ``extra_strip_patterns``.
    """

    @property
    def name(self) -> str:
        return "roms"

    @property
    def display_name(self) -> str:
        return "ROM Library"

    @property
    def description(self) -> str:
        return "Scan local directories for emulator ROMs and game files"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.VIDEO_GAME]

    @property
    def requires_api_key(self) -> bool:
        return False

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="paths",
                field_type=list,
                required=True,
                description=(
                    "List of directory paths to scan. Each direct child "
                    "(folder, or file with a matching extension) becomes "
                    "one game. Each path must sit under an allowed scan root "
                    "and contain no hidden (dot-prefixed) directory."
                ),
            ),
            ConfigField(
                name="include_extensions",
                field_type=list,
                required=False,
                default=[],
                description=(
                    "Extensions added to the built-in ROM extension list "
                    "(e.g. ['.exe'] to also include Windows installers). "
                    "Leading dot optional; case-insensitive."
                ),
            ),
            ConfigField(
                name="exclude_extensions",
                field_type=list,
                required=False,
                default=[],
                description=(
                    "Extensions removed from the built-in list "
                    "(e.g. ['.tgz'] if your stash has tgz archives that "
                    "are not games). Leading dot optional; case-insensitive."
                ),
            ),
            ConfigField(
                name="exclude_names",
                field_type=list,
                required=False,
                default=[],
                description=(
                    "Glob patterns matched against entry names (files or "
                    "folders) to skip — useful for emulator junk folders "
                    "like 'scripts' or 'mlc01'. Hidden dotfiles are always "
                    "skipped."
                ),
            ),
            ConfigField(
                name="extra_strip_patterns",
                field_type=list,
                required=False,
                default=[],
                description=(
                    "Optional Python regex patterns appended to the "
                    "built-in title cleaner via re.sub. Useful for "
                    "stripping site-specific tags the defaults miss. "
                    "At most 10 patterns of 200 characters each. Nothing "
                    "limits how long a pattern runs, so avoid nested "
                    "quantifiers like (a+)+ — they backtrack exponentially "
                    "on a title that does not match."
                ),
            ),
        ]

    def validate_config(
        self,
        config: dict[str, Any],
        storage: StorageManager | None = None,
        user_id: int = 1,
    ) -> list[str]:
        errors: list[str] = []

        paths_raw = config.get("paths")
        if paths_raw is None:
            errors.append("'paths' is required")
            return errors

        paths, paths_error = _coerce_string_list(paths_raw, "paths")
        if paths_error is not None:
            errors.append(paths_error)
            return errors
        if not paths:
            errors.append("'paths' must contain at least one directory")

        for path_str in paths:
            if not path_str.strip():
                # Named separately from the containment message: an empty entry
                # is a typo, not an attempt to reach somewhere disallowed, and
                # "scan path is not an allowed ROM directory: " reads as noise.
                errors.append("'paths' entries must not be empty")
                continue
            # Containment is checked before existence so a path outside the
            # allowed roots is never confirmed to exist (or not) to whoever
            # supplied it.
            path = contained_scan_path(path_str)
            if path is None:
                errors.append(_containment_error(path_str))
            elif not path.exists():
                errors.append(f"Scan path not found: {path_str}")
            elif not path.is_dir():
                errors.append(f"Scan path is not a directory: {path_str}")

        for field_name in ("include_extensions", "exclude_extensions", "exclude_names"):
            _, error = _coerce_string_list(config.get(field_name), field_name)
            if error is not None:
                errors.append(error)

        extra_raw, extra_error = _coerce_string_list(
            config.get("extra_strip_patterns"), "extra_strip_patterns"
        )
        if extra_error is not None:
            errors.append(extra_error)
        else:
            try:
                compile_extra_patterns(extra_raw)
            except ValueError as error:
                errors.append(f"Invalid 'extra_strip_patterns' entry: {error}")

        return errors

    def fetch(
        self,
        config: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> Iterator[ContentItem]:
        # Re-checked here, not just in validate_config: creating a source over
        # the API stores the value without ever calling validate_config, so
        # this is the gate a stored path actually passes through.
        scan_roots: list[Path] = []
        for raw_path in config["paths"]:
            root = contained_scan_path(str(raw_path))
            if root is None:
                raise SourceError(self.name, _containment_error(str(raw_path)))
            if not root.exists():
                raise SourceError(self.name, f"Scan path not found: {root}")
            if not root.is_dir():
                raise SourceError(self.name, f"Scan path is not a directory: {root}")
            scan_roots.append(root)

        active_extensions = _effective_extensions(
            config.get("include_extensions", []),
            config.get("exclude_extensions", []),
        )
        exclude_names = list(config.get("exclude_names", []))
        try:
            extra_patterns = compile_extra_patterns(
                config.get("extra_strip_patterns", [])
            )
        except ValueError as error:
            raise SourceError(
                self.name, f"Invalid 'extra_strip_patterns' entry: {error}"
            ) from error

        source = self.get_source_identifier(config)
        seen_paths: set[Path] = set()
        seen_titles: set[str] = set()
        candidates = _collect_entries(scan_roots, exclude_names)
        total = len(candidates)
        logger.info(
            "Found %d ROM candidates across %d scan roots", total, len(scan_roots)
        )

        count = 0
        for index, entry in enumerate(candidates):
            try:
                absolute = entry.resolve()
                is_file = entry.is_file()
                is_dir = entry.is_dir()
            except OSError as error:
                logger.warning("Failed to stat %s: %s; skipping entry", entry, error)
                continue

            if absolute in seen_paths:
                continue
            seen_paths.add(absolute)

            # Dangling symlinks (or other non-file, non-dir entries) report
            # is_file=False and is_dir=False without raising. Skip them — a
            # broken link should not surface as a phantom item.
            if not is_file and not is_dir:
                logger.debug("Skipping non-file, non-dir entry %s", absolute)
                continue

            if is_file and entry.suffix.lower() not in active_extensions:
                continue

            raw_stem = entry.stem if is_file else entry.name
            title = clean_display_title(raw_stem, extra_patterns)
            if not title:
                continue

            normalized = normalize_title_key(title)
            if normalized in seen_titles:
                logger.debug("Skipping duplicate title %r at %s", title, absolute)
                continue
            seen_titles.add(normalized)

            metadata: dict[str, Any] = {
                "path": str(absolute),
                "is_directory": is_dir,
                "parent_dir": entry.parent.name,
            }
            if is_file:
                size = _safe_size_bytes(entry)
                if size is not None:
                    metadata["size_bytes"] = size

            if progress_callback:
                progress_callback(index + 1, total, title)

            yield ContentItem(
                id=_entry_id(absolute),
                title=title,
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
                rating=None,
                metadata=metadata,
                source=source,
            )
            count += 1

        logger.info("Imported %d items from ROM scan", count)


def _collect_entries(scan_roots: list[Path], exclude_names: list[str]) -> list[Path]:
    """Collect direct children of each scan root in deterministic order.

    Hidden dotfiles (names starting with ``.``) are always skipped. Entries
    whose name matches any glob in *exclude_names* are also skipped.
    """
    entries: list[Path] = []
    for root in scan_roots:
        try:
            children = sorted(root.iterdir(), key=lambda entry: entry.name.lower())
        except OSError as error:
            logger.warning("Failed to read scan root %s: %s", root, error)
            continue
        for child in children:
            if child.name.startswith("."):
                continue
            if _matches_any_glob(child.name, exclude_names):
                continue
            entries.append(child)
    return entries
