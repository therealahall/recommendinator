import logging
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.enrichment.provider_base import ProviderError
from src.enrichment.providers.openlibrary.openlibrary import (
    OpenLibraryProvider,
    clean_title_for_search,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.utils.matching import Candidate


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
        assert "Dystopia" in result.genres
        assert result.tags is not None
        assert "Dystopia" in result.tags
        assert "dystopian" in result.description.lower()
        assert result.match_quality == "high"
        assert result.cover_url == "https://covers.openlibrary.org/b/id/8231856-L.jpg"
        assert result.extra_metadata["pages"] == 328

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


class TestOpenLibraryCandidateFromUrl:
    _BOOK = ContentItem(
        id="book1",
        title="The Norse Myths",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )

    _WORK = {
        "key": "/works/OL1955041W",
        "title": "The Norse Myths",
        "authors": [{"author": {"key": "/authors/OL23919A"}}],
        "covers": [8231856],
        "first_publish_date": "1980",
    }
    _AUTHOR = {"name": "Kevin Crossley-Holland"}

    @pytest.fixture
    def provider(self) -> OpenLibraryProvider:
        return OpenLibraryProvider()

    def _work_reply(self) -> MagicMock:
        return MagicMock(
            spec=requests.Response, status_code=200, json=lambda: self._WORK
        )

    def _work_then_author(self) -> list[MagicMock]:
        return [
            self._work_reply(),
            MagicMock(
                spec=requests.Response, status_code=200, json=lambda: self._AUTHOR
            ),
        ]

    def test_a_work_link_is_labelled_from_the_record_not_the_search_index(
        self, provider: OpenLibraryProvider
    ) -> None:
        with patch(
            "src.enrichment.providers.openlibrary.openlibrary.requests.get"
        ) as mock_get:
            mock_get.side_effect = self._work_then_author()

            candidate = provider.candidate_from_url(
                self._BOOK,
                "https://openlibrary.org/works/OL1955041W/The_Norse_Myths"
                "?edition=key%3A/books/OL14949379M",
                {},
            )

        assert candidate is not None
        assert candidate.record_id == "OL1955041W"
        assert candidate.title == "The Norse Myths"
        # A pasted row sits in the same column as a searched one, which carries
        # the year off the search index.
        assert candidate.year == 1980
        assert candidate.creator == "Kevin Crossley-Holland"
        assert (
            candidate.cover_url == "https://covers.openlibrary.org/b/id/8231856-L.jpg"
        )
        # Offering a record no pin could name would refuse the row the picker
        # just showed, so the two gates are asserted against each other.
        assert provider.accepts_record_id(candidate.record_id) is True
        assert [call.args[0] for call in mock_get.call_args_list] == [
            "https://openlibrary.org/works/OL1955041W.json",
            "https://openlibrary.org/authors/OL23919A.json",
        ]

    @pytest.mark.parametrize(
        ("url", "endpoint"),
        [
            (
                "https://openlibrary.org/books/OL14949379M",
                "https://openlibrary.org/books/OL14949379M.json",
            ),
            (
                "https://openlibrary.org/isbn/9780140447552",
                "https://openlibrary.org/isbn/9780140447552.json",
            ),
        ],
        ids=["an-edition-link", "an-isbn-link"],
    )
    def test_an_edition_link_resolves_to_the_work_the_edition_belongs_to(
        self, provider: OpenLibraryProvider, url: str, endpoint: str
    ) -> None:
        edition = {"works": [{"key": "/works/OL1955041W"}]}

        with patch(
            "src.enrichment.providers.openlibrary.openlibrary.requests.get"
        ) as mock_get:
            mock_get.side_effect = [
                MagicMock(
                    spec=requests.Response, status_code=200, json=lambda: edition
                ),
                *self._work_then_author(),
            ]

            candidate = provider.candidate_from_url(self._BOOK, url, {})

        assert candidate is not None
        assert candidate.record_id == "OL1955041W"
        assert candidate.title == "The Norse Myths"
        assert mock_get.call_args_list[0].args[0] == endpoint

    @pytest.mark.parametrize(
        "author_reply",
        [
            requests.ConnectionError("no route"),
            MagicMock(
                spec=requests.Response,
                status_code=200,
                json=MagicMock(
                    side_effect=requests.exceptions.JSONDecodeError("nope", "<html>", 0)
                ),
            ),
            MagicMock(
                spec=requests.Response,
                status_code=302,
                headers={"Location": "https://evil.test/authors/OL23919A.json"},
            ),
        ],
        ids=[
            "an-author-lookup-that-fails",
            "an-author-reply-that-is-not-json",
            "an-author-lookup-redirected-off-origin",
        ],
    )
    def test_an_author_the_work_record_cannot_name_still_offers_the_work(
        self, provider: OpenLibraryProvider, author_reply: MagicMock | Exception
    ) -> None:
        with patch(
            "src.enrichment.providers.openlibrary.openlibrary.requests.get"
        ) as mock_get:
            mock_get.side_effect = [self._work_reply(), author_reply]

            candidate = provider.candidate_from_url(
                self._BOOK, "https://openlibrary.org/works/OL1955041W", {}
            )

        assert candidate == Candidate(
            record_id="OL1955041W",
            title="The Norse Myths",
            year=1980,
            cover_url="https://covers.openlibrary.org/b/id/8231856-L.jpg",
        )

    def test_a_work_naming_no_author_is_offered_without_an_author_request(
        self, provider: OpenLibraryProvider
    ) -> None:
        work = {"key": "/works/OL1955041W", "title": "Beowulf"}

        with patch(
            "src.enrichment.providers.openlibrary.openlibrary.requests.get"
        ) as mock_get:
            mock_get.return_value = MagicMock(
                spec=requests.Response, status_code=200, json=lambda: work
            )

            candidate = provider.candidate_from_url(
                self._BOOK, "https://openlibrary.org/works/OL1955041W", {}
            )

        assert candidate == Candidate(record_id="OL1955041W", title="Beowulf")
        assert mock_get.call_count == 1

    def test_a_record_naming_another_work_cannot_replace_the_key_the_link_named(
        self, provider: OpenLibraryProvider
    ) -> None:
        work = {"key": "/works/OL99W", "title": "Another Book"}

        with patch(
            "src.enrichment.providers.openlibrary.openlibrary.requests.get"
        ) as mock_get:
            mock_get.return_value = MagicMock(
                spec=requests.Response, status_code=200, json=lambda: work
            )

            candidate = provider.candidate_from_url(
                self._BOOK, "https://openlibrary.org/works/OL1955041W", {}
            )

        assert candidate is not None
        assert candidate.record_id == "OL1955041W"

    @pytest.mark.parametrize(
        "url",
        [
            "https://openlibrary.evil.test/works/OL1955041W",
            "https://openlibrary.org/works/../search.json",
            "https://openlibrary.org/authors/OL23919A",
            "https://openlibrary.org/works",
        ],
        ids=[
            "a-non-openlibrary-host-openlibrary-is-only-a-prefix-of",
            "a-key-that-would-leave-the-works-path",
            "a-record-kind-no-pin-names",
            "a-link-naming-no-record",
        ],
    )
    def test_a_link_this_provider_cannot_read_is_left_for_another_to_claim(
        self, provider: OpenLibraryProvider, url: str
    ) -> None:
        assert provider.claims_url(url) is False

    @pytest.mark.parametrize(
        "url",
        [
            "https://openlibrary.org/books/OL14949379M",
            "https://openlibrary.org/works/OL1955041W",
        ],
        ids=["an-edition-link", "a-work-link"],
    )
    def test_a_record_open_library_does_not_hold_offers_no_candidate(
        self, provider: OpenLibraryProvider, url: str
    ) -> None:
        with patch(
            "src.enrichment.providers.openlibrary.openlibrary.requests.get"
        ) as mock_get:
            mock_get.return_value = MagicMock(spec=requests.Response, status_code=404)

            candidate = provider.candidate_from_url(self._BOOK, url, {})

        assert candidate is None

    def test_a_failed_lookup_is_raised_rather_than_read_as_a_record_that_is_absent(
        self, provider: OpenLibraryProvider
    ) -> None:
        with patch(
            "src.enrichment.providers.openlibrary.openlibrary.requests.get",
            side_effect=requests.ConnectionError("no route"),
        ):
            with pytest.raises(ProviderError):
                provider.candidate_from_url(
                    self._BOOK, "https://openlibrary.org/works/OL1955041W", {}
                )


class TestOpenLibraryStaysOnItsOwnOrigin:
    def test_a_redirect_off_openlibrary_is_refused_rather_than_followed(self) -> None:
        item = ContentItem(
            id="book1",
            title="1984",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )

        with patch(
            "src.enrichment.providers.openlibrary.openlibrary.requests.get"
        ) as mock_get:
            mock_get.return_value = MagicMock(
                spec=requests.Response,
                status_code=302,
                headers={"Location": "https://evil.test/search.json"},
            )

            with pytest.raises(ProviderError) as refused:
                OpenLibraryProvider().enrich(item, {})

        assert "evil.test" in str(refused.value)
        assert mock_get.call_count == 1


class TestOpenLibraryProviderSubjectFiltering:
    def test_filter_subjects_genre_keywords(self) -> None:
        provider = OpenLibraryProvider()
        subjects = [
            "Fiction",
            "Mystery",
            "Some very long subject that should be filtered out because it's too long",
            "Romance -- 20th century -- United States",
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


class TestAPinCannotLeaveTheWorksPath:
    @pytest.mark.parametrize(
        "record_id", ["../../search", "OL1W/../x", "/works/OL1W", "OL1W\n"]
    )
    def test_a_key_carrying_more_than_a_work_id_is_refused(
        self, record_id: str
    ) -> None:
        assert OpenLibraryProvider().accepts_record_id(record_id) is False

    def test_a_bare_work_key_is_accepted(self) -> None:
        assert OpenLibraryProvider().accepts_record_id("OL1234W") is True

    def test_a_stored_pin_the_gate_would_refuse_is_searched_past(self) -> None:
        """The gate tightened after pins were already stored, so it cannot reach
        one the database already holds."""
        item = ContentItem(
            id="book1",
            title="1984",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"enrichment_ids": {"openlibrary": "../../search"}},
        )

        with patch(
            "src.enrichment.providers.openlibrary.openlibrary.requests.get"
        ) as mock_get:
            mock_get.return_value = MagicMock(
                spec=requests.Response, status_code=200, json=lambda: {"docs": []}
            )

            OpenLibraryProvider().enrich(item, {})

        requested = mock_get.call_args_list[0].args[0]
        assert requested == "https://openlibrary.org/search.json"


class TestAnImportedIsbnCannotLeaveTheIsbnPath:
    def test_an_isbn_column_holding_a_path_is_searched_past_rather_than_dialled(
        self,
    ) -> None:
        """An exported catalogue's `isbn` column carries whatever the user typed,
        and it is spliced into `/isbn/<value>.json`: `../search` reads the search
        endpoint, whose payload then fills pages and dates onto the book."""
        item = ContentItem(
            id="book1",
            title="1984",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"isbn": "../search"},
        )

        with patch(
            "src.enrichment.providers.openlibrary.openlibrary.requests.get"
        ) as mock_get:
            mock_get.return_value = MagicMock(
                spec=requests.Response, status_code=200, json=lambda: {"docs": []}
            )

            OpenLibraryProvider().enrich(item, {})

        requested = mock_get.call_args_list[0].args[0]
        assert requested == "https://openlibrary.org/search.json"


