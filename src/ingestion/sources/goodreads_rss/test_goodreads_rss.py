"""Tests for the Goodreads RSS plugin."""

from __future__ import annotations

import math
from datetime import date
from typing import Any

import pytest
import requests

from src.ingestion.plugin_base import SourceError, SourcePlugin
from src.ingestion.sources.goodreads_rss import goodreads_rss
from src.ingestion.sources.goodreads_rss.goodreads_rss import (
    GoodreadsRssError,
    GoodreadsRssPlugin,
    parse_goodreads_user_id,
)
from src.models.content import ConsumptionStatus, ContentType


@pytest.fixture()
def plugin() -> GoodreadsRssPlugin:
    """Create a GoodreadsRssPlugin instance."""
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
    """Render a single Goodreads-style ``<item>`` element."""
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
    """Wrap item XML in a minimal RSS envelope."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<rss version="2.0"><channel><title>shelf</title>{items_xml}'
        "</channel></rss>"
    )


class _FakeResponse:
    """Minimal stand-in for ``requests.Response`` used by the fake ``get``."""

    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            response = requests.Response()
            response.status_code = self.status_code
            error = requests.HTTPError(f"{self.status_code} Server Error")
            error.response = response
            raise error


def _make_get(pages_by_shelf: dict[str, list[str]]) -> Any:
    """Build a fake ``requests.get`` that serves canned pages per shelf.

    Any page beyond the supplied list returns an empty feed, which terminates
    the plugin's pagination loop.
    """

    def _get(url: str, params: dict[str, Any] | None = None, timeout: int = 0) -> Any:
        assert params is not None
        shelf = params["shelf"]
        page = params["page"]
        pages = pages_by_shelf.get(shelf, [])
        if 1 <= page <= len(pages):
            return _FakeResponse(pages[page - 1])
        return _FakeResponse(_feed(""))

    return _get


def _make_paginating_get(shelf: str, count: int) -> tuple[Any, list[dict[str, Any]]]:
    """Build a fake ``requests.get`` that paginates ``count`` books server-side.

    Unlike :func:`_make_get`, this fake honours the ``per_page`` and ``page``
    query params the plugin sends, slicing a flat list of ``count`` synthetic
    books exactly as Goodreads would. It exercises the real pagination loop:
    the plugin must walk every page and stop only when a page comes back empty
    (there is no "short page means last page" shortcut, so an exact multiple of
    ``per_page`` must still terminate on the trailing empty page).

    Returns the fake ``get`` and a list capturing each request's params, so
    callers can assert how many pages were fetched.
    """
    all_items = [
        _item(title=f"Book {index}", book_id=str(index)) for index in range(count)
    ]
    calls: list[dict[str, Any]] = []

    def _get(url: str, params: dict[str, Any] | None = None, timeout: int = 0) -> Any:
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
    """Tests for the pure ``parse_goodreads_user_id`` helper."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("12345", "12345"),
            ("https://www.goodreads.com/user/show/12345-jane-doe", "12345"),
            ("https://www.goodreads.com/review/list/12345", "12345"),
            ("https://www.goodreads.com/review/list/12345?shelf=read&page=2", "12345"),
            (
                "https://www.goodreads.com/user/show/12345-jane-doe?shelf=read",
                "12345",
            ),
            ("http://www.goodreads.com/user/show/12345-jane-doe/", "12345"),
            ("  67890  ", "67890"),
        ],
    )
    def test_extracts_id(self, raw: str, expected: str) -> None:
        """Test numeric IDs and profile URLs resolve to the numeric ID."""
        assert parse_goodreads_user_id(raw) == expected

    @pytest.mark.parametrize(
        ("raw", "message"),
        [
            ("", "empty"),
            ("not-a-url", "Could not extract"),
            ("https://www.goodreads.com/book/show/999-some-book", "Could not extract"),
            # A recognised marker with no digit run after it must still raise,
            # exercising the ``if match:`` false branch inside the loop.
            ("https://www.goodreads.com/user/show/jane-doe", "Could not extract"),
            ("https://www.goodreads.com/review/list/no-digits", "Could not extract"),
        ],
    )
    def test_invalid_raises(self, raw: str, message: str) -> None:
        """Test that inputs without a user ID raise ValueError with a clear message."""
        with pytest.raises(ValueError, match=message):
            parse_goodreads_user_id(raw)


