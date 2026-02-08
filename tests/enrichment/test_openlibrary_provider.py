"""Tests for the OpenLibrary enrichment provider."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from src.enrichment.provider_base import ProviderError
from src.enrichment.providers.openlibrary import OpenLibraryProvider, clean_title_for_search
from src.models.content import ConsumptionStatus, ContentItem, ContentType


class TestCleanTitleForSearch:
    """Tests for title cleaning before search."""

    def test_removes_series_with_comma(self) -> None:
        """Test removal of series info with comma format."""
        assert clean_title_for_search("For We Are Many (Bobiverse, #2)") == "For We Are Many"

    def test_removes_series_without_comma(self) -> None:
        """Test removal of series info without comma."""
        assert clean_title_for_search("The Name of the Wind (The Kingkiller Chronicle #1)") == "The Name of the Wind"

    def test_preserves_title_without_series(self) -> None:
        """Test that titles without series info are unchanged."""
        assert clean_title_for_search("Project Hail Mary") == "Project Hail Mary"

    def test_preserves_simple_titles(self) -> None:
        """Test that simple titles are unchanged."""
        assert clean_title_for_search("1984") == "1984"

    def test_handles_parentheses_without_series_number(self) -> None:
        """Test that parentheses without series numbers are preserved."""
        assert clean_title_for_search("The Stand (Uncut Edition)") == "The Stand (Uncut Edition)"

    def test_removes_series_with_different_formats(self) -> None:
        """Test removal of various series formats."""
        assert clean_title_for_search("Ready Player One (Ready Player One #1)") == "Ready Player One"
        assert clean_title_for_search("Dune (Dune Chronicles, #1)") == "Dune"


class TestOpenLibraryProviderProperties:
    """Tests for OpenLibrary provider properties."""

    def test_name(self) -> None:
        """Test provider name."""
        provider = OpenLibraryProvider()
        assert provider.name == "openlibrary"

    def test_display_name(self) -> None:
        """Test display name."""
        provider = OpenLibraryProvider()
        assert provider.display_name == "Open Library"

    def test_content_types(self) -> None:
        """Test supported content types."""
        provider = OpenLibraryProvider()
        assert provider.content_types == [ContentType.BOOK]
        assert ContentType.MOVIE not in provider.content_types

    def test_requires_api_key(self) -> None:
        """Test that API key is NOT required."""
        provider = OpenLibraryProvider()
        assert provider.requires_api_key is False

    def test_rate_limit(self) -> None:
        """Test rate limit setting."""
        provider = OpenLibraryProvider()
        assert provider.rate_limit_requests_per_second == 1.0

    def test_validate_config(self) -> None:
        """Test config validation (no required fields)."""
        provider = OpenLibraryProvider()
        errors = provider.validate_config({})
        assert errors == []


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

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                MagicMock(status_code=200, json=lambda: mock_edition),
                MagicMock(status_code=200, json=lambda: mock_work),
            ]

            result = provider.enrich(item, {})

        assert result is not None
        assert result.external_id == "openlibrary:OL5678W"
        assert "Dystopia" in result.genres
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

        with patch("requests.get") as mock_get:
            # ISBN lookup returns 404, then search returns empty
            mock_get.side_effect = [
                MagicMock(status_code=404),  # ISBN lookup
                MagicMock(status_code=200, json=lambda: mock_search),  # Search
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

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                MagicMock(status_code=200, json=lambda: mock_search),
                MagicMock(status_code=200, json=lambda: mock_work),
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

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                MagicMock(status_code=200, json=lambda: mock_empty),
                MagicMock(status_code=200, json=lambda: mock_found),
                MagicMock(status_code=200, json=lambda: mock_work),
            ]

            result = provider.enrich(item, {})

        assert result is not None
        assert result.genres == ["Fiction"]

    def test_search_api_error(
        self, provider: OpenLibraryProvider, book_item: ContentItem
    ) -> None:
        """Test that API errors raise ProviderError."""
        with patch("requests.get") as mock_get:
            mock_get.side_effect = requests.RequestException("Connection failed")

            with pytest.raises(ProviderError) as exc_info:
                provider.enrich(book_item, {})

            assert "Failed to search Open Library" in str(exc_info.value)


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

    def test_filter_subjects_limit(self) -> None:
        """Test that subjects are limited to 10."""
        provider = OpenLibraryProvider()
        subjects = [f"Fiction{i}" for i in range(50)]

        filtered = provider._filter_subjects(subjects)

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

    def test_enrich_video_game_returns_none(self) -> None:
        """Test that enriching a video game returns None."""
        provider = OpenLibraryProvider()
        item = ContentItem(
            id="game1",
            title="Some Game",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
        )

        result = provider.enrich(item, {})
        assert result is None
