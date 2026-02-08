"""Tests for the TMDB enrichment provider."""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.enrichment.provider_base import ProviderError
from src.enrichment.providers.tmdb import TMDBProvider, clean_title_for_search
from src.models.content import ConsumptionStatus, ContentItem, ContentType


class TestCleanTitleForSearch:
    """Tests for title cleaning before TMDB search."""

    def test_removes_year_suffix(self) -> None:
        """Test removal of year in parentheses."""
        assert clean_title_for_search("Monster (2022)") == "Monster"
        assert clean_title_for_search("The Matrix (1999)") == "The Matrix"

    def test_removes_country_code(self) -> None:
        """Test removal of country codes."""
        assert clean_title_for_search("Euphoria (US)") == "Euphoria"
        assert clean_title_for_search("The Office (UK)") == "The Office"

    def test_preserves_title_without_suffix(self) -> None:
        """Test that titles without suffixes are unchanged."""
        assert clean_title_for_search("Breaking Bad") == "Breaking Bad"
        assert clean_title_for_search("The Sopranos") == "The Sopranos"

    def test_handles_title_with_parentheses_content(self) -> None:
        """Test that meaningful parenthetical content is preserved."""
        # Only removes specific patterns at end of title
        assert (
            clean_title_for_search("Spider-Man: Into the Spider-Verse")
            == "Spider-Man: Into the Spider-Verse"
        )


class TestTMDBProviderProperties:
    """Tests for TMDB provider properties."""

    def test_name(self) -> None:
        """Test provider name."""
        provider = TMDBProvider()
        assert provider.name == "tmdb"

    def test_display_name(self) -> None:
        """Test display name."""
        provider = TMDBProvider()
        assert provider.display_name == "TMDB"

    def test_content_types(self) -> None:
        """Test supported content types."""
        provider = TMDBProvider()
        assert ContentType.MOVIE in provider.content_types
        assert ContentType.TV_SHOW in provider.content_types
        assert ContentType.BOOK not in provider.content_types

    def test_requires_api_key(self) -> None:
        """Test that API key is required."""
        provider = TMDBProvider()
        assert provider.requires_api_key is True

    def test_rate_limit(self) -> None:
        """Test rate limit setting."""
        provider = TMDBProvider()
        assert provider.rate_limit_requests_per_second == 40.0

    def test_config_schema(self) -> None:
        """Test config schema."""
        provider = TMDBProvider()
        schema = provider.get_config_schema()

        assert len(schema) == 3
        api_key_field = schema[0]
        assert api_key_field.name == "api_key"
        assert api_key_field.required is True
        assert api_key_field.sensitive is True


class TestTMDBProviderValidation:
    """Tests for TMDB provider config validation."""

    def test_validate_valid_config(self) -> None:
        """Test validation with valid config."""
        provider = TMDBProvider()
        errors = provider.validate_config({"api_key": "test-key"})
        assert errors == []

    def test_validate_missing_api_key(self) -> None:
        """Test validation with missing API key."""
        provider = TMDBProvider()
        errors = provider.validate_config({})
        assert "'api_key' is required for TMDB provider" in errors

    def test_validate_empty_api_key(self) -> None:
        """Test validation with empty API key."""
        provider = TMDBProvider()
        errors = provider.validate_config({"api_key": ""})
        assert "'api_key' is required for TMDB provider" in errors


