"""Global settings secrets — registry leaves flagged ``sensitive=True`` (today
the ``api_key`` of the tmdb, rawg and hardcover enrichment providers) — must
never be persisted in plaintext: not in ``config.yaml`` and not in the
``settings`` table.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.storage.credentials import CredentialStore

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

#: The default/primary user that owns global secrets.
GLOBAL_SECRET_USER_ID = 1

# Reserved ``source_id`` prefix that namespaces global settings secrets away
# from user-defined ingestion source ids in the shared ``credentials`` table.
_SETTINGS_SOURCE_PREFIX = "settings:"


def secret_ref(key: str) -> tuple[str, str]:
    parent, _, leaf = key.rpartition(".")
    if not parent:
        raise ValueError(f"Global secret key must be dotted, got {key!r}")
    return f"{_SETTINGS_SOURCE_PREFIX}{parent}", leaf


class SecretStore:
    """Write-only on purpose: the settings UI and CLI may set, clear and test for
    a secret, and only enrichment reads one back, via :func:`read_secret`.
    """

    def __init__(self, credentials: CredentialStore) -> None:
        self._credentials = credentials

    def set(self, key: str, value: str) -> None:
        source_id, credential_key = secret_ref(key)
        self._credentials.save(GLOBAL_SECRET_USER_ID, source_id, credential_key, value)

    def clear(self, key: str) -> bool:
        source_id, credential_key = secret_ref(key)
        return self._credentials.delete(
            GLOBAL_SECRET_USER_ID, source_id, credential_key
        )

    def has(self, key: str) -> bool:
        source_id, credential_key = secret_ref(key)
        return self._credentials.exists(
            GLOBAL_SECRET_USER_ID, source_id, credential_key
        )


def read_secret(storage: StorageManager, key: str) -> str | None:
    """The enrichment-only read path: everything else goes through the write-only
    :class:`SecretStore` rather than reading plaintext back out.
    """
    source_id, credential_key = secret_ref(key)
    return storage.credentials.get(GLOBAL_SECRET_USER_ID, source_id, credential_key)
