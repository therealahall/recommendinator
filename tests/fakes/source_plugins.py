"""Shared fake ``SourcePlugin`` implementations for source-config tests.

Both the web (``tests/web/test_source_config_api.py``) and CLI
(``tests/cli/test_source_commands.py``) suites need a lightweight plugin
without secrets and one with a sensitive ``api_key`` plus a mix of
``str``/``int``/``bool``/``list`` fields. They share the same fakes so a
future change to ``SourcePlugin`` only has to update one file.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from src.ingestion.plugin_base import ConfigField, SourcePlugin
from src.ingestion.registry import PluginRegistry
from src.models.content import ConsumptionStatus, ContentItem, ContentType


class FakeFilePlugin(SourcePlugin):
    """File-based fake: a single non-sensitive ``path`` field."""

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
    """API-based fake: sensitive ``api_key`` plus str/int/list/bool fields."""

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


@pytest.fixture()
def registry_with_source_fakes() -> Iterator[None]:
    """Replace the ``PluginRegistry`` singleton with the two source-config fakes.

    Use ``@pytest.mark.usefixtures("registry_with_source_fakes")`` on the
    test class. The registry is restored on teardown.
    """
    registry = PluginRegistry.get_instance()
    registry._discovered = True
    registry._plugins.clear()
    registry.register(FakeFilePlugin())
    registry.register(FakeApiPlugin())
    yield
    PluginRegistry.reset_instance()