class TestTMDBProviderMovieEnrichment:
    """Tests for TMDB movie enrichment."""

    @pytest.fixture
    def provider(self) -> TMDBProvider:
        """Create TMDB provider instance."""
        return TMDBProvider()

    @pytest.fixture
    def movie_item(self) -> ContentItem:
        """Create a sample movie item."""
        return ContentItem(
            id="movie123",
            title="The Matrix",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            metadata={"release_year": 1999},
        )

    @pytest.fixture
    def config(self) -> dict[str, Any]:
        """Create test config."""
        return {"api_key": "test-api-key", "language": "en-US"}

    def test_enrich_movie_with_id_lookup(
        self, provider: TMDBProvider, config: dict[str, Any]
    ) -> None:
        """Test enriching movie when tmdb_id is in metadata."""
        item = ContentItem(
            id="movie123",
            title="The Matrix",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            metadata={"tmdb_id": 603},
        )

        mock_movie_response = {
            "id": 603,
            "title": "The Matrix",
            "overview": "A computer hacker learns about the true nature of reality.",
            "genres": [
                {"id": 28, "name": "Action"},
                {"id": 878, "name": "Science Fiction"},
            ],
            "runtime": 136,
            "vote_average": 8.2,
            "release_date": "1999-03-30",
            "original_language": "en",
            "production_companies": [{"name": "Warner Bros."}],
        }

        mock_keywords_response = {
            "keywords": [
                {"id": 1, "name": "dystopia"},
                {"id": 2, "name": "virtual reality"},
            ]
        }

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                MagicMock(status_code=200, json=lambda: mock_movie_response),
                MagicMock(status_code=200, json=lambda: mock_keywords_response),
            ]

            result = provider.enrich(item, config)

        assert result is not None
        assert result.external_id == "tmdb:603"
        assert result.genres == ["Action", "Science Fiction"]
        assert result.tags == ["dystopia", "virtual reality"]
        assert "hacker" in result.description
        assert result.match_quality == "high"
        assert result.provider == "tmdb"
        assert result.extra_metadata.get("runtime") == 136
        assert result.extra_metadata.get("release_year") == 1999

    def test_enrich_movie_with_search(
        self, provider: TMDBProvider, movie_item: ContentItem, config: dict[str, Any]
    ) -> None:
        """Test enriching movie using title search."""
        mock_search_response = {"results": [{"id": 603, "title": "The Matrix"}]}

        mock_movie_response = {
            "id": 603,
            "title": "The Matrix",
            "overview": "A computer hacker learns about the true nature of reality.",
            "genres": [{"id": 28, "name": "Action"}],
            "runtime": 136,
            "release_date": "1999-03-30",
        }

        mock_keywords_response = {"keywords": []}

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                MagicMock(status_code=200, json=lambda: mock_search_response),
                MagicMock(status_code=200, json=lambda: mock_movie_response),
                MagicMock(status_code=200, json=lambda: mock_keywords_response),
            ]

            result = provider.enrich(movie_item, config)

        assert result is not None
        assert result.external_id == "tmdb:603"
        assert result.match_quality == "high"

    def test_enrich_movie_not_found(
        self, provider: TMDBProvider, movie_item: ContentItem, config: dict[str, Any]
    ) -> None:
        """Test enriching movie that doesn't exist in TMDB."""
        mock_search_response = {"results": []}

        with patch("requests.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200, json=lambda: mock_search_response
            )

            result = provider.enrich(movie_item, config)

        assert result is not None
        assert result.match_quality == "not_found"
        assert result.genres is None

    def test_enrich_movie_api_error(
        self, provider: TMDBProvider, movie_item: ContentItem, config: dict[str, Any]
    ) -> None:
        """Test that API errors raise ProviderError."""
        with patch("requests.get") as mock_get:
            mock_get.side_effect = requests.RequestException("Connection failed")

            with pytest.raises(ProviderError) as exc_info:
                provider.enrich(movie_item, config)

            assert "Failed to search TMDB" in str(exc_info.value)

    def test_enrich_movie_fallback_to_title_only(
        self, provider: TMDBProvider, config: dict[str, Any]
    ) -> None:
        """Test that search falls back to title-only when year yields no results."""
        item = ContentItem(
            id="movie123",
            title="Some Movie",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            metadata={"release_year": 2020},
        )

        # First search with year returns empty
        mock_empty_response = {"results": []}
        # Second search without year finds movie
        mock_found_response = {"results": [{"id": 12345, "title": "Some Movie"}]}
        mock_movie_response = {
            "id": 12345,
            "title": "Some Movie",
            "overview": "A great movie.",
            "genres": [{"id": 28, "name": "Action"}],
        }
        mock_keywords_response = {"keywords": []}

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                MagicMock(status_code=200, json=lambda: mock_empty_response),
                MagicMock(status_code=200, json=lambda: mock_found_response),
                MagicMock(status_code=200, json=lambda: mock_movie_response),
                MagicMock(status_code=200, json=lambda: mock_keywords_response),
            ]

            result = provider.enrich(item, config)

        assert result is not None
        assert result.external_id == "tmdb:12345"

    def test_enrich_movie_with_external_id_format(
        self, provider: TMDBProvider, config: dict[str, Any]
    ) -> None:
        """Test enriching movie using tmdb: prefix in external_id."""
        item = ContentItem(
            id="tmdb:603",
            title="The Matrix",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )

        mock_movie_response = {
            "id": 603,
            "title": "The Matrix",
            "overview": "A movie about reality.",
            "genres": [{"id": 28, "name": "Action"}],
        }
        mock_keywords_response = {"keywords": []}

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                MagicMock(status_code=200, json=lambda: mock_movie_response),
                MagicMock(status_code=200, json=lambda: mock_keywords_response),
            ]

            result = provider.enrich(item, config)

        assert result is not None
        assert result.external_id == "tmdb:603"


