from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from src.ingestion import registry as registry_module
from src.ingestion.plugin_base import (
    CodePasteFlow,
    ConfigField,
    DeviceAuthorization,
    DeviceCodeFlow,
    DevicePollResult,
    DevicePollStatus,
    OAuthError,
    SourcePlugin,
)
from src.ingestion.registry import PluginRegistry
from src.models.content import ConsumptionStatus, ContentItem, ContentType


class FakeFilePlugin(SourcePlugin):
    @property
    def name(self) -> str:
        return "fake_file"

    @property
    def display_name(self) -> str:
        return "Fake File"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.BOOK]

    @property
    def requires_api_key(self) -> bool:
        return False

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="path",
                field_type=str,
                required=True,
                description="Path to data file",
            ),
            ConfigField(
                name="content_type",
                field_type=str,
                required=False,
                default="book",
                description="Content type for items",
            ),
        ]

    def validate_config(self, config: dict[str, Any], **kwargs: Any) -> list[str]:
        if not config.get("path"):
            return ["'path' is required"]
        return []

    def fetch(self, config: dict[str, Any]) -> Iterator[ContentItem]:
        yield ContentItem(
            id="x",
            title="Stub",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            source=self.get_source_identifier(config),
        )


class FakeApiPlugin(SourcePlugin):
    @property
    def name(self) -> str:
        return "fake_api"

    @property
    def display_name(self) -> str:
        return "Fake API"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.VIDEO_GAME]

    @property
    def requires_api_key(self) -> bool:
        return True

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="api_key",
                field_type=str,
                required=True,
                sensitive=True,
                default="placeholder-key",
                description="API key",
            ),
            ConfigField(
                name="user_id",
                field_type=str,
                required=False,
                default="",
                description="User identifier",
            ),
            ConfigField(
                name="min_minutes",
                field_type=int,
                required=False,
                default=0,
                description="Minimum minutes",
            ),
            ConfigField(
                name="tags",
                field_type=list,
                required=False,
                default=[],
                description="Category filters",
            ),
            ConfigField(
                name="active",
                field_type=bool,
                required=False,
                default=False,
                description="Active toggle",
            ),
        ]

    def validate_config(self, config: dict[str, Any], **kwargs: Any) -> list[str]:
        return []

    def fetch(self, config: dict[str, Any]) -> Iterator[ContentItem]:
        yield ContentItem(
            id="g",
            title="Stub",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            source=self.get_source_identifier(config),
        )


FAKE_AUTH_URL = "https://fake-service.example/sign-in"
FAKE_CODE = "the-code-the-fake-service-shows"
FAKE_REFRESH_TOKEN = "fake-refresh-token"
FAKE_AUTHORIZATION = DeviceAuthorization(
    device_code="fake-device-code",
    user_code="FAKE-1234",
    verification_url="https://fake-service.example/activate",
    expires_in=600,
    interval=5,
)


class FakeCodePasteFlow(CodePasteFlow):
    code_help = "Paste the code the fake service shows:"

    def auth_url(self, config: dict[str, Any]) -> str:
        return FAKE_AUTH_URL

    def exchange_code(self, pasted: str, config: dict[str, Any]) -> str:
        if pasted != FAKE_CODE:
            raise OAuthError("the fake service refused that code")
        return FAKE_REFRESH_TOKEN


class FakeDeviceCodeFlow(DeviceCodeFlow):
    setup_hint = "Save a client ID before you can connect."

    def is_ready(self, config: dict[str, Any]) -> bool:
        return bool(config.get("client_id"))

    def start(self, config: dict[str, Any]) -> DeviceAuthorization:
        return FAKE_AUTHORIZATION

    def poll(self, device_code: str, config: dict[str, Any]) -> DevicePollResult:
        if device_code != FAKE_AUTHORIZATION.device_code:
            raise OAuthError("the fake service does not know that device code")
        return DevicePollResult(DevicePollStatus.SUCCESS, FAKE_REFRESH_TOKEN)


