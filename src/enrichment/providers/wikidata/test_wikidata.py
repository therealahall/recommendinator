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


def _ordinal_qualifier(value: dict[str, Any]) -> dict[str, Any]:
    return {"property": {"id": "P1545", "data_type": "string"}, "value": value}


def _series_claim(series: str, ordinal: str | None = None) -> dict[str, Any]:
    qualifiers: list[dict[str, Any]] = []
    if ordinal is not None:
        qualifiers.append(_ordinal_qualifier({"type": "value", "content": ordinal}))
    return _claim(series, qualifiers)


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
        statements["P179"] = [_series_claim(series, ordinal)]
    return statements


def _search(*hits: dict[str, Any]) -> dict[str, Any]:
    return {"search": list(hits)}


def _response(payload: Any, status: int = 200) -> MagicMock:
    return MagicMock(spec=requests.Response, status_code=status, json=lambda: payload)


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
                _response("Final Fantasy"),
            ]
            ordinal = provider.fetch_series_ordinal(_item(title, year=year), {})

        assert ordinal == SeriesOrdinal(
            position=expected,
            series_name="Final Fantasy",
            authority=SeriesAuthority.AUTHORED,
        )

    def test_a_series_with_no_english_label_states_no_position(
        self, provider: WikidataProvider
    ) -> None:
        with patch(_REQUESTS) as mock_get:
            mock_get.side_effect = [
                _response(_search({"id": "Q245006", "label": "Final Fantasy VIII"})),
                _response(
                    _statements(
                        _VIDEO_GAME, year=1999, series=_FINAL_FANTASY, ordinal="8"
                    )
                ),
                _response(None, status=404),
            ]
            ordinal = provider.fetch_series_ordinal(
                _item("Final Fantasy VIII", year=1999), {}
            )

        assert ordinal is None

    def test_a_series_stating_no_ordinal_still_names_the_series(
        self, provider: WikidataProvider
    ) -> None:
        with patch(_REQUESTS) as mock_get:
            mock_get.side_effect = [
                _response(_search({"id": "Q279744", "label": "Half-Life"})),
                _response(_statements(_VIDEO_GAME, year=1998, series="Q1")),
                _response("Half-Life"),
            ]
            ordinal = provider.fetch_series_ordinal(_item("Half-Life", year=1998), {})

        assert ordinal == SeriesOrdinal(position=None, series_name="Half-Life")

    @pytest.mark.parametrize(
        ("title", "content_type", "year", "instance_of", "series", "narrowest"),
        [
            (
                "Mega Man X2",
                ContentType.VIDEO_GAME,
                1994,
                _VIDEO_GAME,
                [("Mega Man", None), ("Mega Man X", "2")],
                "Mega Man X",
            ),
            (
                "The Empire Strikes Back",
                ContentType.MOVIE,
                1980,
                _FILM,
                [("Star Wars", "5"), ("Star Wars: Original Trilogy", "2")],
                "Star Wars: Original Trilogy",
            ),
        ],
        ids=["named_by_the_title", "named_as_a_sub_series"],
    )
    def test_a_work_in_two_series_takes_the_narrower_one_and_its_position(
        self,
        provider: WikidataProvider,
        title: str,
        content_type: ContentType,
        year: int,
        instance_of: str,
        series: list[tuple[str, str | None]],
        narrowest: str,
    ) -> None:
        statements = _statements(instance_of, year=year)
        statements["P179"] = [
            _series_claim(f"Q{index}", ordinal)
            for index, (_name, ordinal) in enumerate(series, start=1)
        ]

        with patch(_REQUESTS) as mock_get:
            mock_get.side_effect = [
                _response(_search({"id": "Q9", "label": title})),
                _response(statements),
                *(_response(name) for name, _ordinal in series),
            ]
            ordinal = provider.fetch_series_ordinal(
                _item(title, content_type, year=year), {}
            )

        assert ordinal == SeriesOrdinal(position=2.0, series_name=narrowest)

    def test_one_series_stating_two_ordinals_keeps_the_name_and_neither_number(
        self, provider: WikidataProvider
    ) -> None:
        statements = _statements(_VIDEO_GAME, year=1999)
        statements["P179"] = [
            _series_claim(_FINAL_FANTASY, "2"),
            _series_claim(_FINAL_FANTASY, "5"),
        ]

        with patch(_REQUESTS) as mock_get:
            mock_get.side_effect = [
                _response(_search({"id": "Q245006", "label": "Final Fantasy VIII"})),
                _response(statements),
                _response("Final Fantasy"),
            ]
            ordinal = provider.fetch_series_ordinal(
                _item("Final Fantasy VIII", year=1999), {}
            )

        assert ordinal == SeriesOrdinal(position=None, series_name="Final Fantasy")

    def test_an_entity_the_match_gate_rejects_names_no_series(
        self, provider: WikidataProvider
    ) -> None:
        """Neverwinter Nights: Enhanced Edition searches up the 1991 AOL game,
        whose P179 would otherwise hand a 2018 re-release the Gold Box series.
        Cleaning the edition off makes the title match, so the year is the gate.
        """
        with patch(_REQUESTS) as mock_get:
            mock_get.side_effect = [
                _response(_search({"id": "Q2", "label": "Neverwinter Nights"})),
                _response(_statements(_VIDEO_GAME, year=1991, series="Q3")),
            ]
            ordinal = provider.fetch_series_ordinal(
                _item("Neverwinter Nights: Enhanced Edition", year=2018), {}
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
                _response("Dune"),
            ]
            ordinal = provider.fetch_series_ordinal(
                _item("Dune", ContentType.BOOK, year=1965), {}
            )

        assert ordinal == SeriesOrdinal(
            position=1.0, series_name="Dune", authority=SeriesAuthority.AUTHORED
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
                _response("Dune"),
            ]
            ordinal = provider.fetch_series_ordinal(
                _item("Dune Messiah (Dune, #2)", ContentType.BOOK, year=1969), {}
            )

        assert ordinal == SeriesOrdinal(
            position=2.0, series_name="Dune", authority=SeriesAuthority.AUTHORED
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
                _response("Dune"),
            ]
            ordinal = provider.fetch_series_ordinal(reprint, {})

        assert ordinal == SeriesOrdinal(
            position=1.0, series_name="Dune", authority=SeriesAuthority.AUTHORED
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
                _response("Dune"),
            ]
            ordinal = provider.fetch_series_ordinal(
                _item("Dune: House Atreides", ContentType.BOOK, year=1999), {}
            )

        assert ordinal is not None
        assert ordinal.position == 0.0

    @pytest.mark.parametrize(
        "stated",
        [
            {"type": "value", "content": "1001"},
            {"type": "value", "content": "inf"},
            {"type": "somevalue"},
        ],
        ids=["out_of_range", "infinite", "blank_node"],
    )
    def test_a_position_no_reader_could_read_back_leaves_the_series_unpositioned(
        self, provider: WikidataProvider, stated: dict[str, Any]
    ) -> None:
        """The blank node is Punch-Out!!'s, which states P1545 as ``somevalue``."""
        statements = _statements(_VIDEO_GAME, year=1999, series=_FINAL_FANTASY)
        statements["P179"] = [_claim(_FINAL_FANTASY, [_ordinal_qualifier(stated)])]

        with patch(_REQUESTS) as mock_get:
            mock_get.side_effect = [
                _response(_search({"id": "Q245006", "label": "Final Fantasy VIII"})),
                _response(statements),
                _response("Final Fantasy"),
            ]
            ordinal = provider.fetch_series_ordinal(
                _item("Final Fantasy VIII", year=1999), {}
            )

        assert ordinal == SeriesOrdinal(position=None, series_name="Final Fantasy")

    @pytest.mark.parametrize(
        ("title", "content_type", "searched"),
        [
            ("Ultima™ VII", ContentType.VIDEO_GAME, "Ultima VII"),
            ("Dune: Deluxe Edition", ContentType.BOOK, "Dune: Deluxe Edition"),
        ],
        ids=["a_game_is_cleaned", "another_type_is_not"],
    )
    def test_only_a_game_is_searched_under_the_cleaned_title(
        self,
        provider: WikidataProvider,
        title: str,
        content_type: ContentType,
        searched: str,
    ) -> None:
        """Wikidata labels carry no trademark or edition, so a stored game title
        went out unresolvable while the cleaner sat in RAWG."""
        with patch(_REQUESTS) as mock_get:
            mock_get.side_effect = [_response(_search())]
            provider.fetch_series_ordinal(_item(title, content_type), {})

        assert mock_get.call_args.kwargs["params"]["search"] == searched

    def test_every_request_names_the_client(self, provider: WikidataProvider) -> None:
        with patch(_REQUESTS) as mock_get:
            mock_get.side_effect = [
                _response(_search({"id": "Q245006", "label": "Final Fantasy VIII"})),
                _response(
                    _statements(
                        _VIDEO_GAME, year=1999, series=_FINAL_FANTASY, ordinal="8"
                    )
                ),
                _response("Final Fantasy"),
            ]
            provider.fetch_series_ordinal(_item("Final Fantasy VIII", year=1999), {})

        assert mock_get.call_count == 3
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
