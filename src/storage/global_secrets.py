"""Relocate global provider secrets from plaintext config into encrypted storage.

Global settings secrets — registry leaves flagged ``sensitive=True`` (today
``enrichment.providers.tmdb.api_key`` and ``enrichment.providers.rawg.api_key``)
— must never be persisted in plaintext: not in ``config.yaml`` and not in the
``settings`` table. Instead they live in the pre-existing encrypted
``credentials`` table, addressed by a reserved ``settings:`` ``source_id``
namespace so they never collide with user-defined ingestion sources.

**Key scheme for the (user_id, source_id, credential_key) triple:**

* ``user_id`` — :data:`GLOBAL_SECRET_USER_ID`, the default/primary user, mirroring
  :func:`src.storage.credential_migration.migrate_config_credentials`.
* ``source_id`` — ``"settings:<dotted-parent>"`` (e.g.
  ``"settings:enrichment.providers.tmdb"``). The ``settings:`` prefix reserves a
  namespace that real ingestion source ids (``gog``, ``my_steam``, …) never use.
* ``credential_key`` — the leaf segment (e.g. ``"api_key"``).

The pair is derivable uniformly from any dotted registry key via
:func:`secret_ref`, so the enrichment layer and the later settings UI/CLI all
address the same secret the same way.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.settings.metadata import all_entries
from src.utils.dotted_path import get_leaf, pop_leaf

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)

# The default/primary user that owns global secrets, matching the default used
# by ``credential_migration.migrate_config_credentials``.
GLOBAL_SECRET_USER_ID = 1

# Reserved ``source_id`` prefix that namespaces global settings secrets away
# from user-defined ingestion source ids in the shared ``credentials`` table.
_SETTINGS_SOURCE_PREFIX = "settings:"


def secret_ref(key: str) -> tuple[str, str]:
    """Map a dotted registry key to its ``(source_id, credential_key)`` pair.

    e.g. ``"enrichment.providers.tmdb.api_key"`` →
    ``("settings:enrichment.providers.tmdb", "api_key")``.

    Args:
        key: Dotted registry leaf key (must contain at least one dot).

    Returns:
        The ``(source_id, credential_key)`` credentials-table coordinates.

    Raises:
        ValueError: If *key* is not a dotted key.
    """
    parent, _, leaf = key.rpartition(".")
    if not parent:
        raise ValueError(f"Global secret key must be dotted, got {key!r}")
    return f"{_SETTINGS_SOURCE_PREFIX}{parent}", leaf


def read_secret(storage: StorageManager, key: str) -> str | None:
    """Return the decrypted global secret for *key*, or ``None`` if unset.

    Enrichment-only read path: the settings UI/CLI must use the write-only
    ``StorageManager.set_global_secret`` / ``clear_global_secret`` /
    ``has_global_secret`` surface rather than reading plaintext back out.

    Args:
        storage: StorageManager providing encrypted credential access.
        key: Dotted registry leaf key.

    Returns:
        Decrypted plaintext secret, or ``None`` when not stored.
    """
    source_id, credential_key = secret_ref(key)
    return storage.get_credential(GLOBAL_SECRET_USER_ID, source_id, credential_key)


def migrate_config_secrets(
    config: dict[str, Any],
    storage: StorageManager,
    user_id: int = GLOBAL_SECRET_USER_ID,
) -> None:
    """Sweep global settings secrets from plaintext config into credentials.

    For each registry leaf flagged ``sensitive=True``, a non-empty value in
    *config* is encrypted into the ``credentials`` table (under the reserved
    ``settings:`` namespace, see :func:`secret_ref`) and then stripped from the
    in-memory config so no plaintext secret lingers in ``app_state.config``.

    Precedence and idempotency mirror
    :func:`src.storage.credential_migration.migrate_config_credentials`:

    * A readable DB secret is authoritative and is never clobbered by a stale
      YAML copy; the duplicate plaintext is stripped from config regardless.
    * A stale (undecryptable) row is re-encrypted from config when a value is
      present, otherwise left intact for the operator to recover.

    Safe to call on every startup and on config hot-reload. **Mutates *config*
    in place.**

    Args:
        config: Full application config dict (from ``load_config``). Mutated in
            place — sensitive leaves are removed after migration.
        storage: StorageManager instance (provides encrypted DB access).
        user_id: User ID to associate secrets with (default 1).
    """
    for entry in all_entries():
        if not entry.sensitive:
            continue

        parts = tuple(entry.key.split("."))
        source_id, credential_key = secret_ref(entry.key)

        config_value = get_leaf(config, parts)
        has_config_value = bool(config_value and str(config_value).strip())

        # A readable DB secret already exists — it wins. Drop any duplicate or
        # stale plaintext copy so it does not linger in the running config.
        existing = storage.get_credential(user_id, source_id, credential_key)
        if existing is not None:
            pop_leaf(config, parts)
            continue

        # A stale (unreadable) row exists — re-encrypt from config if we can,
        # otherwise leave it for the operator to recover. Never delete it.
        if storage.credential_row_exists(user_id, source_id, credential_key):
            if has_config_value:
                storage.save_credential(
                    user_id, source_id, credential_key, str(config_value)
                )
                logger.info(
                    "Re-encrypted stale global secret %s from config", entry.key
                )
                pop_leaf(config, parts)
            else:
                logger.warning(
                    "Cannot decrypt global secret %s in database "
                    "(encryption key changed?). Re-save it via the settings "
                    "UI/CLI to recover.",
                    entry.key,
                )
            continue

        # No DB row at all — migrate from config when a value is present.
        if has_config_value:
            storage.save_credential(
                user_id, source_id, credential_key, str(config_value)
            )
            logger.info("Migrated global secret %s to database", entry.key)
            pop_leaf(config, parts)
