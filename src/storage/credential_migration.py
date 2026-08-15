"""Auto-migrate sensitive credentials from config file to encrypted DB storage."""

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
    """Drop *entry*'s plaintext secrets without writing any of them back."""
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
    """Migrate sensitive credentials from config to the database.

    For each enabled source in ``config["inputs"]``, looks up the plugin's
    config schema and migrates any ``sensitive=True`` fields that have a
    non-empty value in config but no existing readable DB entry.

    Also purges stale credentials that exist in the DB but can't be
    decrypted (e.g., after an encryption key change), then re-encrypts
    from the config value if available.

    **A source with a ``source_configs`` row is skipped**, its file-held
    secrets discarded rather than read. Otherwise clearing a secret through
    ``DELETE /api/sync/sources/{id}/secret/{key}`` — what an operator does
    before repointing a source at a new host — would be undone by the next
    reload re-seeding it from the file, handing the secret to that host.

    Reading secrets from ``config.yaml`` is a **deprecated** legacy path kept so
    existing installs keep working: a ``sensitive=True`` field found in the file
    logs a deprecation warning telling the user to delete it. Nothing writes
    secrets to the file any more — the Add-source modal, the connect flows, and
    ``source set-secret`` all write straight to encrypted storage.

    This is safe to call on every startup and on config hot-reload.

    **Mutates ``config`` in place:** once a sensitive field has been migrated to
    the database — or superseded by a credential already stored there — its
    plaintext value is removed from the in-memory config dict so it does not
    linger in ``app_state.config`` for the process lifetime.

    Args:
        config: Full application config dict (from ``load_config``).
            Mutated in place — sensitive fields are removed after migration.
        storage: StorageManager instance (provides encrypted DB access).
        user_id: User ID to associate credentials with (default 1).
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

        if storage.get_source_config(user_id, source_id) is not None:
            _discard_file_secrets(entry, plugin, source_id)
            continue

        for field in plugin.get_config_schema():
            if not field.sensitive:
                continue

            config_value = entry.get(field.name)
            has_config_value = bool(config_value and str(config_value).strip())

            # Check if a readable DB entry already exists
            existing = storage.get_credential(user_id, source_id, field.name)
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

            # Check if a stale (unreadable) row exists in the DB
            if storage.credential_row_exists(user_id, source_id, field.name):
                if has_config_value:
                    # Re-encrypt from config value (UPSERT overwrites stale row)
                    storage.save_credential(
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

            # No DB row at all — migrate from config if available
            if has_config_value:
                storage.save_credential(
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
