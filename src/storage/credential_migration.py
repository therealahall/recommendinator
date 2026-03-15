"""Auto-migrate sensitive credentials from config file to encrypted DB storage."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.ingestion.registry import get_registry

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)


def migrate_config_credentials(
    config: dict[str, Any],
    storage: StorageManager,
    user_id: int = 1,
) -> None:
    """Migrate sensitive credentials from config to the database.

    For each enabled source in ``config["inputs"]``, looks up the plugin's
    config schema and migrates any ``sensitive=True`` fields that have a
    non-empty value in config but no existing readable DB entry.

    Also purges stale credentials that exist in the DB but can't be
    decrypted (e.g., after an encryption key change), then re-encrypts
    from the config value if available.

    This is safe to call on every startup and on config hot-reload.

    **Mutates ``config`` in place:** after a credential is migrated to the
    database, its plaintext value is removed from the in-memory config dict
    so it does not linger in ``app_state.config`` for the process lifetime.

    Args:
        config: Full application config dict (from ``load_config``).
            Mutated in place — sensitive fields are removed after migration.
        storage: StorageManager instance (provides encrypted DB access).
        user_id: User ID to associate credentials with (default 1).
    """
    registry = get_registry()
    inputs_config = config.get("inputs", {})

    if not inputs_config:
        logger.debug("No inputs in config, skipping credential migration")
        return

    for source_id, entry in inputs_config.items():
        if not isinstance(entry, dict):
            continue

        plugin_name = entry.get("plugin")
        if not plugin_name:
            continue

        plugin = registry.get_plugin(plugin_name)
        if plugin is None:
            continue

        for field in plugin.get_config_schema():
            if not field.sensitive:
                continue

            config_value = entry.get(field.name)
            has_config_value = bool(config_value and str(config_value).strip())

            # Check if a readable DB entry already exists
            existing = storage.get_credential(user_id, source_id, field.name)
            if existing is not None:
                # DB credential is readable — nothing to do
                continue

            # Check if a stale (unreadable) row exists in the DB
            if storage.credential_row_exists(user_id, source_id, field.name):
                if has_config_value:
                    # Re-encrypt from config value (UPSERT overwrites stale row)
                    storage.save_credential(
                        user_id, source_id, field.name, str(config_value)
                    )
                    logger.info(
                        "Re-encrypted stale %s.%s credential from config",
                        source_id,
                        field.name,
                    )
                    entry.pop(field.name, None)
                else:
                    # Stale row with no config fallback — purge it
                    storage.delete_credential(user_id, source_id, field.name)
                    logger.warning(
                        "Purged unreadable %s.%s credential from database "
                        "(encryption key changed, no config value to re-encrypt from)",
                        source_id,
                        field.name,
                    )
                continue

            # No DB row at all — migrate from config if available
            if has_config_value:
                storage.save_credential(
                    user_id, source_id, field.name, str(config_value)
                )
                logger.info(
                    "Migrated %s.%s credential to database",
                    source_id,
                    field.name,
                )
                entry.pop(field.name, None)
