import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.enrichment.provider_base import ProviderError
from src.enrichment.providers.rawg.rawg import (
    RAWGProvider,
    _filter_outlier_titles,
    _longest_common_prefix,
    clean_game_title_for_search,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType


class TestCleanTitleForSearch:
    def test_removes_goty_edition_dash(self) -> None:
        assert (
            clean_game_title_for_search("The Witcher 3: Wild Hunt - GOTY Edition")
            == "The Witcher 3: Wild Hunt"
        )

    def test_removes_edition_in_parentheses(self) -> None:
        assert clean_game_title_for_search("Mass Effect (Legendary)") == "Mass Effect"
        assert clean_game_title_for_search("Skyrim (Special Edition)") == "Skyrim"

    def test_removes_trademark_symbols(self) -> None:
        assert clean_game_title_for_search("Cyberpunk 2077™") == "Cyberpunk 2077"
        assert clean_game_title_for_search("DOOM®") == "DOOM"
        assert clean_game_title_for_search("The Sims™ 4") == "The Sims 4"

    def test_preserves_colons_in_subtitles(self) -> None:
        assert (
            clean_game_title_for_search("The Witcher 3: Wild Hunt")
            == "The Witcher 3: Wild Hunt"
        )
        assert (
            clean_game_title_for_search("Resident Evil 4: Separate Ways")
            == "Resident Evil 4: Separate Ways"
        )

    def test_removes_dlc_suffix(self) -> None:
        assert (
            clean_game_title_for_search("KINGDOM HEARTS III + Re Mind (DLC)")
            == "KINGDOM HEARTS III"
        )


class TestRAWGProviderValidation:
    def test_validate_missing_api_key(self) -> None:
        provider = RAWGProvider()
        errors = provider.validate_config({})
        assert "'api_key' is required for RAWG provider" in errors


class TestRAWGProviderEnrichment:
    @pytest.fixture
    def provider(self) -> RAWGProvider:
        return RAWGProvider()

    @pytest.fixture
    def game_item(self) -> ContentItem:
        return ContentItem(
            id="game1",
            title="The Witcher 3: Wild Hunt",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={"release_year": 2015},
        )

    @pytest.fixture
    def config(self) -> dict[str, Any]:
        return {"api_key": "test-api-key"}

    def test_enrich_game_success(
        self,
        provider: RAWGProvider,
        game_item: ContentItem,
        config: dict[str, Any],
    ) -> None:
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
            "background_image": "https://media.rawg.io/media/games/witcher3.jpg",
        }

        mock_series = {"results": []}

        with patch("src.enrichment.providers.rawg.rawg.requests.get") as mock_get:
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
        assert result.cover_url == "https://media.rawg.io/media/games/witcher3.jpg"
        # Its own key, not the playtime_hours Steam fills with the user's hours
        assert result.extra_metadata.get("average_playtime_hours") == 46
        assert "playtime_hours" not in result.extra_metadata

    def test_enrich_game_with_no_playtime_writes_no_average(
        self,
        provider: RAWGProvider,
        game_item: ContentItem,
        config: dict[str, Any],
    ) -> None:
        mock_search = {"results": [{"id": 3328, "name": "The Witcher 3: Wild Hunt"}]}
        mock_game = {
            "id": 3328,
            "name": "The Witcher 3: Wild Hunt",
            "genres": [{"name": "RPG"}],
            "tags": [],
            "playtime": 0,
        }

        with patch("src.enrichment.providers.rawg.rawg.requests.get") as mock_get:
            mock_get.side_effect = [
                MagicMock(
                    spec=requests.Response, status_code=200, json=lambda: mock_search
                ),
                MagicMock(
                    spec=requests.Response, status_code=200, json=lambda: mock_game
                ),
                MagicMock(
                    spec=requests.Response,
                    status_code=200,
                    json=lambda: {"results": []},
                ),
            ]

            result = provider.enrich(game_item, config)

        assert result is not None
        assert "average_playtime_hours" not in result.extra_metadata
        assert result.cover_url is None

    def test_a_plaintext_cover_url_is_dropped_where_it_arrives(
        self,
        provider: RAWGProvider,
        game_item: ContentItem,
        config: dict[str, Any],
    ) -> None:
        mock_search = {"results": [{"id": 3328, "name": "The Witcher 3: Wild Hunt"}]}
        mock_game = {
            "id": 3328,
            "name": "The Witcher 3: Wild Hunt",
            "background_image": "http://media.rawg.io/media/games/witcher3.jpg",
        }

        with patch("src.enrichment.providers.rawg.rawg.requests.get") as mock_get:
            mock_get.side_effect = [
                MagicMock(
                    spec=requests.Response, status_code=200, json=lambda: mock_search
                ),
                MagicMock(
                    spec=requests.Response, status_code=200, json=lambda: mock_game
                ),
                MagicMock(
                    spec=requests.Response,
                    status_code=200,
                    json=lambda: {"results": []},
                ),
            ]

            result = provider.enrich(game_item, config)

        assert result is not None
        assert result.cover_url is None

    def test_enrich_game_not_found(
        self,
        provider: RAWGProvider,
        config: dict[str, Any],
    ) -> None:
        item = ContentItem(
            id="game1",
            title="Nonexistent Game",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        )

        mock_search = {"results": []}

        with patch("src.enrichment.providers.rawg.rawg.requests.get") as mock_get:
            mock_get.return_value = MagicMock(
                spec=requests.Response, status_code=200, json=lambda: mock_search
            )

            result = provider.enrich(item, config)

        assert result is not None
        assert result.match_quality == "not_found"

    def test_enrich_game_matches_by_year(
        self,
        provider: RAWGProvider,
        config: dict[str, Any],
    ) -> None:
        item = ContentItem(
            id="game1",
            title="Doom",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={"release_year": 2016},
        )

        mock_search = {
            "results": [
                {"id": 1, "name": "Doom", "released": "1993-12-10"},
                {"id": 2, "name": "Doom", "released": "2016-05-13"},
            ]
        }

        mock_game = {
            "id": 2,
            "name": "Doom",
            "genres": [{"name": "Shooter"}],
            "tags": [],
        }

        mock_series = {"results": []}

        with patch("src.enrichment.providers.rawg.rawg.requests.get") as mock_get:
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
        assert result.external_id == "rawg:2"


