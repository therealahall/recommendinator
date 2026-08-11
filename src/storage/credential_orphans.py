"""Credentials stranded under a plugin name: report them, never move one.

A release before per-source ids filed rotated tokens there. Sources sharing a
plugin cannot be told apart, and a misattributed token fails where a reconnect
works.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.storage.source_migration import configured_source_plugins
from src.utils.text import sanitize_for_log

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)


def _credential_keys(storage: StorageManager, user_id: int, source_id: str) -> set[str]:
    """Which credential keys *source_id* holds, decrypting none of them.

    A stranded row can predate the current encryption key, so reading its value
    would fail on exactly the installs that need the advice.
    """
    with storage.connection() as conn:
        cursor = conn.execute(
            "SELECT credential_key FROM credentials WHERE user_id = ? AND source_id = ?",
            (user_id, source_id),
        )
        return {row[0] for row in cursor.fetchall()}


def _a_namesake_source_owns_the_row(
    storage: StorageManager,
    plugin_name: str,
    config: dict[str, Any] | None,
    user_id: int,
) -> bool:
    """Whether a configured source is itself called *plugin_name*.

    It reads the row under its own id, so neither path may call it a leftover.
    """
    return plugin_name in configured_source_plugins(config or {}, storage, user_id)


def warn_about_orphaned_credentials(
    storage: StorageManager,
    plugin_name: str,
    source_id: str,
    user_id: int = 1,
) -> None:
    """Tell the operator to reconnect *source_id* when a token is stranded.

    A copy under *source_id* proves nothing: these refresh tokens are
    single-use, so after a rotation the source's own copy is the spent one.
    """
    if source_id == plugin_name:
        return
    # No config file reaches the sync executor, so only a DB-backed namesake
    # is visible here.
    if _a_namesake_source_owns_the_row(storage, plugin_name, None, user_id):
        return

    stranded = sorted(_credential_keys(storage, user_id, plugin_name))
    if not stranded:
        return

    logger.warning(
        "Source '%s' cannot read the %s filed under the plugin name '%s' by a "
        "release that stored credentials per plugin, and it is left there "
        "because a plugin's sources cannot be told apart. Any copy this source "
        "does hold may be the spent half of a single-use rotation. Reconnect "
        "'%s' to store a live one where this source reads it.",
        sanitize_for_log(source_id),
        ", ".join(sanitize_for_log(key) for key in stranded),
        sanitize_for_log(plugin_name),
        sanitize_for_log(source_id),
    )


def delete_orphaned_credentials(
    storage: StorageManager,
    plugin_name: str,
    config: dict[str, Any],
    user_id: int = 1,
) -> None:
    """Drop the rows under *plugin_name* once no configured source can own them.

    Call after the deleted source's own row is gone, so *config* and the
    database together answer who is left.
    """
    if _a_namesake_source_owns_the_row(storage, plugin_name, config, user_id):
        return
    # Two sources sharing the plugin keep the row: this delete was asked about
    # one of them, and nothing records which rotated that token.
    if plugin_name in configured_source_plugins(config, storage, user_id).values():
        return

    deleted = storage.delete_credentials_for_source(user_id, plugin_name)
    if deleted:
        logger.info(
            "Deleted %d credential(s) stranded under plugin name '%s': no "
            "configured source uses that plugin any more.",
            deleted,
            sanitize_for_log(plugin_name),
        )
