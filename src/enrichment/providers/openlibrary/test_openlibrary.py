"""Tests for the OpenLibrary enrichment provider."""

import logging
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.enrichment.providers.openlibrary.openlibrary import (
    OpenLibraryProvider,
    clean_title_for_search,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType


class TestCleanTitleForSearch:
    """Tests for title cleaning before search."""

    def test_removes_series_with_comma(self) -> None:
        """Test removal of series info with comma format."""
        assert (
            clean_title_for_search("For We Are Many (Bobiverse, #2)")
            == "For We Are Many"
        )

    def test_handles_parentheses_without_series_number(self) -> None:
        """Test that parentheses without series numbers are preserved."""
        assert (
            clean_title_for_search("The Stand (Uncut Edition)")
            == "The Stand (Uncut Edition)"
        )


class TestOpenLibraryProviderISBNLookup:
    """Tests for ISBN lookup."""

    @pytest.fixture
    def provider(self) -> OpenLibraryProvider:
        """Create provider instance."""
        return OpenLibraryProvider()

    def test_isbn_lookup_success(self, provider: OpenLibraryProvider) -> None:
        """Test successful ISBN lookup."""
        item = ContentItem(
            id="book1",
            title="1984",
            author="George Orwell",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"isbn13": "9780451524935"},
        )

        mock_edition = {
            "key": "/books/OL1234E",
            "works": [{"key": "/works/OL5678W"}],
            "number_of_pages": 328,
            "publishers": ["Signet Classic"],
            "publish_date": "1961",
        }

        mock_work = {
            "key": "/works/OL5678W",
            "subjects": ["Dystopia", "Science fiction", "Political fiction"],
            "description": "A dystopian novel about totalitarianism.",
            "first_publish_date": "1949",
        }

        with patch(
            "src.enrichment.providers.openlibrary.openlibrary.requests.get"
        ) as mock_get:
            mock_get.side_effect = [
                MagicMock(
                    spec=requests.Response, status_code=200, json=lambda: mock_edition
                ),
                MagicMock(
                    spec=requests.Response, status_code=200, json=lambda: mock_work
                ),
            ]

            result = provider.enrich(item, {})

        assert result is not None
        assert result.external_id == "openlibrary:OL5678W"
        assert "Dystopia" in result.genres
        # Tags should also be populated for cross-content-type matching
        assert result.tags is not None
        assert "Dystopia" in result.tags
        assert "dystopian" in result.description.lower()
        assert result.match_quality == "high"

    def test_isbn_not_found(self, provider: OpenLibraryProvider) -> None:
        """Test ISBN lookup when not found."""
        item = ContentItem(
            id="book1",
            title="Unknown Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"isbn": "0000000000"},
        )

        mock_search = {"docs": []}

        with patch(
            "src.enrichment.providers.openlibrary.openlibrary.requests.get"
        ) as mock_get:
            # ISBN lookup returns 404, then search returns empty
            mock_get.side_effect = [
                MagicMock(spec=requests.Response, status_code=404),  # ISBN lookup
                MagicMock(
                    spec=requests.Response, status_code=200, json=lambda: mock_search
                ),  # Search
            ]

            result = provider.enrich(item, {})

        assert result is not None
        assert result.match_quality == "not_found"


class TestOpenLibraryProviderSearch:
    """Tests for title/author search."""

    @pytest.fixture
    def provider(self) -> OpenLibraryProvider:
        """Create provider instance."""
        return OpenLibraryProvider()

    @pytest.fixture
    def book_item(self) -> ContentItem:
        """Create sample book item."""
        return ContentItem(
            id="book1",
            title="Pride and Prejudice",
            author="Jane Austen",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )

    def test_search_with_author(
        self, provider: OpenLibraryProvider, book_item: ContentItem
    ) -> None:
        """Test search with title and author."""
        mock_search = {
            "docs": [
                {
                    "key": "/works/OL1234W",
                    "title": "Pride and Prejudice",
                    "author_name": ["Jane Austen"],
                    "first_publish_year": 1813,
                    "subject": ["Romance", "Classic literature"],
                }
            ]
        }

        mock_work = {
            "key": "/works/OL1234W",
            "subjects": ["Romance", "Classic literature"],
            "description": "A classic romance novel.",
        }

        with patch(
            "src.enrichment.providers.openlibrary.openlibrary.requests.get"
        ) as mock_get:
            mock_get.side_effect = [
                MagicMock(
                    spec=requests.Response, status_code=200, json=lambda: mock_search
                ),
                MagicMock(
                    spec=requests.Response, status_code=200, json=lambda: mock_work
                ),
            ]

            result = provider.enrich(book_item, {})

        assert result is not None
        assert result.match_quality == "high"
        assert "Romance" in result.genres

    def test_search_fallback_to_title_only(self, provider: OpenLibraryProvider) -> None:
        """Test that search falls back to title-only when author search fails."""
        item = ContentItem(
            id="book1",
            title="Some Book",
            author="Unknown Author",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )

        # First search with author returns empty, second without author finds it
        mock_empty = {"docs": []}
        mock_found = {
            "docs": [
                {
                    "key": "/works/OL1234W",
                    "title": "Some Book",
                    "subject": ["Fiction"],
                }
            ]
        }
        mock_work = {
            "key": "/works/OL1234W",
            "subjects": ["Fiction"],
        }

        with patch(
            "src.enrichment.providers.openlibrary.openlibrary.requests.get"
        ) as mock_get:
            mock_get.side_effect = [
                MagicMock(
                    spec=requests.Response, status_code=200, json=lambda: mock_empty
                ),
                MagicMock(
                    spec=requests.Response, status_code=200, json=lambda: mock_found
                ),
                MagicMock(
                    spec=requests.Response, status_code=200, json=lambda: mock_work
                ),
            ]

            result = provider.enrich(item, {})

        assert result is not None
        assert result.genres == ["Fiction"]


