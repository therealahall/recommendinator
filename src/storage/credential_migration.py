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
    non-empty value in config but no existing DB entry.

    This is safe to call on every startup — it only writes when a DB entry
    is missing, so existing DB credentials are never overwritten.

    Args:
        config: Full application config dict (from ``load_config``).
        storage: StorageManager instance (provides encrypted DB access).
        user_id: User ID to associate credentials with (default 1).
    """
    registry = get_registry()
    inputs_config = config.get("inputs", {})

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
            if not config_value or not str(config_value).strip():
                continue

            # Only migrate if no DB entry exists yet
            existing = storage.get_credential(user_id, source_id, field.name)
            if existing is not None:
                continue

            storage.save_credential(user_id, source_id, field.name, str(config_value))
            logger.info("Migrated %s.%s credential to database", source_id, field.name)

            # Scrub plaintext from in-memory config so it doesn't linger
            # in app_state.config for the process lifetime
            entry.pop(field.name, None)
