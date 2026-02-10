"""Tests for Radarr movie import plugin."""

from unittest.mock import Mock, patch

import pytest

from src.ingestion.plugin_base import SourceError, SourcePlugin
from src.ingestion.sources.radarr import RadarrPlugin
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

    @pytest.fixture(autouse=True)
    def _patch_requests(self):
        """Patch requests.get shared by arr_base and radarr modules."""
        with patch("src.ingestion.sources.arr_base.requests.get") as mock_get:
            self.mock_get = mock_get
            yield

    def _mock_radarr_responses(
        self, movies: list, collections: list | None = None
    ) -> None:
        """Configure mock to return movies and collections for Radarr API calls."""
        if collections is None:
            collections = []

        def side_effect(*args, **kwargs):
            response = Mock()
            response.raise_for_status = Mock()
            url = args[0] if args else ""
            response.json.return_value = collections if "collection" in url else movies
            return response

        self.mock_get.side_effect = side_effect

    def test_fetch_all_movies(
        self,
        plugin: RadarrPlugin,
        sample_movies: list[dict],
    ) -> None:
        """All movies should be imported regardless of monitored state."""
        self._mock_radarr_responses(sample_movies)

        items = list(
            plugin.fetch({"url": "http://localhost:7878", "api_key": "test_key"})
        )

        # All 3 movies imported (monitored state is ignored)
        assert len(items) == 3
        assert items[0].title == "Inception"
        assert items[1].title == "Blade Runner 2049"
        assert items[2].title == "Old Unmonitored Movie"

    def test_fetch_all_items_are_unread(
        self,
        plugin: RadarrPlugin,
        sample_movies: list[dict],
    ) -> None:
        """All imported items should have UNREAD status."""
        self._mock_radarr_responses(sample_movies)

        items = list(
            plugin.fetch({"url": "http://localhost:7878", "api_key": "test_key"})
        )

        for item in items:
            assert item.status == ConsumptionStatus.UNREAD.value

    def test_fetch_content_type_is_movie(
        self,
        plugin: RadarrPlugin,
        sample_movies: list[dict],
    ) -> None:
        self._mock_radarr_responses(sample_movies)

        items = list(
            plugin.fetch({"url": "http://localhost:7878", "api_key": "test_key"})
        )

        for item in items:
            assert item.content_type == ContentType.MOVIE.value

    def test_fetch_external_id_format(
        self,
        plugin: RadarrPlugin,
        sample_movies: list[dict],
    ) -> None:
        """External ID should be tmdb:{tmdbId} for deduplication."""
        self._mock_radarr_responses(sample_movies)

        items = list(
            plugin.fetch({"url": "http://localhost:7878", "api_key": "test_key"})
        )

        assert items[0].id == "tmdb:27205"
        assert items[1].id == "tmdb:335984"

    def test_fetch_rating_is_none(
        self,
        plugin: RadarrPlugin,
        sample_movies: list[dict],
    ) -> None:
        """Radarr does not track personal ratings; rating should always be None."""
        self._mock_radarr_responses(sample_movies)

        items = list(
            plugin.fetch({"url": "http://localhost:7878", "api_key": "test_key"})
        )

        for item in items:
            assert item.rating is None

    def test_fetch_metadata(
        self,
        plugin: RadarrPlugin,
        sample_movies: list[dict],
    ) -> None:
        """Metadata should include genres, studio, runtime, etc."""
        self._mock_radarr_responses(sample_movies)

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

    def test_fetch_source_identifier(
        self,
        plugin: RadarrPlugin,
        sample_movies: list[dict],
    ) -> None:
        self._mock_radarr_responses(sample_movies)

        items = list(
            plugin.fetch({"url": "http://localhost:7878", "api_key": "test_key"})
        )

        for item in items:
            assert item.source == "radarr"

    def test_fetch_api_key_sent_in_header(
        self,
        plugin: RadarrPlugin,
    ) -> None:
        self._mock_radarr_responses([])

        list(plugin.fetch({"url": "http://localhost:7878", "api_key": "my_secret_key"}))

        assert self.mock_get.call_count >= 1
        call_kwargs = self.mock_get.call_args[1]
        assert call_kwargs["headers"]["X-Api-Key"] == "my_secret_key"

    def test_fetch_correct_api_endpoint(
        self,
        plugin: RadarrPlugin,
    ) -> None:
        self._mock_radarr_responses([])

        list(plugin.fetch({"url": "http://myradarr:7878", "api_key": "key"}))

        calls = [call[0][0] for call in self.mock_get.call_args_list]
        assert "http://myradarr:7878/api/v3/movie" in calls

    def test_fetch_trailing_slash_handled(
        self,
        plugin: RadarrPlugin,
    ) -> None:
        self._mock_radarr_responses([])

        list(plugin.fetch({"url": "http://localhost:7878/", "api_key": "key"}))

        for call in self.mock_get.call_args_list:
            url = call[0][0]
            assert "//" not in url.replace("http://", "").replace("https://", "")

    def test_fetch_empty_library(
        self,
        plugin: RadarrPlugin,
    ) -> None:
        self._mock_radarr_responses([])

        items = list(plugin.fetch({"url": "http://localhost:7878", "api_key": "key"}))

        assert len(items) == 0

    def test_fetch_skips_empty_title(
        self,
        plugin: RadarrPlugin,
    ) -> None:
        movies = [
            {"title": "", "monitored": True, "tmdbId": 123},
            {"title": "Valid Movie", "monitored": True, "tmdbId": 456},
        ]
        self._mock_radarr_responses(movies)

        items = list(plugin.fetch({"url": "http://localhost:7878", "api_key": "key"}))

        assert len(items) == 1
        assert items[0].title == "Valid Movie"