class TestRAWGProviderDescriptionCleaning:
    def test_clean_description_removes_html(self) -> None:
        provider = RAWGProvider()
        html = "<p>This is a <b>great</b> game with <i>amazing</i> graphics.</p>"

        cleaned = provider._clean_description(html)

        assert cleaned == "This is a great game with amazing graphics."
        assert "<" not in cleaned

    def test_clean_description_limits_length(self) -> None:
        provider = RAWGProvider()
        long_desc = "A" * 3000

        cleaned = provider._clean_description(long_desc)

        assert len(cleaned) == 2000
        assert cleaned.endswith("...")


class TestRAWGProviderUnsupportedTypes:
    def test_enrich_movie_returns_none(self) -> None:
        provider = RAWGProvider()
        item = ContentItem(
            id="movie1",
            title="Some Movie",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )

        result = provider.enrich(item, {"api_key": "test"})
        assert result is None


class TestLongestCommonPrefix:
    def test_dragon_age_series(self) -> None:
        titles = [
            "Dragon Age: Origins",
            "Dragon Age II",
            "Dragon Age: Inquisition",
        ]
        assert _longest_common_prefix(titles) == "Dragon Age"

    def test_no_common_prefix(self) -> None:
        titles = ["Halo", "Zelda", "Mario"]
        assert _longest_common_prefix(titles) == ""


class TestLongestCommonPrefixOutlierFiltering:
    def test_ff_xiii_with_lightning_returns_outlier_regression(self) -> None:
        titles = [
            "Final Fantasy XIII",
            "Final Fantasy XIII-2",
            "Lightning Returns: Final Fantasy XIII",
        ]
        assert _longest_common_prefix(titles) == "Final Fantasy XIII"

    def test_filter_outlier_titles_returns_original_if_fewer_than_2(self) -> None:
        titles = ["Alpha Game", "Beta Game", "Gamma Game"]
        filtered = _filter_outlier_titles(titles)
        assert filtered == titles


