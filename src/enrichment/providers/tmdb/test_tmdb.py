import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.enrichment.provider_base import EnrichmentResult, ProviderError
from src.enrichment.providers.tmdb.tmdb import (
    TMDBProvider,
    clean_media_title_for_search,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType


class TestCleanTitleForSearch:
    def test_removes_year_suffix(self) -> None:
        assert clean_media_title_for_search("Monster (2022)") == "Monster"
        assert clean_media_title_for_search("The Matrix (1999)") == "The Matrix"

    def test_removes_country_code(self) -> None:
        assert clean_media_title_for_search("Euphoria (US)") == "Euphoria"
        assert clean_media_title_for_search("The Office (UK)") == "The Office"


class TestTMDBProviderValidation:
    def test_validate_missing_api_key(self) -> None:
        provider = TMDBProvider()
        errors = provider.validate_config({})
        assert "'api_key' is required for TMDB provider" in errors


class TestTMDBProviderMovieEnrichment:
    @pytest.fixture
    def provider(self) -> TMDBProvider:
        return TMDBProvider()

    @pytest.fixture
    def movie_item(self) -> ContentItem:
        return ContentItem(
            id="movie123",
            title="The Matrix",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            metadata={"release_year": 1999},
        )

    @pytest.fixture
    def config(self) -> dict[str, Any]:
        return {"api_key": "test-api-key", "language": "en-US"}

    def test_enrich_movie_with_id_lookup(
        self, provider: TMDBProvider, config: dict[str, Any]
    ) -> None:
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
            "poster_path": "/abc123.jpg",
        }

        mock_keywords_response = {
            "keywords": [
                {"id": 1, "name": "dystopia"},
                {"id": 2, "name": "virtual reality"},
            ]
        }

        with patch("src.enrichment.providers.tmdb.tmdb.requests.get") as mock_get:
            mock_get.side_effect = [
                MagicMock(
                    spec=requests.Response,
                    status_code=200,
                    json=lambda: mock_movie_response,
                ),
                MagicMock(
                    spec=requests.Response,
                    status_code=200,
                    json=lambda: mock_keywords_response,
                ),
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
        assert result.cover_url == "https://image.tmdb.org/t/p/w500/abc123.jpg"
        assert "director" not in result.extra_metadata

    def test_enrich_movie_with_search(
        self, provider: TMDBProvider, movie_item: ContentItem, config: dict[str, Any]
    ) -> None:
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

        with patch("src.enrichment.providers.tmdb.tmdb.requests.get") as mock_get:
            mock_get.side_effect = [
                MagicMock(
                    spec=requests.Response,
                    status_code=200,
                    json=lambda: mock_search_response,
                ),
                MagicMock(
                    spec=requests.Response,
                    status_code=200,
                    json=lambda: mock_movie_response,
                ),
                MagicMock(
                    spec=requests.Response,
                    status_code=200,
                    json=lambda: mock_keywords_response,
                ),
            ]

            result = provider.enrich(movie_item, config)

        assert result is not None
        assert result.external_id == "tmdb:603"
        assert result.match_quality == "high"
        assert result.cover_url is None

    def test_enrich_movie_not_found(
        self, provider: TMDBProvider, movie_item: ContentItem, config: dict[str, Any]
    ) -> None:
        mock_search_response = {"results": []}

        with patch("src.enrichment.providers.tmdb.tmdb.requests.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200, json=lambda: mock_search_response
            )

            result = provider.enrich(movie_item, config)

        assert result is not None
        assert result.match_quality == "not_found"
        assert result.genres is None

    def test_enrich_movie_fallback_to_title_only(
        self, provider: TMDBProvider, config: dict[str, Any]
    ) -> None:
        item = ContentItem(
            id="movie123",
            title="Some Movie",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            metadata={"release_year": 2020},
        )

        mock_empty_response = {"results": []}
        mock_found_response = {"results": [{"id": 12345, "title": "Some Movie"}]}
        mock_movie_response = {
            "id": 12345,
            "title": "Some Movie",
            "overview": "A great movie.",
            "genres": [{"id": 28, "name": "Action"}],
        }
        mock_keywords_response = {"keywords": []}

        with patch("src.enrichment.providers.tmdb.tmdb.requests.get") as mock_get:
            mock_get.side_effect = [
                MagicMock(
                    spec=requests.Response,
                    status_code=200,
                    json=lambda: mock_empty_response,
                ),
                MagicMock(
                    spec=requests.Response,
                    status_code=200,
                    json=lambda: mock_found_response,
                ),
                MagicMock(
                    spec=requests.Response,
                    status_code=200,
                    json=lambda: mock_movie_response,
                ),
                MagicMock(
                    spec=requests.Response,
                    status_code=200,
                    json=lambda: mock_keywords_response,
                ),
            ]

            result = provider.enrich(item, config)

        assert result is not None
        assert result.external_id == "tmdb:12345"

    def test_enrich_movie_sets_director_and_excludes_non_director_roles(
        self, provider: TMDBProvider, config: dict[str, Any]
    ) -> None:
        item = ContentItem(
            id="movie123",
            title="Pulp Fiction",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            metadata={"tmdb_id": 680},
        )

        mock_movie_response = {
            "id": 680,
            "title": "Pulp Fiction",
            "genres": [{"id": 80, "name": "Crime"}],
            "credits": {
                "crew": [
                    {"job": "Writer", "name": "Roger Avary"},
                    {"job": "Director", "name": "Quentin Tarantino"},
                ]
            },
        }
        mock_keywords_response = {"keywords": []}

        with patch("src.enrichment.providers.tmdb.tmdb.requests.get") as mock_get:
            mock_get.side_effect = [
                MagicMock(
                    spec=requests.Response,
                    status_code=200,
                    json=lambda: mock_movie_response,
                ),
                MagicMock(
                    spec=requests.Response,
                    status_code=200,
                    json=lambda: mock_keywords_response,
                ),
            ]

            result = provider.enrich(item, config)

        assert result is not None
        assert result.extra_metadata.get("director") == "Quentin Tarantino"

    def test_enrich_movie_comma_joins_multiple_directors(
        self, provider: TMDBProvider, config: dict[str, Any]
    ) -> None:
        item = ContentItem(
            id="movie123",
            title="Cloud Atlas",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            metadata={"tmdb_id": 83542},
        )

        mock_movie_response = {
            "id": 83542,
            "title": "Cloud Atlas",
            "genres": [{"id": 18, "name": "Drama"}],
            "credits": {
                "crew": [
                    {"job": "Director", "name": "Lana Wachowski"},
                    {"job": "Director", "name": "Tom Tykwer"},
                    {"job": "Director", "name": "Lilly Wachowski"},
                    {"job": "Director", "name": "Extra Director"},
                ]
            },
        }
        mock_keywords_response = {"keywords": []}

        with patch("src.enrichment.providers.tmdb.tmdb.requests.get") as mock_get:
            mock_get.side_effect = [
                MagicMock(
                    spec=requests.Response,
                    status_code=200,
                    json=lambda: mock_movie_response,
                ),
                MagicMock(
                    spec=requests.Response,
                    status_code=200,
                    json=lambda: mock_keywords_response,
                ),
            ]

            result = provider.enrich(item, config)

        assert result is not None
        assert (
            result.extra_metadata.get("director")
            == "Lana Wachowski, Tom Tykwer, Lilly Wachowski"
        )


class TestTMDBProviderTVShowEnrichment:
    @pytest.fixture
    def provider(self) -> TMDBProvider:
        return TMDBProvider()

    @pytest.fixture
    def tv_item(self) -> ContentItem:
        return ContentItem(
            id="show123",
            title="Breaking Bad",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"release_year": 2008},
        )

    @pytest.fixture
    def config(self) -> dict[str, Any]:
        return {"api_key": "test-api-key"}

    def test_enrich_tv_show_with_search(
        self, provider: TMDBProvider, tv_item: ContentItem, config: dict[str, Any]
    ) -> None:
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
            "poster_path": "/bb.jpg",
        }

        mock_keywords_response = {
            "results": [
                {"id": 1, "name": "crime"},
                {"id": 2, "name": "drug trade"},
            ]
        }

        with patch("src.enrichment.providers.tmdb.tmdb.requests.get") as mock_get:
            mock_get.side_effect = [
                MagicMock(
                    spec=requests.Response,
                    status_code=200,
                    json=lambda: mock_search_response,
                ),
                MagicMock(
                    spec=requests.Response,
                    status_code=200,
                    json=lambda: mock_tv_response,
                ),
                MagicMock(
                    spec=requests.Response,
                    status_code=200,
                    json=lambda: mock_keywords_response,
                ),
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
        assert result.cover_url == "https://image.tmdb.org/t/p/w500/bb.jpg"


class TestTMDBSeasonEpisodeCounts:
    """Without a count per season every season looks the same size, so nothing
    downstream can tell a finished season from a merely caught-up one."""

    def _enrich(self, show: dict[str, Any]) -> EnrichmentResult:
        item = ContentItem(
            id="show123",
            title="Breaking Bad",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"tmdb_id": show["id"]},
        )

        with patch("src.enrichment.providers.tmdb.tmdb.requests.get") as mock_get:
            mock_get.return_value = MagicMock(
                spec=requests.Response, status_code=200, json=lambda: show
            )
            result = TMDBProvider().enrich(
                item, {"api_key": "test-api-key", "include_keywords": False}
            )

        assert result is not None
        return result

    def test_each_season_keeps_its_episode_count_without_specials(self) -> None:
        result = self._enrich(
            {
                "id": 1396,
                "name": "Breaking Bad",
                "number_of_seasons": 5,
                "number_of_episodes": 62,
                "seasons": [
                    {"season_number": 0, "episode_count": 5},
                    {"season_number": 1, "episode_count": 7},
                    {"season_number": 2, "episode_count": 13},
                ],
            }
        )

        assert result.extra_metadata["season_episode_counts"] == {"1": 7, "2": 13}
        assert result.extra_metadata["seasons"] == 5
        assert result.extra_metadata["episodes"] == 62

    def test_a_show_with_no_countable_season_writes_no_counts_key(self) -> None:
        result = self._enrich(
            {
                "id": 1396,
                "name": "Breaking Bad",
                "seasons": [
                    {"season_number": 0, "episode_count": 5},
                    {"season_number": 3, "episode_count": 0},
                ],
            }
        )

        assert "season_episode_counts" not in result.extra_metadata


