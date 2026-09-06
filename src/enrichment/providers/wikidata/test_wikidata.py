from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from src import __version__ as APP_VERSION
from src.enrichment.provider_base import (
    SeriesOrdinal,
    states_a_match,
    states_a_series_ordinal,
)
from src.enrichment.providers.wikidata.wikidata import WikidataProvider
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.utils.series import SeriesAuthority

_REQUESTS = "src.enrichment.providers.wikidata.wikidata.requests.get"

_FINAL_FANTASY = "Q99416119"
_DUNE_NOVELS = "Q1493024"

_VIDEO_GAME = "Q7889"
_VIDEO_GAME_REMAKE = "Q4393107"
_FILM = "Q11424"
_LITERARY_WORK = "Q7725634"


def _claim(
    content: Any, qualifiers: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    claim: dict[str, Any] = {
        "rank": "normal",
        "value": {"type": "value", "content": content},
    }
    if qualifiers is not None:
        claim["qualifiers"] = qualifiers
    return claim


def _statements(
    instance_of: str,
    year: int | None = None,
    series: str | None = None,
    ordinal: str | None = None,
) -> dict[str, Any]:
    statements: dict[str, Any] = {"P31": [_claim(instance_of)]}
    if year is not None:
        statements["P577"] = [
            _claim({"time": f"+{year}-01-01T00:00:00Z", "precision": 9})
        ]
    if series is not None:
        qualifiers: list[dict[str, Any]] = []
        if ordinal is not None:
            qualifiers.append(
                {
                    "property": {"id": "P1545", "data_type": "string"},
                    "value": {"type": "value", "content": ordinal},
                }
            )
        statements["P179"] = [_claim(series, qualifiers)]
    return statements


def _search(*hits: dict[str, Any]) -> dict[str, Any]:
    return {"search": list(hits)}


def _response(payload: Any) -> MagicMock:
    return MagicMock(spec=requests.Response, status_code=200, json=lambda: payload)


def _item(
    title: str,
    content_type: ContentType = ContentType.VIDEO_GAME,
    year: int | None = None,
) -> ContentItem:
    return ContentItem(
        id="item1",
        title=title,
        content_type=content_type,
        status=ConsumptionStatus.UNREAD,
        metadata={"release_year": year} if year is not None else {},
    )


class TestWikidataSeriesOrdinal:
    @pytest.fixture
    def provider(self) -> WikidataProvider:
        return WikidataProvider()

    @pytest.mark.parametrize(
        ("title", "year", "hit", "statements", "expected"),
        [
            (
                "Final Fantasy VIII",
                1999,
                {"id": "Q245006", "label": "Final Fantasy VIII"},
                _statements(_VIDEO_GAME, year=1999, series=_FINAL_FANTASY, ordinal="8"),
                8.0,
            ),
            (
                "Final Fantasy I",
                1987,
                {
                    "id": "Q45925",
                    "label": "Final Fantasy",
                    "aliases": ["Final Fantasy I"],
                },
                _statements(_VIDEO_GAME, year=1987, series=_FINAL_FANTASY, ordinal="1"),
                1.0,
            ),
            (
                "Final Fantasy XII: The Zodiac Age",
                2017,
                {"id": "Q27478545", "label": "Final Fantasy XII: The Zodiac Age"},
                _statements(
                    _VIDEO_GAME_REMAKE, year=2017, series=_FINAL_FANTASY, ordinal="12"
                ),
                12.0,
            ),
        ],
        ids=["labelled", "aliased", "remake"],
    )
    def test_takes_the_position_the_series_qualifier_states(
        self,
        provider: WikidataProvider,
        title: str,
        year: int,
        hit: dict[str, Any],
        statements: dict[str, Any],
        expected: float,
    ) -> None:
        with patch(_REQUESTS) as mock_get:
            mock_get.side_effect = [
                _response(_search(hit)),
                _response(statements),
            ]
            ordinal = provider.fetch_series_ordinal(_item(title, year=year), {})

        assert ordinal == SeriesOrdinal(
            position=expected, authority=SeriesAuthority.AUTHORED
        )

    def test_a_series_stating_no_ordinal_leaves_the_position_unwritten(
        self, provider: WikidataProvider
    ) -> None:
        with patch(_REQUESTS) as mock_get:
            mock_get.side_effect = [
                _response(_search({"id": "Q245006", "label": "Final Fantasy VIII"})),
                _response(_statements(_VIDEO_GAME, year=1999, series=_FINAL_FANTASY)),
            ]
            ordinal = provider.fetch_series_ordinal(
                _item("Final Fantasy VIII", year=1999), {}
            )

        assert ordinal is None

    def test_a_work_belonging_to_no_series_writes_nothing(
        self, provider: WikidataProvider
    ) -> None:
        with patch(_REQUESTS) as mock_get:
            mock_get.side_effect = [
                _response(_search({"id": "Q207266", "label": "Journey"})),
                _response(_statements(_VIDEO_GAME, year=2012)),
            ]
            ordinal = provider.fetch_series_ordinal(_item("Journey", year=2012), {})

        assert ordinal is None

    def test_a_film_is_never_taken_for_the_book_of_the_same_name(
        self, provider: WikidataProvider
    ) -> None:
        with patch(_REQUESTS) as mock_get:
            mock_get.side_effect = [
                _response(_search({"id": "Q55811171", "label": "Dune"})),
                _response(
                    _statements(_FILM, year=2021, series="Q98814655", ordinal="1")
                ),
            ]
            ordinal = provider.fetch_series_ordinal(_item("Dune", ContentType.BOOK), {})

        assert ordinal is None

    def test_a_rejected_film_does_not_stand_in_for_the_novel_ranked_behind_it(
        self, provider: WikidataProvider
    ) -> None:
        with patch(_REQUESTS) as mock_get:
            mock_get.side_effect = [
                _response(
                    _search(
                        {"id": "Q55811171", "label": "Dune"},
                        {"id": "Q133855", "label": "Dune"},
                    )
                ),
                _response(
                    _statements(_FILM, year=2021, series="Q98814655", ordinal="5")
                ),
                _response(
                    _statements(
                        _LITERARY_WORK, year=1965, series=_DUNE_NOVELS, ordinal="1"
                    )
                ),
            ]
            ordinal = provider.fetch_series_ordinal(
                _item("Dune", ContentType.BOOK, year=1965), {}
            )

        assert ordinal == SeriesOrdinal(
            position=1.0, authority=SeriesAuthority.AUTHORED
        )

    def test_a_release_year_that_disagrees_rejects_the_entity(
        self, provider: WikidataProvider
    ) -> None:
        with patch(_REQUESTS) as mock_get:
            mock_get.side_effect = [
                _response(_search({"id": "Q55811171", "label": "Dune"})),
                _response(
                    _statements(_FILM, year=2021, series="Q98814655", ordinal="1")
                ),
            ]
            ordinal = provider.fetch_series_ordinal(
                _item("Dune", ContentType.MOVIE, year=1984), {}
            )

        assert ordinal is None

    def test_a_title_matching_nothing_costs_no_statement_request(
        self, provider: WikidataProvider
    ) -> None:
        with patch(_REQUESTS) as mock_get:
            mock_get.side_effect = [
                _response(_search({"id": "Q1091448", "label": "Herbie: Fully Loaded"}))
            ]
            ordinal = provider.fetch_series_ordinal(
                _item("Fully Loaded", ContentType.MOVIE), {}
            )

        assert ordinal is None
        assert mock_get.call_count == 1

    def test_a_book_series_ordinal_is_read_for_a_marked_up_title(
        self, provider: WikidataProvider
    ) -> None:
        with patch(_REQUESTS) as mock_get:
            mock_get.side_effect = [
                _response(_search({"id": "Q80251", "label": "Dune Messiah"})),
                _response(
                    _statements(
                        _LITERARY_WORK, year=1969, series=_DUNE_NOVELS, ordinal="2"
                    )
                ),
            ]
            ordinal = provider.fetch_series_ordinal(
                _item("Dune Messiah (Dune, #2)", ContentType.BOOK, year=1969), {}
            )

        assert ordinal == SeriesOrdinal(
            position=2.0, authority=SeriesAuthority.AUTHORED
        )

    def test_a_reprints_year_does_not_reject_the_work_regression(
        self, provider: WikidataProvider
    ) -> None:
        """A 2011 mass-market Dune drifts 46 years from the 1965 work, so
        reading ``year_published`` as a release year rejected every reprint.
        """
        reprint = _item("Dune", ContentType.BOOK)
        reprint.metadata["year_published"] = 2011

        with patch(_REQUESTS) as mock_get:
            mock_get.side_effect = [
                _response(_search({"id": "Q188442", "label": "Dune"})),
                _response(
                    _statements(
                        _LITERARY_WORK, year=1965, series=_DUNE_NOVELS, ordinal="1"
                    )
                ),
            ]
            ordinal = provider.fetch_series_ordinal(reprint, {})

        assert ordinal == SeriesOrdinal(
            position=1.0, authority=SeriesAuthority.AUTHORED
        )

    def test_a_prequel_stated_as_position_zero_keeps_that_position(
        self, provider: WikidataProvider
    ) -> None:
        with patch(_REQUESTS) as mock_get:
            mock_get.side_effect = [
                _response(_search({"id": "Q962795", "label": "Dune: House Atreides"})),
                _response(
                    _statements(
                        _LITERARY_WORK, year=1999, series=_DUNE_NOVELS, ordinal="0"
                    )
                ),
            ]
            ordinal = provider.fetch_series_ordinal(
                _item("Dune: House Atreides", ContentType.BOOK, year=1999), {}
            )

        assert ordinal is not None
        assert ordinal.position == 0.0

    @pytest.mark.parametrize("stated", ["1001", "inf"])
    def test_a_position_no_reader_could_read_back_is_never_stated(
        self, provider: WikidataProvider, stated: str
    ) -> None:
        with patch(_REQUESTS) as mock_get:
            mock_get.side_effect = [
                _response(_search({"id": "Q245006", "label": "Final Fantasy VIII"})),
                _response(
                    _statements(
                        _VIDEO_GAME, year=1999, series=_FINAL_FANTASY, ordinal=stated
                    )
                ),
            ]
            ordinal = provider.fetch_series_ordinal(
                _item("Final Fantasy VIII", year=1999), {}
            )

        assert ordinal is None

    def test_every_request_names_the_client(self, provider: WikidataProvider) -> None:
        with patch(_REQUESTS) as mock_get:
            mock_get.side_effect = [
                _response(_search({"id": "Q245006", "label": "Final Fantasy VIII"})),
                _response(
                    _statements(
                        _VIDEO_GAME, year=1999, series=_FINAL_FANTASY, ordinal="8"
                    )
                ),
            ]
            provider.fetch_series_ordinal(_item("Final Fantasy VIII", year=1999), {})

        assert mock_get.call_count == 2
        for call in mock_get.call_args_list:
            agent = call.kwargs["headers"]["User-Agent"]
            assert agent.startswith(f"Recommendinator/{APP_VERSION}")
            assert "python-requests" not in agent
            assert "Mozilla" not in agent


class TestWikidataStandsAside:
    def test_states_an_ordinal_without_ever_being_an_items_match(self) -> None:
        provider = WikidataProvider()

        assert states_a_series_ordinal(provider) is True
        assert states_a_match(provider) is False
        assert provider.enrich(_item("Final Fantasy VIII"), {}) is None

    def test_covers_every_content_type_without_an_api_key(self) -> None:
        provider = WikidataProvider()

        assert set(provider.content_types) == set(ContentType)
        assert provider.requires_api_key is False
        assert provider.validate_config({}) == []