class TestTMDBProviderTVShowEnrichment:
    """Tests for TMDB TV show enrichment."""

    @pytest.fixture
    def provider(self) -> TMDBProvider:
        """Create TMDB provider instance."""
        return TMDBProvider()

    @pytest.fixture
    def tv_item(self) -> ContentItem:
        """Create a sample TV show item."""
        return ContentItem(
            id="show123",
            title="Breaking Bad",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"release_year": 2008},
        )

    @pytest.fixture
    def config(self) -> dict[str, Any]:
        """Create test config."""
        return {"api_key": "test-api-key"}

    def test_enrich_tv_show_with_search(
        self, provider: TMDBProvider, tv_item: ContentItem, config: dict[str, Any]
    ) -> None:
        """Test enriching TV show using title search."""
        mock_search_response = {"results": [{"id": 1396, "name": "Breaking Bad"}]}

        mock_tv_response = {
            "id": 1396,
            "name": "Breaking Bad",
            "overview": "A high school chemistry teacher diagnosed with cancer.",
            "genres": [{"id": 18, "name": "Drama"}, {"id": 80, "name": "Crime"}],
            "number_of_seasons": 5,
            "number_of_episodes": 62,
            "vote_average": 8.9,
            "first_air_date": "2008-01-20",
            "networks": [{"name": "AMC"}],
            "created_by": [{"name": "Vince Gilligan"}],
            "status": "Ended",
        }

        mock_keywords_response = {
            "results": [
                {"id": 1, "name": "crime"},
                {"id": 2, "name": "drug trade"},
            ]
        }

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                MagicMock(status_code=200, json=lambda: mock_search_response),
                MagicMock(status_code=200, json=lambda: mock_tv_response),
                MagicMock(status_code=200, json=lambda: mock_keywords_response),
            ]

            result = provider.enrich(tv_item, config)

        assert result is not None
        assert result.external_id == "tmdb:1396"
        assert result.genres == ["Drama", "Crime"]
        assert result.tags == ["crime", "drug trade"]
        assert "chemistry teacher" in result.description
        assert result.match_quality == "high"
        assert result.extra_metadata.get("seasons") == 5
        assert result.extra_metadata.get("episodes") == 62
        assert result.extra_metadata.get("network") == "AMC"
        assert "Vince Gilligan" in result.extra_metadata.get("creators", "")

    def test_enrich_tv_show_not_found(
        self, provider: TMDBProvider, tv_item: ContentItem, config: dict[str, Any]
    ) -> None:
        """Test enriching TV show that doesn't exist."""
        mock_search_response = {"results": []}

        with patch("requests.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200, json=lambda: mock_search_response
            )

            result = provider.enrich(tv_item, config)

        assert result is not None
        assert result.match_quality == "not_found"

    def test_enrich_tv_show_with_id(
        self, provider: TMDBProvider, config: dict[str, Any]
    ) -> None:
        """Test enriching TV show with tmdb_id in metadata."""
        item = ContentItem(
            id="show123",
            title="Breaking Bad",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"tmdb_id": 1396},
        )

        mock_tv_response = {
            "id": 1396,
            "name": "Breaking Bad",
            "overview": "A great show.",
            "genres": [{"id": 18, "name": "Drama"}],
        }
        mock_keywords_response = {"results": []}

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                MagicMock(status_code=200, json=lambda: mock_tv_response),
                MagicMock(status_code=200, json=lambda: mock_keywords_response),
            ]

            result = provider.enrich(item, config)

        assert result is not None
        assert result.external_id == "tmdb:1396"