class TestTMDBProviderKeywords:
    @pytest.fixture
    def provider(self) -> TMDBProvider:
        return TMDBProvider()

    @pytest.fixture
    def config(self) -> dict[str, Any]:
        return {"api_key": "test-api-key", "include_keywords": True}

    def test_keywords_failure_does_not_fail_enrichment(
        self, provider: TMDBProvider, config: dict[str, Any]
    ) -> None:
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

        with patch("src.enrichment.providers.tmdb.tmdb.requests.get") as mock_get:
            mock_get.side_effect = [
                MagicMock(
                    spec=requests.Response,
                    status_code=200,
                    json=lambda: mock_movie_response,
                ),
                requests.RequestException("Keywords failed"),
            ]

            result = provider.enrich(item, config)

        assert result is not None
        assert result.genres == ["Action"]
        assert result.tags is None

    def test_skip_keywords_when_disabled(self, provider: TMDBProvider) -> None:
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

        with patch("src.enrichment.providers.tmdb.tmdb.requests.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200, json=lambda: mock_movie_response
            )

            result = provider.enrich(item, config)

        assert mock_get.call_count == 1
        assert result is not None
        assert result.tags is None


class TestTMDBProviderUnsupportedTypes:
    def test_enrich_book_returns_none(self) -> None:
        provider = TMDBProvider()
        item = ContentItem(
            id="book123",
            title="Some Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )

        result = provider.enrich(item, {"api_key": "test"})
        assert result is None


class TestTMDBApiKeyScrubbingRegression:
    _API_KEY = "SECRET_TMDB_KEY_123"

    def _http_error(self, status_code: int) -> requests.HTTPError:
        """Build an HTTPError whose str() embeds the api_key, like requests."""
        response = MagicMock(spec=requests.Response)
        response.status_code = status_code
        url = (
            "https://api.themoviedb.org/3/movie/603"
            f"?api_key={self._API_KEY}&language=en-US"
        )
        return requests.HTTPError(
            f"{status_code} Client Error for url: {url}", response=response
        )

    def _config(self) -> dict[str, Any]:
        return {"api_key": self._API_KEY, "language": "en-US"}

    def test_search_error_does_not_leak_api_key(self) -> None:
        provider = TMDBProvider()
        item = ContentItem(
            id="movie1",
            title="The Matrix",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            metadata={"release_year": 1999},
        )

        with patch("src.enrichment.providers.tmdb.tmdb.requests.get") as mock_get:
            response = mock_get.return_value
            response.raise_for_status.side_effect = self._http_error(401)

            with pytest.raises(ProviderError) as exc_info:
                provider.enrich(item, self._config())

        message = str(exc_info.value)
        assert self._API_KEY not in message
        assert "api_key=" not in message
        assert "Failed to search TMDB: HTTP 401" in message

    def test_transport_error_surfaces_only_exception_type(self) -> None:
        provider = TMDBProvider()
        item = ContentItem(
            id="movie1",
            title="The Matrix",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            metadata={"release_year": 1999},
        )

        with patch("src.enrichment.providers.tmdb.tmdb.requests.get") as mock_get:
            mock_get.side_effect = requests.ConnectionError(
                f"Failed to connect; api_key={self._API_KEY} was in URL"
            )

            with pytest.raises(ProviderError) as exc_info:
                provider.enrich(item, self._config())

        message = str(exc_info.value)
        assert self._API_KEY not in message
        assert "api_key=" not in message
        assert "ConnectionError" in message


class TestSearchTitleCannotForgeALogLineRegression:
    _FORGED = "Real Title\nWARNING  | forged | line (1999)"

    def test_a_newline_in_a_title_is_escaped_before_the_search_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Reached whenever cleaning changes the title, so every year suffix."""
        provider = TMDBProvider()
        item = ContentItem(
            id="movie1",
            title=self._FORGED,
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )

        with (
            patch("src.enrichment.providers.tmdb.tmdb.requests.get") as mock_get,
            caplog.at_level(logging.DEBUG, logger="src.enrichment.providers.tmdb.tmdb"),
        ):
            mock_get.return_value.json.return_value = {"results": []}
            assert provider._search_movie(item, "test-key", "en-US") is None

        assert "Real Title\\nWARNING" in caplog.text
        assert self._FORGED not in caplog.text
