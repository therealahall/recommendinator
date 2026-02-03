"""Tests for the enrichment provider base class."""

from typing import Any

import pytest

from src.enrichment.provider_base import (
    ConfigField,
    EnrichmentProvider,
    EnrichmentResult,
    ProviderError,
    ProviderInfo,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType


class MockProvider(EnrichmentProvider):
    """Mock enrichment provider for testing."""

    @property
    def name(self) -> str:
        return "mock_provider"

    @property
    def display_name(self) -> str:
        return "Mock Provider"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.MOVIE, ContentType.TV_SHOW]

    @property
    def requires_api_key(self) -> bool:
        return True

    @property
    def rate_limit_requests_per_second(self) -> float:
        return 10.0

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="api_key",
                field_type=str,
                required=True,
                description="API key for the service",
                sensitive=True,
            ),
            ConfigField(
                name="language",
                field_type=str,
                required=False,
                default="en",
                description="Language code",
            ),
        ]

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors = []
        if not config.get("api_key"):
            errors.append("'api_key' is required")
        return errors

    def enrich(
        self, item: ContentItem, config: dict[str, Any]
    ) -> EnrichmentResult | None:
        if item.title == "Not Found":
            return None
        return EnrichmentResult(
            external_id=f"mock:{item.id}",
            genres=["Action", "Drama"],
            tags=["exciting", "award-winning"],
            description="A mock description",
            match_quality="high",
            provider=self.name,
        )


class TestConfigField:
    """Tests for ConfigField dataclass."""

    def test_create_required_field(self) -> None:
        """Test creating a required config field."""
        field = ConfigField(
            name="api_key",
            field_type=str,
            required=True,
            description="API key",
            sensitive=True,
        )

        assert field.name == "api_key"
        assert field.field_type is str
        assert field.required is True
        assert field.default is None
        assert field.description == "API key"
        assert field.sensitive is True

    def test_create_optional_field_with_default(self) -> None:
        """Test creating an optional config field with a default value."""
        field = ConfigField(
            name="timeout",
            field_type=int,
            required=False,
            default=30,
            description="Request timeout in seconds",
        )

        assert field.name == "timeout"
        assert field.field_type is int
        assert field.required is False
        assert field.default == 30
        assert field.sensitive is False


class TestProviderInfo:
    """Tests for ProviderInfo dataclass."""

    def test_create_provider_info(self) -> None:
        """Test creating provider info."""
        info = ProviderInfo(
            name="test",
            display_name="Test Provider",
            content_types=[ContentType.BOOK],
            requires_api_key=False,
        )

        assert info.name == "test"
        assert info.display_name == "Test Provider"
        assert info.content_types == [ContentType.BOOK]
        assert info.requires_api_key is False
        assert info.config_schema == []


class TestEnrichmentResult:
    """Tests for EnrichmentResult dataclass."""

    def test_create_minimal_result(self) -> None:
        """Test creating a minimal enrichment result."""
        result = EnrichmentResult()

        assert result.external_id is None
        assert result.genres is None
        assert result.tags is None
        assert result.description is None
        assert result.extra_metadata == {}
        assert result.match_quality == "high"
        assert result.provider == ""

    def test_create_full_result(self) -> None:
        """Test creating a full enrichment result."""
        result = EnrichmentResult(
            external_id="tmdb:12345",
            genres=["Action", "Sci-Fi"],
            tags=["blockbuster", "franchise"],
            description="A great movie about heroes.",
            extra_metadata={"budget": 150000000, "revenue": 800000000},
            match_quality="high",
            provider="tmdb",
        )

        assert result.external_id == "tmdb:12345"
        assert result.genres == ["Action", "Sci-Fi"]
        assert result.tags == ["blockbuster", "franchise"]
        assert result.description == "A great movie about heroes."
        assert result.extra_metadata["budget"] == 150000000
        assert result.match_quality == "high"
        assert result.provider == "tmdb"


class TestProviderError:
    """Tests for ProviderError exception."""

    def test_provider_error_message(self) -> None:
        """Test that ProviderError formats message correctly."""
        error = ProviderError("tmdb", "Rate limit exceeded")

        assert error.provider_name == "tmdb"
        assert error.message == "Rate limit exceeded"
        assert str(error) == "tmdb: Rate limit exceeded"

    def test_provider_error_raises(self) -> None:
        """Test that ProviderError can be raised and caught."""
        with pytest.raises(ProviderError) as exc_info:
            raise ProviderError("test", "Something went wrong")

        assert exc_info.value.provider_name == "test"


class TestEnrichmentProvider:
    """Tests for the EnrichmentProvider abstract base class."""

    def test_mock_provider_properties(self) -> None:
        """Test that mock provider has correct properties."""
        provider = MockProvider()

        assert provider.name == "mock_provider"
        assert provider.display_name == "Mock Provider"
        assert provider.content_types == [ContentType.MOVIE, ContentType.TV_SHOW]
        assert provider.requires_api_key is True
        assert provider.rate_limit_requests_per_second == 10.0

    def test_default_description(self) -> None:
        """Test the default description property."""
        provider = MockProvider()

        assert provider.description == "Enrich metadata from Mock Provider"

    def test_get_config_schema(self) -> None:
        """Test getting config schema."""
        provider = MockProvider()
        schema = provider.get_config_schema()

        assert len(schema) == 2
        assert schema[0].name == "api_key"
        assert schema[0].required is True
        assert schema[0].sensitive is True
        assert schema[1].name == "language"
        assert schema[1].required is False
        assert schema[1].default == "en"

    def test_validate_config_success(self) -> None:
        """Test config validation with valid config."""
        provider = MockProvider()
        errors = provider.validate_config({"api_key": "test-key"})

        assert errors == []

    def test_validate_config_failure(self) -> None:
        """Test config validation with missing API key."""
        provider = MockProvider()
        errors = provider.validate_config({})

        assert errors == ["'api_key' is required"]

    def test_enrich_success(self) -> None:
        """Test successful enrichment."""
        provider = MockProvider()
        item = ContentItem(
            id="movie123",
            title="Test Movie",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )

        result = provider.enrich(item, {"api_key": "test-key"})

        assert result is not None
        assert result.external_id == "mock:movie123"
        assert result.genres == ["Action", "Drama"]
        assert result.tags == ["exciting", "award-winning"]
        assert result.description == "A mock description"
        assert result.provider == "mock_provider"

    def test_enrich_not_found(self) -> None:
        """Test enrichment when item is not found."""
        provider = MockProvider()
        item = ContentItem(
            id="movie456",
            title="Not Found",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )

        result = provider.enrich(item, {"api_key": "test-key"})

        assert result is None

    def test_transform_config_default(self) -> None:
        """Test default transform_config returns unchanged dict."""
        raw_config = {"api_key": "test", "extra": "value"}
        transformed = MockProvider.transform_config(raw_config)

        assert transformed == raw_config
        assert transformed is not raw_config  # Should be a copy

    def test_get_info(self) -> None:
        """Test getting provider info."""
        provider = MockProvider()
        info = provider.get_info()

        assert isinstance(info, ProviderInfo)
        assert info.name == "mock_provider"
        assert info.display_name == "Mock Provider"
        assert info.content_types == [ContentType.MOVIE, ContentType.TV_SHOW]
        assert info.requires_api_key is True
        assert len(info.config_schema) == 2