class TestAServerSuppliedWorkKeyCannotRedirectTheRequest:
    _HOSTILE = "@evil.example/x"

    def test_an_isbn_edition_naming_another_host_is_read_from_the_edition(self) -> None:
        item = ContentItem(
            id="book1",
            title="1984",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            metadata={"isbn13": "9780451524935"},
        )
        edition = {"works": [{"key": self._HOSTILE}], "number_of_pages": 328}

        with patch(
            "src.enrichment.providers.openlibrary.openlibrary.requests.get"
        ) as mock_get:
            mock_get.return_value = MagicMock(
                spec=requests.Response, status_code=200, json=lambda: edition
            )

            result = OpenLibraryProvider().enrich(item, {})

        assert mock_get.call_count == 1
        assert result is not None
        assert result.extra_metadata["pages"] == 328

    def test_a_search_doc_naming_another_host_is_read_from_the_doc(self) -> None:
        item = ContentItem(
            id="book1",
            title="1984",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        docs = {"docs": [{"key": self._HOSTILE, "first_publish_year": 1949}]}

        with patch(
            "src.enrichment.providers.openlibrary.openlibrary.requests.get"
        ) as mock_get:
            mock_get.return_value = MagicMock(
                spec=requests.Response, status_code=200, json=lambda: docs
            )

            result = OpenLibraryProvider().enrich(item, {})

        assert mock_get.call_count == 1
        assert result is not None
        assert result.extra_metadata["year_published"] == 1949
