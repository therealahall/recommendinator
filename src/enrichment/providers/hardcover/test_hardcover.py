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
    SeriesAuthority,
    reconcile_series_ordinal,
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
) -> dict[str, Any]:
    return {
        "title": title,
        "contributions": [{"author": {"name": author}}],
        "featured_book_series": {"position": position} if in_a_series else None,
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

    def test_no_token_is_skipped_rather_than_failing_the_item(
        self, provider: HardcoverProvider, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG)
        with patch(
            "src.enrichment.providers.hardcover.hardcover.requests.post"
        ) as mock_post:
            assert provider.fetch_series_ordinal(_book(), {}) is None

        assert mock_post.call_count == 0
        assert _TOKEN not in caplog.text


class TestHardcoverMatching:
    def test_an_isbn_is_filtered_on_in_place_of_the_title(
        self, provider: HardcoverProvider
    ) -> None:
        item = _book(isbn13="978-0-316-12908-4")

        with patch(
            "src.enrichment.providers.hardcover.hardcover.requests.post"
        ) as mock_post:
            mock_post.return_value = _response(_books(_hardcover_book()))
            ordinal = provider.fetch_series_ordinal(item, _CONFIG)

        where = mock_post.call_args.kwargs["json"]["variables"]["where"]
        assert where["editions"] == {"isbn_13": {"_eq": "9780316129084"}}
        assert "title" not in where
        assert ordinal is not None

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
            ordinal = provider.fetch_series_ordinal(item, _CONFIG)

        second_where = mock_post.call_args.kwargs["json"]["variables"]["where"]
        assert second_where["title"] == {"_ilike": "%Leviathan Wakes%"}
        assert ordinal is not None

    def test_two_books_sharing_a_title_are_refused(
        self, provider: HardcoverProvider
    ) -> None:
        with patch(
            "src.enrichment.providers.hardcover.hardcover.requests.post"
        ) as mock_post:
            mock_post.return_value = _response(
                _books(
                    _hardcover_book(position=1),
                    _hardcover_book(position=4),
                )
            )
            assert provider.fetch_series_ordinal(_book(), _CONFIG) is None

    def test_another_authors_book_of_the_same_name_is_refused(
        self, provider: HardcoverProvider
    ) -> None:
        with patch(
            "src.enrichment.providers.hardcover.hardcover.requests.post"
        ) as mock_post:
            mock_post.return_value = _response(
                _books(_hardcover_book(author="Someone Else"))
            )
            assert provider.fetch_series_ordinal(_book(), _CONFIG) is None

    def test_a_book_whose_two_authors_share_one_stored_field_still_matches(
        self, provider: HardcoverProvider
    ) -> None:
        illuminae = _hardcover_book(title="Illuminae", author="Amie Kaufman")
        illuminae["contributions"].append({"author": {"name": "Jay Kristoff"}})

        with patch(
            "src.enrichment.providers.hardcover.hardcover.requests.post"
        ) as mock_post:
            mock_post.return_value = _response(_books(illuminae))
            ordinal = provider.fetch_series_ordinal(
                _book(title="Illuminae", author="Amie Kaufman, Jay Kristoff"), _CONFIG
            )

        assert ordinal is not None

    def test_an_isbn_column_holding_no_digits_falls_back_to_the_title(
        self, provider: HardcoverProvider
    ) -> None:
        with patch(
            "src.enrichment.providers.hardcover.hardcover.requests.post"
        ) as mock_post:
            mock_post.return_value = _response(_books(_hardcover_book()))
            ordinal = provider.fetch_series_ordinal(_book(isbn13='=""'), _CONFIG)

        assert mock_post.call_count == 1
        where = mock_post.call_args.kwargs["json"]["variables"]["where"]
        assert "editions" not in where
        assert ordinal is not None

    def test_a_title_that_only_contains_the_searched_one_is_refused(
        self, provider: HardcoverProvider
    ) -> None:
        with patch(
            "src.enrichment.providers.hardcover.hardcover.requests.post"
        ) as mock_post:
            mock_post.return_value = _response(
                _books(_hardcover_book(title="The Leviathan Wakes Companion"))
            )
            assert provider.fetch_series_ordinal(_book(), _CONFIG) is None


class TestHardcoverSeriesPosition:
    def test_a_featured_series_becomes_an_authored_ordinal(
        self, provider: HardcoverProvider
    ) -> None:
        with patch(
            "src.enrichment.providers.hardcover.hardcover.requests.post"
        ) as mock_post:
            mock_post.return_value = _response(_books(_hardcover_book()))
            ordinal = provider.fetch_series_ordinal(
                _book(isbn13="9780316129084"), _CONFIG
            )

        assert ordinal is not None
        assert ordinal.position == 1.0
        assert ordinal.authority is SeriesAuthority.AUTHORED

    def test_a_book_in_no_series_states_no_ordinal(
        self, provider: HardcoverProvider
    ) -> None:
        with patch(
            "src.enrichment.providers.hardcover.hardcover.requests.post"
        ) as mock_post:
            mock_post.return_value = _response(
                _books(_hardcover_book(in_a_series=False))
            )
            assert provider.fetch_series_ordinal(_book(), _CONFIG) is None

    def test_a_series_membership_with_no_position_states_no_ordinal(
        self, provider: HardcoverProvider
    ) -> None:
        with patch(
            "src.enrichment.providers.hardcover.hardcover.requests.post"
        ) as mock_post:
            mock_post.return_value = _response(_books(_hardcover_book(position=None)))
            assert provider.fetch_series_ordinal(_book(), _CONFIG) is None

    def test_a_position_no_reader_could_read_back_is_never_stated(
        self, provider: HardcoverProvider
    ) -> None:
        with patch(
            "src.enrichment.providers.hardcover.hardcover.requests.post"
        ) as mock_post:
            mock_post.return_value = _response(_books(_hardcover_book(position=1001)))
            assert provider.fetch_series_ordinal(_book(), _CONFIG) is None

    def test_a_stated_position_keeps_its_value_and_gains_authored_authority(
        self, provider: HardcoverProvider
    ) -> None:
        stored = {
            "series_name": "The Expanse",
            "series_position": 2.5,
            SERIES_AUTHORITY_KEY: SeriesAuthority.STATED.value,
        }

        with patch(
            "src.enrichment.providers.hardcover.hardcover.requests.post"
        ) as mock_post:
            mock_post.return_value = _response(
                _books(_hardcover_book(title="Gods of Risk", position=2.5))
            )
            ordinal = provider.fetch_series_ordinal(
                _book(title="Gods of Risk (The Expanse, #2.5)", **stored), _CONFIG
            )

        assert ordinal is not None
        settled = reconcile_series_ordinal(stored, ordinal.as_metadata())
        assert settled["series_position"] == 2.5
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
                provider.fetch_series_ordinal(_book(), _CONFIG)

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
                provider.fetch_series_ordinal(_book(), _CONFIG)

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
                provider.fetch_series_ordinal(_book(), _CONFIG)

        assert mock_post.call_count == 1
        assert "elsewhere.example.com" in str(raised.value)
        assert _TOKEN not in str(raised.value)
