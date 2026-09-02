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
    def test_removes_series_with_comma(self) -> None:
        assert (
            clean_title_for_search("For We Are Many (Bobiverse, #2)")
            == "For We Are Many"
        )

    def test_handles_parentheses_without_series_number(self) -> None:
        assert (
            clean_title_for_search("The Stand (Uncut Edition)")
            == "The Stand (Uncut Edition)"
        )


class TestOpenLibraryProviderISBNLookup:
    @pytest.fixture
    def provider(self) -> OpenLibraryProvider:
        return OpenLibraryProvider()

    def test_isbn_lookup_success(self, provider: OpenLibraryProvider) -> None:
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
            "covers": [8231856, -1],
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
        assert result.cover_url == "https://covers.openlibrary.org/b/id/8231856-L.jpg"

    def test_isbn_not_found(self, provider: OpenLibraryProvider) -> None:
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
            mock_get.side_effect = [
                MagicMock(spec=requests.Response, status_code=404),
                MagicMock(
                    spec=requests.Response, status_code=200, json=lambda: mock_search
                ),
            ]

            result = provider.enrich(item, {})

        assert result is not None
        assert result.match_quality == "not_found"


class TestOpenLibraryProviderSearch:
    @pytest.fixture
    def provider(self) -> OpenLibraryProvider:
        return OpenLibraryProvider()

    @pytest.fixture
    def book_item(self) -> ContentItem:
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
            "covers": [-1],
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
        assert result.cover_url is None

    @pytest.mark.parametrize(
        ("cover_i", "expected"),
        [(8231856, "https://covers.openlibrary.org/b/id/8231856-L.jpg"), (-1, None)],
    )
    def test_a_doc_with_no_work_key_takes_its_cover_from_cover_i(
        self,
        provider: OpenLibraryProvider,
        book_item: ContentItem,
        cover_i: int,
        expected: str | None,
    ) -> None:
        mock_search = {"docs": [{"title": "Pride and Prejudice", "cover_i": cover_i}]}

        with patch(
            "src.enrichment.providers.openlibrary.openlibrary.requests.get"
        ) as mock_get:
            mock_get.return_value = MagicMock(
                spec=requests.Response, status_code=200, json=lambda: mock_search
            )

            result = provider.enrich(book_item, {})

        assert result is not None
        assert result.cover_url == expected

    def test_search_fallback_to_title_only(self, provider: OpenLibraryProvider) -> None:
        item = ContentItem(
            id="book1",
            title="Some Book",
            author="Unknown Author",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )

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
    def test_filter_subjects_genre_keywords(self) -> None:
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
        provider = OpenLibraryProvider()
        subjects = ["Fiction", "fiction", "FICTION", "Mystery"]

        filtered = provider._filter_subjects(subjects)

        fiction_count = sum(1 for s in filtered if s.lower() == "fiction")
        assert fiction_count == 1


class TestOpenLibraryProviderUnsupportedTypes:
    def test_enrich_movie_returns_none(self) -> None:
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
