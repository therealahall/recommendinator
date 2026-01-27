"""Tests for Radarr movie import plugin."""

from unittest.mock import Mock, patch

import pytest

from src.ingestion.plugin_base import SourceError, SourcePlugin
from src.ingestion.sources.radarr import RadarrPlugin, _extract_movie_rating
from src.models.content import ConsumptionStatus, ContentType


@pytest.fixture()
def plugin() -> RadarrPlugin:
    """Create a RadarrPlugin instance."""
    return RadarrPlugin()


@pytest.fixture()
def sample_movies() -> list[dict]:
    """Create sample Radarr API response data."""
    return [
        {
            "title": "Inception",
            "monitored": True,
            "tmdbId": 27205,
            "imdbId": "tt1375666",
            "year": 2010,
            "studio": "Warner Bros. Pictures",
            "overview": "A thief who steals corporate secrets through dreams.",
            "runtime": 148,
            "genres": ["Action", "Sci-Fi", "Thriller"],
            "status": "released",
            "hasFile": True,
            "ratings": {
                "imdb": {"value": 8.8, "votes": 2000000},
                "tmdb": {"value": 8.4, "votes": 30000},
            },
        },
        {
            "title": "Blade Runner 2049",
            "monitored": True,
            "tmdbId": 335984,
            "year": 2017,
            "studio": "Columbia Pictures",
            "runtime": 164,
            "genres": ["Sci-Fi", "Drama"],
            "status": "released",
            "hasFile": False,
            "ratings": {
                "imdb": {"value": 8.0, "votes": 500000},
                "tmdb": {"value": 7.5, "votes": 10000},
            },
        },
        {
            "title": "Old Unmonitored Movie",
            "monitored": False,
            "tmdbId": 99999,
            "year": 2000,
            "ratings": {"imdb": {"value": 5.0}},
        },
    ]


class TestRadarrPluginProperties:
    """Tests for RadarrPlugin metadata properties."""

    def test_is_source_plugin(self, plugin: RadarrPlugin) -> None:
        assert isinstance(plugin, SourcePlugin)

    def test_name(self, plugin: RadarrPlugin) -> None:
        assert plugin.name == "radarr"

    def test_display_name(self, plugin: RadarrPlugin) -> None:
        assert plugin.display_name == "Radarr"

    def test_content_types(self, plugin: RadarrPlugin) -> None:
        assert plugin.content_types == [ContentType.MOVIE]

    def test_requires_api_key(self, plugin: RadarrPlugin) -> None:
        assert plugin.requires_api_key is True

    def test_requires_network(self, plugin: RadarrPlugin) -> None:
        assert plugin.requires_network is True

    def test_config_schema(self, plugin: RadarrPlugin) -> None:
        schema = plugin.get_config_schema()
        assert len(schema) == 2
        names = [field.name for field in schema]
        assert "url" in names
        assert "api_key" in names
        api_key_field = next(field for field in schema if field.name == "api_key")
        assert api_key_field.sensitive is True

    def test_get_source_identifier(self, plugin: RadarrPlugin) -> None:
        assert plugin.get_source_identifier() == "radarr"

    def test_get_info(self, plugin: RadarrPlugin) -> None:
        info = plugin.get_info()
        assert info.name == "radarr"
        assert info.display_name == "Radarr"
        assert info.requires_api_key is True
        assert info.requires_network is True


class TestRadarrPluginValidation:
    """Tests for RadarrPlugin config validation."""

    def test_validate_valid_config(self, plugin: RadarrPlugin) -> None:
        errors = plugin.validate_config(
            {"url": "http://localhost:7878", "api_key": "abc123"}
        )
        assert errors == []

    def test_validate_missing_api_key(self, plugin: RadarrPlugin) -> None:
        errors = plugin.validate_config({"url": "http://localhost:7878"})
        assert any("api_key" in error for error in errors)

    def test_validate_empty_api_key(self, plugin: RadarrPlugin) -> None:
        errors = plugin.validate_config({"url": "http://localhost:7878", "api_key": ""})
        assert any("api_key" in error for error in errors)

    def test_validate_missing_url(self, plugin: RadarrPlugin) -> None:
        errors = plugin.validate_config({"url": "", "api_key": "abc123"})
        assert any("url" in error for error in errors)