class TestTMDBProviderKeywords:
    """Tests for TMDB keyword fetching."""

    @pytest.fixture
    def provider(self) -> TMDBProvider:
        """Create TMDB provider instance."""
        return TMDBProvider()

    @pytest.fixture
    def config(self) -> dict[str, Any]:
        """Create test config."""
        return {"api_key": "test-api-key", "include_keywords": True}

    def test_keywords_failure_does_not_fail_enrichment(
        self, provider: TMDBProvider, config: dict[str, Any]
    ) -> None:
        """Test that keyword fetch failure doesn't fail the whole enrichment."""
        item = ContentItem(
            id="movie123",
            title="Test Movie",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            metadata={"tmdb_id": 12345},
        )

        mock_movie_response = {
            "id": 12345,
            "title": "Test Movie",
            "overview": "A test movie.",
            "genres": [{"id": 28, "name": "Action"}],
        }

        with patch("requests.get") as mock_get:
            # Movie details succeeds, keywords fails
            mock_get.side_effect = [
                MagicMock(status_code=200, json=lambda: mock_movie_response),
                requests.RequestException("Keywords failed"),
            ]

            result = provider.enrich(item, config)

        assert result is not None
        assert result.genres == ["Action"]
        assert result.tags is None  # Keywords failed but enrichment succeeded

    def test_skip_keywords_when_disabled(self, provider: TMDBProvider) -> None:
        """Test that keywords are not fetched when disabled."""
        config = {"api_key": "test-api-key", "include_keywords": False}
        item = ContentItem(
            id="movie123",
            title="Test Movie",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            metadata={"tmdb_id": 12345},
        )

        mock_movie_response = {
            "id": 12345,
            "title": "Test Movie",
            "overview": "A test movie.",
            "genres": [{"id": 28, "name": "Action"}],
        }

        with patch("requests.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200, json=lambda: mock_movie_response
            )

            result = provider.enrich(item, config)

        # Should only be one call (movie details, no keywords)
        assert mock_get.call_count == 1
        assert result is not None
        assert result.tags is None


class TestTMDBProviderUnsupportedTypes:
    """Tests for handling unsupported content types."""

    def test_enrich_book_returns_none(self) -> None:
        """Test that enriching a book returns None."""
        provider = TMDBProvider()
        item = ContentItem(
            id="book123",
            title="Some Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )

        result = provider.enrich(item, {"api_key": "test"})
        assert result is None

    def test_enrich_video_game_returns_none(self) -> None:
        """Test that enriching a video game returns None."""
        provider = TMDBProvider()
        item = ContentItem(
            id="game123",
            title="Some Game",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        )

        result = provider.enrich(item, {"api_key": "test"})
        assert result is None
