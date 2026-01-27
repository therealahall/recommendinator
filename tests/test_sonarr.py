"""Tests for Sonarr TV series import plugin."""

from unittest.mock import Mock, patch

import pytest

from src.ingestion.plugin_base import SourceError, SourcePlugin
from src.ingestion.sources.sonarr import SonarrPlugin
from src.models.content import ConsumptionStatus, ContentType


@pytest.fixture()
def plugin() -> SonarrPlugin:
    """Create a SonarrPlugin instance."""
    return SonarrPlugin()


@pytest.fixture()
def sample_series() -> list[dict]:
    """Create sample Sonarr API response data."""
    return [
        {
            "title": "Breaking Bad",
            "monitored": True,
            "tvdbId": 81189,
            "imdbId": "tt0903747",
            "year": 2008,
            "network": "AMC",
            "overview": "A high school chemistry teacher turned meth manufacturer.",
            "genres": ["Drama", "Crime", "Thriller"],
            "seriesType": "standard",
            "status": "ended",
            "ratings": {"value": 9.5},
            "statistics": {
                "seasonCount": 5,
                "episodeCount": 62,
                "episodeFileCount": 62,
            },
        },
        {
            "title": "The Expanse",
            "monitored": True,
            "tvdbId": 280619,
            "year": 2015,
            "network": "Amazon",
            "genres": ["Sci-Fi"],
            "ratings": {"value": 8.4},
            "statistics": {
                "seasonCount": 6,
                "episodeCount": 62,
                "episodeFileCount": 50,
            },
        },
        {
            "title": "Old Unmonitored Show",
            "monitored": False,
            "tvdbId": 99999,
            "year": 2000,
            "ratings": {"value": 5.0},
        },
    ]


class TestSonarrPluginProperties:
    """Tests for SonarrPlugin metadata properties."""

    def test_is_source_plugin(self, plugin: SonarrPlugin) -> None:
        assert isinstance(plugin, SourcePlugin)

    def test_name(self, plugin: SonarrPlugin) -> None:
        assert plugin.name == "sonarr"

    def test_display_name(self, plugin: SonarrPlugin) -> None:
        assert plugin.display_name == "Sonarr"

    def test_content_types(self, plugin: SonarrPlugin) -> None:
        assert plugin.content_types == [ContentType.TV_SHOW]

    def test_requires_api_key(self, plugin: SonarrPlugin) -> None:
        assert plugin.requires_api_key is True

    def test_requires_network(self, plugin: SonarrPlugin) -> None:
        assert plugin.requires_network is True

    def test_config_schema(self, plugin: SonarrPlugin) -> None:
        schema = plugin.get_config_schema()
        assert len(schema) == 2
        names = [field.name for field in schema]
        assert "url" in names
        assert "api_key" in names
        # api_key should be sensitive
        api_key_field = next(field for field in schema if field.name == "api_key")
        assert api_key_field.sensitive is True

    def test_get_source_identifier(self, plugin: SonarrPlugin) -> None:
        assert plugin.get_source_identifier() == "sonarr"

    def test_get_info(self, plugin: SonarrPlugin) -> None:
        info = plugin.get_info()
        assert info.name == "sonarr"
        assert info.display_name == "Sonarr"
        assert info.requires_api_key is True
        assert info.requires_network is True


class TestSonarrPluginValidation:
    """Tests for SonarrPlugin config validation."""

    def test_validate_valid_config(self, plugin: SonarrPlugin) -> None:
        errors = plugin.validate_config(
            {"url": "http://localhost:8989", "api_key": "abc123"}
        )
        assert errors == []

    def test_validate_missing_api_key(self, plugin: SonarrPlugin) -> None:
        errors = plugin.validate_config({"url": "http://localhost:8989"})
        assert any("api_key" in error for error in errors)

    def test_validate_empty_api_key(self, plugin: SonarrPlugin) -> None:
        errors = plugin.validate_config({"url": "http://localhost:8989", "api_key": ""})
        assert any("api_key" in error for error in errors)

    def test_validate_missing_url(self, plugin: SonarrPlugin) -> None:
        errors = plugin.validate_config({"url": "", "api_key": "abc123"})
        assert any("url" in error for error in errors)


