from collections.abc import Iterator
from typing import Any

from src.ingestion.plugin_base import (
    ConfigField,
    ProgressCallback,
    SourcePlugin,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType


class MockPlugin(SourcePlugin):
    @property
    def name(self) -> str:
        return "mock_plugin"

    @property
    def display_name(self) -> str:
        return "Mock Plugin"

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
                name="limit",
                field_type=int,
                required=False,
                default=100,
                description="Maximum items to fetch",
            ),
        ]

    def validate_config(self, config: dict[str, Any], **kwargs: Any) -> list[str]:
        errors = []
        if not config.get("path"):
            errors.append("'path' is required")
        return errors

    def fetch(
        self,
        config: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> Iterator[ContentItem]:
        yield ContentItem(
            id="test_1",
            title="Test Item",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            source=self.get_source_identifier(),
        )


class TestNormalizeRating:
    def test_normalize_rating_zero_is_none(self) -> None:
        plugin = MockPlugin()
        assert plugin.normalize_rating(0) is None
        assert plugin.normalize_rating("0") is None

    def test_normalize_rating_valid_string(self) -> None:
        plugin = MockPlugin()
        assert plugin.normalize_rating("4") == 4
        assert plugin.normalize_rating("2") == 2

    def test_normalize_rating_clamps_high(self) -> None:
        plugin = MockPlugin()
        assert plugin.normalize_rating(10) == 5
        assert plugin.normalize_rating(100) == 5

    def test_normalize_rating_clamps_low(self) -> None:
        plugin = MockPlugin()
        assert plugin.normalize_rating(-1) == 1
        assert plugin.normalize_rating(-5) == 1

    def test_normalize_rating_invalid_string(self) -> None:
        plugin = MockPlugin()
        assert plugin.normalize_rating("invalid") is None
        assert plugin.normalize_rating("N/A") is None
        assert plugin.normalize_rating("") is None
