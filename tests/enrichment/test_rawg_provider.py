"""Tests for the RAWG enrichment provider."""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.enrichment.provider_base import ProviderError
from src.enrichment.providers.rawg import (
    RAWGProvider,
    _filter_outlier_titles,
    _longest_common_prefix,
    clean_game_title_for_search,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType


class TestCleanTitleForSearch:
    """Tests for title cleaning before search."""

    def test_removes_goty_edition_dash(self) -> None:
        """Test removal of GOTY Edition with dash."""
        assert (
            clean_game_title_for_search("The Witcher 3: Wild Hunt - GOTY Edition")
            == "The Witcher 3: Wild Hunt"
        )

    def test_removes_deluxe_edition_colon(self) -> None:
        """Test removal of Deluxe Edition with colon."""
        assert (
            clean_game_title_for_search("Horizon Zero Dawn: Complete Edition")
            == "Horizon Zero Dawn"
        )

    def test_removes_remastered_dash(self) -> None:
        """Test removal of Remastered suffix."""
        assert clean_game_title_for_search("Dark Souls - Remastered") == "Dark Souls"

    def test_removes_edition_in_parentheses(self) -> None:
        """Test removal of edition in parentheses."""
        assert clean_game_title_for_search("Mass Effect (Legendary)") == "Mass Effect"
        assert clean_game_title_for_search("Skyrim (Special Edition)") == "Skyrim"

    def test_removes_trademark_symbols(self) -> None:
        """Test removal of trademark and registered symbols."""
        assert clean_game_title_for_search("Cyberpunk 2077™") == "Cyberpunk 2077"
        assert clean_game_title_for_search("DOOM®") == "DOOM"
        assert clean_game_title_for_search("The Sims™ 4") == "The Sims 4"

    def test_preserves_title_without_edition(self) -> None:
        """Test that titles without edition info are unchanged."""
        assert clean_game_title_for_search("Elden Ring") == "Elden Ring"
        assert clean_game_title_for_search("Hollow Knight") == "Hollow Knight"

    def test_preserves_colons_in_subtitles(self) -> None:
        """Test that colons in game subtitles are preserved."""
        assert (
            clean_game_title_for_search("The Witcher 3: Wild Hunt")
            == "The Witcher 3: Wild Hunt"
        )
        assert (
            clean_game_title_for_search("Resident Evil 4: Separate Ways")
            == "Resident Evil 4: Separate Ways"
        )

    def test_handles_combined_patterns(self) -> None:
        """Test that multiple patterns are handled together."""
        assert (
            clean_game_title_for_search("DOOM® Eternal - Deluxe Edition")
            == "DOOM Eternal"
        )

    def test_removes_dlc_suffix(self) -> None:
        """Test removal of DLC suffix like '+ Re Mind (DLC)'."""
        assert (
            clean_game_title_for_search("KINGDOM HEARTS III + Re Mind (DLC)")
            == "KINGDOM HEARTS III"
        )

    def test_removes_generic_dlc_suffix(self) -> None:
        """Test removal of generic DLC suffix."""
        assert clean_game_title_for_search("Game + DLC Pack (DLC)") == "Game"

    def test_dlc_suffix_case_insensitive(self) -> None:
        """Test that DLC suffix removal is case insensitive."""
        assert clean_game_title_for_search("Game + Expansion (dlc)") == "Game"

    def test_title_without_dlc_unchanged(self) -> None:
        """Test that titles without DLC suffix are unchanged."""
        assert clean_game_title_for_search("Elden Ring") == "Elden Ring"
        assert clean_game_title_for_search("Final Fantasy X") == "Final Fantasy X"


class TestRAWGProviderProperties:
    """Tests for RAWG provider properties."""

    def test_name(self) -> None:
        """Test provider name."""
        provider = RAWGProvider()
        assert provider.name == "rawg"

    def test_display_name(self) -> None:
        """Test display name."""
        provider = RAWGProvider()
        assert provider.display_name == "RAWG"

    def test_content_types(self) -> None:
        """Test supported content types."""
        provider = RAWGProvider()
        assert provider.content_types == [ContentType.VIDEO_GAME]
        assert ContentType.MOVIE not in provider.content_types

    def test_requires_api_key(self) -> None:
        """Test that API key IS required."""
        provider = RAWGProvider()
        assert provider.requires_api_key is True

    def test_rate_limit(self) -> None:
        """Test rate limit setting."""
        provider = RAWGProvider()
        assert provider.rate_limit_requests_per_second == 5.0


class TestRAWGProviderValidation:
    """Tests for RAWG provider config validation."""

    def test_validate_valid_config(self) -> None:
        """Test validation with valid config."""
        provider = RAWGProvider()
        errors = provider.validate_config({"api_key": "test-key"})
        assert errors == []

    def test_validate_missing_api_key(self) -> None:
        """Test validation with missing API key."""
        provider = RAWGProvider()
        errors = provider.validate_config({})
        assert "'api_key' is required for RAWG provider" in errors


class TestRAWGProviderEnrichment:
    """Tests for RAWG game enrichment."""

    @pytest.fixture
    def provider(self) -> RAWGProvider:
        """Create provider instance."""
        return RAWGProvider()

    @pytest.fixture
    def game_item(self) -> ContentItem:
        """Create sample game item."""
        return ContentItem(
            id="game1",
            title="The Witcher 3: Wild Hunt",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={"release_year": 2015},
        )

    @pytest.fixture
    def config(self) -> dict[str, Any]:
        """Create test config."""
        return {"api_key": "test-api-key"}

    def test_enrich_game_success(
        self,
        provider: RAWGProvider,
        game_item: ContentItem,
        config: dict[str, Any],
    ) -> None:
        """Test successful game enrichment."""
        mock_search = {
            "results": [
                {
                    "id": 3328,
                    "name": "The Witcher 3: Wild Hunt",
                    "released": "2015-05-18",
                }
            ]
        }

        mock_game = {
            "id": 3328,
            "name": "The Witcher 3: Wild Hunt",
            "released": "2015-05-18",
            "genres": [{"name": "RPG"}, {"name": "Action"}],
            "tags": [
                {"name": "Open World"},
                {"name": "Story Rich"},
                {"name": "Atmospheric"},
            ],
            "description": "<p>The Witcher 3 is an <b>epic</b> RPG.</p>",
            "developers": [{"name": "CD Projekt Red"}],
            "publishers": [{"name": "CD Projekt"}],
            "platforms": [
                {"platform": {"name": "PC"}},
                {"platform": {"name": "PlayStation 4"}},
            ],
            "rating": 4.66,
            "metacritic": 93,
            "playtime": 46,
            "esrb_rating": {"name": "Mature"},
        }

        mock_series = {"results": []}

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                MagicMock(
                    spec=requests.Response, status_code=200, json=lambda: mock_search
                ),
                MagicMock(
                    spec=requests.Response, status_code=200, json=lambda: mock_game
                ),
                MagicMock(
                    spec=requests.Response, status_code=200, json=lambda: mock_series
                ),
            ]

            result = provider.enrich(game_item, config)

        assert result is not None
        assert result.external_id == "rawg:3328"
        assert result.genres == ["RPG", "Action"]
        assert "Open World" in result.tags
        assert "epic" in result.description.lower()
        assert result.match_quality == "high"
        assert result.extra_metadata.get("developer") == "CD Projekt Red"
        assert result.extra_metadata.get("metacritic") == 93
        assert result.extra_metadata.get("release_year") == 2015

    def test_enrich_game_not_found(
        self,
        provider: RAWGProvider,
        config: dict[str, Any],
    ) -> None:
        """Test enrichment when game is not found."""
        item = ContentItem(
            id="game1",
            title="Nonexistent Game",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        )

        mock_search = {"results": []}

        with patch("requests.get") as mock_get:
            mock_get.return_value = MagicMock(
                spec=requests.Response, status_code=200, json=lambda: mock_search
            )

            result = provider.enrich(item, config)

        assert result is not None
        assert result.match_quality == "not_found"

    def test_enrich_game_api_error(
        self,
        provider: RAWGProvider,
        game_item: ContentItem,
        config: dict[str, Any],
    ) -> None:
        """Test that API errors raise ProviderError."""
        with patch("requests.get") as mock_get:
            mock_get.side_effect = requests.RequestException("Connection failed")

            with pytest.raises(ProviderError) as exc_info:
                provider.enrich(game_item, config)

            assert "Failed to search RAWG" in str(exc_info.value)

    def test_enrich_game_matches_by_year(
        self,
        provider: RAWGProvider,
        config: dict[str, Any],
    ) -> None:
        """Test that search prefers matches with correct year."""
        item = ContentItem(
            id="game1",
            title="Doom",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={"release_year": 2016},
        )

        mock_search = {
            "results": [
                {"id": 1, "name": "Doom", "released": "1993-12-10"},  # Wrong year
                {"id": 2, "name": "Doom", "released": "2016-05-13"},  # Correct year
            ]
        }

        mock_game = {
            "id": 2,
            "name": "Doom",
            "genres": [{"name": "Shooter"}],
            "tags": [],
        }

        mock_series = {"results": []}

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                MagicMock(
                    spec=requests.Response, status_code=200, json=lambda: mock_search
                ),
                MagicMock(
                    spec=requests.Response, status_code=200, json=lambda: mock_game
                ),
                MagicMock(
                    spec=requests.Response, status_code=200, json=lambda: mock_series
                ),
            ]

            result = provider.enrich(item, config)

        assert result is not None
        assert result.external_id == "rawg:2"  # Should match 2016 version