class TestSonarrPluginFetch:
    """Tests for SonarrPlugin fetch functionality."""

    @patch("src.ingestion.sources.sonarr.requests.get")
    def test_fetch_monitored_series(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
        sample_series: list[dict],
    ) -> None:
        """Monitored series should be imported, unmonitored skipped."""
        mock_response = Mock()
        mock_response.json.return_value = sample_series
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        items = list(
            plugin.fetch({"url": "http://localhost:8989", "api_key": "test_key"})
        )

        # Only 2 monitored series (third is unmonitored)
        assert len(items) == 2
        assert items[0].title == "Breaking Bad"
        assert items[1].title == "The Expanse"

    @patch("src.ingestion.sources.sonarr.requests.get")
    def test_fetch_all_items_are_unread(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
        sample_series: list[dict],
    ) -> None:
        """All imported items should have UNREAD status (Sonarr can't track watching)."""
        mock_response = Mock()
        mock_response.json.return_value = sample_series
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        items = list(
            plugin.fetch({"url": "http://localhost:8989", "api_key": "test_key"})
        )

        for item in items:
            assert item.status == ConsumptionStatus.UNREAD.value

    @patch("src.ingestion.sources.sonarr.requests.get")
    def test_fetch_content_type_is_tv_show(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
        sample_series: list[dict],
    ) -> None:
        mock_response = Mock()
        mock_response.json.return_value = sample_series
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        items = list(
            plugin.fetch({"url": "http://localhost:8989", "api_key": "test_key"})
        )

        for item in items:
            assert item.content_type == ContentType.TV_SHOW.value

    @patch("src.ingestion.sources.sonarr.requests.get")
    def test_fetch_external_id_format(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
        sample_series: list[dict],
    ) -> None:
        """External ID should be tvdb:{tvdbId} for deduplication."""
        mock_response = Mock()
        mock_response.json.return_value = sample_series
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        items = list(
            plugin.fetch({"url": "http://localhost:8989", "api_key": "test_key"})
        )

        assert items[0].id == "tvdb:81189"
        assert items[1].id == "tvdb:280619"

    @patch("src.ingestion.sources.sonarr.requests.get")
    def test_fetch_rating_normalization(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
        sample_series: list[dict],
    ) -> None:
        """Ratings should be normalized from 0-10 to 1-5."""
        mock_response = Mock()
        mock_response.json.return_value = sample_series
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        items = list(
            plugin.fetch({"url": "http://localhost:8989", "api_key": "test_key"})
        )

        # 9.5 / 2 = 4.75, rounds to 5
        assert items[0].rating == 5
        # 8.4 / 2 = 4.2, rounds to 4
        assert items[1].rating == 4

    @patch("src.ingestion.sources.sonarr.requests.get")
    def test_fetch_metadata(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
        sample_series: list[dict],
    ) -> None:
        """Metadata should include genres, network, seasons, etc."""
        mock_response = Mock()
        mock_response.json.return_value = sample_series
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        items = list(
            plugin.fetch({"url": "http://localhost:8989", "api_key": "test_key"})
        )

        metadata = items[0].metadata
        assert metadata["tvdb_id"] == 81189
        assert metadata["imdb_id"] == "tt0903747"
        assert metadata["year"] == 2008
        assert metadata["network"] == "AMC"
        assert metadata["genres"] == ["Drama", "Crime", "Thriller"]
        assert metadata["total_seasons"] == 5
        assert metadata["total_episodes"] == 62
        assert metadata["downloaded_episodes"] == 62

    @patch("src.ingestion.sources.sonarr.requests.get")
    def test_fetch_source_identifier(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
        sample_series: list[dict],
    ) -> None:
        mock_response = Mock()
        mock_response.json.return_value = sample_series
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        items = list(
            plugin.fetch({"url": "http://localhost:8989", "api_key": "test_key"})
        )

        for item in items:
            assert item.source == "sonarr"

    @patch("src.ingestion.sources.sonarr.requests.get")
    def test_fetch_api_key_sent_in_header(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
    ) -> None:
        """API key should be sent as X-Api-Key header."""
        mock_response = Mock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        list(plugin.fetch({"url": "http://localhost:8989", "api_key": "my_secret_key"}))

        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args[1]
        assert call_kwargs["headers"]["X-Api-Key"] == "my_secret_key"

    @patch("src.ingestion.sources.sonarr.requests.get")
    def test_fetch_correct_api_endpoint(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
    ) -> None:
        """Should call /api/v3/series endpoint."""
        mock_response = Mock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        list(plugin.fetch({"url": "http://mysonarr:8989", "api_key": "key"}))

        call_args = mock_get.call_args[0]
        assert call_args[0] == "http://mysonarr:8989/api/v3/series"

    @patch("src.ingestion.sources.sonarr.requests.get")
    def test_fetch_trailing_slash_handled(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
    ) -> None:
        """Trailing slash in URL should not cause double slash."""
        mock_response = Mock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        list(plugin.fetch({"url": "http://localhost:8989/", "api_key": "key"}))

        call_args = mock_get.call_args[0]
        assert "//" not in call_args[0].replace("http://", "")

    @patch("src.ingestion.sources.sonarr.requests.get")
    def test_fetch_empty_library(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
    ) -> None:
        mock_response = Mock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        items = list(plugin.fetch({"url": "http://localhost:8989", "api_key": "key"}))

        assert len(items) == 0

    @patch("src.ingestion.sources.sonarr.requests.get")
    def test_fetch_skips_empty_title(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
    ) -> None:
        mock_response = Mock()
        mock_response.json.return_value = [
            {"title": "", "monitored": True, "tvdbId": 123},
            {"title": "Valid Show", "monitored": True, "tvdbId": 456},
        ]
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        items = list(plugin.fetch({"url": "http://localhost:8989", "api_key": "key"}))

        assert len(items) == 1
        assert items[0].title == "Valid Show"


