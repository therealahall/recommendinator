from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.ingestion.plugin_base import SourcePlugin
from src.ingestion.registry import get_registry

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)


def _discard_file_secrets(
    entry: dict[str, Any], plugin: SourcePlugin, source_id: str
) -> None:
    for field in plugin.get_config_schema():
        if not field.sensitive:
            continue
        if entry.pop(field.name, None):
            logger.warning(
                "DEPRECATED: '%s.%s' is set in config.yaml and is IGNORED — "
                "this source is managed in the database. Change it with "
                "'source set-secret %s %s', then delete it from config.yaml.",
                source_id,
                field.name,
                source_id,
                field.name,
            )


def migrate_config_credentials(
    config: dict[str, Any],
    storage: StorageManager,
    user_id: int = 1,
) -> None:
    """**A source with a ``source_configs`` row is skipped**, its file-held
    secrets discarded rather than read. **Mutates ``config`` in place.**
    """
    registry = get_registry()
    inputs_config = config.get("inputs")

    if not isinstance(inputs_config, dict) or not inputs_config:
        # A list-shaped ``inputs:`` is truthy and has no ``.items()``. Raising
        # here would abort every verb at boot, the read-only ones an operator
        # would diagnose it with included.
        logger.debug("No usable inputs in config, skipping credential migration")
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

        if storage.sources.get(user_id, source_id) is not None:
            _discard_file_secrets(entry, plugin, source_id)
            continue

        for field in plugin.get_config_schema():
            if not field.sensitive:
                continue

            config_value = entry.get(field.name)
            has_config_value = bool(config_value and str(config_value).strip())

            existing = storage.credentials.get(user_id, source_id, field.name)
            if existing is not None:
                if has_config_value:
                    # The stored credential wins and the file value is DISCARDED,
                    # not migrated. Say so precisely: telling the user it was
                    # saved would invite them to delete a value that was never
                    # persisted — losing it, unrecoverably for an OAuth token.
                    logger.warning(
                        "DEPRECATED: '%s.%s' is set in config.yaml, but an "
                        "encrypted credential already exists and takes "
                        "precedence — the file value is IGNORED, not migrated. "
                        "To change it use 'source set-secret %s %s', then delete "
                        "it from config.yaml.",
                        source_id,
                        field.name,
                        source_id,
                        field.name,
                    )
                # Drop the duplicate plaintext copy so it does not linger in
                # app_state.config for the lifetime of the process.
                entry.pop(field.name, None)
                continue

            if storage.credentials.exists(user_id, source_id, field.name):
                if has_config_value:
                    storage.credentials.save(
                        user_id, source_id, field.name, str(config_value)
                    )
                    logger.warning(
                        "DEPRECATED: '%s.%s' is set in config.yaml. It has been "
                        "re-encrypted into the database (replacing an "
                        "undecryptable row) and removed from the running config "
                        "— you can now delete it from config.yaml. A future "
                        "release will stop reading secrets from the file.",
                        source_id,
                        field.name,
                    )
                    entry.pop(field.name, None)
                else:
                    # Stale row with no config fallback — leave it alone.
                    # Never silently delete credentials; the user may be able
                    # to fix the encryption key and recover the value.
                    logger.warning(
                        "Cannot decrypt %s.%s credential in database "
                        "(encryption key changed?). Fix the key file or "
                        "reconnect via the web UI to re-save the credential.",
                        source_id,
                        field.name,
                    )
                continue

            if has_config_value:
                storage.credentials.save(
                    user_id, source_id, field.name, str(config_value)
                )
                logger.warning(
                    "DEPRECATED: '%s.%s' is set in config.yaml. It has been "
                    "moved to encrypted storage and removed from the running "
                    "config — you can now delete it from config.yaml. A future "
                    "release will stop reading secrets from the file.",
                    source_id,
                    field.name,
                )
                entry.pop(field.name, None)