class _FakeAccountPlugin(SourcePlugin):
    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.VIDEO_GAME]

    @property
    def requires_api_key(self) -> bool:
        return True

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="client_id",
                field_type=str,
                required=False,
                default="",
                description="Client ID",
            )
        ]

    def validate_config(self, config: dict[str, Any], **kwargs: Any) -> list[str]:
        return []

    def fetch(self, config: dict[str, Any]) -> Iterator[ContentItem]:
        yield from ()


class FakeCodePastePlugin(_FakeAccountPlugin):
    oauth = FakeCodePasteFlow()

    @property
    def name(self) -> str:
        return "fake_paste"

    @property
    def display_name(self) -> str:
        return "Fake Paste"


class FakeDevicePlugin(_FakeAccountPlugin):
    oauth = FakeDeviceCodeFlow()

    @property
    def name(self) -> str:
        return "fake_device"

    @property
    def display_name(self) -> str:
        return "Fake Device"


FAILED_PLUGIN_MODULE = "goodreads_rss"
FAILED_PLUGIN_REASON = "ModuleNotFoundError: No module named 'nonesuch'"
UNLOADED_PLUGIN = "goodreads_rss_shelves"

UNLOADED_PLUGIN_DETAIL = (
    "Plugin 'goodreads_rss_shelves' is not loaded. Modules that failed to "
    "import: goodreads_rss: ModuleNotFoundError: No module named 'nonesuch'"
)


@pytest.fixture()
def registry_with_source_fakes() -> Iterator[None]:
    registry = PluginRegistry.get_instance()
    registry._discovered = True
    registry._plugins.clear()
    registry._import_errors.clear()
    registry.register(FakeFilePlugin())
    registry.register(FakeApiPlugin())
    yield
    PluginRegistry.reset_instance()


@pytest.fixture()
def registry_with_oauth_fakes() -> Iterator[None]:
    """Only fakes, so no connect path can lean on a real service's code."""
    registry = PluginRegistry.get_instance()
    registry._discovered = True
    registry._plugins.clear()
    registry._import_errors.clear()
    registry.register(FakeFilePlugin())
    registry.register(FakeCodePastePlugin())
    registry.register(FakeDevicePlugin())
    yield
    PluginRegistry.reset_instance()


@pytest.fixture()
def registry_with_a_failed_import() -> Iterator[None]:
    """``tests/test_registry.py`` proves a raising module lands here."""
    registry = PluginRegistry.get_instance()
    registry._discovered = True
    registry._plugins.clear()
    registry._import_errors.clear()
    registry.register(FakeFilePlugin())
    registry.register(FakeApiPlugin())
    registry._import_errors[FAILED_PLUGIN_MODULE] = FAILED_PLUGIN_REASON
    yield
    PluginRegistry.reset_instance()


BROKEN_PRIVATE_MODULE = "broken_provider"
BROKEN_PRIVATE_REASON = "ModuleNotFoundError: no module named 'httpx'"


def _private_module_names() -> list[str]:
    return [
        name
        for name in list(sys.modules)
        if name == "private" or name.startswith("private.")
    ]


@pytest.fixture()
def private_plugins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """The scan reads the project root off ``registry.py``, three levels down."""
    private_path = tmp_path / "private" / "plugins"
    private_path.mkdir(parents=True)
    (private_path.parent / "__init__.py").write_text("")
    (private_path / "__init__.py").write_text("")
    monkeypatch.setattr(
        registry_module,
        "__file__",
        str(tmp_path / "src" / "ingestion" / "registry.py"),
    )
    for name in _private_module_names():
        monkeypatch.delitem(sys.modules, name)
    importlib.invalidate_caches()

    yield private_path

    for name in _private_module_names():
        del sys.modules[name]
    if str(tmp_path) in sys.path:
        sys.path.remove(str(tmp_path))


@pytest.fixture()
def registry_with_a_broken_private_module(private_plugins: Path) -> Iterator[None]:
    (private_plugins / f"{BROKEN_PRIVATE_MODULE}.py").write_text(
        "raise ModuleNotFoundError(\"no module named 'httpx'\")\n"
    )
    PluginRegistry.reset_instance()
    yield
    PluginRegistry.reset_instance()
