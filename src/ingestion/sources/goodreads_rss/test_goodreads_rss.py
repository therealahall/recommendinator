from __future__ import annotations

import logging
import math
from datetime import date
from typing import Any

import defusedxml.ElementTree as ET
import pytest
import requests

from src import __version__ as APP_VERSION
from src.ingestion.plugin_base import SourceError
from src.ingestion.sources.goodreads_rss import goodreads_rss
from src.ingestion.sources.goodreads_rss.goodreads_rss import (
    GoodreadsRssError,
    GoodreadsRssPlugin,
    parse_goodreads_user_id,
)
from src.models.content import ConsumptionStatus, ContentType


@pytest.fixture()
def plugin() -> GoodreadsRssPlugin:
    return GoodreadsRssPlugin()


def _item(
    *,
    title: str,
    author: str = "",
    isbn: str = "",
    book_id: str = "",
    num_pages: str = "",
    nested_pages: str = "",
    user_rating: str = "0",
    average_rating: str = "",
    book_published: str = "",
    user_read_at: str = "",
    book_description: str = "",
) -> str:
    book_el = (
        f'<book id="{book_id}"><num_pages>{nested_pages}</num_pages></book>'
        if nested_pages
        else ""
    )
    return f"""<item>
      <title>{title}</title>
      <author_name>{author}</author_name>
      <isbn>{isbn}</isbn>
      <book_id>{book_id}</book_id>
      <num_pages>{num_pages}</num_pages>
      <user_rating>{user_rating}</user_rating>
      <average_rating>{average_rating}</average_rating>
      <book_published>{book_published}</book_published>
      <user_read_at>{user_read_at}</user_read_at>
      <book_description>{book_description}</book_description>
      {book_el}
    </item>"""


def _feed(items_xml: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<rss version="2.0"><channel><title>shelf</title>{items_xml}'
        "</channel></rss>"
    )


class _FakeResponse:
    def __init__(
        self,
        text: str,
        status_code: int = 200,
        url: str = "https://www.goodreads.com/review/list_rss/12345",
    ) -> None:
        self.text = text
        self.status_code = status_code
        self.url = url

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            response = requests.Response()
            response.status_code = self.status_code
            error = requests.HTTPError(f"{self.status_code} Server Error")
            error.response = response
            raise error


def _make_get(pages_by_shelf: dict[str, list[str]]) -> Any:
    def _get(
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 0,
    ) -> Any:
        assert params is not None
        shelf = params["shelf"]
        page = params["page"]
        pages = pages_by_shelf.get(shelf, [])
        if 1 <= page <= len(pages):
            return _FakeResponse(pages[page - 1])
        return _FakeResponse(_feed(""))

    return _get


def _make_paginating_get(shelf: str, count: int) -> tuple[Any, list[dict[str, Any]]]:
    all_items = [
        _item(title=f"Book {index}", book_id=str(index)) for index in range(count)
    ]
    calls: list[dict[str, Any]] = []

    def _get(
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 0,
    ) -> Any:
        assert params is not None
        assert params["shelf"] == shelf
        calls.append(dict(params))
        per_page = int(params["per_page"])
        page = int(params["page"])
        start = (page - 1) * per_page
        chunk = all_items[start : start + per_page]
        return _FakeResponse(_feed("".join(chunk)))

    return _get, calls


