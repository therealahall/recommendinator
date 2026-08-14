"""Auto-migrate stored source labels, plugin names and item attribution."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)

# The default/example ingestion block was renamed from ``goodreads`` to
# ``goodreads_csv``. The value stored in ``content_items.source`` is the
# user's config-block KEY, not the plugin name, so this migration matches the
# literal historical key ``goodreads`` and rewrites only that exact value.
# Arbitrary user-chosen keys are intentionally left untouched.
_OLD_SOURCE = "goodreads"
_NEW_SOURCE = "goodreads_csv"

# The plugin itself was renamed from ``goodreads`` to ``goodreads_csv``. A
# source config moved into the database stores the PLUGIN NAME in
# ``source_configs.plugin``; the values coincide with the source labels above
# but name a different concept, so they get their own constants.
_OLD_PLUGIN = "goodreads"
_NEW_PLUGIN = "goodreads_csv"


def _count_items(storage: StorageManager, source: str, user_id: int) -> int:
    """How many of *user_id*'s items carry *source* as their label."""
    with storage.connection() as conn:
        cursor = conn.execute(
            "SELECT COUNT(*) FROM content_items WHERE source = ? AND user_id = ?",
            (source, user_id),
        )
        count: int = cursor.fetchone()[0]
    return count


def _relabel_items(
    storage: StorageManager,
    old_source: str,
    new_source: str,
    user_id: int,
) -> int:
    """Move *user_id*'s items from one source label to another; count the rows."""
    with storage.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE content_items SET source = ? WHERE source = ? AND user_id = ?",
            (new_source, old_source, user_id),
        )
        updated: int = cursor.rowcount
        if updated:
            conn.commit()
    return updated


def migrate_source_labels(
    storage: StorageManager,
    user_id: int = 1,
) -> None:
    """Relabel stored ``goodreads`` source values to ``goodreads_csv``.

    Updates every ``content_items`` row whose ``source`` is the literal
    historical key ``goodreads`` so it reflects the renamed default ingestion
    block ``goodreads_csv``.

    This is safe to call on every startup: once the rows are relabeled the
    UPDATE matches nothing and the call is a silent no-op.

    Args:
        storage: StorageManager instance (provides SQLite access).
        user_id: User ID whose items are relabeled (default 1), matching the
            single-user scope of the credential migration.
    """
    updated = _relabel_items(storage, _OLD_SOURCE, _NEW_SOURCE, user_id)
    if updated:
        logger.info(
            "Relabeled %d content item(s) from source %r to %r",
            updated,
            _OLD_SOURCE,
            _NEW_SOURCE,
        )


def migrate_source_config_plugins(
    storage: StorageManager,
    user_id: int = 1,
) -> None:
    """Relabel stored ``goodreads`` plugin values to ``goodreads_csv``.

    Updates every ``source_configs`` row whose ``plugin`` is the historical
    plugin name ``goodreads`` so a source config a user moved into the database
    keeps resolving after the plugin rename. Without this, once a
    ``plugin = 'goodreads'`` row exists ``get_plugin('goodreads')`` returns
    ``None`` and that source silently stops syncing.

    This is safe to call on every startup: once the rows are relabeled the
    UPDATE matches nothing and the call is a silent no-op.

    Args:
        storage: StorageManager instance (provides SQLite access).
        user_id: User ID whose source configs are relabeled (default 1),
            matching the single-user scope of ``migrate_source_labels`` and the
            user-scoped ``source_configs`` table.
    """
    with storage.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE source_configs SET plugin = ? WHERE plugin = ? AND user_id = ?",
            (_NEW_PLUGIN, _OLD_PLUGIN, user_id),
        )
        updated = cursor.rowcount
        if updated:
            conn.commit()
            logger.info(
                "Relabeled %d source config(s) from plugin %r to %r",
                updated,
                _OLD_PLUGIN,
                _NEW_PLUGIN,
            )


def configured_source_plugins(
    config: dict[str, Any],
    storage: StorageManager,
    user_id: int,
) -> dict[str, str]:
    """Map every configured source id to its plugin, disabled ones included.

    A disabled source still owns items and still makes its plugin ambiguous,
    so leaving it out would let a sibling claim its rows.
    """
    sources: dict[str, str] = {}
    inputs = config.get("inputs")
    if isinstance(inputs, dict):
        for source_id, entry in inputs.items():
            if isinstance(entry, dict) and entry.get("plugin"):
                sources[str(source_id)] = str(entry["plugin"])
    # A DB row wins over YAML for the same id, as ``resolve_inputs`` has it.
    for row in storage.list_source_configs(user_id):
        sources[row["source_id"]] = row["plugin"]
    return sources


def _attribution_migration_name(user_id: int) -> str:
    """Name the attribution pass records itself under, once it is finished.

    The pass is user-scoped while the table is not, so the id is part of the
    name.
    """
    return f"source_attribution.user_{user_id}"


@dataclass(frozen=True)
class _Refusal:
    """One reason the pass declines to move a plugin's rows.

    The record key and the log level both follow from the kind, so a new branch
    cannot declare half of one — and only a resolvable kind reruns the pass.
    """

    token: str
    resolvable: bool

    @property
    def level(self) -> int:
        """A warning demands an action; a refusal with no remedy is a note."""
        return logging.WARNING if self.resolvable else logging.INFO