class TestRadarrPluginErrors:
    """Tests for error handling."""

    @pytest.fixture(autouse=True)
    def _patch_requests(self):
        """Patch requests.get shared by arr_base and radarr modules."""
        with patch("src.ingestion.sources.arr_base.requests.get") as mock_get:
            self.mock_get = mock_get
            yield

    def test_connection_error_raises_source_error(
        self,
        plugin: RadarrPlugin,
    ) -> None:
        import requests as req

        self.mock_get.side_effect = req.ConnectionError("Connection refused")

        with pytest.raises(SourceError, match="Failed to connect to Radarr"):
            list(plugin.fetch({"url": "http://localhost:7878", "api_key": "key"}))

    def test_http_error_raises_source_error(
        self,
        plugin: RadarrPlugin,
    ) -> None:
        import requests as req

        mock_response = Mock()
        mock_response.raise_for_status.side_effect = req.HTTPError("401 Unauthorized")
        self.mock_get.return_value = mock_response

        with pytest.raises(SourceError, match="Failed to connect to Radarr"):
            list(plugin.fetch({"url": "http://localhost:7878", "api_key": "bad_key"}))


class TestRadarrCollections:
    """Tests for Radarr collection metadata (movie series)."""

    @pytest.fixture(autouse=True)
    def _patch_requests(self):
        """Patch requests.get shared by arr_base and radarr modules."""
        with patch("src.ingestion.sources.arr_base.requests.get") as mock_get:
            self.mock_get = mock_get
            yield

    def test_fetch_adds_collection_metadata(
        self,
        plugin: RadarrPlugin,
    ) -> None:
        """Movies in collections should get series_name and movie_number."""
        movies = [
            {
                "title": "Back to the Future",
                "monitored": True,
                "tmdbId": 105,
                "year": 1985,
            },
            {
                "title": "Back to the Future Part II",
                "monitored": True,
                "tmdbId": 165,
                "year": 1989,
            },
        ]
        collections = [
            {
                "title": "Back to the Future Collection",
                "movies": [
                    {"tmdbId": 105},
                    {"tmdbId": 165},
                    {"tmdbId": 166},  # Part III - not in our library
                ],
            }
        ]

        def side_effect(*args, **kwargs):
            response = Mock()
            response.raise_for_status = Mock()
            url = args[0] if args else ""
            response.json.return_value = collections if "collection" in url else movies
            return response

        self.mock_get.side_effect = side_effect

        items = list(plugin.fetch({"url": "http://localhost:7878", "api_key": "key"}))

        assert len(items) == 2
        assert items[0].metadata.get("series_name") == "Back to the Future Collection"
        assert items[0].metadata.get("movie_number") == 1
        assert items[1].metadata.get("series_name") == "Back to the Future Collection"
        assert items[1].metadata.get("movie_number") == 2