class TestRAWGProviderDescriptionCleaning:
    """Tests for HTML description cleaning."""

    def test_clean_description_removes_html(self) -> None:
        """Test that HTML tags are removed."""
        provider = RAWGProvider()
        html = "<p>This is a <b>great</b> game with <i>amazing</i> graphics.</p>"

        cleaned = provider._clean_description(html)

        assert cleaned == "This is a great game with amazing graphics."
        assert "<" not in cleaned

    def test_clean_description_handles_none(self) -> None:
        """Test that None description returns None."""
        provider = RAWGProvider()
        assert provider._clean_description(None) is None

    def test_clean_description_limits_length(self) -> None:
        """Test that long descriptions are truncated."""
        provider = RAWGProvider()
        long_desc = "A" * 3000

        cleaned = provider._clean_description(long_desc)

        assert len(cleaned) == 2000
        assert cleaned.endswith("...")


class TestRAWGProviderUnsupportedTypes:
    """Tests for handling unsupported content types."""

    def test_enrich_movie_returns_none(self) -> None:
        """Test that enriching a movie returns None."""
        provider = RAWGProvider()
        item = ContentItem(
            id="movie1",
            title="Some Movie",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )

        result = provider.enrich(item, {"api_key": "test"})
        assert result is None

    def test_enrich_book_returns_none(self) -> None:
        """Test that enriching a book returns None."""
        provider = RAWGProvider()
        item = ContentItem(
            id="book1",
            title="Some Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )

        result = provider.enrich(item, {"api_key": "test"})
        assert result is None


class TestLongestCommonPrefix:
    """Tests for _longest_common_prefix franchise name derivation."""

    def test_dragon_age_series(self) -> None:
        """Dragon Age titles share 'Dragon Age' prefix."""
        titles = [
            "Dragon Age: Origins",
            "Dragon Age II",
            "Dragon Age: Inquisition",
        ]
        assert _longest_common_prefix(titles) == "Dragon Age"

    def test_final_fantasy_series(self) -> None:
        """Final Fantasy titles share 'Final Fantasy' prefix."""
        titles = [
            "Final Fantasy X",
            "Final Fantasy X-2",
            "Final Fantasy XII",
        ]
        assert _longest_common_prefix(titles) == "Final Fantasy"

    def test_kingdom_hearts_series(self) -> None:
        """Kingdom Hearts titles share 'Kingdom Hearts' prefix."""
        titles = [
            "Kingdom Hearts",
            "Kingdom Hearts II",
            "Kingdom Hearts HD 2.8 Final Chapter Prologue",
        ]
        assert _longest_common_prefix(titles) == "Kingdom Hearts"

    def test_single_title(self) -> None:
        """Single title returns the full title."""
        assert _longest_common_prefix(["Dragon Age: Origins"]) == "Dragon Age: Origins"

    def test_empty_list(self) -> None:
        """Empty list returns empty string."""
        assert _longest_common_prefix([]) == ""

    def test_no_common_prefix(self) -> None:
        """Unrelated titles return empty string."""
        titles = ["Halo", "Zelda", "Mario"]
        assert _longest_common_prefix(titles) == ""

    def test_trailing_colon_stripped(self) -> None:
        """Trailing colon after trimming to word boundary is stripped."""
        titles = ["Mass Effect: Andromeda", "Mass Effect: Legendary Edition"]
        assert _longest_common_prefix(titles) == "Mass Effect"

    def test_trailing_dash_stripped(self) -> None:
        """Trailing dash is stripped from the prefix."""
        titles = ["The Witcher - Enhanced", "The Witcher - Wild Hunt"]
        assert _longest_common_prefix(titles) == "The Witcher"


class TestLongestCommonPrefixOutlierFiltering:
    """Regression tests for outlier title filtering in _longest_common_prefix.

    Bug reported: All Final Fantasy games missing franchise data after RAWG
    enrichment.

    Root cause: _longest_common_prefix computes a character-level prefix of
    ALL titles.  RAWG returns titles like ["Final Fantasy XIII",
    "Final Fantasy XIII-2", "Lightning Returns: Final Fantasy XIII"] for the
    FF XIII series.  "F" != "L" at index 0 collapses the prefix to "" and
    no franchise data is produced.

    Fix: Before computing the prefix, filter out outlier titles by
    majority-based first-word voting.  "Lightning" is the minority first
    word (1 of 3), "Final" is the majority (2 of 3), so the outlier is
    excluded and the prefix is computed from the two "Final Fantasy" titles.
    """

    def test_ff_xiii_with_lightning_returns_outlier_regression(self) -> None:
        """FF XIII series with 'Lightning Returns' outlier -> 'Final Fantasy XIII'.

        After filtering, the two remaining titles are "Final Fantasy XIII" and
        "Final Fantasy XIII-2".  Their LCP is "Final Fantasy XIII" (the '-2'
        suffix starts with a non-alphanumeric delimiter so no word-boundary
        trim is needed).  This is the correct sub-series franchise name.
        """
        titles = [
            "Final Fantasy XIII",
            "Final Fantasy XIII-2",
            "Lightning Returns: Final Fantasy XIII",
        ]
        assert _longest_common_prefix(titles) == "Final Fantasy XIII"

    def test_all_titles_share_first_word_no_filtering(self) -> None:
        """When all titles share the same first word, no filtering occurs."""
        titles = [
            "Dragon Age: Origins",
            "Dragon Age II",
            "Dragon Age: Inquisition",
        ]
        assert _longest_common_prefix(titles) == "Dragon Age"

    def test_all_different_first_words_returns_empty(self) -> None:
        """All titles have different first words -> returns empty string."""
        titles = ["Alpha Game", "Beta Game", "Gamma Game"]
        assert _longest_common_prefix(titles) == ""

    def test_two_equal_groups_picks_first_most_common(self) -> None:
        """Two groups of equal size -> picks whichever Counter returns first."""
        titles = [
            "Final Fantasy X",
            "Final Fantasy XII",
            "Kingdom Hearts",
            "Kingdom Hearts II",
        ]
        result = _longest_common_prefix(titles)
        # Either "Final Fantasy" or "Kingdom Hearts" is valid
        assert result in ("Final Fantasy", "Kingdom Hearts")

    def test_filter_outlier_titles_basic(self) -> None:
        """_filter_outlier_titles keeps majority first-word titles."""
        titles = [
            "Final Fantasy XIII",
            "Final Fantasy XIII-2",
            "Lightning Returns: Final Fantasy XIII",
        ]
        filtered = _filter_outlier_titles(titles)
        assert len(filtered) == 2
        assert "Lightning Returns: Final Fantasy XIII" not in filtered

    def test_filter_outlier_titles_returns_original_if_fewer_than_2(self) -> None:
        """If filtering leaves < 2 titles, original list is returned."""
        titles = ["Alpha Game", "Beta Game", "Gamma Game"]
        filtered = _filter_outlier_titles(titles)
        assert filtered == titles


class TestRAWGFranchiseExtraction:
    """Tests for RAWG franchise/game-series extraction."""

    @pytest.fixture
    def provider(self) -> RAWGProvider:
        """Create provider instance."""
        return RAWGProvider()

    def test_fetch_game_series_success_with_position(
        self, provider: RAWGProvider
    ) -> None:
        """Franchise name and position are extracted from game-series API."""
        mock_series_response = {
            "results": [
                {"id": 100, "name": "Dragon Age: Origins", "released": "2009-11-03"},
                {"id": 101, "name": "Dragon Age II", "released": "2011-03-08"},
            ]
        }

        with patch("requests.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200, json=lambda: mock_series_response
            )

            franchise_name, position = provider._fetch_game_series(
                game_id=102,
                game_name="Dragon Age: Inquisition",
                game_released="2014-11-18",
                api_key="test-key",
            )

        assert franchise_name == "Dragon Age"
        assert position == 3  # Third by release date

    def test_fetch_game_series_empty_results(self, provider: RAWGProvider) -> None:
        """Empty results return (None, None)."""
        mock_series_response = {"results": []}

        with patch("requests.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200, json=lambda: mock_series_response
            )

            franchise_name, position = provider._fetch_game_series(
                game_id=999,
                game_name="Standalone Game",
                game_released="2020-01-01",
                api_key="test-key",
            )

        assert franchise_name is None
        assert position is None

    def test_fetch_game_series_api_error_graceful_fallback(
        self, provider: RAWGProvider
    ) -> None:
        """API error returns (None, None) without raising."""
        with patch("requests.get") as mock_get:
            mock_get.side_effect = requests.RequestException("Connection failed")

            franchise_name, position = provider._fetch_game_series(
                game_id=100,
                game_name="Dragon Age: Origins",
                game_released="2009-11-03",
                api_key="test-key",
            )

        assert franchise_name is None
        assert position is None

    def test_fetch_game_series_game_already_in_results(
        self, provider: RAWGProvider
    ) -> None:
        """When current game is already in results, it is not duplicated."""
        mock_series_response = {
            "results": [
                {"id": 100, "name": "Dragon Age: Origins", "released": "2009-11-03"},
                {"id": 101, "name": "Dragon Age II", "released": "2011-03-08"},
                {
                    "id": 102,
                    "name": "Dragon Age: Inquisition",
                    "released": "2014-11-18",
                },
            ]
        }

        with patch("requests.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200, json=lambda: mock_series_response
            )

            franchise_name, position = provider._fetch_game_series(
                game_id=102,
                game_name="Dragon Age: Inquisition",
                game_released="2014-11-18",
                api_key="test-key",
            )

        assert franchise_name == "Dragon Age"
        assert position == 3

    def test_full_enrich_flow_populates_franchise_data(
        self, provider: RAWGProvider
    ) -> None:
        """End-to-end: enrich() stores franchise and series_position in metadata."""
        item = ContentItem(
            id="game1",
            title="Dragon Age: Inquisition",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        )

        mock_search = {
            "results": [
                {"id": 102, "name": "Dragon Age: Inquisition", "released": "2014-11-18"}
            ]
        }
        mock_game = {
            "id": 102,
            "name": "Dragon Age: Inquisition",
            "released": "2014-11-18",
            "genres": [{"name": "RPG"}],
            "tags": [],
        }
        mock_series = {
            "results": [
                {"id": 100, "name": "Dragon Age: Origins", "released": "2009-11-03"},
                {"id": 101, "name": "Dragon Age II", "released": "2011-03-08"},
            ]
        }

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                MagicMock(
                    spec=requests.Response, status_code=200, json=lambda: mock_search
                ),
                MagicMock(
                    spec=requests.Response, status_code=200, json=lambda: mock_game
                ),
                MagicMock(
                    spec=requests.Response, status_code=200, json=lambda: mock_series
                ),
            ]

            result = provider.enrich(item, {"api_key": "test-key"})

        assert result is not None
        assert result.extra_metadata.get("franchise") == "Dragon Age"
        assert result.extra_metadata.get("series_position") == 3

    def test_enrich_game_series_api_failure_still_returns_result(
        self, provider: RAWGProvider
    ) -> None:
        """When game-series API fails, enrich still returns genres/tags/description."""
        item = ContentItem(
            id="game1",
            title="The Witcher 3: Wild Hunt",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        )

        mock_search = {
            "results": [
                {
                    "id": 3328,
                    "name": "The Witcher 3: Wild Hunt",
                    "released": "2015-05-18",
                }
            ]
        }
        mock_game = {
            "id": 3328,
            "name": "The Witcher 3: Wild Hunt",
            "released": "2015-05-18",
            "genres": [{"name": "RPG"}, {"name": "Action"}],
            "tags": [{"name": "Open World"}],
            "description": "An epic RPG.",
        }

        with patch("requests.get") as mock_get:
            # Search succeeds, game details succeed, game-series fails
            mock_get.side_effect = [
                MagicMock(
                    spec=requests.Response, status_code=200, json=lambda: mock_search
                ),
                MagicMock(
                    spec=requests.Response, status_code=200, json=lambda: mock_game
                ),
                requests.RequestException("Series endpoint failed"),
            ]

            result = provider.enrich(item, {"api_key": "test-key"})

        assert result is not None
        assert result.genres == ["RPG", "Action"]
        assert "franchise" not in result.extra_metadata
        assert "series_position" not in result.extra_metadata