class TestRadarrPluginFetch:
    """Tests for RadarrPlugin fetch functionality."""

    @patch("src.ingestion.sources.radarr.requests.get")
    def test_fetch_monitored_movies(
        self,
        mock_get: Mock,
        plugin: RadarrPlugin,
        sample_movies: list[dict],
    ) -> None:
        """Monitored movies should be imported, unmonitored skipped."""
        mock_response = Mock()
        mock_response.json.return_value = sample_movies
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        items = list(
            plugin.fetch({"url": "http://localhost:7878", "api_key": "test_key"})
        )

        # Only 2 monitored movies (third is unmonitored)
        assert len(items) == 2
        assert items[0].title == "Inception"
        assert items[1].title == "Blade Runner 2049"

    @patch("src.ingestion.sources.radarr.requests.get")
    def test_fetch_all_items_are_unread(
        self,
        mock_get: Mock,
        plugin: RadarrPlugin,
        sample_movies: list[dict],
    ) -> None:
        """All imported items should have UNREAD status."""
        mock_response = Mock()
        mock_response.json.return_value = sample_movies
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        items = list(
            plugin.fetch({"url": "http://localhost:7878", "api_key": "test_key"})
        )

        for item in items:
            assert item.status == ConsumptionStatus.UNREAD.value

    @patch("src.ingestion.sources.radarr.requests.get")
    def test_fetch_content_type_is_movie(
        self,
        mock_get: Mock,
        plugin: RadarrPlugin,
        sample_movies: list[dict],
    ) -> None:
        mock_response = Mock()
        mock_response.json.return_value = sample_movies
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        items = list(
            plugin.fetch({"url": "http://localhost:7878", "api_key": "test_key"})
        )

        for item in items:
            assert item.content_type == ContentType.MOVIE.value

    @patch("src.ingestion.sources.radarr.requests.get")
    def test_fetch_external_id_format(
        self,
        mock_get: Mock,
        plugin: RadarrPlugin,
        sample_movies: list[dict],
    ) -> None:
        """External ID should be tmdb:{tmdbId} for deduplication."""
        mock_response = Mock()
        mock_response.json.return_value = sample_movies
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        items = list(
            plugin.fetch({"url": "http://localhost:7878", "api_key": "test_key"})
        )

        assert items[0].id == "tmdb:27205"
        assert items[1].id == "tmdb:335984"

    @patch("src.ingestion.sources.radarr.requests.get")
    def test_fetch_rating_from_imdb(
        self,
        mock_get: Mock,
        plugin: RadarrPlugin,
        sample_movies: list[dict],
    ) -> None:
        """Ratings should prefer IMDb, normalized from 0-10 to 1-5."""
        mock_response = Mock()
        mock_response.json.return_value = sample_movies
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        items = list(
            plugin.fetch({"url": "http://localhost:7878", "api_key": "test_key"})
        )

        # 8.8 / 2 = 4.4, rounds to 4
        assert items[0].rating == 4
        # 8.0 / 2 = 4.0, rounds to 4
        assert items[1].rating == 4

    @patch("src.ingestion.sources.radarr.requests.get")
    def test_fetch_metadata(
        self,
        mock_get: Mock,
        plugin: RadarrPlugin,
        sample_movies: list[dict],
    ) -> None:
        """Metadata should include genres, studio, runtime, etc."""
        mock_response = Mock()
        mock_response.json.return_value = sample_movies
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        items = list(
            plugin.fetch({"url": "http://localhost:7878", "api_key": "test_key"})
        )

        metadata = items[0].metadata
        assert metadata["tmdb_id"] == 27205
        assert metadata["imdb_id"] == "tt1375666"
        assert metadata["year"] == 2010
        assert metadata["studio"] == "Warner Bros. Pictures"
        assert metadata["runtime_minutes"] == 148
        assert metadata["genres"] == ["Action", "Sci-Fi", "Thriller"]
        assert metadata["has_file"] is True

    @patch("src.ingestion.sources.radarr.requests.get")
    def test_fetch_source_identifier(
        self,
        mock_get: Mock,
        plugin: RadarrPlugin,
        sample_movies: list[dict],
    ) -> None:
        mock_response = Mock()
        mock_response.json.return_value = sample_movies
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        items = list(
            plugin.fetch({"url": "http://localhost:7878", "api_key": "test_key"})
        )

        for item in items:
            assert item.source == "radarr"

    @patch("src.ingestion.sources.radarr.requests.get")
    def test_fetch_api_key_sent_in_header(
        self,
        mock_get: Mock,
        plugin: RadarrPlugin,
    ) -> None:
        mock_response = Mock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        list(plugin.fetch({"url": "http://localhost:7878", "api_key": "my_secret_key"}))

        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args[1]
        assert call_kwargs["headers"]["X-Api-Key"] == "my_secret_key"

    @patch("src.ingestion.sources.radarr.requests.get")
    def test_fetch_correct_api_endpoint(
        self,
        mock_get: Mock,
        plugin: RadarrPlugin,
    ) -> None:
        mock_response = Mock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        list(plugin.fetch({"url": "http://myradarr:7878", "api_key": "key"}))

        call_args = mock_get.call_args[0]
        assert call_args[0] == "http://myradarr:7878/api/v3/movie"

    @patch("src.ingestion.sources.radarr.requests.get")
    def test_fetch_trailing_slash_handled(
        self,
        mock_get: Mock,
        plugin: RadarrPlugin,
    ) -> None:
        mock_response = Mock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        list(plugin.fetch({"url": "http://localhost:7878/", "api_key": "key"}))

        call_args = mock_get.call_args[0]
        assert "//" not in call_args[0].replace("http://", "")

    @patch("src.ingestion.sources.radarr.requests.get")
    def test_fetch_empty_library(
        self,
        mock_get: Mock,
        plugin: RadarrPlugin,
    ) -> None:
        mock_response = Mock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        items = list(plugin.fetch({"url": "http://localhost:7878", "api_key": "key"}))

        assert len(items) == 0

    @patch("src.ingestion.sources.radarr.requests.get")
    def test_fetch_skips_empty_title(
        self,
        mock_get: Mock,
        plugin: RadarrPlugin,
    ) -> None:
        mock_response = Mock()
        mock_response.json.return_value = [
            {"title": "", "monitored": True, "tmdbId": 123},
            {"title": "Valid Movie", "monitored": True, "tmdbId": 456},
        ]
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        items = list(plugin.fetch({"url": "http://localhost:7878", "api_key": "key"}))

        assert len(items) == 1
        assert items[0].title == "Valid Movie"