class TestGoodreadsRssPluginProperties:
    """Tests for GoodreadsRssPlugin metadata properties."""

    def test_is_source_plugin(self, plugin: GoodreadsRssPlugin) -> None:
        """Test that GoodreadsRssPlugin is a SourcePlugin subclass."""
        assert isinstance(plugin, SourcePlugin)

    def test_name(self, plugin: GoodreadsRssPlugin) -> None:
        """Test plugin name identifier."""
        assert plugin.name == "goodreads_rss"

    def test_display_name(self, plugin: GoodreadsRssPlugin) -> None:
        """Test human-readable display name."""
        assert plugin.display_name == "Goodreads (Public Shelves via RSS)"

    def test_description(self, plugin: GoodreadsRssPlugin) -> None:
        """Test the one-line plugin description."""
        assert plugin.description == "Sync books from public Goodreads shelves via RSS"

    def test_content_types(self, plugin: GoodreadsRssPlugin) -> None:
        """Test that plugin provides books."""
        assert plugin.content_types == [ContentType.BOOK]

    def test_requires_api_key(self, plugin: GoodreadsRssPlugin) -> None:
        """Test that plugin does not require an API key."""
        assert plugin.requires_api_key is False

    def test_requires_network(self, plugin: GoodreadsRssPlugin) -> None:
        """Test that plugin requires network access."""
        assert plugin.requires_network is True

    def test_config_schema(self, plugin: GoodreadsRssPlugin) -> None:
        """Test configuration schema defines user_id and shelves."""
        schema = plugin.get_config_schema()

        assert [field.name for field in schema] == ["user_id", "shelves"]
        assert schema[0].field_type is str
        assert schema[0].required is True
        assert schema[1].field_type is list
        assert schema[1].required is False
        assert schema[1].default == ["read", "currently-reading", "to-read"]

    def test_get_source_identifier_default(self, plugin: GoodreadsRssPlugin) -> None:
        """Test source identifier defaults to the plugin name."""
        assert plugin.get_source_identifier() == "goodreads_rss"

    def test_get_source_identifier_from_config(
        self, plugin: GoodreadsRssPlugin
    ) -> None:
        """Test source identifier uses the injected _source_id when present."""
        assert plugin.get_source_identifier({"_source_id": "my_books"}) == "my_books"


class TestGoodreadsRssPluginValidation:
    """Tests for GoodreadsRssPlugin config validation."""

    def test_valid_config(self, plugin: GoodreadsRssPlugin) -> None:
        """Test validation passes for a well-formed config."""
        errors = plugin.validate_config(
            {"user_id": "12345", "shelves": ["read", "to-read"]}
        )

        assert errors == []

    def test_valid_config_without_shelves(self, plugin: GoodreadsRssPlugin) -> None:
        """Test validation passes when shelves is omitted (defaults apply)."""
        assert plugin.validate_config({"user_id": "12345"}) == []

    def test_empty_user_id(self, plugin: GoodreadsRssPlugin) -> None:
        """Test validation fails when user_id is empty."""
        errors = plugin.validate_config({"user_id": ""})

        assert len(errors) == 1
        assert "'user_id' is required" in errors[0]

    def test_missing_user_id(self, plugin: GoodreadsRssPlugin) -> None:
        """Test validation fails when user_id is absent."""
        errors = plugin.validate_config({})

        assert len(errors) == 1
        assert "'user_id' is required" in errors[0]

    def test_unparseable_user_id(self, plugin: GoodreadsRssPlugin) -> None:
        """Test validation fails when user_id is present but has no numeric ID.

        Exercises the ``parse_goodreads_user_id`` ValueError branch inside
        ``validate_config`` — distinct from the empty/missing-user_id branch.
        """
        errors = plugin.validate_config({"user_id": "not-a-url", "shelves": ["read"]})

        assert any("Could not extract" in error for error in errors)

    def test_shelves_not_a_list_string(self, plugin: GoodreadsRssPlugin) -> None:
        """Test validation fails when shelves is a string, not a list."""
        errors = plugin.validate_config({"user_id": "12345", "shelves": "read"})

        assert any("must be a list" in error for error in errors)

    def test_shelves_not_a_list_int(self, plugin: GoodreadsRssPlugin) -> None:
        """Test validation fails when shelves is a non-list, non-string scalar.

        Exercises the ``_coerce_string_list`` ``not isinstance(value, list)``
        branch, distinct from the string branch above.
        """
        errors = plugin.validate_config({"user_id": "12345", "shelves": 5})

        assert any("must be a list" in error for error in errors)

    def test_shelves_entry_not_a_string(self, plugin: GoodreadsRssPlugin) -> None:
        """Test validation fails when a shelves entry is not a string.

        Exercises the ``_coerce_string_list`` per-entry type check: a list with
        a non-string element is rejected before the emptiness check runs.
        """
        errors = plugin.validate_config({"user_id": "12345", "shelves": ["read", 5]})

        assert any("entries must be strings" in error for error in errors)

    def test_empty_shelf_name(self, plugin: GoodreadsRssPlugin) -> None:
        """Test validation fails when a shelf name is blank."""
        errors = plugin.validate_config({"user_id": "12345", "shelves": ["read", "  "]})

        assert any("non-empty" in error for error in errors)


