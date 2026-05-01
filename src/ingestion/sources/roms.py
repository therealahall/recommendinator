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
"""

from __future__ import annotations

import fnmatch
import hashlib
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.ingestion.plugin_base import (
    ConfigField,
    ProgressCallback,
    SourceError,
    SourcePlugin,
)
from src.ingestion.sources._rom_title import (
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
                    "one game."
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
                    "Avoid unbounded repetition that could backtrack "
                    "catastrophically on long titles."
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
            path = Path(path_str).expanduser()
            if not path.exists():
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
        scan_roots = [Path(str(p)).expanduser() for p in config["paths"]]
        for root in scan_roots:
            if not root.exists():
                raise SourceError(self.name, f"Scan path not found: {root}")
            if not root.is_dir():
                raise SourceError(self.name, f"Scan path is not a directory: {root}")

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
