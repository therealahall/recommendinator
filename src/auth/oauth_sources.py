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
    """Refusing an id no source claims would leave the credential undeletable."""
    owner = resolve_source_plugin(source_id, config, storage, user_id)
    return owner is None or owner.name == plugin_name


@dataclass(frozen=True)
class OAuthSourceBinding:
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
        """Reads the database as well as ``inputs``, so a source added from the
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
        return self.resolve(config, storage, source_id, user_id) is not None

    def has_token(
        self,
        config: dict[str, Any],
        storage: StorageManager | None,
        source_id: str,
        user_id: int,
    ) -> bool:
        """The stored row, not the resolved value: disconnect deletes rows."""
        return (
            storage is not None
            and may_revoke(self.plugin_name, source_id, config, storage, user_id)
            and storage.credentials.exists(user_id, source_id, REFRESH_TOKEN_KEY)
        )

    def save_token(
        self,
        storage: StorageManager,
        refresh_token: str,
        source_id: str,
        user_id: int,
    ) -> None:
        try:
            storage.credentials.save(
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
