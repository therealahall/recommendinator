"""What a source is called, decided here for every surface that names one."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.ingestion.importers.registry import get_importer
from src.ingestion.registry import get_registry
from src.utils.text import humanize_source_id

if TYPE_CHECKING:
    from src.storage.manager import StorageManager
    from src.storage.schema import SourceConfigDict, SourceConfigRow


def _declared_name(name: str) -> str | None:
    plugin = get_registry().get_plugin(name)
    if plugin is not None:
        return plugin.display_name
    importer = get_importer(name)
    return importer.display_name if importer is not None else None


def source_label(
    source_id: str, named: str | None = None, plugin: str | None = None
) -> str:
    """*named* and *plugin* are the configured source's columns, ``None`` for an
    id no configured source carries."""
    if named:
        return named
    owner = plugin or source_id
    declared = _declared_name(owner)
    rest = source_id.removeprefix(owner)
    if declared is None or rest == source_id:
        return humanize_source_id(source_id)
    if not rest:
        return declared
    # A second account on a plugin keeps the part of its id that tells it apart.
    if rest[0] in "_-":
        return f"{declared} {humanize_source_id(rest[1:])}"
    return humanize_source_id(source_id)


def label_for_row(
    source_id: str, row: SourceConfigRow | SourceConfigDict | None
) -> str:
    if row is None:
        return source_label(source_id)
    return source_label(source_id, row["display_name"], row["plugin"])


def label_for_source(storage: StorageManager, source_id: str, user_id: int = 1) -> str:
    return label_for_row(source_id, storage.sources.get(user_id, source_id))
