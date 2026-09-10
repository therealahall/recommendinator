import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.enrichment.provider_base import ProviderError
from src.enrichment.providers.hardcover.hardcover import HardcoverProvider
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.utils.series import (
    SERIES_AUTHORITY_KEY,
    SERIES_POSITION_KEY,
    SeriesAuthority,
    reconcile_series,
)

_TOKEN = "hardcover-personal-access-token"

_CONFIG = {"api_key": _TOKEN}


def _book(
    title: str = "Leviathan Wakes",
    author: str = "James S. A. Corey",
    **metadata: Any,
) -> ContentItem:
    return ContentItem(
        title=title,
        author=author,
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
        metadata=metadata,
    )


def _hardcover_book(
    title: str = "Leviathan Wakes",
    author: str = "James S. A. Corey",
    in_a_series: bool = True,
    position: float | None = 1,
    series: str | None = "The Expanse",
) -> dict[str, Any]:
    membership = {
        "position": position,
        "series": {"name": series} if series is not None else None,
    }
    return {
        "id": 427621,
        "title": title,
        "description": "Humanity has colonized the solar system.",
        "release_year": 2011,
        "image": {"url": "https://assets.hardcover.app/editions/1/cover.jpg"},
        "cached_tags": {
            "Genre": [{"tag": "Science Fiction", "count": 18}],
            "Mood": [{"tag": "tense", "count": 39}],
            "Tag": [{"tag": "Plot driven", "count": 27}],
            "Content Warning": [{"tag": "Violence", "count": 3}],
        },
        "contributions": [{"author": {"name": author}}],
        "featured_book_series": membership if in_a_series else None,
    }