class TestGoodreadsRssPluginFetch:
    """Tests for GoodreadsRssPlugin.fetch()."""

    def test_status_mapping_per_shelf(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test each shelf maps to the expected consumption status."""
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

    def test_rating_zero_is_unrated(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test user_rating of 0 becomes None."""
        fake_get = _make_get(
            {"read": [_feed(_item(title="Book", book_id="1", user_rating="0"))]}
        )
        monkeypatch.setattr(goodreads_rss.requests, "get", fake_get)

        items = list(plugin.fetch({"user_id": "12345", "shelves": ["read"]}))

        assert items[0].rating is None

    def test_rating_normalized(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test a numeric user_rating is normalized to the 1-5 scale."""
        fake_get = _make_get(
            {"read": [_feed(_item(title="Book", book_id="1", user_rating="4"))]}
        )
        monkeypatch.setattr(goodreads_rss.requests, "get", fake_get)

        items = list(plugin.fetch({"user_id": "12345", "shelves": ["read"]}))

        assert items[0].rating == 4

    @pytest.mark.parametrize(
        ("raw_rating", "expected"),
        [
            ("0", None),
            ("1", 1),
            ("2", 2),
            ("3", 3),
            ("4", 4),
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
        """Test ratings across the valid 0-5 Goodreads range normalize correctly.

        A 0 (or blank) Goodreads rating is treated as unrated (None) and 1-5
        pass through unchanged. This covers only the in-range values Goodreads
        emits, not out-of-range handling.
        """
        fake_get = _make_get(
            {"read": [_feed(_item(title="Book", book_id="1", user_rating=raw_rating))]}
        )
        monkeypatch.setattr(goodreads_rss.requests, "get", fake_get)

        items = list(plugin.fetch({"user_id": "12345", "shelves": ["read"]}))

        assert items[0].rating == expected

    def test_metadata_extraction(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test metadata keys extracted from an RSS item."""
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
        # RSS cannot supply these, so the keys are omitted entirely.
        assert "isbn13" not in item.metadata
        assert "publisher" not in item.metadata

    def test_pages_from_nested_book_element(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test page count falls back to the nested <book><num_pages>."""
        fake_get = _make_get(
            {"read": [_feed(_item(title="Book", book_id="1", nested_pages="320"))]}
        )
        monkeypatch.setattr(goodreads_rss.requests, "get", fake_get)

        items = list(plugin.fetch({"user_id": "12345", "shelves": ["read"]}))

        assert items[0].metadata["pages"] == "320"

    def test_date_completed_from_read_shelf(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test date_completed is parsed from user_read_at on the read shelf."""
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

    def test_malformed_read_date_yields_none(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test a non-empty but unparseable user_read_at leaves date_completed None.

        Exercises the ``_parse_rss_date`` except branch: a garbage timestamp
        must be swallowed into ``None`` rather than raising.
        """
        fake_get = _make_get(
            {
                "read": [
                    _feed(_item(title="Book", book_id="1", user_read_at="not-a-date"))
                ]
            }
        )
        monkeypatch.setattr(goodreads_rss.requests, "get", fake_get)

        items = list(plugin.fetch({"user_id": "12345", "shelves": ["read"]}))

        assert items[0].date_completed is None

    def test_pagination_across_two_full_pages(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test two populated pages are both consumed before termination."""
        page1 = _feed(
            "".join(_item(title=f"P1-{n}", book_id=f"1{n}") for n in range(3))
        )
        page2 = _feed(
            "".join(_item(title=f"P2-{n}", book_id=f"2{n}") for n in range(3))
        )
        fake_get = _make_get({"read": [page1, page2]})  # page 3 auto-empty
        monkeypatch.setattr(goodreads_rss.requests, "get", fake_get)

        items = list(plugin.fetch({"user_id": "12345", "shelves": ["read"]}))

        assert len(items) == 6

    @pytest.mark.parametrize("count", [0, 1, 99, 100, 101, 250])
    def test_pagination_returns_all_items_at_boundaries(
        self,
        plugin: GoodreadsRssPlugin,
        monkeypatch: pytest.MonkeyPatch,
        count: int,
    ) -> None:
        """Test every book is returned across page boundaries of PER_PAGE.

        Covers the classic off-by-one pagination bug: an exact multiple of the
        page size (100) must still yield all items and terminate on the
        trailing empty page, and larger counts (250 -> 3 pages) must not drop
        the tail. A count of 0 must be a clean empty result.
        """
        fake_get, calls = _make_paginating_get("read", count)
        monkeypatch.setattr(goodreads_rss.requests, "get", fake_get)

        items = list(plugin.fetch({"user_id": "12345", "shelves": ["read"]}))

        assert len(items) == count
        assert {item.title for item in items} == {
            f"Book {index}" for index in range(count)
        }
        # The loop must fetch one page beyond the last populated page to see
        # the empty terminator: ceil(count / PER_PAGE) + 1 requests (and a bare
        # 1 request when there are no items at all).
        expected_pages = math.ceil(count / goodreads_rss.PER_PAGE) + 1
        assert len(calls) == expected_pages
        assert [call["page"] for call in calls] == list(range(1, expected_pages + 1))

    def test_fully_sparse_item_yields_sane_content_item(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test an item with only a title (no author/isbn/rating/date) is sane.

        Missing optional fields must produce a ContentItem with ``None`` values
        rather than empty strings or a crash.
        """
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

    def test_custom_shelf_name_passed_through_verbatim(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test a hyphenated custom shelf name reaches the request unmodified.

        URL-encoding of the shelf value is delegated to ``requests`` via the
        ``params`` dict, so the plugin must pass the raw shelf name through.
        """
        requested: list[str] = []

        def _get(
            url: str, params: dict[str, Any] | None = None, timeout: int = 0
        ) -> Any:
            assert params is not None
            requested.append(params["shelf"])
            # Serve one item on page 1 and an empty terminator afterwards so
            # the pagination loop stops instead of spinning.
            if params["page"] == 1:
                return _FakeResponse(_feed(_item(title="Book", book_id="1")))
            return _FakeResponse(_feed(""))

        monkeypatch.setattr(goodreads_rss.requests, "get", _get)

        items = list(plugin.fetch({"user_id": "12345", "shelves": ["sci-fi"]}))

        assert requested[0] == "sci-fi"
        assert items[0].status == ConsumptionStatus.UNREAD

    def test_dedup_read_plus_custom_shelf_keeps_completed(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test a book on 'read' and a custom shelf is emitted once as COMPLETED.

        The custom shelf is requested first (UNREAD) and the read shelf must
        upgrade the surviving item to COMPLETED, preserving its read date.
        """
        favorite = _item(title="Shared", book_id="42")
        read = _item(
            title="Shared",
            book_id="42",
            user_read_at="Wed, 10 Jan 2018 00:00:00 -0800",
        )
        fake_get = _make_get({"favorites": [_feed(favorite)], "read": [_feed(read)]})
        monkeypatch.setattr(goodreads_rss.requests, "get", fake_get)

        items = list(
            plugin.fetch({"user_id": "12345", "shelves": ["favorites", "read"]})
        )

        assert len(items) == 1
        assert items[0].status == ConsumptionStatus.COMPLETED
        assert items[0].date_completed == date(2018, 1, 10)

    def test_dedup_across_overlapping_shelves(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test an overlapping book is yielded once with the strongest status."""
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

        # to-read is fetched first, then read upgrades the status.
        items = list(plugin.fetch({"user_id": "12345", "shelves": ["to-read", "read"]}))

        assert len(items) == 1
        assert items[0].status == ConsumptionStatus.COMPLETED
        assert items[0].date_completed == date(2018, 1, 10)

    def test_dedup_falls_back_to_title_author(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test dedup keys on title+author when book_id is absent."""
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

    def test_default_shelves_used_when_omitted(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test fetch queries the three default shelves when none are given."""
        requested: list[str] = []

        def _get(
            url: str, params: dict[str, Any] | None = None, timeout: int = 0
        ) -> Any:
            assert params is not None
            requested.append(params["shelf"])
            return _FakeResponse(_feed(""))

        monkeypatch.setattr(goodreads_rss.requests, "get", _get)

        list(plugin.fetch({"user_id": "12345"}))

        assert requested == ["read", "currently-reading", "to-read"]

    def test_explicit_empty_shelves_fetches_nothing(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test an explicit empty ``shelves`` list fetches nothing and no HTTP.

        Distinct from omitting ``shelves`` (which applies the three defaults):
        an explicit ``[]`` means "sync no shelves", so no request is made.
        """

        def _fail_get(
            url: str, params: dict[str, Any] | None = None, timeout: int = 0
        ) -> Any:
            raise AssertionError("no request should be made for empty shelves")

        monkeypatch.setattr(goodreads_rss.requests, "get", _fail_get)

        items = list(plugin.fetch({"user_id": "12345", "shelves": []}))

        assert items == []

    def test_duplicate_shelf_names_collapse_to_one(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test a shelf listed twice yields each book once (dedup collapses it)."""
        fake_get = _make_get({"read": [_feed(_item(title="Book", book_id="1"))]})
        monkeypatch.setattr(goodreads_rss.requests, "get", fake_get)

        items = list(plugin.fetch({"user_id": "12345", "shelves": ["read", "read"]}))

        assert len(items) == 1

    def test_progress_callback_invoked_in_order(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test progress_callback fires once per emitted item with (i, total, title)."""
        page = _feed(
            _item(title="One", book_id="1")
            + _item(title="Two", book_id="2")
            + _item(title="Three", book_id="3")
        )
        fake_get = _make_get({"read": [page]})
        monkeypatch.setattr(goodreads_rss.requests, "get", fake_get)

        calls: list[tuple[int, int | None, str | None]] = []

        def _callback(index: int, total: int | None, title: str | None) -> None:
            calls.append((index, total, title))

        list(
            plugin.fetch(
                {"user_id": "12345", "shelves": ["read"]},
                progress_callback=_callback,
            )
        )

        assert calls == [(1, 3, "One"), (2, 3, "Two"), (3, 3, "Three")]

    def test_unicode_and_entities_round_trip(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test XML entities and accented characters decode into a clean item.

        A ``&amp;`` entity in the title and an accented author name must survive
        parsing and emerge as their decoded Unicode forms.
        """
        fake_get = _make_get(
            {
                "read": [
                    _feed(
                        _item(
                            title="Cakes &amp; Ale",
                            author="Émile Zola",
                            book_id="1",
                        )
                    )
                ]
            }
        )
        monkeypatch.setattr(goodreads_rss.requests, "get", fake_get)

        items = list(plugin.fetch({"user_id": "12345", "shelves": ["read"]}))

        assert items[0].title == "Cakes & Ale"
        assert items[0].author == "Émile Zola"

    def test_sets_source_identifier(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test fetched items carry the configured source identifier."""
        fake_get = _make_get({"read": [_feed(_item(title="Book", book_id="1"))]})
        monkeypatch.setattr(goodreads_rss.requests, "get", fake_get)

        items = list(
            plugin.fetch(
                {"user_id": "12345", "shelves": ["read"], "_source_id": "my_books"}
            )
        )

        assert items[0].source == "my_books"

    def test_untitled_items_skipped(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test items with an empty title are skipped."""
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

    def test_empty_shelf_is_clean_no_op(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test a shelf with no items yields nothing and raises nothing."""
        fake_get = _make_get({"read": [_feed("")]})
        monkeypatch.setattr(goodreads_rss.requests, "get", fake_get)

        items = list(plugin.fetch({"user_id": "12345", "shelves": ["read"]}))

        assert items == []


class TestGoodreadsRssRegression:
    """Regression tests for the Goodreads RSS plugin.

    SYMPTOM: a ``shelves: null`` (or a bare ``shelves:``) entry in the YAML
    config crashed ``fetch()`` with ``TypeError: 'NoneType' object is not
    iterable``.

    ROOT CAUSE: ``config.get("shelves", DEFAULT_SHELVES)`` only substitutes the
    default when the key is ABSENT; a present-but-null key returns ``None``, and
    ``validate_config`` treated null as valid, so validation passed and ``fetch``
    then tried to iterate ``None``.

    FIX: ``fetch()`` resolves ``config.get("shelves")`` and falls back to
    ``DEFAULT_SHELVES`` only when the result is ``None``, while an explicit empty
    list still syncs nothing.
    """

    def test_explicit_null_shelves_uses_defaults(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test an explicit ``shelves: null`` falls back to the three defaults.

        A YAML ``shelves:`` with no value yields ``None`` (key present), which
        ``config.get("shelves", DEFAULT_SHELVES)`` would NOT default. fetch must
        treat it like an omitted key rather than raising ``TypeError`` on a
        non-iterable ``None``.
        """
        requested: list[str] = []

        def _get(
            url: str, params: dict[str, Any] | None = None, timeout: int = 0
        ) -> Any:
            assert params is not None
            requested.append(params["shelf"])
            return _FakeResponse(_feed(""))

        monkeypatch.setattr(goodreads_rss.requests, "get", _get)

        list(plugin.fetch({"user_id": "12345", "shelves": None}))

        assert requested == ["read", "currently-reading", "to-read"]


class TestGoodreadsRssPluginErrors:
    """Tests for GoodreadsRssPlugin error handling."""

    def test_http_error_raises_without_url(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test an HTTP error raises GoodreadsRssError with no URL leaked."""

        def _get(
            url: str, params: dict[str, Any] | None = None, timeout: int = 0
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

    def test_not_found_profile_raises_clean_error(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test a 404 (private/nonexistent profile) raises a scrubbed error.

        The message must carry the status code but neither the host nor the
        user id, so a private-profile failure cannot leak the identifier.
        """

        def _get(
            url: str, params: dict[str, Any] | None = None, timeout: int = 0
        ) -> Any:
            return _FakeResponse("", status_code=404)

        monkeypatch.setattr(goodreads_rss.requests, "get", _get)

        with pytest.raises(GoodreadsRssError) as exc_info:
            list(plugin.fetch({"user_id": "98765", "shelves": ["read"]}))

        message = str(exc_info.value)
        assert "HTTP 404" in message
        assert "goodreads.com" not in message
        assert "98765" not in message

    def test_connection_error_raises(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test a transport error raises GoodreadsRssError."""

        def _get(
            url: str, params: dict[str, Any] | None = None, timeout: int = 0
        ) -> Any:
            raise requests.ConnectionError("connection refused to www.goodreads.com")

        monkeypatch.setattr(goodreads_rss.requests, "get", _get)

        with pytest.raises(GoodreadsRssError) as exc_info:
            list(plugin.fetch({"user_id": "12345", "shelves": ["read"]}))

        message = str(exc_info.value)
        assert "ConnectionError" in message
        assert "goodreads.com" not in message

    def test_malformed_xml_raises(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test malformed RSS raises GoodreadsRssError."""

        def _get(
            url: str, params: dict[str, Any] | None = None, timeout: int = 0
        ) -> Any:
            return _FakeResponse("<rss><channel><item></broken>")

        monkeypatch.setattr(goodreads_rss.requests, "get", _get)

        with pytest.raises(GoodreadsRssError, match="Malformed RSS"):
            list(plugin.fetch({"user_id": "12345", "shelves": ["read"]}))

    def test_invalid_user_id_raises(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test an unparseable user_id raises before any network call.

        A fail-loud fake ``requests.get`` proves the parse guard rejects the
        user_id up front, so the plugin never makes an outbound request for a
        malformed identifier.
        """

        def _fail_get(
            url: str, params: dict[str, Any] | None = None, timeout: int = 0
        ) -> Any:
            raise AssertionError("requests.get must not be called for a bad user_id")

        monkeypatch.setattr(goodreads_rss.requests, "get", _fail_get)

        with pytest.raises(GoodreadsRssError, match="Could not extract"):
            list(plugin.fetch({"user_id": "not-a-url", "shelves": ["read"]}))

    def test_later_shelf_failure_aborts_and_yields_nothing(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test an HTTP error on a later shelf raises and emits no items.

        The first shelf returns a book, but the second shelf 500s. Because
        fetch accumulates every shelf before yielding, the failure aborts the
        whole run: the caller sees GoodreadsRssError and no items.
        """

        def _get(
            url: str, params: dict[str, Any] | None = None, timeout: int = 0
        ) -> Any:
            assert params is not None
            if params["shelf"] == "read":
                if params["page"] == 1:
                    return _FakeResponse(_feed(_item(title="Book", book_id="1")))
                return _FakeResponse(_feed(""))
            return _FakeResponse("", status_code=500)

        monkeypatch.setattr(goodreads_rss.requests, "get", _get)

        emitted = []
        with pytest.raises(GoodreadsRssError):
            for item in plugin.fetch(
                {"user_id": "12345", "shelves": ["read", "to-read"]}
            ):
                emitted.append(item)

        assert emitted == []

    def test_exceeding_max_pages_raises(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test a shelf that never returns an empty page fails at the page ceiling.

        A feed that always yields a fresh item would paginate forever; the
        MAX_PAGES cap must convert that into a loud GoodreadsRssError.
        """

        def _get(
            url: str, params: dict[str, Any] | None = None, timeout: int = 0
        ) -> Any:
            assert params is not None
            page = params["page"]
            return _FakeResponse(_feed(_item(title=f"B{page}", book_id=str(page))))

        monkeypatch.setattr(goodreads_rss.requests, "get", _get)

        with pytest.raises(GoodreadsRssError, match="page fetch limit"):
            list(plugin.fetch({"user_id": "12345", "shelves": ["read"]}))


# A billion-laughs / entity-expansion payload. Nine nested internal entities
# would each expand ten-fold, so &lol9; alone resolves to 10^9 "lol" strings
# in a naive parser — enough to exhaust memory. defusedxml must reject the
# entity declarations at parse time before any expansion occurs.
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

# An external-entity (XXE) payload attempting to read a local file. defusedxml
# must refuse to resolve the external reference.
_XXE_PAYLOAD = (
    '<?xml version="1.0"?>'
    '<!DOCTYPE rss [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
    '<rss version="2.0"><channel><item><title>&xxe;</title>'
    "</channel></rss>"
)


class TestGoodreadsRssPluginSecurity:
    """Security tests for the defusedxml-based RSS parser.

    The feed is untrusted remote XML, so the parser must reject entity-
    expansion (billion-laughs) and external-entity (XXE) payloads at parse
    time rather than expanding them (memory bomb) or resolving external
    references (local-file disclosure).
    """

    def test_billion_laughs_is_rejected_without_expansion(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test an entity-expansion bomb is rejected, not expanded.

        Regression guard for the stdlib->defusedxml swap: the parser must
        raise on the entity declarations before any expansion, so the fetch
        raises rather than returning a memory-bomb title or hanging. defusedxml
        raises a DefusedXmlException subclass, which the plugin wraps in a
        GoodreadsRssError whose message never echoes the hostile payload.
        """
        fake_get = _make_get({"read": [_BILLION_LAUGHS]})
        monkeypatch.setattr(goodreads_rss.requests, "get", fake_get)

        with pytest.raises(GoodreadsRssError) as exc_info:
            list(plugin.fetch({"user_id": "12345", "shelves": ["read"]}))

        message = str(exc_info.value)
        # Positive assertion so a blank/broken message cannot satisfy the test.
        assert "Malformed or unsafe RSS" in message
        assert "lol" not in message
        assert "ENTITY" not in message
        assert "12345" not in message
        assert "goodreads.com" not in message

    def test_external_entity_is_rejected(
        self, plugin: GoodreadsRssPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test an external-entity (XXE) payload is refused, not resolved.

        A DOCTYPE that declares a SYSTEM entity pointing at a local file must
        be rejected by defusedxml so the file contents can never leak into a
        ContentItem title. The plugin wraps the defused exception in a
        GoodreadsRssError whose message never echoes the payload, URL, or user
        id.
        """
        fake_get = _make_get({"read": [_XXE_PAYLOAD]})
        monkeypatch.setattr(goodreads_rss.requests, "get", fake_get)

        with pytest.raises(GoodreadsRssError) as exc_info:
            list(plugin.fetch({"user_id": "12345", "shelves": ["read"]}))

        message = str(exc_info.value)
        # Positive assertion so a blank/broken message cannot satisfy the test.
        assert "Malformed or unsafe RSS" in message
        assert "passwd" not in message
        assert "SYSTEM" not in message
        assert "12345" not in message
        assert "goodreads.com" not in message
