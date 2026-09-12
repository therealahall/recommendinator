import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.enrichment.provider_base import ProviderError, SeriesOrdinal
from src.enrichment.providers.igdb.igdb import (
    GAMES_URL,
    TWITCH_TOKEN_URL,
    IGDBProvider,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType

_CLIENT_ID = "igdb-client-id"
_CLIENT_SECRET = "igdb-client-secret"
_TOKEN = "twitch-app-access-token"

_CONFIG = {"client_id": _CLIENT_ID, "client_secret": _CLIENT_SECRET}

_TOKEN_PAYLOAD = {"access_token": _TOKEN, "expires_in": 3600, "token_type": "bearer"}

_COVER_PATH = "images.igdb.com/igdb/image/upload/t_thumb/co1x2y.jpg"

_RELEASED_AT = 946684800
_RELEASE_YEAR = 2000


def _response(
    payload: Any,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> MagicMock:
    response = MagicMock(
        spec=requests.Response, status_code=status, headers=headers or {}
    )
    response.json.return_value = payload
    if status >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
    return response


def _game(name: str = "Planescape: Torment", **stated: Any) -> dict[str, Any]:
    return {
        "id": 8,
        "name": name,
        "summary": "The Nameless One wakes in a mortuary.",
        "genres": [{"name": "Role-playing (RPG)"}],
        "themes": [{"name": "Fantasy"}],
        "cover": {"url": f"//{_COVER_PATH}"},
        "first_release_date": _RELEASED_AT,
        **stated,
    }


def _item(title: str = "Planescape: Torment", **metadata: Any) -> ContentItem:
    return ContentItem(
        title=title,
        content_type=ContentType.VIDEO_GAME,
        status=ConsumptionStatus.UNREAD,
        metadata=metadata,
    )


class _Transport:
    """Answers the token URL apart from the games URL, so a test counts either."""

    def __init__(self, *games: MagicMock) -> None:
        self.games = list(games) or [_response([])]
        self.token = _response(_TOKEN_PAYLOAD)
        self.token_requests = 0
        self.game_requests: list[dict[str, Any]] = []

    def __call__(self, url: str, **kwargs: Any) -> MagicMock:
        if url == TWITCH_TOKEN_URL:
            self.token_requests += 1
            return self.token
        self.game_requests.append({"url": url, **kwargs})
        return self.games.pop(0) if len(self.games) > 1 else self.games[0]


def _served(*games: dict[str, Any]) -> _Transport:
    return _Transport(_response(list(games)))


def _run(transport: _Transport, call: Any, item: ContentItem | None = None) -> Any:
    with patch(
        "src.enrichment.providers.igdb.igdb.requests.post", side_effect=transport
    ):
        return call(item if item is not None else _item(), _CONFIG)


@pytest.fixture
def provider() -> IGDBProvider:
    return IGDBProvider()


class TestIGDBConfig:
    def test_validate_names_both_missing_credentials(
        self, provider: IGDBProvider
    ) -> None:
        assert provider.validate_config({}) == [
            "'client_id' is required for IGDB provider",
            "'client_secret' is required for IGDB provider",
        ]

    def test_half_the_credentials_makes_no_request_rather_than_a_rejected_one(
        self, provider: IGDBProvider
    ) -> None:
        with patch("src.enrichment.providers.igdb.igdb.requests.post") as mock_post:
            assert provider.enrich(_item(), {"client_id": _CLIENT_ID}) is None

        assert mock_post.call_count == 0

    def test_a_book_is_left_to_the_providers_that_hold_books(
        self, provider: IGDBProvider
    ) -> None:
        book = ContentItem(
            title="Leviathan Wakes",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )

        with patch("src.enrichment.providers.igdb.igdb.requests.post") as mock_post:
            assert provider.enrich(book, _CONFIG) is None

        assert mock_post.call_count == 0


class TestIGDBSeriesName:
    def test_a_collection_names_the_series_and_never_positions_the_game(
        self, provider: IGDBProvider
    ) -> None:
        transport = _served(
            {
                "name": "Planescape: Torment",
                "collections": [{"name": "Planescape", "slug": "planescape"}],
            }
        )

        ordinal = _run(transport, provider.fetch_series_ordinal)

        assert ordinal == SeriesOrdinal(position=None, series_name="Planescape")
        assert ordinal.as_metadata() == {"series_name": "Planescape"}

    def test_a_franchise_names_the_series_where_no_collection_does(
        self, provider: IGDBProvider
    ) -> None:
        transport = _served(
            {"name": "Ultima VII: The Black Gate", "franchise": {"name": "Ultima"}}
        )

        ordinal = _run(
            transport,
            provider.fetch_series_ordinal,
            _item("Ultima™ VII: The Black Gate"),
        )

        assert ordinal == SeriesOrdinal(position=None, series_name="Ultima")

    def test_a_franchise_list_names_the_series_where_the_single_field_is_absent(
        self, provider: IGDBProvider
    ) -> None:
        transport = _served(
            {"name": "Deep Rock Galactic", "franchises": [{"name": "Deep Rock"}]}
        )

        ordinal = _run(
            transport, provider.fetch_series_ordinal, _item("Deep Rock Galactic")
        )

        assert ordinal == SeriesOrdinal(position=None, series_name="Deep Rock")

    def test_the_collection_wins_over_a_franchise_the_game_also_belongs_to(
        self, provider: IGDBProvider
    ) -> None:
        transport = _served(
            {
                "name": "Planescape: Torment",
                "collections": [{"name": "Planescape"}],
                "franchise": {"name": "Dungeons & Dragons"},
            }
        )

        ordinal = _run(transport, provider.fetch_series_ordinal)

        assert ordinal == SeriesOrdinal(position=None, series_name="Planescape")

    def test_a_grouping_that_numbers_the_game_still_states_no_position(
        self, provider: IGDBProvider
    ) -> None:
        transport = _served(
            {
                "name": "Planescape: Torment",
                "collections": [{"name": "Planescape", "number": 2}],
                "franchise": {"name": "Dungeons & Dragons", "position": 7},
            }
        )

        ordinal = _run(transport, provider.fetch_series_ordinal)

        assert ordinal is not None and ordinal.position is None
        assert ordinal.as_metadata() == {"series_name": "Planescape"}

    def test_a_game_in_no_grouping_states_nothing_rather_than_its_own_title(
        self, provider: IGDBProvider
    ) -> None:
        transport = _served({"name": "Prey"})

        assert _run(transport, provider.fetch_series_ordinal, _item("Prey")) is None

    def test_a_grouping_with_no_readable_name_states_nothing(
        self, provider: IGDBProvider
    ) -> None:
        transport = _served(
            {"name": "Prey", "collections": [{"slug": "prey"}], "franchise": {}}
        )

        assert _run(transport, provider.fetch_series_ordinal, _item("Prey")) is None

    def test_a_game_igdb_cannot_match_takes_no_series_from_a_near_title(
        self, provider: IGDBProvider
    ) -> None:
        transport = _served(
            {"name": "Torment: Tides of Numenera", "collections": [{"name": "Torment"}]}
        )

        assert _run(transport, provider.fetch_series_ordinal, _item("Styx")) is None


class TestIGDBEnrichment:
    def test_a_matched_game_fills_the_fields_its_detail_table_declares(
        self, provider: IGDBProvider
    ) -> None:
        result = _run(_served(_game()), provider.enrich)

        assert result is not None
        assert result.genres == ["Role-playing (RPG)"]
        assert result.tags == ["Fantasy"]
        assert result.description == "The Nameless One wakes in a mortuary."
        assert result.cover_url == (
            "https://images.igdb.com/igdb/image/upload/t_cover_big/co1x2y.jpg"
        )
        assert result.extra_metadata == {"release_year": _RELEASE_YEAR}

    def test_a_game_igdb_cannot_match_is_not_found_rather_than_partial(
        self, provider: IGDBProvider
    ) -> None:
        result = _run(
            _served(_game(name="Torment: Tides of Numenera")), provider.enrich
        )

        assert result is not None
        assert result.match_quality == "not_found"
        assert result.genres is None
        assert result.description is None
        assert result.cover_url is None

    def test_an_alternative_name_matches_the_title_a_store_recorded(
        self, provider: IGDBProvider
    ) -> None:
        transport = _served(
            _game(name="Planescape: Torment", alternative_names=[{"name": "PST"}])
        )

        result = _run(transport, provider.enrich, _item("PST"))

        assert result is not None and result.match_quality != "not_found"

    def test_a_release_date_igdb_states_as_nonsense_leaves_the_year_unfilled(
        self, provider: IGDBProvider
    ) -> None:
        result = _run(_served(_game(first_release_date="soon")), provider.enrich)

        assert result is not None and result.extra_metadata == {}

    @pytest.mark.parametrize(
        "url", ["http://images.igdb.com/co1x2y.jpg", {"src": 1}, None]
    )
    def test_only_an_https_cover_reaches_the_backfill(
        self, provider: IGDBProvider, url: Any
    ) -> None:
        result = _run(_served(_game(cover={"url": url})), provider.enrich)

        assert result is not None and result.cover_url is None

    def test_a_store_title_is_searched_under_the_shared_cleaned_title(
        self, provider: IGDBProvider
    ) -> None:
        transport = _served(_game(name="Ultima VII: The Black Gate"))

        _run(transport, provider.enrich, _item("Ultima™ VII: The Black Gate"))

        assert 'search "Ultima VII: The Black Gate";' in (
            transport.game_requests[0]["data"]
        )

    def test_a_title_carrying_apicalypse_punctuation_cannot_end_the_statement(
        self, provider: IGDBProvider
    ) -> None:
        transport = _served(_game())

        _run(transport, provider.enrich, _item('Prey"; fields id;'))

        body = transport.game_requests[0]["data"]
        assert body.count('"') == 2
        assert body.count(";") == 3


class TestIGDBAppToken:
    def test_the_token_is_minted_once_and_reused_across_items(
        self, provider: IGDBProvider
    ) -> None:
        transport = _served(_game())

        with patch(
            "src.enrichment.providers.igdb.igdb.requests.post", side_effect=transport
        ):
            for _ in range(3):
                provider.enrich(_item(), _CONFIG)

        assert transport.token_requests == 1
        assert len(transport.game_requests) == 3

    def test_a_rejected_token_is_minted_again_once_and_the_call_retried(
        self, provider: IGDBProvider
    ) -> None:
        transport = _Transport(_response({}, status=401), _response([_game()]))

        result = _run(transport, provider.enrich)

        assert transport.token_requests == 2
        assert result is not None and result.match_quality != "not_found"

    def test_the_token_a_retry_mints_is_the_one_the_next_item_uses(
        self, provider: IGDBProvider
    ) -> None:
        transport = _Transport(_response({}, status=401), _response([_game()]))

        with patch(
            "src.enrichment.providers.igdb.igdb.requests.post", side_effect=transport
        ):
            provider.enrich(_item(), _CONFIG)
            provider.enrich(_item(), _CONFIG)

        assert transport.token_requests == 2
        assert len(transport.game_requests) == 3

    def test_a_second_rejection_fails_the_item_rather_than_minting_forever(
        self, provider: IGDBProvider
    ) -> None:
        transport = _Transport(_response({}, status=401), _response({}, status=401))

        with pytest.raises(ProviderError) as raised:
            _run(transport, provider.enrich)

        assert transport.token_requests == 2
        assert "HTTP 401" in str(raised.value)

    def test_a_lifetime_igdb_states_as_nonsense_mints_again_next_item(
        self, provider: IGDBProvider
    ) -> None:
        transport = _served(_game())
        transport.token = _response({"access_token": _TOKEN, "expires_in": "soon"})

        with patch(
            "src.enrichment.providers.igdb.igdb.requests.post", side_effect=transport
        ):
            provider.enrich(_item(), _CONFIG)
            provider.enrich(_item(), _CONFIG)

        assert transport.token_requests == 2

    def test_a_token_response_naming_none_fails_before_a_bearer_is_sent(
        self, provider: IGDBProvider
    ) -> None:
        transport = _served(_game())
        transport.token = _response({"expires_in": 3600})

        with pytest.raises(ProviderError) as raised:
            _run(transport, provider.enrich)

        assert transport.game_requests == []
        assert "no app access token" in str(raised.value)

    def test_every_request_carries_the_client_id_and_the_bearer(
        self, provider: IGDBProvider
    ) -> None:
        transport = _served(_game())

        _run(transport, provider.enrich)

        assert transport.game_requests[0]["headers"] == {
            "Client-ID": _CLIENT_ID,
            "Authorization": f"Bearer {_TOKEN}",
            "Accept": "application/json",
        }


class TestIGDBFailures:
    def test_a_redirect_off_the_api_origin_is_refused_before_the_token_follows(
        self, provider: IGDBProvider
    ) -> None:
        transport = _Transport(
            _response(
                {},
                status=302,
                headers={"Location": "https://elsewhere.example.com/v4/games"},
            )
        )

        with pytest.raises(ProviderError) as raised:
            _run(transport, provider.enrich)

        assert [call["url"] for call in transport.game_requests] == [GAMES_URL]
        assert "elsewhere.example.com" in str(raised.value)

    def test_a_rejected_client_secret_surfaces_the_status_and_not_the_secret(
        self, provider: IGDBProvider, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG)
        transport = _served(_game())
        transport.token = _response({}, status=403)

        with pytest.raises(ProviderError) as raised:
            _run(transport, provider.enrich)

        assert "HTTP 403" in str(raised.value)
        assert _CLIENT_SECRET not in str(raised.value)
        assert _CLIENT_SECRET not in caplog.text

    def test_an_unreachable_api_fails_the_item_rather_than_matching_nothing(
        self, provider: IGDBProvider
    ) -> None:
        with patch(
            "src.enrichment.providers.igdb.igdb.requests.post",
            side_effect=requests.ConnectionError(),
        ):
            with pytest.raises(ProviderError) as raised:
                provider.enrich(_item(), _CONFIG)

        assert "ConnectionError" in str(raised.value)