def _response(
    payload: dict[str, Any],
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


def _books(*books: dict[str, Any]) -> dict[str, Any]:
    return {"data": {"books": list(books)}}


def _enriched(
    provider: HardcoverProvider,
    book: dict[str, Any],
    item: ContentItem | None = None,
) -> dict[str, Any]:
    with patch(
        "src.enrichment.providers.hardcover.hardcover.requests.post"
    ) as mock_post:
        mock_post.return_value = _response(_books(book))
        result = provider.enrich(item if item is not None else _book(), _CONFIG)

    # A miss returns not_found rather than None, so every caller asserting on
    # extra_metadata would pass against an empty dict without this.
    assert result is not None
    assert result.match_quality != "not_found"
    return result.extra_metadata


@pytest.fixture
def provider() -> HardcoverProvider:
    return HardcoverProvider()


class TestHardcoverConfig:
    def test_validate_rejects_a_missing_api_key(
        self, provider: HardcoverProvider
    ) -> None:
        assert (
            "'api_key' is required for Hardcover provider"
            in provider.validate_config({})
        )


class TestHardcoverMatching:
    def test_an_isbn_is_filtered_on_in_place_of_the_title(
        self, provider: HardcoverProvider
    ) -> None:
        item = _book(isbn13="978-0-316-12908-4")

        with patch(
            "src.enrichment.providers.hardcover.hardcover.requests.post"
        ) as mock_post:
            mock_post.return_value = _response(_books(_hardcover_book()))
            result = provider.enrich(item, _CONFIG)

        where = mock_post.call_args.kwargs["json"]["variables"]["where"]
        assert where["editions"] == {"isbn_13": {"_eq": "9780316129084"}}
        assert "title" not in where
        assert result is not None and result.match_quality == "high"

    def test_an_isbn_hardcover_does_not_hold_falls_back_to_the_title(
        self, provider: HardcoverProvider
    ) -> None:
        item = _book(isbn="0316129089")

        with patch(
            "src.enrichment.providers.hardcover.hardcover.requests.post"
        ) as mock_post:
            mock_post.side_effect = [
                _response(_books()),
                _response(_books(_hardcover_book())),
            ]
            result = provider.enrich(item, _CONFIG)

        second_where = mock_post.call_args.kwargs["json"]["variables"]["where"]
        assert second_where["title"] == {"_ilike": "%Leviathan Wakes%"}
        assert result is not None and result.match_quality != "not_found"

    def test_a_title_search_is_reported_as_resembling_rather_than_identifying(
        self, provider: HardcoverProvider
    ) -> None:
        with patch(
            "src.enrichment.providers.hardcover.hardcover.requests.post"
        ) as mock_post:
            mock_post.return_value = _response(_books(_hardcover_book()))
            result = provider.enrich(_book(), _CONFIG)

        assert result is not None and result.match_quality == "medium"

    def test_another_authors_book_of_the_same_name_is_refused(
        self, provider: HardcoverProvider
    ) -> None:
        with patch(
            "src.enrichment.providers.hardcover.hardcover.requests.post"
        ) as mock_post:
            mock_post.return_value = _response(
                _books(_hardcover_book(author="Someone Else"))
            )
            result = provider.enrich(_book(), _CONFIG)

        assert result is not None and result.match_quality == "not_found"

    def test_a_book_whose_two_authors_share_one_stored_field_still_matches(
        self, provider: HardcoverProvider
    ) -> None:
        illuminae = _hardcover_book(title="Illuminae", author="Amie Kaufman")
        illuminae["contributions"].append({"author": {"name": "Jay Kristoff"}})

        with patch(
            "src.enrichment.providers.hardcover.hardcover.requests.post"
        ) as mock_post:
            mock_post.return_value = _response(_books(illuminae))
            result = provider.enrich(
                _book(title="Illuminae", author="Amie Kaufman, Jay Kristoff"), _CONFIG
            )

        assert result is not None and result.match_quality != "not_found"

    def test_an_isbn_column_holding_no_digits_falls_back_to_the_title(
        self, provider: HardcoverProvider
    ) -> None:
        with patch(
            "src.enrichment.providers.hardcover.hardcover.requests.post"
        ) as mock_post:
            mock_post.return_value = _response(_books(_hardcover_book()))
            result = provider.enrich(_book(isbn13='=""'), _CONFIG)

        assert mock_post.call_count == 1
        where = mock_post.call_args.kwargs["json"]["variables"]["where"]
        assert "editions" not in where
        assert result is not None and result.match_quality != "not_found"

    def test_a_title_that_only_contains_the_searched_one_is_refused(
        self, provider: HardcoverProvider
    ) -> None:
        with patch(
            "src.enrichment.providers.hardcover.hardcover.requests.post"
        ) as mock_post:
            mock_post.return_value = _response(
                _books(_hardcover_book(title="The Leviathan Wakes Companion"))
            )
            result = provider.enrich(_book(), _CONFIG)

        assert result is not None and result.match_quality == "not_found"


class TestHardcoverEnrichment:
    def test_a_matched_book_is_enriched_rather_than_only_positioned(
        self, provider: HardcoverProvider
    ) -> None:
        with patch(
            "src.enrichment.providers.hardcover.hardcover.requests.post"
        ) as mock_post:
            mock_post.return_value = _response(_books(_hardcover_book()))
            result = provider.enrich(_book(), _CONFIG)

        assert result is not None
        # Hardcover's other tag categories are moods and content warnings.
        assert result.genres == ["Science Fiction"]
        assert result.tags == ["Science Fiction"]
        assert result.description == "Humanity has colonized the solar system."
        assert result.cover_url == "https://assets.hardcover.app/editions/1/cover.jpg"
        assert result.extra_metadata["year_published"] == 2011
        assert result.extra_metadata[SERIES_POSITION_KEY] == 1.0
        assert (
            result.extra_metadata[SERIES_AUTHORITY_KEY]
            == SeriesAuthority.AUTHORED.value
        )

    def test_a_pinned_record_is_enriched_without_a_title_search(
        self, provider: HardcoverProvider
    ) -> None:
        with patch(
            "src.enrichment.providers.hardcover.hardcover.requests.post"
        ) as mock_post:
            mock_post.return_value = _response(
                _books(_hardcover_book(title="Allanon's Quest", author="Terry Brooks"))
            )
            result = provider.enrich(
                _book(title="Allanon's Quest", enrichment_ids={"hardcover": "460708"}),
                _CONFIG,
            )

        assert mock_post.call_count == 1
        where = mock_post.call_args.kwargs["json"]["variables"]["where"]
        assert where["id"] == {"_eq": "460708"}
        assert "title" not in where
        assert result is not None and result.match_quality == "high"

    def test_a_book_hardcover_cannot_match_is_not_found_rather_than_partial(
        self, provider: HardcoverProvider
    ) -> None:
        with patch(
            "src.enrichment.providers.hardcover.hardcover.requests.post"
        ) as mock_post:
            mock_post.return_value = _response(
                _books(_hardcover_book(), _hardcover_book(position=4))
            )
            result = provider.enrich(_book(), _CONFIG)

        assert result is not None
        assert result.match_quality == "not_found"
        assert result.genres is None
        assert result.description is None
        assert result.cover_url is None

    def test_no_token_makes_no_request_rather_than_a_rejected_one(
        self, provider: HardcoverProvider
    ) -> None:
        with patch(
            "src.enrichment.providers.hardcover.hardcover.requests.post"
        ) as mock_post:
            assert provider.enrich(_book(), {}) is None

        assert mock_post.call_count == 0

    def test_the_ordinal_pass_never_repeats_the_lookup_enrich_already_made(
        self, provider: HardcoverProvider
    ) -> None:
        with patch(
            "src.enrichment.providers.hardcover.hardcover.requests.post"
        ) as mock_post:
            assert provider.fetch_series_ordinal(_book(), _CONFIG) is None

        assert mock_post.call_count == 0

    def test_a_long_tail_of_one_reader_tags_is_ranked_and_cut_to_ten(
        self, provider: HardcoverProvider
    ) -> None:
        crowded = _hardcover_book()
        crowded["cached_tags"]["Genre"] = [
            {"tag": f"Niche {index}", "count": 1} for index in range(12)
        ] + [{"tag": "Science Fiction", "count": 18}]

        with patch(
            "src.enrichment.providers.hardcover.hardcover.requests.post"
        ) as mock_post:
            mock_post.return_value = _response(_books(crowded))
            result = provider.enrich(_book(), _CONFIG)

        assert result is not None and result.genres is not None
        assert result.genres[0] == "Science Fiction"
        assert len(result.genres) == 10

    @pytest.mark.parametrize("url", ["http://assets.hardcover.app/c.jpg", {"src": 1}])
    def test_only_an_https_string_becomes_a_cover_the_backfill_dials(
        self, provider: HardcoverProvider, url: Any
    ) -> None:
        insecure = _hardcover_book()
        insecure["image"] = {"url": url}

        with patch(
            "src.enrichment.providers.hardcover.hardcover.requests.post"
        ) as mock_post:
            mock_post.return_value = _response(_books(insecure))
            result = provider.enrich(_book(), _CONFIG)
            candidates = provider.search(_book(), _CONFIG)

        assert result is not None and result.cover_url is None
        assert candidates[0].cover_url is None


class TestHardcoverSeriesPosition:
    def test_a_book_in_no_series_states_no_ordinal(
        self, provider: HardcoverProvider
    ) -> None:
        stated = _enriched(provider, _hardcover_book(in_a_series=False))
        assert SERIES_POSITION_KEY not in stated

    def test_a_series_membership_with_no_position_states_no_ordinal(
        self, provider: HardcoverProvider
    ) -> None:
        stated = _enriched(provider, _hardcover_book(position=None))
        assert SERIES_POSITION_KEY not in stated

    def test_a_position_no_reader_could_read_back_is_never_stated(
        self, provider: HardcoverProvider
    ) -> None:
        stated = _enriched(provider, _hardcover_book(position=1001))
        assert SERIES_POSITION_KEY not in stated

    def test_an_unnamed_series_states_no_ordinal(
        self, provider: HardcoverProvider
    ) -> None:
        stated = _enriched(provider, _hardcover_book(series=None))
        assert SERIES_POSITION_KEY not in stated

    def test_a_featured_sub_series_never_positions_the_stored_parent_series(
        self, provider: HardcoverProvider
    ) -> None:
        stored = {"series_name": "The Expanse"}
        stated = _enriched(
            provider,
            _hardcover_book(
                title="Gods of Risk", position=1, series="The Expanse: Novellas"
            ),
            _book(title="Gods of Risk", **stored),
        )

        assert reconcile_series(stored, stated) == {}

    def test_a_stated_position_keeps_its_value_and_gains_authored_authority(
        self, provider: HardcoverProvider
    ) -> None:
        stored = {
            "series_name": "The Expanse",
            SERIES_POSITION_KEY: 2.5,
            SERIES_AUTHORITY_KEY: SeriesAuthority.STATED.value,
        }
        stated = _enriched(
            provider,
            _hardcover_book(title="Gods of Risk", position=2.5),
            _book(title="Gods of Risk (The Expanse, #2.5)", **stored),
        )

        settled = reconcile_series(stored, stated)
        assert settled[SERIES_POSITION_KEY] == 2.5
        assert settled[SERIES_AUTHORITY_KEY] == SeriesAuthority.AUTHORED.value


class TestHardcoverFailures:
    def test_a_rejected_token_surfaces_the_status_and_not_the_token(
        self, provider: HardcoverProvider, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG)
        with patch(
            "src.enrichment.providers.hardcover.hardcover.requests.post"
        ) as mock_post:
            mock_post.return_value = _response({}, status=401)
            with pytest.raises(ProviderError) as raised:
                provider.enrich(_book(), _CONFIG)

        assert "HTTP 401" in str(raised.value)
        assert _TOKEN not in str(raised.value)
        assert _TOKEN not in caplog.text

    def test_a_graphql_auth_error_fails_without_the_token(
        self, provider: HardcoverProvider, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG)
        with patch(
            "src.enrichment.providers.hardcover.hardcover.requests.post"
        ) as mock_post:
            mock_post.return_value = _response(
                {
                    "errors": [
                        {
                            "message": "invalid token",
                            "extensions": {"code": "invalid-headers"},
                        }
                    ]
                }
            )
            with pytest.raises(ProviderError) as raised:
                provider.enrich(_book(), _CONFIG)

        assert "invalid-headers" in str(raised.value)
        assert _TOKEN not in str(raised.value)
        assert _TOKEN not in caplog.text

    def test_a_redirect_off_the_api_origin_is_refused_before_it_is_followed(
        self, provider: HardcoverProvider
    ) -> None:
        with patch(
            "src.enrichment.providers.hardcover.hardcover.requests.post"
        ) as mock_post:
            mock_post.return_value = _response(
                {},
                status=302,
                headers={"Location": "https://elsewhere.example.com/v1/graphql"},
            )
            with pytest.raises(ProviderError) as raised:
                provider.enrich(_book(), _CONFIG)

        assert mock_post.call_count == 1
        assert "elsewhere.example.com" in str(raised.value)
        assert _TOKEN not in str(raised.value)
