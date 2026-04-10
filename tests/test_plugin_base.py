"""Tests for plugin base classes."""

from collections.abc import Iterator
from typing import Any

import pytest

from src.ingestion.plugin_base import (
    ConfigField,
    PluginInfo,
    ProgressCallback,
    SourceError,
    SourcePlugin,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType


class MockPlugin(SourcePlugin):
    """Mock plugin for testing."""

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


class MockAPIPlugin(SourcePlugin):
    """Mock plugin that requires API key."""

    @property
    def name(self) -> str:
        return "mock_api"

    @property
    def display_name(self) -> str:
        return "Mock API"

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
            ),
        ]

    def validate_config(self, config: dict[str, Any], **kwargs: Any) -> list[str]:
        errors = []
        if not config.get("api_key"):
            errors.append("'api_key' is required")
        return errors

    def fetch(
        self,
        config: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> Iterator[ContentItem]:
        yield ContentItem(
            id="game_1",
            title="Test Game",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            source=self.get_source_identifier(),
        )


class TestConfigField:
    """Tests for ConfigField dataclass."""

    def test_create_required_field(self) -> None:
        """Test creating a required config field."""
        field = ConfigField(
            name="api_key",
            field_type=str,
            required=True,
            description="API key for authentication",
        )

        assert field.name == "api_key"
        assert field.field_type is str
        assert field.required is True
        assert field.default is None
        assert field.description == "API key for authentication"
        assert field.sensitive is False

    def test_create_optional_field_with_default(self) -> None:
        """Test creating an optional field with default value."""
        field = ConfigField(
            name="timeout",
            field_type=int,
            required=False,
            default=30,
        )

        assert field.name == "timeout"
        assert field.required is False
        assert field.default == 30

    def test_create_sensitive_field(self) -> None:
        """Test creating a sensitive field (for API keys)."""
        field = ConfigField(
            name="password",
            field_type=str,
            required=True,
            sensitive=True,
        )

        assert field.sensitive is True


class TestSourceError:
    """Tests for SourceError exception."""

    def test_create_source_error(self) -> None:
        """Test creating a SourceError."""
        error = SourceError("goodreads", "File not found")

        assert error.plugin_name == "goodreads"
        assert error.message == "File not found"
        assert str(error) == "goodreads: File not found"

    def test_source_error_is_exception(self) -> None:
        """Test that SourceError can be raised and caught."""
        with pytest.raises(SourceError) as exc_info:
            raise SourceError("steam", "API error")

        assert exc_info.value.plugin_name == "steam"
        assert exc_info.value.message == "API error"


class TestSourcePlugin:
    """Tests for SourcePlugin ABC."""

    def test_plugin_properties(self) -> None:
        """Test plugin property accessors."""
        plugin = MockPlugin()

        assert plugin.name == "mock_plugin"
        assert plugin.display_name == "Mock Plugin"
        assert plugin.content_types == [ContentType.BOOK]
        assert plugin.requires_api_key is False

    def test_requires_network_defaults_to_requires_api_key(self) -> None:
        """Test that requires_network defaults to requires_api_key value."""
        file_plugin = MockPlugin()
        api_plugin = MockAPIPlugin()

        assert file_plugin.requires_network is False
        assert api_plugin.requires_network is True

    def test_get_config_schema(self) -> None:
        """Test getting configuration schema."""
        plugin = MockPlugin()
        schema = plugin.get_config_schema()

        assert len(schema) == 2
        assert schema[0].name == "path"
        assert schema[0].required is True
        assert schema[1].name == "limit"
        assert schema[1].required is False
        assert schema[1].default == 100

    def test_validate_config_valid(self) -> None:
        """Test config validation with valid config."""
        plugin = MockPlugin()
        errors = plugin.validate_config({"path": "/data/books.csv"})

        assert errors == []

    def test_validate_config_missing_required(self) -> None:
        """Test config validation with missing required field."""
        plugin = MockPlugin()
        errors = plugin.validate_config({})

        assert len(errors) == 1
        assert "'path' is required" in errors[0]

    def test_fetch_yields_content_items(self) -> None:
        """Test that fetch yields ContentItem objects."""
        plugin = MockPlugin()
        items = list(plugin.fetch({"path": "/test"}))

        assert len(items) == 1
        assert items[0].title == "Test Item"
        assert items[0].content_type == ContentType.BOOK
        assert items[0].source == "mock_plugin"

    def test_get_source_identifier(self) -> None:
        """Test default source identifier is plugin name."""
        plugin = MockPlugin()
        assert plugin.get_source_identifier() == "mock_plugin"

    def test_get_info(self) -> None:
        """Test getting plugin info."""
        plugin = MockPlugin()
        info = plugin.get_info()

        assert isinstance(info, PluginInfo)
        assert info.name == "mock_plugin"
        assert info.display_name == "Mock Plugin"
        assert info.content_types == [ContentType.BOOK]
        assert info.requires_api_key is False
        assert info.requires_network is False
        assert len(info.config_schema) == 2


class TestNormalizeRating:
    """Tests for rating normalization."""

    def test_normalize_rating_none(self) -> None:
        """Test that None rating returns None."""
        plugin = MockPlugin()
        assert plugin.normalize_rating(None) is None

    def test_normalize_rating_zero_is_none(self) -> None:
        """Test that 0 rating returns None (unrated)."""
        plugin = MockPlugin()
        assert plugin.normalize_rating(0) is None
        assert plugin.normalize_rating("0") is None

    def test_normalize_rating_valid_int(self) -> None:
        """Test valid integer ratings."""
        plugin = MockPlugin()
        assert plugin.normalize_rating(1) == 1
        assert plugin.normalize_rating(3) == 3
        assert plugin.normalize_rating(5) == 5

    def test_normalize_rating_valid_string(self) -> None:
        """Test valid string ratings are converted."""
        plugin = MockPlugin()
        assert plugin.normalize_rating("4") == 4
        assert plugin.normalize_rating("2") == 2

    def test_normalize_rating_clamps_high(self) -> None:
        """Test ratings above 5 are clamped."""
        plugin = MockPlugin()
        assert plugin.normalize_rating(10) == 5
        assert plugin.normalize_rating(100) == 5

    def test_normalize_rating_clamps_low(self) -> None:
        """Test ratings below 1 are clamped (except 0)."""
        plugin = MockPlugin()
        assert plugin.normalize_rating(-1) == 1
        assert plugin.normalize_rating(-5) == 1

    def test_normalize_rating_invalid_string(self) -> None:
        """Test invalid string returns None."""
        plugin = MockPlugin()
        assert plugin.normalize_rating("invalid") is None
        assert plugin.normalize_rating("N/A") is None
        assert plugin.normalize_rating("") is None

    def test_normalize_rating_invalid_type(self) -> None:
        """Test invalid types return None."""
        plugin = MockPlugin()
        assert plugin.normalize_rating([1, 2, 3]) is None
        assert plugin.normalize_rating({"rating": 5}) is None


class TestPluginInfo:
    """Tests for PluginInfo dataclass."""

    def test_create_plugin_info(self) -> None:
        """Test creating PluginInfo directly."""
        info = PluginInfo(
            name="test",
            display_name="Test Plugin",
            content_types=[ContentType.MOVIE],
            requires_api_key=True,
            requires_network=True,
        )

        assert info.name == "test"
        assert info.display_name == "Test Plugin"
        assert ContentType.MOVIE in info.content_types
        assert info.requires_api_key is True
        assert info.config_schema == []

    def test_plugin_info_with_schema(self) -> None:
        """Test PluginInfo with config schema."""
        schema = [
            ConfigField(name="url", field_type=str, required=True),
        ]
        info = PluginInfo(
            name="test",
            display_name="Test",
            content_types=[ContentType.TV_SHOW],
            requires_api_key=True,
            requires_network=True,
            config_schema=schema,
        )

        assert len(info.config_schema) == 1
        assert info.config_schema[0].name == "url"