class TestRadarrPluginErrors:
    """Tests for error handling."""

    @patch("src.ingestion.sources.radarr.requests.get")
    def test_connection_error_raises_source_error(
        self,
        mock_get: Mock,
        plugin: RadarrPlugin,
    ) -> None:
        import requests as req

        mock_get.side_effect = req.ConnectionError("Connection refused")

        with pytest.raises(SourceError, match="Failed to connect to Radarr"):
            list(plugin.fetch({"url": "http://localhost:7878", "api_key": "key"}))

    @patch("src.ingestion.sources.radarr.requests.get")
    def test_http_error_raises_source_error(
        self,
        mock_get: Mock,
        plugin: RadarrPlugin,
    ) -> None:
        import requests as req

        mock_response = Mock()
        mock_response.raise_for_status.side_effect = req.HTTPError("401 Unauthorized")
        mock_get.return_value = mock_response

        with pytest.raises(SourceError, match="Failed to connect to Radarr"):
            list(plugin.fetch({"url": "http://localhost:7878", "api_key": "bad_key"}))


class TestRadarrRatingNormalization:
    """Tests for rating extraction and normalization."""

    def test_imdb_rating_preferred(self) -> None:
        """IMDb rating should be used first."""
        movie = {
            "ratings": {
                "imdb": {"value": 8.0},
                "tmdb": {"value": 6.0},
            }
        }
        assert _extract_movie_rating(movie) == 4  # 8.0 / 2 = 4.0

    def test_tmdb_fallback_when_no_imdb(self) -> None:
        """TMDB rating should be used when IMDb is not available."""
        movie = {
            "ratings": {
                "tmdb": {"value": 7.0},
            }
        }
        assert _extract_movie_rating(movie) == 4  # 7.0 / 2 = 3.5, rounds to 4

    def test_tmdb_fallback_when_imdb_zero(self) -> None:
        """TMDB rating should be used when IMDb rating is 0."""
        movie = {
            "ratings": {
                "imdb": {"value": 0},
                "tmdb": {"value": 6.0},
            }
        }
        assert _extract_movie_rating(movie) == 3  # 6.0 / 2 = 3.0

    def test_rating_10_becomes_5(self) -> None:
        assert _extract_movie_rating({"ratings": {"imdb": {"value": 10.0}}}) == 5

    def test_rating_2_becomes_1(self) -> None:
        assert _extract_movie_rating({"ratings": {"imdb": {"value": 2.0}}}) == 1

    def test_no_ratings_is_none(self) -> None:
        assert _extract_movie_rating({}) is None

    def test_empty_ratings_is_none(self) -> None:
        assert _extract_movie_rating({"ratings": {}}) is None

    def test_all_zero_ratings_is_none(self) -> None:
        movie = {
            "ratings": {
                "imdb": {"value": 0},
                "tmdb": {"value": 0},
            }
        }
        assert _extract_movie_rating(movie) is None

    def test_null_values_is_none(self) -> None:
        movie = {
            "ratings": {
                "imdb": {"value": None},
                "tmdb": {"value": None},
            }
        }
        assert _extract_movie_rating(movie) is None
