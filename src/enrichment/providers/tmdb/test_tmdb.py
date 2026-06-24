"""Tests for the TMDB enrichment provider."""

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
    """Tests for title cleaning before TMDB search."""

    def test_removes_year_suffix(self) -> None:
        """Test removal of year in parentheses."""
        assert clean_media_title_for_search("Monster (2022)") == "Monster"
        assert clean_media_title_for_search("The Matrix (1999)") == "The Matrix"

    def test_removes_country_code(self) -> None:
        """Test removal of country codes."""
        assert clean_media_title_for_search("Euphoria (US)") == "Euphoria"
        assert clean_media_title_for_search("The Office (UK)") == "The Office"

    def test_preserves_title_without_suffix(self) -> None:
        """Test that titles without suffixes are unchanged."""
        assert clean_media_title_for_search("Breaking Bad") == "Breaking Bad"
        assert clean_media_title_for_search("The Sopranos") == "The Sopranos"

    def test_handles_title_with_parentheses_content(self) -> None:
        """Test that meaningful parenthetical content is preserved."""
        # Only removes specific patterns at end of title
        assert (
            clean_media_title_for_search("Spider-Man: Into the Spider-Verse")
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
        # Backward compat: a response without a 'credits' key degrades cleanly.
        assert "director" not in result.extra_metadata

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

    def test_enrich_movie_not_found(
        self, provider: TMDBProvider, movie_item: ContentItem, config: dict[str, Any]
    ) -> None:
        """Test enriching movie that doesn't exist in TMDB."""
        mock_search_response = {"results": []}

        with patch("src.enrichment.providers.tmdb.tmdb.requests.get") as mock_get:
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
        with patch("src.enrichment.providers.tmdb.tmdb.requests.get") as mock_get:
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

    def test_enrich_movie_requests_credits_via_append_to_response(
        self, provider: TMDBProvider, config: dict[str, Any]
    ) -> None:
        """Test that movie detail request appends credits in one round trip."""
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
            "genres": [{"id": 28, "name": "Action"}],
            "credits": {"crew": []},
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

            provider.enrich(item, config)

        detail_call = mock_get.call_args_list[0]
        # The append_to_response must be on the movie DETAIL call, not keywords.
        assert detail_call.args[0].endswith("/movie/603")
        assert detail_call.kwargs["params"]["append_to_response"] == "credits"

    def test_enrich_movie_sets_director_and_excludes_non_director_roles(
        self, provider: TMDBProvider, config: dict[str, Any]
    ) -> None:
        """Test that only Director-job crew is stored, excluding other roles.

        The crew below includes a Writer that must NOT be picked up: only the
        Director-job entry should land in the 'director' field.
        """
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
        # The Writer (Roger Avary) is excluded; only the Director remains.
        assert result.extra_metadata.get("director") == "Quentin Tarantino"

    def test_enrich_movie_comma_joins_multiple_directors(
        self, provider: TMDBProvider, config: dict[str, Any]
    ) -> None:
        """Test that multiple directors are comma-joined and capped at three."""
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

    def _enrich_movie_with_credits(
        self,
        provider: TMDBProvider,
        config: dict[str, Any],
        movie_response: dict[str, Any],
    ) -> EnrichmentResult | None:
        """Enrich a movie via tmdb_id lookup with the given detail payload.

        Mocks the movie-detail call (returning ``movie_response``) followed by an
        empty keywords call, then returns the enrichment result.

        Note: the item fixture is hardcoded with ``tmdb_id`` 603, so the movie
        detail call always targets ``/movie/603`` regardless of the ``id`` field
        in ``movie_response``.
        """
        item = ContentItem(
            id="movie123",
            title="The Matrix",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            metadata={"tmdb_id": 603},
        )
        mock_keywords_response = {"keywords": []}

        with patch("src.enrichment.providers.tmdb.tmdb.requests.get") as mock_get:
            mock_get.side_effect = [
                MagicMock(
                    spec=requests.Response,
                    status_code=200,
                    json=lambda: movie_response,
                ),
                MagicMock(
                    spec=requests.Response,
                    status_code=200,
                    json=lambda: mock_keywords_response,
                ),
            ]

            return provider.enrich(item, config)

    def test_enrich_movie_without_credits_key_omits_director(
        self, provider: TMDBProvider, config: dict[str, Any]
    ) -> None:
        """Test that a response with no 'credits' key omits 'director', no raise."""
        result = self._enrich_movie_with_credits(
            provider,
            config,
            {
                "id": 603,
                "title": "The Matrix",
                "genres": [{"id": 28, "name": "Action"}],
            },
        )

        assert result is not None
        assert "director" not in result.extra_metadata

    def test_enrich_movie_with_credits_but_no_crew_omits_director(
        self, provider: TMDBProvider, config: dict[str, Any]
    ) -> None:
        """Test that 'credits' present without 'crew' omits 'director', no raise."""
        result = self._enrich_movie_with_credits(
            provider,
            config,
            {
                "id": 603,
                "title": "The Matrix",
                "genres": [{"id": 28, "name": "Action"}],
                "credits": {},
            },
        )

        assert result is not None
        assert "director" not in result.extra_metadata

    def test_enrich_movie_with_no_director_in_crew_omits_director(
        self, provider: TMDBProvider, config: dict[str, Any]
    ) -> None:
        """Test that crew with no Director job omits 'director'."""
        result = self._enrich_movie_with_credits(
            provider,
            config,
            {
                "id": 603,
                "title": "The Matrix",
                "genres": [{"id": 28, "name": "Action"}],
                "credits": {"crew": [{"job": "Producer", "name": "Joel Silver"}]},
            },
        )

        assert result is not None
        assert "director" not in result.extra_metadata

    def test_enrich_movie_director_missing_name_is_excluded(
        self, provider: TMDBProvider, config: dict[str, Any]
    ) -> None:
        """Test that a Director entry missing the 'name' key is excluded, no raise."""
        result = self._enrich_movie_with_credits(
            provider,
            config,
            {
                "id": 603,
                "title": "The Matrix",
                "genres": [{"id": 28, "name": "Action"}],
                "credits": {"crew": [{"job": "Director"}]},
            },
        )

        assert result is not None
        assert "director" not in result.extra_metadata

    def test_enrich_movie_director_empty_name_is_excluded(
        self, provider: TMDBProvider, config: dict[str, Any]
    ) -> None:
        """Test that a Director entry with an empty-string name is excluded."""
        result = self._enrich_movie_with_credits(
            provider,
            config,
            {
                "id": 603,
                "title": "The Matrix",
                "genres": [{"id": 28, "name": "Action"}],
                "credits": {"crew": [{"job": "Director", "name": ""}]},
            },
        )

        assert result is not None
        assert "director" not in result.extra_metadata

    def test_enrich_movie_non_exact_director_jobs_excluded(
        self, provider: TMDBProvider, config: dict[str, Any]
    ) -> None:
        """Test that only the exact 'Director' job matches, not variants.

        'Co-Director' and lowercase 'director' must be excluded so the match
        stays an exact, case-sensitive equality.
        """
        result = self._enrich_movie_with_credits(
            provider,
            config,
            {
                "id": 603,
                "title": "The Matrix",
                "genres": [{"id": 28, "name": "Action"}],
                "credits": {
                    "crew": [
                        {"job": "Co-Director", "name": "Someone Else"},
                        {"job": "director", "name": "Lowercase Name"},
                    ]
                },
            },
        )

        assert result is not None
        assert "director" not in result.extra_metadata


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

    def test_enrich_tv_show_not_found(
        self, provider: TMDBProvider, tv_item: ContentItem, config: dict[str, Any]
    ) -> None:
        """Test enriching TV show that doesn't exist."""
        mock_search_response = {"results": []}

        with patch("src.enrichment.providers.tmdb.tmdb.requests.get") as mock_get:
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

        with patch("src.enrichment.providers.tmdb.tmdb.requests.get") as mock_get:
            mock_get.side_effect = [
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

            result = provider.enrich(item, config)

        assert result is not None
        assert result.external_id == "tmdb:1396"

    def test_enrich_tv_show_creator_missing_or_empty_name_is_excluded(
        self, provider: TMDBProvider, config: dict[str, Any]
    ) -> None:
        """Test that creators missing or with empty 'name' are excluded, no raise.

        The 'created_by' list mixes a valid creator with one missing the 'name'
        key and one with an empty-string name. Only the valid creator should be
        captured, and the malformed entries must not raise.
        """
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
            "created_by": [
                {"id": 1},
                {"name": ""},
                {"name": "Vince Gilligan"},
            ],
        }
        mock_keywords_response = {"results": []}

        with patch("src.enrichment.providers.tmdb.tmdb.requests.get") as mock_get:
            mock_get.side_effect = [
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

            result = provider.enrich(item, config)

        assert result is not None
        # Malformed entries are skipped; the valid creator is still captured.
        assert result.extra_metadata.get("creators") == "Vince Gilligan"

    def test_enrich_tv_show_comma_joins_multiple_creators(
        self, provider: TMDBProvider, config: dict[str, Any]
    ) -> None:
        """Test that multiple creators are comma-joined and capped at three."""
        item = ContentItem(
            id="show123",
            title="Game of Thrones",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"tmdb_id": 1399},
        )

        mock_tv_response = {
            "id": 1399,
            "name": "Game of Thrones",
            "overview": "Noble families vie for control of the Iron Throne.",
            "genres": [{"id": 18, "name": "Drama"}],
            "created_by": [
                {"name": "David Benioff"},
                {"name": "D. B. Weiss"},
                {"name": "George R. R. Martin"},
                {"name": "Extra Creator"},
            ],
        }
        mock_keywords_response = {"results": []}

        with patch("src.enrichment.providers.tmdb.tmdb.requests.get") as mock_get:
            mock_get.side_effect = [
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

            result = provider.enrich(item, config)

        assert result is not None
        assert (
            result.extra_metadata.get("creators")
            == "David Benioff, D. B. Weiss, George R. R. Martin"
        )


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

        with patch("src.enrichment.providers.tmdb.tmdb.requests.get") as mock_get:
            # Movie details succeeds, keywords fails
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

        with patch("src.enrichment.providers.tmdb.tmdb.requests.get") as mock_get:
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


class TestTMDBApiKeyScrubbingRegression:
    """Regression tests for the TMDB API key leaking via error messages.

    Bug: TMDB requests pass the key as a query parameter
    (``?api_key=<secret>``). On a failed request, ``requests.HTTPError``'s
    ``str()`` embeds the full request URL — key and all. The provider
    interpolated the raw exception into ``ProviderError(... f"...: {error}")``,
    and that message flows into the enrichment status the web API and CLI
    surface to users and logs, leaking the key.

    Root cause: ``f"...: {error}"`` called the default
    ``RequestException.__str__`` containing the request URL with the
    ``api_key`` query parameter.

    Fix: each ``except requests.RequestException`` block now renders the
    error through :func:`src.utils.request_errors.scrub_request_error`, which
    emits only ``HTTP <status>`` or the exception class name.
    """

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
        """A failed search surfaces only the status code, not the key."""
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

    def test_search_retry_error_does_not_leak_api_key(self) -> None:
        """A failed year-less retry search surfaces only the status code.

        The first request returns empty results, triggering the year-less
        retry; that second request fails. Its error must still be scrubbed.
        """
        provider = TMDBProvider()
        item = ContentItem(
            id="movie1",
            title="The Matrix",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            metadata={"release_year": 1999},
        )

        empty_response = MagicMock(spec=requests.Response)
        empty_response.raise_for_status.return_value = None
        empty_response.json.return_value = {"results": []}

        retry_response = MagicMock(spec=requests.Response)
        retry_response.raise_for_status.side_effect = self._http_error(502)

        with patch("src.enrichment.providers.tmdb.tmdb.requests.get") as mock_get:
            mock_get.side_effect = [empty_response, retry_response]

            with pytest.raises(ProviderError) as exc_info:
                provider.enrich(item, self._config())

        assert mock_get.call_count == 2
        message = str(exc_info.value)
        assert self._API_KEY not in message
        assert "api_key=" not in message
        assert "Failed to search TMDB: HTTP 502" in message

    def test_movie_details_error_does_not_leak_api_key(self) -> None:
        """A failed movie-details fetch surfaces only the status code."""
        provider = TMDBProvider()
        item = ContentItem(
            id="movie1",
            title="The Matrix",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            metadata={"tmdb_id": 603},
        )

        with patch("src.enrichment.providers.tmdb.tmdb.requests.get") as mock_get:
            response = mock_get.return_value
            response.raise_for_status.side_effect = self._http_error(503)

            with pytest.raises(ProviderError) as exc_info:
                provider.enrich(item, self._config())

        message = str(exc_info.value)
        assert self._API_KEY not in message
        assert "api_key=" not in message
        assert "Failed to fetch movie details: HTTP 503" in message

    def test_tv_details_error_does_not_leak_api_key(self) -> None:
        """A failed TV-details fetch surfaces only the status code."""
        provider = TMDBProvider()
        item = ContentItem(
            id="show1",
            title="Breaking Bad",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.UNREAD,
            metadata={"tmdb_id": 1396},
        )

        with patch("src.enrichment.providers.tmdb.tmdb.requests.get") as mock_get:
            response = mock_get.return_value
            response.raise_for_status.side_effect = self._http_error(500)

            with pytest.raises(ProviderError) as exc_info:
                provider.enrich(item, self._config())

        message = str(exc_info.value)
        assert self._API_KEY not in message
        assert "api_key=" not in message
        assert "Failed to fetch TV show details: HTTP 500" in message

    def test_transport_error_surfaces_only_exception_type(self) -> None:
        """A connection error surfaces only the class name, not its message."""
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