class TestSonarrPluginErrors:
    """Tests for error handling."""

    @patch("src.ingestion.sources.sonarr.requests.get")
    def test_connection_error_raises_source_error(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
    ) -> None:
        import requests as req

        mock_get.side_effect = req.ConnectionError("Connection refused")

        with pytest.raises(SourceError, match="Failed to connect to Sonarr"):
            list(plugin.fetch({"url": "http://localhost:8989", "api_key": "key"}))

    @patch("src.ingestion.sources.sonarr.requests.get")
    def test_http_error_raises_source_error(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
    ) -> None:
        import requests as req

        mock_response = Mock()
        mock_response.raise_for_status.side_effect = req.HTTPError("401 Unauthorized")
        mock_get.return_value = mock_response

        with pytest.raises(SourceError, match="Failed to connect to Sonarr"):
            list(plugin.fetch({"url": "http://localhost:8989", "api_key": "bad_key"}))


class TestSonarrRatingNormalization:
    """Tests for rating normalization from 0-10 to 1-5 scale."""

    def test_rating_10_becomes_5(self, plugin: SonarrPlugin) -> None:
        assert plugin._extract_rating({"ratings": {"value": 10.0}}) == 5

    def test_rating_8_becomes_4(self, plugin: SonarrPlugin) -> None:
        assert plugin._extract_rating({"ratings": {"value": 8.0}}) == 4

    def test_rating_6_becomes_3(self, plugin: SonarrPlugin) -> None:
        assert plugin._extract_rating({"ratings": {"value": 6.0}}) == 3

    def test_rating_4_becomes_2(self, plugin: SonarrPlugin) -> None:
        assert plugin._extract_rating({"ratings": {"value": 4.0}}) == 2

    def test_rating_2_becomes_1(self, plugin: SonarrPlugin) -> None:
        assert plugin._extract_rating({"ratings": {"value": 2.0}}) == 1

    def test_rating_0_is_none(self, plugin: SonarrPlugin) -> None:
        assert plugin._extract_rating({"ratings": {"value": 0}}) is None

    def test_no_ratings_is_none(self, plugin: SonarrPlugin) -> None:
        assert plugin._extract_rating({}) is None

    def test_empty_ratings_is_none(self, plugin: SonarrPlugin) -> None:
        assert plugin._extract_rating({"ratings": {}}) is None

    def test_no_value_is_none(self, plugin: SonarrPlugin) -> None:
        assert plugin._extract_rating({"ratings": {"value": None}}) is None
