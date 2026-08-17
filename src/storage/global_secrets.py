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
from src.storage.credentials import CredentialStore
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

    Raises:
        ValueError: If *key* is not a dotted key.
    """
    parent, _, leaf = key.rpartition(".")
    if not parent:
        raise ValueError(f"Global secret key must be dotted, got {key!r}")
    return f"{_SETTINGS_SOURCE_PREFIX}{parent}", leaf


class SecretStore:
    """The sensitive registry leaves. ``StorageManager.secrets``.

    Write-only on purpose: the settings UI and CLI may set, clear and test for
    a secret, and only enrichment reads one back, via :func:`read_secret`.
    """

    def __init__(self, credentials: CredentialStore) -> None:
        self._credentials = credentials

    def set(self, key: str, value: str) -> None:
        """Encrypt and store the secret *key* names."""
        source_id, credential_key = secret_ref(key)
        self._credentials.save(GLOBAL_SECRET_USER_ID, source_id, credential_key, value)

    def clear(self, key: str) -> bool:
        """Delete a stored secret, reporting whether there was one."""
        source_id, credential_key = secret_ref(key)
        return self._credentials.delete(
            GLOBAL_SECRET_USER_ID, source_id, credential_key
        )

    def has(self, key: str) -> bool:
        """Report whether a secret is stored, without decrypting it."""
        source_id, credential_key = secret_ref(key)
        return self._credentials.exists(
            GLOBAL_SECRET_USER_ID, source_id, credential_key
        )


def read_secret(storage: StorageManager, key: str) -> str | None:
    """Return the decrypted global secret for *key*, or ``None`` if unset.

    The enrichment-only read path: everything else goes through the write-only
    :class:`SecretStore` rather than reading plaintext back out.
    """
    source_id, credential_key = secret_ref(key)
    return storage.credentials.get(GLOBAL_SECRET_USER_ID, source_id, credential_key)


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
        existing = storage.credentials.get(user_id, source_id, credential_key)
        if existing is not None:
            if has_config_value:
                # The stored secret wins and the file value is DISCARDED, not
                # migrated. Say so precisely: telling the user it was saved
                # would invite them to delete a value that was never persisted.
                logger.warning(
                    "DEPRECATED: '%s' is set in config.yaml, but an encrypted "
                    "secret already exists and takes precedence — the file "
                    "value is IGNORED, not migrated. To change it use "
                    "'settings set-secret %s', then delete it from config.yaml.",
                    entry.key,
                    entry.key,
                )
            pop_leaf(config, parts)
            continue

        # A stale (unreadable) row exists — re-encrypt from config if we can,
        # otherwise leave it for the operator to recover. Never delete it.
        if storage.credentials.exists(user_id, source_id, credential_key):
            if has_config_value:
                storage.credentials.save(
                    user_id, source_id, credential_key, str(config_value)
                )
                logger.warning(
                    "DEPRECATED: '%s' is set in config.yaml. It has been "
                    "re-encrypted into the database (replacing an undecryptable "
                    "row) and removed from the running config — you can now "
                    "delete it from config.yaml. A future release will stop "
                    "reading secrets from the file.",
                    entry.key,
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

        if has_config_value:
            storage.credentials.save(
                user_id, source_id, credential_key, str(config_value)
            )
            logger.warning(
                "DEPRECATED: '%s' is set in config.yaml. It has been moved to "
                "encrypted storage and removed from the running config — you "
                "can now delete it from config.yaml. A future release will stop "
                "reading secrets from the file.",
                entry.key,
            )
            pop_leaf(config, parts)