class TestParseGoodreadsUserId:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("  67890  ", "67890"),
            ("https://www.goodreads.com/user/show/12345-jane-doe", "12345"),
            ("https://www.goodreads.com/review/list/12345?shelf=read&page=2", "12345"),
        ],
    )
    def test_extracts_id(self, raw: str, expected: str) -> None:
        assert parse_goodreads_user_id(raw) == expected

    @pytest.mark.parametrize(
        ("raw", "message"),
        [
            ("", "empty"),
            ("https://www.goodreads.com/book/show/999-some-book", "Could not extract"),
            ("https://www.goodreads.com/user/show/jane-doe", "Could not extract"),
        ],
    )
    def test_invalid_raises(self, raw: str, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            parse_goodreads_user_id(raw)


class TestGoodreadsRssPluginValidation:
    def test_empty_user_id(self, plugin: GoodreadsRssPlugin) -> None:
        errors = plugin.validate_config({"user_id": ""})

        assert len(errors) == 1
        assert "'user_id' is required" in errors[0]

    def test_unparseable_user_id(self, plugin: GoodreadsRssPlugin) -> None:
        errors = plugin.validate_config({"user_id": "not-a-url", "shelves": ["read"]})

        assert any("Could not extract" in error for error in errors)

    def test_shelves_not_a_list_string(self, plugin: GoodreadsRssPlugin) -> None:
        errors = plugin.validate_config({"user_id": "12345", "shelves": "read"})

        assert any("must be a list" in error for error in errors)


class TestGoodreadsRssPluginFetch:
    def test_status_mapping_per_shelf(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cases = {
            "read": ConsumptionStatus.COMPLETED,
            "currently-reading": ConsumptionStatus.CURRENTLY_CONSUMING,
            "to-read": ConsumptionStatus.UNREAD,
            "favorites": ConsumptionStatus.UNREAD,
        }
        for shelf, expected in cases.items():
            fake_get = _make_get({shelf: [_feed(_item(title="A Book", book_id="1"))]})
            monkeypatch.setattr(goodreads_rss.requests, "get", fake_get)

            items = list(plugin.fetch({"user_id": "12345", "shelves": [shelf]}))

            assert len(items) == 1
            assert items[0].status == expected

    @pytest.mark.parametrize(
        ("raw_rating", "expected"),
        [
            ("0", None),
            ("3", 3),
            ("5", 5),
            ("", None),
        ],
    )
    def test_rating_normalization_over_0_to_5_range(
        self,
        plugin: GoodreadsRssPlugin,
        monkeypatch: pytest.MonkeyPatch,
        raw_rating: str,
        expected: int | None,
    ) -> None:
        fake_get = _make_get(
            {"read": [_feed(_item(title="Book", book_id="1", user_rating=raw_rating))]}
        )
        monkeypatch.setattr(goodreads_rss.requests, "get", fake_get)

        items = list(plugin.fetch({"user_id": "12345", "shelves": ["read"]}))

        assert items[0].rating == expected

    def test_metadata_extraction(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_get = _make_get(
            {
                "read": [
                    _feed(
                        _item(
                            title="Dune",
                            author="Frank Herbert",
                            isbn="0441013597",
                            book_id="234225",
                            num_pages="412",
                            average_rating="4.25",
                            book_published="1965",
                            book_description="A desert planet.",
                        )
                    )
                ]
            }
        )
        monkeypatch.setattr(goodreads_rss.requests, "get", fake_get)

        items = list(plugin.fetch({"user_id": "12345", "shelves": ["read"]}))

        item = items[0]
        assert item.title == "Dune"
        assert item.author == "Frank Herbert"
        assert item.content_type == ContentType.BOOK
        assert item.id == "234225"
        assert item.metadata["book_id"] == "234225"
        assert item.metadata["isbn"] == "0441013597"
        assert item.metadata["pages"] == "412"
        assert item.metadata["year_published"] == "1965"
        assert item.metadata["average_rating"] == "4.25"
        assert item.metadata["description"] == "A desert planet."
        assert item.metadata["shelf"] == "read"
        assert "isbn13" not in item.metadata
        assert "publisher" not in item.metadata

    def test_a_shelved_series_title_arrives_as_a_title_and_a_series(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_get = _make_get(
            {
                "read": [
                    _feed(
                        _item(
                            title="All Systems Red (The Murderbot Diaries, #1)",
                            book_id="32758901",
                        )
                    )
                ]
            }
        )
        monkeypatch.setattr(goodreads_rss.requests, "get", fake_get)

        items = list(plugin.fetch({"user_id": "12345", "shelves": ["read"]}))

        assert items[0].title == "All Systems Red"
        assert items[0].metadata["series"] == "The Murderbot Diaries"
        assert items[0].metadata["series_index"] == 1.0

    def test_pages_from_nested_book_element(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_get = _make_get(
            {"read": [_feed(_item(title="Book", book_id="1", nested_pages="320"))]}
        )
        monkeypatch.setattr(goodreads_rss.requests, "get", fake_get)

        items = list(plugin.fetch({"user_id": "12345", "shelves": ["read"]}))

        assert items[0].metadata["pages"] == "320"

    def test_date_completed_from_read_shelf(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_get = _make_get(
            {
                "read": [
                    _feed(
                        _item(
                            title="Book",
                            book_id="1",
                            user_read_at="Wed, 10 Jan 2018 00:00:00 -0800",
                        )
                    )
                ]
            }
        )
        monkeypatch.setattr(goodreads_rss.requests, "get", fake_get)

        items = list(plugin.fetch({"user_id": "12345", "shelves": ["read"]}))

        assert items[0].date_completed == date(2018, 1, 10)

    @pytest.mark.parametrize("count", [0, 100, 250])
    def test_pagination_returns_all_items_at_boundaries(
        self,
        plugin: GoodreadsRssPlugin,
        monkeypatch: pytest.MonkeyPatch,
        count: int,
    ) -> None:
        fake_get, calls = _make_paginating_get("read", count)
        monkeypatch.setattr(goodreads_rss.requests, "get", fake_get)

        items = list(plugin.fetch({"user_id": "12345", "shelves": ["read"]}))

        assert len(items) == count
        assert {item.title for item in items} == {
            f"Book {index}" for index in range(count)
        }
        expected_pages = math.ceil(count / goodreads_rss.PER_PAGE) + 1
        assert len(calls) == expected_pages
        assert [call["page"] for call in calls] == list(range(1, expected_pages + 1))

    def test_fully_sparse_item_yields_sane_content_item(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sparse = "<item><title>Lonely Book</title></item>"
        fake_get = _make_get({"read": [_feed(sparse)]})
        monkeypatch.setattr(goodreads_rss.requests, "get", fake_get)

        items = list(plugin.fetch({"user_id": "12345", "shelves": ["read"]}))

        assert len(items) == 1
        item = items[0]
        assert item.title == "Lonely Book"
        assert item.author is None
        assert item.id is None
        assert item.rating is None
        assert item.date_completed is None
        assert item.metadata["isbn"] is None
        assert item.metadata["pages"] is None
        assert item.metadata["year_published"] is None
        assert item.metadata["book_id"] == ""

    def test_dedup_across_overlapping_shelves(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        read_book = _item(
            title="Shared",
            book_id="99",
            user_read_at="Wed, 10 Jan 2018 00:00:00 -0800",
        )
        to_read_book = _item(title="Shared", book_id="99")
        fake_get = _make_get(
            {
                "to-read": [_feed(to_read_book)],
                "read": [_feed(read_book)],
            }
        )
        monkeypatch.setattr(goodreads_rss.requests, "get", fake_get)

        items = list(plugin.fetch({"user_id": "12345", "shelves": ["to-read", "read"]}))

        assert len(items) == 1
        assert items[0].status == ConsumptionStatus.COMPLETED
        assert items[0].date_completed == date(2018, 1, 10)

    def test_dedup_falls_back_to_title_author(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        read_book = _item(title="Untitled", author="Anon")
        to_read_book = _item(title="Untitled", author="Anon")
        fake_get = _make_get(
            {
                "to-read": [_feed(to_read_book)],
                "read": [_feed(read_book)],
            }
        )
        monkeypatch.setattr(goodreads_rss.requests, "get", fake_get)

        items = list(plugin.fetch({"user_id": "12345", "shelves": ["to-read", "read"]}))

        assert len(items) == 1
        assert items[0].status == ConsumptionStatus.COMPLETED

    def test_explicit_empty_shelves_fetches_nothing(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _fail_get(
            url: str,
            params: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
            timeout: int = 0,
        ) -> Any:
            raise AssertionError("no request should be made for empty shelves")

        monkeypatch.setattr(goodreads_rss.requests, "get", _fail_get)

        items = list(plugin.fetch({"user_id": "12345", "shelves": []}))

        assert items == []

    def test_untitled_items_skipped(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_get = _make_get(
            {
                "read": [
                    _feed(
                        _item(title="", book_id="1") + _item(title="Real", book_id="2")
                    )
                ]
            }
        )
        monkeypatch.setattr(goodreads_rss.requests, "get", fake_get)

        items = list(plugin.fetch({"user_id": "12345", "shelves": ["read"]}))

        assert [item.title for item in items] == ["Real"]

    def test_requests_the_shelf_rss_endpoint(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        requested: list[str] = []

        def _get(
            url: str,
            params: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
            timeout: int = 0,
        ) -> Any:
            requested.append(url)
            return _FakeResponse(_feed(""))

        monkeypatch.setattr(goodreads_rss.requests, "get", _get)

        list(plugin.fetch({"user_id": "12345", "shelves": ["read"]}))

        assert requested == ["https://www.goodreads.com/review/list_rss/12345"]


class TestGoodreadsRssRegression:
    def test_explicit_null_shelves_uses_defaults(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        requested: list[str] = []

        def _get(
            url: str,
            params: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
            timeout: int = 0,
        ) -> Any:
            assert params is not None
            requested.append(params["shelf"])
            return _FakeResponse(_feed(""))

        monkeypatch.setattr(goodreads_rss.requests, "get", _get)

        list(plugin.fetch({"user_id": "12345", "shelves": None}))

        assert requested == ["read", "currently-reading", "to-read"]


class TestGoodreadsRssUserAgentRegression:
    def test_the_request_identifies_the_app_and_asks_for_rss(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent: list[dict[str, str] | None] = []

        def _get(
            url: str,
            params: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
            timeout: int = 0,
        ) -> Any:
            sent.append(headers)
            return _FakeResponse(_feed(""))

        monkeypatch.setattr(goodreads_rss.requests, "get", _get)

        list(plugin.fetch({"user_id": "12345", "shelves": ["read"]}))

        headers = sent[0]
        assert headers is not None
        assert headers["User-Agent"].startswith(f"Recommendinator/{APP_VERSION}")
        assert "python-requests" not in headers["User-Agent"]
        assert "Mozilla" not in headers["User-Agent"]
        assert "rss" in headers["Accept"]
        assert "*/*" in headers["Accept"]


_SIGN_IN_PAGE = """<!DOCTYPE html>
<html>
<head>
  <title>Sign in</title>

<meta content='telephone=no' name='format-detection'>
<link href='https://www.goodreads.com/user/sign_in' rel='canonical'>

  <script type="text/javascript">
    var ue_mid = "PLACEHOLDER";
    var ue_sid = "000-0000000-0000000";
    if("ue_https" in e){f=e.ue_https}else{f=e.location&&e.location.protocol=="https:"?1:0}
  </script>
</head>
</html>
"""


class TestGoodreadsRssSignInRedirectRegression:
    def test_the_captured_page_is_the_parse_failure_that_was_reported(self) -> None:
        """A fixture edited into valid XML would make the test below vacuous."""
        with pytest.raises(ET.ParseError, match="not well-formed"):
            ET.fromstring(_SIGN_IN_PAGE)

    def test_the_sign_in_page_is_named_not_reported_as_malformed_rss(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _get(
            url: str,
            params: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
            timeout: int = 0,
        ) -> Any:
            return _FakeResponse(
                _SIGN_IN_PAGE,
                url="https://www.goodreads.com/user/sign_in?returnurl=%2Ffeed",
            )

        monkeypatch.setattr(goodreads_rss.requests, "get", _get)

        with pytest.raises(GoodreadsRssError) as exc_info:
            list(plugin.fetch({"user_id": "12345", "shelves": ["read"]}))

        message = str(exc_info.value)
        assert "sign-in page" in message
        assert "not public" in message
        assert "Malformed RSS" not in message
        assert "not well-formed" not in message
        assert "12345" not in message
        assert "goodreads.com" not in message


class TestGoodreadsRssPluginErrors:
    def test_http_error_raises_without_url(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _get(
            url: str,
            params: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
            timeout: int = 0,
        ) -> Any:
            return _FakeResponse("", status_code=500)

        monkeypatch.setattr(goodreads_rss.requests, "get", _get)

        with pytest.raises(GoodreadsRssError) as exc_info:
            list(plugin.fetch({"user_id": "12345", "shelves": ["read"]}))

        message = str(exc_info.value)
        assert "HTTP 500" in message
        assert "goodreads.com" not in message
        assert "12345" not in message
        assert isinstance(exc_info.value, SourceError)

    def test_malformed_xml_raises(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _get(
            url: str,
            params: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
            timeout: int = 0,
        ) -> Any:
            return _FakeResponse("<rss><channel><item></broken>")

        monkeypatch.setattr(goodreads_rss.requests, "get", _get)

        with pytest.raises(GoodreadsRssError, match="Malformed RSS"):
            list(plugin.fetch({"user_id": "12345", "shelves": ["read"]}))

    def test_invalid_user_id_raises(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _fail_get(
            url: str,
            params: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
            timeout: int = 0,
        ) -> Any:
            raise AssertionError("requests.get must not be called for a bad user_id")

        monkeypatch.setattr(goodreads_rss.requests, "get", _fail_get)

        with pytest.raises(GoodreadsRssError, match="Could not extract"):
            list(plugin.fetch({"user_id": "not-a-url", "shelves": ["read"]}))

    def test_exceeding_max_pages_raises(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _get(
            url: str,
            params: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
            timeout: int = 0,
        ) -> Any:
            assert params is not None
            page = params["page"]
            return _FakeResponse(_feed(_item(title=f"B{page}", book_id=str(page))))

        monkeypatch.setattr(goodreads_rss.requests, "get", _get)

        with pytest.raises(GoodreadsRssError, match="page fetch limit"):
            list(plugin.fetch({"user_id": "12345", "shelves": ["read"]}))


_BILLION_LAUGHS = (
    '<?xml version="1.0"?>'
    "<!DOCTYPE lolz ["
    '<!ENTITY lol "lol">'
    '<!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
    '<!ENTITY lol2 "&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;">'
    '<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">'
    '<!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">'
    '<!ENTITY lol5 "&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;">'
    '<!ENTITY lol6 "&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;">'
    '<!ENTITY lol7 "&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;">'
    '<!ENTITY lol8 "&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;">'
    '<!ENTITY lol9 "&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;">'
    "]>"
    '<rss version="2.0"><channel><item><title>&lol9;</title>'
    "</channel></rss>"
)


class TestGoodreadsRssPluginSecurity:
    def test_billion_laughs_is_rejected_without_expansion(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_get = _make_get({"read": [_BILLION_LAUGHS]})
        monkeypatch.setattr(goodreads_rss.requests, "get", fake_get)

        with pytest.raises(GoodreadsRssError) as exc_info:
            list(plugin.fetch({"user_id": "12345", "shelves": ["read"]}))

        message = str(exc_info.value)
        assert "Malformed or unsafe RSS" in message
        assert "lol" not in message
        assert "ENTITY" not in message
        assert "12345" not in message
        assert "goodreads.com" not in message


GOODREADS_RSS_LOGGER = "src.ingestion.sources.goodreads_rss.goodreads_rss"


class TestGoodreadsRssLogInjectionRegression:
    def test_a_newline_in_a_shelf_name_cannot_forge_a_log_entry(
        self,
        plugin: GoodreadsRssPlugin,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        def fail(*_args: Any, **_kwargs: Any) -> None:
            raise requests.ConnectionError("boom")

        monkeypatch.setattr(goodreads_rss.requests, "get", fail)
        shelf = "read\nCollected 9999 unique books across 1 Goodreads shelves"

        with caplog.at_level(logging.ERROR, logger=GOODREADS_RSS_LOGGER):
            with pytest.raises(GoodreadsRssError):
                list(plugin.fetch({"user_id": "12345", "shelves": [shelf]}))

        messages = [
            record.getMessage()
            for record in caplog.records
            if record.name == GOODREADS_RSS_LOGGER
        ]
        assert messages, "nothing was logged, so this proves nothing"
        assert "\n" not in messages[0], messages
