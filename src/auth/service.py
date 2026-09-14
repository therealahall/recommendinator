"""Connecting a source's account, for any plugin declaring an ``OAuthFlow``.
Both interfaces call these, so they cannot answer differently.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.ingestion.plugin_base import (
    CodePasteFlow,
    DeviceAuthorization,
    DeviceCodeFlow,
    DevicePollResult,
    DevicePollStatus,
    OAuthError,
    OAuthFlow,
    SourcePlugin,
)
from src.ingestion.registry import get_registry
from src.sources.service import resolve_input_for_plugin, resolve_source_plugin
from src.utils.text import exception_for_log, sanitize_for_log

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)

#: The one credential key these verbs write or delete: disconnecting must not
#: take the source's client credentials with it.
REFRESH_TOKEN_KEY = "refresh_token"


class NotConnectable(OAuthError):
    """The source is disabled, runs another plugin, lacks the setup its flow
    needs, or the plugin runs the other flow.
    """


@dataclass(frozen=True)
class OAuthPlugin:
    plugin: SourcePlugin
    flow: OAuthFlow

    @property
    def display_name(self) -> str:
        return self.plugin.display_name


def oauth_plugins() -> dict[str, OAuthPlugin]:
    """Keyed by plugin name, which the registry already holds unique."""
    return {
        name: OAuthPlugin(plugin, plugin.oauth)
        for name, plugin in get_registry().get_all_plugins().items()
        if plugin.oauth is not None
    }


def connectable_config(
    declared: OAuthPlugin,
    source_id: str,
    storage: StorageManager,
    user_id: int,
) -> dict[str, Any] | None:
    """A client-supplied id is a credential key: unchecked, one plugin's
    exchange files its token where another plugin reads one.
    """
    resolved = resolve_input_for_plugin(
        source_id, declared.plugin.name, storage, user_id
    )
    if resolved is None or not declared.flow.is_ready(resolved.config):
        return None
    return resolved.config


def connection_status(
    declared: OAuthPlugin,
    source_id: str,
    storage: StorageManager,
    user_id: int,
) -> dict[str, Any]:
    """``connected`` reads the stored row, not the resolved config: a disabled
    source keeps its token, and only this says there is something to revoke.
    """
    config = connectable_config(declared, source_id, storage, user_id)
    status: dict[str, Any] = {
        "enabled": config is not None,
        "connected": _may_revoke(declared, source_id, storage, user_id)
        and storage.credentials.exists(user_id, source_id, REFRESH_TOKEN_KEY),
    }
    if isinstance(declared.flow, CodePasteFlow):
        status["auth_url"] = (
            None if config is None else _sign_in_url(declared, declared.flow, config)
        )
    return status


def refusal_message(declared: OAuthPlugin, error: Exception) -> str:
    """The words both interfaces answer a refused connect with. A flow's own
    message may quote the service's response, so it is logged, never shown.
    """
    if isinstance(error, NotConnectable):
        return f"{declared.display_name} is not enabled or set up for that source."
    return f"{declared.display_name} authentication failed"


def sign_in_url(
    declared: OAuthPlugin,
    source_id: str,
    storage: StorageManager,
    user_id: int,
) -> str | None:
    """None where the flow could not build the link."""
    if not isinstance(declared.flow, CodePasteFlow):
        raise NotConnectable(f"{declared.plugin.name} does not take a pasted code")
    config = _config_or_refuse(declared, source_id, storage, user_id)
    return _sign_in_url(declared, declared.flow, config)


def connect_with_code(
    declared: OAuthPlugin,
    source_id: str,
    pasted: str,
    storage: StorageManager,
    user_id: int,
) -> None:
    if not isinstance(declared.flow, CodePasteFlow):
        raise NotConnectable(f"{declared.plugin.name} does not take a pasted code")
    config = _config_or_refuse(declared, source_id, storage, user_id)
    refresh_token = declared.flow.exchange_code(pasted, config)
    _save_refresh_token(declared, source_id, refresh_token, storage, user_id)


def start_device_flow(
    declared: OAuthPlugin,
    source_id: str,
    storage: StorageManager,
    user_id: int,
) -> DeviceAuthorization:
    if not isinstance(declared.flow, DeviceCodeFlow):
        raise NotConnectable(f"{declared.plugin.name} does not run a device flow")
    config = _config_or_refuse(declared, source_id, storage, user_id)
    return declared.flow.start(config)


def poll_device_flow(
    declared: OAuthPlugin,
    source_id: str,
    device_code: str,
    storage: StorageManager,
    user_id: int,
) -> DevicePollResult:
    if not isinstance(declared.flow, DeviceCodeFlow):
        raise NotConnectable(f"{declared.plugin.name} does not run a device flow")
    config = _config_or_refuse(declared, source_id, storage, user_id)
    result = declared.flow.poll(device_code, config)
    if result.status is DevicePollStatus.SUCCESS:
        _save_refresh_token(
            declared, source_id, result.refresh_token or "", storage, user_id
        )
    return result


def disconnect(
    declared: OAuthPlugin,
    source_id: str,
    storage: StorageManager,
    user_id: int,
) -> bool:
    """False both for an id this plugin may not act on and for one holding no
    token: telling them apart names sources the caller did not ask about.
    """
    return _may_revoke(declared, source_id, storage, user_id) and (
        storage.credentials.delete(user_id, source_id, REFRESH_TOKEN_KEY)
    )


def _may_revoke(
    declared: OAuthPlugin,
    source_id: str,
    storage: StorageManager,
    user_id: int,
) -> bool:
    """Refusing an id no source claims would leave the credential undeletable."""
    owner = resolve_source_plugin(source_id, storage, user_id)
    return owner is None or owner.name == declared.plugin.name


def _config_or_refuse(
    declared: OAuthPlugin,
    source_id: str,
    storage: StorageManager,
    user_id: int,
) -> dict[str, Any]:
    config = connectable_config(declared, source_id, storage, user_id)
    if config is None:
        raise NotConnectable(f"{declared.plugin.name} cannot connect that source")
    return config


def _sign_in_url(
    declared: OAuthPlugin, flow: CodePasteFlow, config: dict[str, Any]
) -> str | None:
    # A link the flow cannot build leaves Connect disabled with a remedy, where
    # raising would leave the whole panel unreadable.
    try:
        return flow.auth_url(config)
    except Exception as error:
        logger.warning(
            "Failed to build the %s sign-in link: %s",
            sanitize_for_log(declared.display_name),
            exception_for_log(error),
        )
        return None


def _save_refresh_token(
    declared: OAuthPlugin,
    source_id: str,
    refresh_token: str,
    storage: StorageManager,
    user_id: int,
) -> None:
    if not refresh_token.strip():
        raise OAuthError(f"{declared.plugin.name} returned no refresh token")
    try:
        storage.credentials.save(
            user_id, source_id, REFRESH_TOKEN_KEY, refresh_token.strip()
        )
    except Exception as error:
        logger.error(
            "Failed to save %s token to database: %s",
            sanitize_for_log(declared.display_name),
            type(error).__name__,
        )
        raise OAuthError(f"Failed to save {declared.plugin.name} token") from error
    logger.info(
        "Saved %s refresh token to database", sanitize_for_log(declared.display_name)
    )
