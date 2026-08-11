"""One resolution rule for every OAuth provider's sources and tokens.

Written out per provider the copies drifted: two resolved the source before
answering, Trakt's read the credential row under whatever id it was handed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.web.sync_sources import is_nonempty_secret_value, resolve_input_for_plugin

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)


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
        *,
        require_enabled: bool = True,
    ) -> dict[str, Any] | None:
        """*source_id*'s sync-ready config, unless it runs another plugin.

        Reads the database as well as ``inputs``, so a source added from the
        Data tab is found too.
        """
        resolved = resolve_input_for_plugin(
            source_id,
            self.plugin_name,
            config,
            storage,
            user_id,
            require_enabled=require_enabled,
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
        """Whether *source_id* holds a refresh token, enabled or not.

        Asks the resolved config, which layers the stored secret over the
        ``inputs`` entry as the sync does. A disabled source answers too: its
        token is there to be revoked.
        """
        resolved = self.resolve(
            config, storage, source_id, user_id, require_enabled=False
        )
        return resolved is not None and is_nonempty_secret_value(
            resolved.get("refresh_token")
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
            storage.save_credential(user_id, source_id, "refresh_token", refresh_token)
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
