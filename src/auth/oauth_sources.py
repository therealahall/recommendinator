"""The two questions an OAuth route asks about a source id.

Which plugin runs it, and whether a token under it may be revoked. Written out
per provider the copies drifted, one reading back whatever row the id named.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.sources.service import resolve_input_for_plugin, resolve_source_plugin

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)

#: The one credential key these routes read or delete: disconnecting must not
#: take the source's client credentials with it.
REFRESH_TOKEN_KEY = "refresh_token"


def may_revoke(
    plugin_name: str,
    source_id: str,
    config: dict[str, Any] | None,
    storage: StorageManager | None,
    user_id: int = 1,
) -> bool:
    """Whether *plugin_name* may delete *source_id*'s stored token.

    Only a source running another plugin puts an id out of reach: that row is
    what its sync reads. Refusing an id no source claims would leave the
    credential undeletable.
    """
    owner = resolve_source_plugin(source_id, config, storage, user_id)
    return owner is None or owner.name == plugin_name


@dataclass(frozen=True)
class OAuthSourceBinding:
    """How one OAuth plugin's sources are resolved and their tokens stored."""

    plugin_name: str
    display_name: str
    error_class: type[Exception]

    def resolve(
        self,
        config: dict[str, Any],
        storage: StorageManager | None,
        source_id: str,
        user_id: int,
    ) -> dict[str, Any] | None:
        """*source_id*'s sync-ready config, unless it runs another plugin.

        Reads the database as well as ``inputs``, so a source added from the
        Data tab is found too.
        """
        resolved = resolve_input_for_plugin(
            source_id, self.plugin_name, config, storage, user_id
        )
        return resolved.config if resolved is not None else None

    def is_enabled(
        self,
        config: dict[str, Any],
        storage: StorageManager | None,
        source_id: str,
        user_id: int,
    ) -> bool:
        """Whether *source_id* is an enabled source on this plugin."""
        return self.resolve(config, storage, source_id, user_id) is not None

    def has_token(
        self,
        config: dict[str, Any],
        storage: StorageManager | None,
        source_id: str,
        user_id: int,
    ) -> bool:
        """Whether *source_id* holds a token this plugin could revoke.

        The stored row, not the resolved value: disconnect deletes rows.
        Startup moves a file-held token into one, unless the source already had
        a row — then it is discarded.
        """
        return (
            storage is not None
            and may_revoke(self.plugin_name, source_id, config, storage, user_id)
            and storage.credential_row_exists(user_id, source_id, REFRESH_TOKEN_KEY)
        )

    def save_token(
        self,
        storage: StorageManager,
        refresh_token: str,
        source_id: str,
        user_id: int,
    ) -> None:
        """Encrypt and store the refresh token under *source_id*."""
        try:
            storage.save_credential(
                user_id, source_id, REFRESH_TOKEN_KEY, refresh_token
            )
            logger.info("Saved %s refresh token to database", self.display_name)
        except Exception as error:
            logger.error(
                "Failed to save %s token to database: %s",
                self.display_name,
                type(error).__name__,
            )
            raise self.error_class(
                f"Failed to save {self.display_name} token"
            ) from error
