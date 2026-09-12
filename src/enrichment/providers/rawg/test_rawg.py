import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.enrichment.provider_base import ProviderError
from src.enrichment.providers.rawg.rawg import RAWGProvider
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.utils.series import SERIES_NAME_KEY


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

        with patch("src.enrichment.providers.rawg.rawg.requests.get") as mock_get:
            mock_get.side_effect = [
                MagicMock(
                    spec=requests.Response, status_code=200, json=lambda: mock_search
                ),
                MagicMock(
                    spec=requests.Response, status_code=200, json=lambda: mock_game
                ),
            ]

            result = provider.enrich(game_item, config)

        assert result is not None
        assert result.genres == ["RPG", "Action"]
        assert "Open World" in result.tags
        assert "epic" in result.description.lower()
        assert result.match_quality == "high"
        assert result.extra_metadata.get("developer") == "CD Projekt Red"
        assert result.extra_metadata.get("metacritic") == 93
        assert result.extra_metadata.get("release_year") == 2015
        assert result.cover_url == "https://media.rawg.io/media/games/witcher3.jpg"
        assert result.extra_metadata.get("average_playtime_hours") == 46
        assert "playtime_hours" not in result.extra_metadata
        assert SERIES_NAME_KEY not in result.extra_metadata

    def test_a_store_title_matches_the_catalogues_subtitled_name_for_it(
        self,
        provider: RAWGProvider,
        config: dict[str, Any],
    ) -> None:
        shelved = ContentItem(
            id="game2",
            title="Ultima™ VII",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={"release_year": 1992},
        )
        mock_search = {
            "results": [
                {
                    "id": 4242,
                    "name": "Ultima VII: The Black Gate",
                    "released": "1992-04-16",
                }
            ]
        }
        mock_game = {
            "id": 4242,
            "name": "Ultima VII: The Black Gate",
            "genres": [{"name": "RPG"}],
            "tags": [],
        }

        with patch("src.enrichment.providers.rawg.rawg.requests.get") as mock_get:
            mock_get.side_effect = [
                MagicMock(
                    spec=requests.Response, status_code=200, json=lambda: mock_search
                ),
                MagicMock(
                    spec=requests.Response, status_code=200, json=lambda: mock_game
                ),
            ]

            result = provider.enrich(shelved, config)

        assert result is not None
        assert result.genres == ["RPG"]
        assert result.match_quality != "not_found"

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

    @pytest.mark.parametrize(
        ("release_year", "search_results"),
        [
            (2017, [{"id": 1, "name": "Prey", "released": "2006-07-11"}]),
            (None, [{"id": 2, "name": "Prey Day: Survival", "released": "2019-01-01"}]),
        ],
        ids=["another-year", "another-game"],
    )
    def test_a_top_hit_the_item_does_not_match_is_not_enriched_in(
        self,
        provider: RAWGProvider,
        config: dict[str, Any],
        release_year: int | None,
        search_results: list[dict[str, Any]],
    ) -> None:
        item = ContentItem(
            id="game1",
            title="Prey",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={"release_year": release_year} if release_year else {},
        )

        with patch("src.enrichment.providers.rawg.rawg.requests.get") as mock_get:
            mock_get.return_value = MagicMock(
                spec=requests.Response,
                status_code=200,
                json=lambda: {"results": search_results},
            )

            result = provider.enrich(item, config)

        assert mock_get.call_count == 1
        assert result is not None
        assert result.match_quality == "not_found"


class TestRAWGPinnedRecord:
    def test_a_pinned_record_is_fetched_without_searching_by_title(self) -> None:
        item = ContentItem(
            id="game1",
            title="Prey",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={"enrichment_ids": {"rawg": "3328"}},
        )

        with patch("src.enrichment.providers.rawg.rawg.requests.get") as mock_get:
            mock_get.return_value = MagicMock(
                spec=requests.Response,
                status_code=200,
                json=lambda: {"id": 3328, "name": "The Witcher 3", "results": []},
            )

            result = RAWGProvider().enrich(item, {"api_key": "k"})

        assert result is not None
        assert "/games/3328" in mock_get.call_args_list[0].args[0]


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
            assert provider._search_game(item, "test-key") == []

        assert "Real Game\\nWARNING" in caplog.text
        assert self._FORGED not in caplog.text