_NAMESAKE_RUNS_ITS_PLUGIN = _Refusal("namesake", resolvable=False)
_SHARED_BY_SIBLINGS = _Refusal("shared", resolvable=True)
_NAMESAKE_IS_RENAMEABLE = _Refusal("renameable", resolvable=True)


def _attribution_refusal_name(user_id: int, plugin_name: str, kind: _Refusal) -> str:
    """Name a refusal is recorded under, so it is said once.

    Kept apart from the completion record, and keyed by kind: a config edit can
    swap one refusal for another, and a plugin-only key would suppress the
    new remedy.
    """
    return f"{_attribution_migration_name(user_id)}.refused.{plugin_name}.{kind.token}"


def _is_recorded(storage: StorageManager, name: str) -> bool:
    """Whether *name* has already been written to ``completed_migrations``."""
    with storage.connection() as conn:
        cursor = conn.execute(
            "SELECT 1 FROM completed_migrations WHERE name = ?", (name,)
        )
        return cursor.fetchone() is not None


def _record(storage: StorageManager, name: str) -> None:
    """Write *name* to ``completed_migrations``, at most once."""
    with storage.connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO completed_migrations (name) VALUES (?)", (name,)
        )
        conn.commit()


def _refuse_once(
    storage: StorageManager,
    user_id: int,
    plugin_name: str,
    kind: _Refusal,
    message: str,
    *args: object,
) -> bool:
    """Report a plugin's refusal once, and never again.

    Repeating it every boot nags about a library the operator may have looked
    at and left alone. Returns whether the config can still resolve it, which
    the silence must not change.
    """
    refusal_name = _attribution_refusal_name(user_id, plugin_name, kind)
    if not _is_recorded(storage, refusal_name):
        logger.log(kind.level, message, *args)
        _record(storage, refusal_name)
    return kind.resolvable


def migrate_source_attribution(
    config: dict[str, Any],
    storage: StorageManager,
    user_id: int = 1,
) -> None:
    """Move items stored under a plugin name onto the source that owns them.

    Six plugins once dropped ``_source_id``. Ambiguity is never guessed, but two
    refusals name a config change that resolves it, so this reruns until then.
    """
    migration_name = _attribution_migration_name(user_id)
    if _is_recorded(storage, migration_name):
        return

    sources = configured_source_plugins(config, storage, user_id)
    if not sources:
        # ``create_storage_manager`` defaults to the real ``data/`` database
        # whichever config was loaded, so a sourceless example.yaml would
        # retire the migration having read nothing. Only that case: a foreign
        # config with ``inputs`` still relabels rows there.
        return

    resolvable_refusal = False

    owners: dict[str, list[str]] = {}
    for source_id, plugin_name in sources.items():
        owners.setdefault(plugin_name, []).append(source_id)

    for plugin_name, source_ids in sorted(owners.items()):
        if source_ids == [plugin_name]:
            continue
        # Counted before anything is said, so the refusals below stay quiet for
        # a user who has no stranded items at all.
        stranded = _count_items(storage, plugin_name, user_id)
        if not stranded:
            continue

        namesake_plugin = sources.get(plugin_name)
        if namesake_plugin == plugin_name:
            # The one refusal this configuration cannot talk its way out of, so
            # it does not hold the completion record open.
            resolvable_refusal |= _refuse_once(
                storage,
                user_id,
                plugin_name,
                _NAMESAKE_RUNS_ITS_PLUGIN,
                "Leaving %d content item(s) under plugin %r: the source named "
                "after it runs it, so its own rows are spelled the same way as "
                "its siblings'. No rename separates them.",
                stranded,
                plugin_name,
            )
        elif len(source_ids) > 1:
            # Ordered ahead of the rename advice below, which a namesake source
            # running some other plugin can only half-follow: renaming it leaves
            # these siblings sharing the plugin and the rows still unattributed.
            resolvable_refusal |= _refuse_once(
                storage,
                user_id,
                plugin_name,
                _SHARED_BY_SIBLINGS,
                "Leaving %d content item(s) under plugin %r: %d sources share "
                "it (%s) and nothing records which one each item came from. "
                "Remove all but one to separate them.",
                stranded,
                plugin_name,
                len(source_ids),
                ", ".join(repr(source_id) for source_id in sorted(source_ids)),
            )
        elif namesake_plugin is not None:
            # The remedy is a config edit, not a split: nothing relabels
            # ``content_items`` on a rename, so the sibling inherits the
            # namesake source's own rows too until its next sync reclaims them.
            resolvable_refusal |= _refuse_once(
                storage,
                user_id,
                plugin_name,
                _NAMESAKE_IS_RENAMEABLE,
                "Leaving %d content item(s) under plugin %r: a source is named "
                "after it but runs %r, so its own rows are spelled the same "
                "way. Renaming it hands every one of these rows to %r, the "
                "renamed source's included, until its next sync relabels "
                "those still upstream.",
                stranded,
                plugin_name,
                namesake_plugin,
                source_ids[0],
            )
        else:
            source_id = source_ids[0]
            moved = _relabel_items(storage, plugin_name, source_id, user_id)
            logger.info(
                "Re-attributed %d content item(s) from plugin name %r to source %r",
                moved,
                plugin_name,
                source_id,
            )

    if not resolvable_refusal:
        _record(storage, migration_name)