class TestOpenLibraryProviderSubjectFiltering:
    """Tests for subject/genre filtering."""

    def test_filter_subjects_genre_keywords(self) -> None:
        """Test that genre keywords are kept."""
        provider = OpenLibraryProvider()
        subjects = [
            "Fiction",
            "Mystery",
            "Some very long subject that should be filtered out because it's too long",
            "Romance -- 20th century -- United States",  # Subdivided, skip
            "Thriller",
        ]

        filtered = provider._filter_subjects(subjects)

        assert "Fiction" in filtered
        assert "Mystery" in filtered
        assert "Thriller" in filtered
        assert len(filtered) <= 10

    def test_filter_subjects_deduplication(self) -> None:
        """Test that duplicate subjects are removed."""
        provider = OpenLibraryProvider()
        subjects = ["Fiction", "fiction", "FICTION", "Mystery"]

        filtered = provider._filter_subjects(subjects)

        # Should only have one "Fiction" variant
        fiction_count = sum(1 for s in filtered if s.lower() == "fiction")
        assert fiction_count == 1


class TestOpenLibraryProviderUnsupportedTypes:
    """Tests for handling unsupported content types."""

    def test_enrich_movie_returns_none(self) -> None:
        """Test that enriching a movie returns None."""
        provider = OpenLibraryProvider()
        item = ContentItem(
            id="movie1",
            title="Some Movie",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )

        result = provider.enrich(item, {})
        assert result is None


class TestSearchTitleCannotForgeALogLineRegression:
    """Reported: the search log writes ``item.title`` unescaped.

    A title restricts no characters and the file formatter is single-line, so
    a newline writes its own entry (CWE-117). Fix: ``log_search_title``.
    """

    _FORGED = "Real Book\nWARNING  | forged | line (Bobiverse, #2)"

    def test_a_newline_in_a_title_is_escaped_before_the_search_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Reached whenever cleaning changes the title, so every series."""
        provider = OpenLibraryProvider()
        item = ContentItem(
            id="book1",
            title=self._FORGED,
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )

        with (
            patch(
                "src.enrichment.providers.openlibrary.openlibrary.requests.get"
            ) as mock_get,
            caplog.at_level(
                logging.DEBUG,
                logger="src.enrichment.providers.openlibrary.openlibrary",
            ),
        ):
            mock_get.return_value.json.return_value = {"docs": []}
            assert provider._search_book(item).match_quality == "not_found"

        assert "Real Book\\nWARNING" in caplog.text
        assert self._FORGED not in caplog.text


class TestIsbnLookupRendersItsFailureThroughTheScrubberRegression:
    """Reported: ``docs/SECURITY.md`` points here for OpenLibrary's
    ``scrub_request_error`` claim; nothing held it. Root cause: the transport
    error above carries no status, so ``exception_for_log`` clears the same
    assertions. Fix: an ``HTTPError``, where the two renderers differ.
    """

    _URL = "https://openlibrary.org/isbn/9780441013593.json"

    def test_an_http_failure_reaches_the_log_as_a_status_alone(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        provider = OpenLibraryProvider()
        response = MagicMock(spec=requests.Response)
        response.status_code = 500

        with (
            patch(
                "src.enrichment.providers.openlibrary.openlibrary.requests.get"
            ) as mock_get,
            caplog.at_level(
                logging.WARNING,
                logger="src.enrichment.providers.openlibrary.openlibrary",
            ),
        ):
            mock_get.return_value.status_code = 200
            mock_get.return_value.raise_for_status.side_effect = requests.HTTPError(
                f"500 Server Error for url: {self._URL}", response=response
            )
            assert provider._lookup_by_isbn("978-0-441-01359-3") is None

        assert "HTTP 500" in caplog.text
        assert self._URL not in caplog.text
        assert "Server Error" not in caplog.text