class TestRAWGFranchiseExtraction:
    @pytest.fixture
    def provider(self) -> RAWGProvider:
        return RAWGProvider()

    def test_fetch_game_series_success_with_position(
        self, provider: RAWGProvider
    ) -> None:
        mock_series_response = {
            "results": [
                {"id": 100, "name": "Dragon Age: Origins", "released": "2009-11-03"},
                {"id": 101, "name": "Dragon Age II", "released": "2011-03-08"},
            ]
        }

        with patch("src.enrichment.providers.rawg.rawg.requests.get") as mock_get:
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

    def test_fetch_game_series_api_error_graceful_fallback(
        self, provider: RAWGProvider
    ) -> None:
        with patch("src.enrichment.providers.rawg.rawg.requests.get") as mock_get:
            mock_get.side_effect = requests.RequestException("Connection failed")

            franchise_name, position = provider._fetch_game_series(
                game_id=100,
                game_name="Dragon Age: Origins",
                game_released="2009-11-03",
                api_key="test-key",
            )

        assert franchise_name is None
        assert position is None

    def test_full_enrich_flow_populates_franchise_data(
        self, provider: RAWGProvider
    ) -> None:
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

        with patch("src.enrichment.providers.rawg.rawg.requests.get") as mock_get:
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


class TestRAWGApiKeyScrubbingRegression:
    _API_KEY = "SECRET_RAWG_KEY_123"

    def _http_error(self, status_code: int) -> requests.HTTPError:
        """Build an HTTPError whose str() embeds the key, like requests."""
        response = MagicMock(spec=requests.Response)
        response.status_code = status_code
        url = f"https://api.rawg.io/api/games?key={self._API_KEY}&search=Doom"
        return requests.HTTPError(
            f"{status_code} Client Error for url: {url}", response=response
        )

    def _game_item(self) -> ContentItem:
        return ContentItem(
            id="game1",
            title="The Witcher 3: Wild Hunt",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={"release_year": 2015},
        )

    def test_search_error_does_not_leak_api_key(self) -> None:
        provider = RAWGProvider()

        with patch("src.enrichment.providers.rawg.rawg.requests.get") as mock_get:
            response = mock_get.return_value
            response.raise_for_status.side_effect = self._http_error(401)

            with pytest.raises(ProviderError) as exc_info:
                provider.enrich(self._game_item(), {"api_key": self._API_KEY})

        message = str(exc_info.value)
        assert self._API_KEY not in message
        assert "key=" not in message
        assert "Failed to search RAWG: HTTP 401" in message

    def test_transport_error_surfaces_only_exception_type(self) -> None:
        provider = RAWGProvider()

        with patch("src.enrichment.providers.rawg.rawg.requests.get") as mock_get:
            mock_get.side_effect = requests.ConnectionError(
                f"Failed to connect; key={self._API_KEY} was in URL"
            )

            with pytest.raises(ProviderError) as exc_info:
                provider.enrich(self._game_item(), {"api_key": self._API_KEY})

        message = str(exc_info.value)
        assert self._API_KEY not in message
        assert "key=" not in message
        assert "ConnectionError" in message


class TestSearchTitleCannotForgeALogLineRegression:
    _FORGED = "Real Game\nWARNING  | forged | line - GOTY Edition"

    def test_a_newline_in_a_title_is_escaped_before_the_search_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Reached whenever cleaning changes the title, so every edition."""
        provider = RAWGProvider()
        item = ContentItem(
            id="game1",
            title=self._FORGED,
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        )

        with (
            patch("src.enrichment.providers.rawg.rawg.requests.get") as mock_get,
            caplog.at_level(logging.DEBUG, logger="src.enrichment.providers.rawg.rawg"),
        ):
            mock_get.return_value.json.return_value = {"results": []}
            assert provider._search_game(item, "test-key") is None

        assert "Real Game\\nWARNING" in caplog.text
        assert self._FORGED not in caplog.text
