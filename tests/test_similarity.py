"""Tests for SimilarityMatcher vector similarity matching."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from src.llm.embeddings import EmbeddingGenerator
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.recommendations.similarity import SimilarityMatcher
from src.storage.manager import StorageManager
from src.storage.vector_db import VectorDB
from tests.factories import make_item


@pytest.fixture
def mock_storage() -> Mock:
    """Create a mock storage manager."""
    storage = Mock(spec=StorageManager)
    storage.vector_db = Mock(spec=VectorDB)
    storage.vector_db.has_embedding = Mock(return_value=False)
    storage.vector_db.get_embedding = Mock(return_value=None)
    return storage


@pytest.fixture
def real_storage(tmp_path: Path) -> StorageManager:
    """Create a real SQLite-backed storage manager with a stubbed vector DB.

    Resolving vector hits back to library items is a SQLite concern, so the
    library is real while the embeddings are stubbed.
    """
    storage = StorageManager(tmp_path / "similarity.db", ai_enabled=False)
    storage.vector_db = Mock(spec=VectorDB)
    storage.vector_db.has_embedding = Mock(return_value=False)
    storage.vector_db.get_embedding = Mock(return_value=None)
    return storage


@pytest.fixture
def mock_embedding_gen() -> Mock:
    """Create a mock embedding generator."""
    gen = Mock(spec=EmbeddingGenerator)
    gen.generate_content_embedding = Mock(return_value=[0.1, 0.2, 0.3])
    return gen


def _stub_lookup(storage: Mock, items: list[ContentItem]) -> None:
    """Resolve embedding keys against *items*, as real storage would.

    Keys naming none of *items* are absent from the result, so a test can
    exercise a hit that resolves to nothing.
    """
    by_key = {item.id: item for item in items if item.id}
    storage.get_items_by_embedding_keys.side_effect = lambda keys, **kwargs: {
        key: by_key[key] for key in keys if key in by_key
    }


@pytest.fixture
def matcher(mock_storage: Mock, mock_embedding_gen: Mock) -> SimilarityMatcher:
    """Create a SimilarityMatcher with mocked dependencies."""
    return SimilarityMatcher(
        storage_manager=mock_storage,
        embedding_generator=mock_embedding_gen,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestFindSimilarHappyPath:
    """Tests for the normal success flow of find_similar."""

    def test_returns_items_sorted_by_score(
        self, matcher: SimilarityMatcher, mock_storage: Mock
    ) -> None:
        """find_similar returns (ContentItem, score) tuples sorted descending."""
        ref = make_item(item_id="ref1", title="Reference Book")

        stored_items = [
            make_item(item_id="a", title="Book A"),
            make_item(item_id="b", title="Book B"),
        ]

        mock_storage.search_similar.return_value = [
            {"content_id": "a", "score": 0.7},
            {"content_id": "b", "score": 0.9},
        ]
        _stub_lookup(mock_storage, stored_items)

        results = matcher.find_similar([ref], content_type=ContentType.BOOK)

        assert len(results) == 2
        # Sorted descending by score
        assert results[0][1] == 0.9
        assert results[0][0].id == "b"
        assert results[1][1] == 0.7
        assert results[1][0].id == "a"

    def test_uses_cached_embedding_when_available(
        self, matcher: SimilarityMatcher, mock_storage: Mock, mock_embedding_gen: Mock
    ) -> None:
        """find_similar uses cached embeddings from vector DB when present."""
        ref = make_item(item_id="ref1", title="Reference Book")

        mock_storage.vector_db.has_embedding.return_value = True
        mock_storage.vector_db.get_embedding.return_value = [0.5, 0.6, 0.7]
        mock_storage.search_similar.return_value = []

        matcher.find_similar([ref])

        mock_storage.vector_db.has_embedding.assert_called_once_with("ref1")
        mock_storage.vector_db.get_embedding.assert_called_once_with("ref1")
        mock_embedding_gen.generate_content_embedding.assert_not_called()

    def test_generates_embedding_when_not_cached(
        self, matcher: SimilarityMatcher, mock_storage: Mock, mock_embedding_gen: Mock
    ) -> None:
        """find_similar generates a new embedding when not found in vector DB."""
        ref = make_item(item_id="ref1", title="Reference Book")

        mock_storage.vector_db.has_embedding.return_value = False
        mock_storage.search_similar.return_value = []

        matcher.find_similar([ref])

        mock_embedding_gen.generate_content_embedding.assert_called_once_with(ref)
        mock_storage.save_content_item.assert_called_once()

    def test_averages_multiple_reference_embeddings(
        self, matcher: SimilarityMatcher, mock_storage: Mock, mock_embedding_gen: Mock
    ) -> None:
        """find_similar averages embeddings from multiple reference items."""
        refs = [
            make_item(item_id="ref1", title="Book One"),
            make_item(item_id="ref2", title="Book Two"),
        ]

        mock_embedding_gen.generate_content_embedding.side_effect = [
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
        ]
        mock_storage.search_similar.return_value = []

        matcher.find_similar(refs)

        # Verify search_similar was called with the averaged embedding
        call_args = mock_storage.search_similar.call_args
        query = call_args.kwargs["query_embedding"]
        assert abs(query[0] - 0.5) < 1e-6
        assert abs(query[1] - 0.5) < 1e-6
        assert abs(query[2] - 0.0) < 1e-6

    def test_passes_limit_and_content_type(
        self, matcher: SimilarityMatcher, mock_storage: Mock
    ) -> None:
        """find_similar forwards limit and content_type to search_similar."""
        ref = make_item(item_id="ref1", title="Reference Book")

        mock_storage.search_similar.return_value = []

        matcher.find_similar([ref], content_type=ContentType.MOVIE, limit=10)

        call_kwargs = mock_storage.search_similar.call_args.kwargs
        assert call_kwargs["n_results"] == 10
        assert call_kwargs["content_type"] == ContentType.MOVIE

    def test_passes_user_id_to_lookup(
        self, matcher: SimilarityMatcher, mock_storage: Mock
    ) -> None:
        """find_similar passes user_id when resolving the search hits."""
        ref = make_item(item_id="ref1", title="Reference Book")

        mock_storage.search_similar.return_value = [
            {"content_id": "a", "score": 0.8},
        ]
        _stub_lookup(mock_storage, [make_item(item_id="a", title="Book A")])

        matcher.find_similar([ref], user_id=42)

        call_kwargs = mock_storage.get_items_by_embedding_keys.call_args.kwargs
        assert call_kwargs["user_id"] == 42

    def test_resolves_only_the_keys_the_search_returned(
        self, matcher: SimilarityMatcher, mock_storage: Mock
    ) -> None:
        """The lookup is asked for the hit keys, never for the whole library."""
        ref = make_item(item_id="ref1", title="Reference Book")

        mock_storage.search_similar.return_value = [
            {"content_id": "a", "score": 0.8},
            {"content_id": "b", "score": 0.7},
        ]
        _stub_lookup(mock_storage, [make_item(item_id="a", title="Book A")])

        matcher.find_similar([ref])

        assert mock_storage.get_items_by_embedding_keys.call_args.args[0] == ["a", "b"]
        mock_storage.get_content_items.assert_not_called()


# ---------------------------------------------------------------------------
# Empty references
# ---------------------------------------------------------------------------


class TestFindSimilarEmptyRefs:
    """Tests for empty reference item lists."""

    def test_returns_empty_list_for_empty_references(
        self, matcher: SimilarityMatcher
    ) -> None:
        """find_similar returns [] immediately when reference_items is empty."""
        results = matcher.find_similar([])
        assert results == []

    def test_does_not_call_storage_for_empty_references(
        self, matcher: SimilarityMatcher, mock_storage: Mock
    ) -> None:
        """find_similar does not call storage methods when no references given."""
        matcher.find_similar([])
        mock_storage.search_similar.assert_not_called()
        mock_storage.get_items_by_embedding_keys.assert_not_called()


# ---------------------------------------------------------------------------
# Missing embeddings
# ---------------------------------------------------------------------------


class TestFindSimilarMissingEmbeddings:
    """Tests for missing or failed embedding scenarios."""

    def test_returns_empty_when_all_embeddings_fail(
        self, matcher: SimilarityMatcher, mock_embedding_gen: Mock
    ) -> None:
        """find_similar returns [] when no embeddings can be generated."""
        ref = make_item(item_id="ref1", title="Reference Book")

        mock_embedding_gen.generate_content_embedding.side_effect = RuntimeError(
            "Embedding failed"
        )

        results = matcher.find_similar([ref])
        assert results == []

    def test_returns_empty_when_cached_embedding_is_none(
        self, matcher: SimilarityMatcher, mock_storage: Mock, mock_embedding_gen: Mock
    ) -> None:
        """find_similar falls back to generation when cached embedding returns None."""
        ref = make_item(item_id="ref1", title="Reference Book")

        mock_storage.vector_db.has_embedding.return_value = True
        mock_storage.vector_db.get_embedding.return_value = None
        # Generation also fails
        mock_embedding_gen.generate_content_embedding.side_effect = RuntimeError(
            "Failed"
        )

        results = matcher.find_similar([ref])
        assert results == []

    def test_skips_item_with_no_id(
        self, matcher: SimilarityMatcher, mock_storage: Mock, mock_embedding_gen: Mock
    ) -> None:
        """find_similar handles item with None id by generating embedding without caching."""
        ref = ContentItem(
            id=None,
            title="No ID Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )

        mock_storage.search_similar.return_value = []

        matcher.find_similar([ref])

        # Should generate embedding but not save (no content_id)
        mock_embedding_gen.generate_content_embedding.assert_called_once_with(ref)
        mock_storage.save_content_item.assert_not_called()

    def test_handles_vector_db_none(self, mock_embedding_gen: Mock) -> None:
        """find_similar generates embeddings when vector_db is None."""
        storage = Mock(spec=StorageManager)
        storage.vector_db = None
        storage.search_similar.return_value = []

        matcher = SimilarityMatcher(storage, mock_embedding_gen)
        ref = make_item(item_id="ref1", title="Reference Book")

        matcher.find_similar([ref])

        mock_embedding_gen.generate_content_embedding.assert_called_once_with(ref)


# ---------------------------------------------------------------------------
# Exclude IDs
# ---------------------------------------------------------------------------


class TestFindSimilarExcludeIds:
    """Tests for the exclude_ids filtering."""

    def test_excludes_specified_ids(
        self, matcher: SimilarityMatcher, mock_storage: Mock
    ) -> None:
        """find_similar filters out items whose content_id is in exclude_ids."""
        ref = make_item(item_id="ref1", title="Reference Book")

        mock_storage.search_similar.return_value = [
            {"content_id": "a", "score": 0.9},
            {"content_id": "b", "score": 0.8},
            {"content_id": "c", "score": 0.7},
        ]
        _stub_lookup(
            mock_storage,
            [
                make_item(item_id="a", title="Book A"),
                make_item(item_id="b", title="Book B"),
                make_item(item_id="c", title="Book C"),
            ],
        )

        results = matcher.find_similar([ref], exclude_ids=["a", "c"])

        assert len(results) == 1
        assert results[0][0].id == "b"

    def test_exclude_ids_none_returns_all(
        self, matcher: SimilarityMatcher, mock_storage: Mock
    ) -> None:
        """find_similar returns all results when exclude_ids is None."""
        ref = make_item(item_id="ref1", title="Reference Book")

        mock_storage.search_similar.return_value = [
            {"content_id": "a", "score": 0.9},
            {"content_id": "b", "score": 0.8},
        ]
        _stub_lookup(
            mock_storage,
            [
                make_item(item_id="a", title="Book A"),
                make_item(item_id="b", title="Book B"),
            ],
        )

        results = matcher.find_similar([ref], exclude_ids=None)

        assert len(results) == 2


# ---------------------------------------------------------------------------
# Exception handling
# ---------------------------------------------------------------------------


class TestFindSimilarExceptionHandling:
    """Tests for exception handling during similarity search."""

    def test_returns_empty_on_search_failure(
        self, matcher: SimilarityMatcher, mock_storage: Mock
    ) -> None:
        """find_similar returns [] when search_similar raises an exception."""
        ref = make_item(item_id="ref1", title="Reference Book")

        mock_storage.search_similar.side_effect = RuntimeError("DB connection lost")

        results = matcher.find_similar([ref])
        assert results == []

    def test_partial_embedding_failure_continues(
        self, matcher: SimilarityMatcher, mock_storage: Mock, mock_embedding_gen: Mock
    ) -> None:
        """find_similar continues with successful embeddings when some fail."""
        refs = [
            make_item(item_id="ref1", title="Book One"),
            make_item(item_id="ref2", title="Book Two"),
        ]

        # First item fails, second succeeds
        mock_embedding_gen.generate_content_embedding.side_effect = [
            RuntimeError("Embedding failed"),
            [0.1, 0.2, 0.3],
        ]
        mock_storage.search_similar.return_value = []

        results = matcher.find_similar(refs)

        # Should not error out — uses the one successful embedding
        assert results == []
        mock_storage.search_similar.assert_called_once()

    def test_skips_results_with_no_content_id(
        self, matcher: SimilarityMatcher, mock_storage: Mock
    ) -> None:
        """find_similar skips search results that have no content_id."""
        ref = make_item(item_id="ref1", title="Reference Book")

        mock_storage.search_similar.return_value = [
            {"content_id": None, "score": 0.9},
            {"content_id": "a", "score": 0.8},
        ]
        _stub_lookup(mock_storage, [make_item(item_id="a", title="Book A")])

        results = matcher.find_similar([ref])

        assert len(results) == 1
        assert results[0][0].id == "a"

    def test_skips_results_with_no_matching_item(
        self, matcher: SimilarityMatcher, mock_storage: Mock
    ) -> None:
        """find_similar skips results whose content_id has no matching item in storage."""
        ref = make_item(item_id="ref1", title="Reference Book")

        mock_storage.search_similar.return_value = [
            {"content_id": "missing", "score": 0.9},
            {"content_id": "a", "score": 0.8},
        ]
        _stub_lookup(mock_storage, [make_item(item_id="a", title="Book A")])

        results = matcher.find_similar([ref])

        assert len(results) == 1
        assert results[0][0].id == "a"

    def test_returns_empty_when_search_returns_empty(
        self, matcher: SimilarityMatcher, mock_storage: Mock
    ) -> None:
        """find_similar returns [] when search_similar returns an empty list."""
        ref = make_item(item_id="ref1", title="Reference Book")

        mock_storage.search_similar.return_value = []

        results = matcher.find_similar([ref])
        assert results == []


class TestFindSimilarIgnoredRegression:
    """Ignored items must not be resolved from the similarity hits.

    Bug (issue #99): find_similar resolved its hits at the default
    ``include_ignored=True``, so an ignored item returned by the vector search
    could be surfaced as a similar candidate feeding recommendation scoring.

    Root cause: find_similar did not expose or thread an ``include_ignored``
    flag to its lookup.

    Fix: find_similar accepts ``include_ignored`` and passes it to the lookup;
    recommendation callers pass False.
    """

    def test_threads_include_ignored_to_lookup_fetch_regression(
        self, matcher: SimilarityMatcher, mock_storage: Mock
    ) -> None:
        """include_ignored is forwarded to the embedding-key lookup."""
        ref = make_item(item_id="ref1", title="Reference Book")
        mock_storage.search_similar.return_value = [{"content_id": "a", "score": 0.9}]
        _stub_lookup(mock_storage, [make_item(item_id="a", title="Book A")])

        matcher.find_similar([ref], include_ignored=False)

        call_kwargs = mock_storage.get_items_by_embedding_keys.call_args.kwargs
        assert call_kwargs["include_ignored"] is False

    def test_defaults_to_including_ignored(
        self, matcher: SimilarityMatcher, mock_storage: Mock
    ) -> None:
        """The lookup defaults to include_ignored=True for other callers."""
        ref = make_item(item_id="ref1", title="Reference Book")
        mock_storage.search_similar.return_value = [{"content_id": "a", "score": 0.9}]
        _stub_lookup(mock_storage, [make_item(item_id="a", title="Book A")])

        matcher.find_similar([ref])

        call_kwargs = mock_storage.get_items_by_embedding_keys.call_args.kwargs
        assert call_kwargs["include_ignored"] is True


def _save_book(
    storage: StorageManager,
    title: str,
    *,
    item_id: str | None,
    status: ConsumptionStatus = ConsumptionStatus.UNREAD,
) -> int:
    """Save one book to *storage* and return its database ID."""
    return storage.save_content_item(
        ContentItem(
            id=item_id,
            title=title,
            content_type=ContentType.BOOK,
            status=status,
        )
    )


class TestLookupBeyondTheCap:
    """Bug reported: semantic similarity quietly stopped working as a library grew.

    Bug reported: on a large library most unread items scored 0.0 for semantic
    similarity while alphabetically early ones still scored, with no error and
    no log line.
    Root cause: ``find_similar`` resolved vector hits through a lookup table
    built by a single ``get_content_items`` call capped at 500 rows and sorted
    by title, so a hit outside the alphabetically first 500 items of that
    content type was dropped, and already consumed items took slots.
    Fix: hits are resolved by fetching exactly the keys the vector search
    returned, so the lookup no longer depends on library size or ordering.
    """

    def test_hit_outside_alphabetical_window_is_resolved_regression(
        self, real_storage: StorageManager, mock_embedding_gen: Mock
    ) -> None:
        """A hit on the alphabetically last of 600 books still resolves."""
        for index in range(600):
            _save_book(real_storage, f"Book {index:03d}", item_id=f"book-{index:03d}")
        reference = ContentItem(
            id="reference",
            title="Reference Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        real_storage.vector_db.search_similar.return_value = [
            {"content_id": "book-599", "score": 0.95}
        ]

        matcher = SimilarityMatcher(real_storage, mock_embedding_gen)
        results = matcher.find_similar(
            [reference], content_type=ContentType.BOOK, limit=10
        )

        assert [(item.title, score) for item, score in results] == [("Book 599", 0.95)]

    def test_consumed_items_do_not_consume_lookup_slots_regression(
        self, real_storage: StorageManager, mock_embedding_gen: Mock
    ) -> None:
        """500 completed books sorting first do not crowd out an unread hit."""
        for index in range(500):
            _save_book(
                real_storage,
                f"Alpha {index:03d}",
                item_id=f"alpha-{index:03d}",
                status=ConsumptionStatus.COMPLETED,
            )
        for index in range(10):
            _save_book(real_storage, f"Zulu {index:02d}", item_id=f"zulu-{index:02d}")
        reference = ContentItem(
            id="reference",
            title="Reference Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        real_storage.vector_db.search_similar.return_value = [
            {"content_id": "zulu-07", "score": 0.88}
        ]

        matcher = SimilarityMatcher(real_storage, mock_embedding_gen)
        results = matcher.find_similar(
            [reference], content_type=ContentType.BOOK, limit=10
        )

        assert [(item.title, score) for item, score in results] == [("Zulu 07", 0.88)]


class TestSimilarityForItemsWithoutExternalId:
    """Bug reported: CSV-imported items never received a similarity score.

    Bug reported: with AI enabled, items imported without an external id were
    ranked as if nothing in the library resembled them.
    Root cause: their embedding is stored under the synthetic ``db_<db_id>``
    key, but the lookup resolving hits back to items was keyed on the external
    id and dropped every item whose id was ``None``.
    Fix: the lookup resolves both key forms, so an id-less item is found under
    the key its embedding was written with.
    """

    def test_id_less_item_receives_similarity_score_regression(
        self, real_storage: StorageManager, mock_embedding_gen: Mock
    ) -> None:
        """An item with no external id resolves via its ``db_`` embedding key."""
        db_id = _save_book(real_storage, "CSV Import", item_id=None)
        reference = ContentItem(
            id="reference",
            title="Reference Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        real_storage.vector_db.search_similar.return_value = [
            {"content_id": f"db_{db_id}", "score": 0.77}
        ]

        matcher = SimilarityMatcher(real_storage, mock_embedding_gen)
        results = matcher.find_similar(
            [reference], content_type=ContentType.BOOK, limit=10
        )

        assert [(item.title, score) for item, score in results] == [
            ("CSV Import", 0.77)
        ]

    def test_ignored_id_less_item_is_excluded_from_lookup(
        self, real_storage: StorageManager, mock_embedding_gen: Mock
    ) -> None:
        """include_ignored=False still filters an item resolved by ``db_`` key."""
        db_id = real_storage.save_content_item(
            ContentItem(
                id=None,
                title="Ignored CSV Import",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
                ignored=True,
            )
        )
        reference = ContentItem(
            id="reference",
            title="Reference Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        real_storage.vector_db.search_similar.return_value = [
            {"content_id": f"db_{db_id}", "score": 0.77}
        ]

        matcher = SimilarityMatcher(real_storage, mock_embedding_gen)
        results = matcher.find_similar(
            [reference], content_type=ContentType.BOOK, include_ignored=False
        )

        assert results == []


class TestHitResolvesWithinTheSearchedContentType:
    """A hit must resolve to an item of the content type that was searched.

    The library's uniqueness constraint is ``(user_id, external_id,
    content_type)``, so one external id can name a row of two different
    content types for the same user.
    """

    def test_shared_external_id_resolves_to_the_searched_type(
        self, real_storage: StorageManager, mock_embedding_gen: Mock
    ) -> None:
        """A book search does not return the movie sharing the hit's id."""
        real_storage.save_content_item(
            ContentItem(
                id="shared",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            )
        )
        real_storage.save_content_item(
            ContentItem(
                id="shared",
                title="Dune",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
            )
        )
        reference = ContentItem(
            id="reference",
            title="Reference Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        real_storage.vector_db.search_similar.return_value = [
            {"content_id": "shared", "score": 0.9}
        ]

        matcher = SimilarityMatcher(real_storage, mock_embedding_gen)
        results = matcher.find_similar(
            [reference], content_type=ContentType.BOOK, limit=10
        )

        assert [item.content_type for item, _ in results] == [ContentType.BOOK]


class TestMalformedEmbeddingKeys:
    """A stored key the resolver cannot parse must not take the search down."""

    def test_non_decimal_digit_key_does_not_abort_the_search(
        self, real_storage: StorageManager, mock_embedding_gen: Mock
    ) -> None:
        """A ``db_`` key whose suffix is a non-decimal digit is just skipped.

        ``"²".isdigit()`` is True while ``int("²")`` raises, so a key
        of that shape must not stop the other hits resolving.
        """
        _save_book(real_storage, "Real Book", item_id="book-1")
        reference = ContentItem(
            id="reference",
            title="Reference Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        real_storage.vector_db.search_similar.return_value = [
            {"content_id": "db_²", "score": 0.95},
            {"content_id": "book-1", "score": 0.8},
        ]

        matcher = SimilarityMatcher(real_storage, mock_embedding_gen)
        results = matcher.find_similar(
            [reference], content_type=ContentType.BOOK, limit=10
        )

        assert [(item.title, score) for item, score in results] == [("Real Book", 0.8)]


class TestBothKeyFormsAgainstARealVectorStore:
    """Both live embedding-key forms resolve through a real ChromaDB.

    Every test above stubs the vector store, so none of them proves the key
    the store actually holds is the key the lookup asks for.  A library synced
    before the ``db_`` key existed holds external-id keys, and re-keying it
    would orphan every one of them.
    """

    def test_both_key_forms_resolve(self, tmp_path: Path) -> None:
        """An external-id row and an id-less row both come back from a search."""
        storage = StorageManager(
            tmp_path / "e2e.db", tmp_path / "vectors", ai_enabled=True
        )
        storage.save_content_item(
            ContentItem(
                id="ext-1",
                title="Legacy Sync",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
            embedding=[1.0, 0.0, 0.0],
        )
        storage.save_content_item(
            ContentItem(
                id=None,
                title="CSV Import",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
            embedding=[0.9, 0.1, 0.0],
        )
        reference = ContentItem(
            id="reference",
            title="Reference Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        embedding_gen = Mock(spec=EmbeddingGenerator)
        embedding_gen.generate_content_embedding = Mock(return_value=[1.0, 0.0, 0.0])

        matcher = SimilarityMatcher(storage, embedding_gen)
        results = matcher.find_similar(
            [reference], content_type=ContentType.BOOK, limit=10
        )

        assert {item.title for item, _ in results} == {"Legacy Sync", "CSV Import"}

    def test_a_completed_id_less_item_is_not_returned_as_similar(
        self, tmp_path: Path
    ) -> None:
        """A finished item is excluded whether or not it has an external id."""
        storage = StorageManager(
            tmp_path / "consumed.db", tmp_path / "vectors", ai_enabled=True
        )
        storage.save_content_item(
            ContentItem(
                id=None,
                title="Already Read",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
            ),
            embedding=[1.0, 0.0, 0.0],
        )
        storage.save_content_item(
            ContentItem(
                id="unread-1",
                title="Unread Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
            embedding=[0.9, 0.1, 0.0],
        )
        reference = ContentItem(
            id="reference",
            title="Reference Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
        embedding_gen = Mock(spec=EmbeddingGenerator)
        embedding_gen.generate_content_embedding = Mock(return_value=[1.0, 0.0, 0.0])

        matcher = SimilarityMatcher(storage, embedding_gen)
        results = matcher.find_similar(
            [reference], content_type=ContentType.BOOK, limit=10
        )

        assert [item.title for item, _ in results] == ["Unread Book"]

    def test_a_stored_embedding_is_reused_for_an_id_less_reference(
        self, tmp_path: Path
    ) -> None:
        """A reference item's stored embedding is reused whatever keys it.

        ``find_similar`` reuses a reference item's stored embedding rather
        than paying for a fresh one, but it looks the embedding up under the
        item's external id — which an item imported from CSV does not have,
        even though its embedding is sitting in the store under ``db_<id>``.
        """
        storage = StorageManager(
            tmp_path / "reference.db", tmp_path / "vectors", ai_enabled=True
        )
        db_id = storage.save_content_item(
            ContentItem(
                id=None,
                title="CSV Favourite",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
            ),
            embedding=[1.0, 0.0, 0.0],
        )
        reference = storage.get_content_item(db_id)
        assert reference is not None
        embedding_gen = Mock(spec=EmbeddingGenerator)
        embedding_gen.generate_content_embedding = Mock(return_value=[1.0, 0.0, 0.0])

        matcher = SimilarityMatcher(storage, embedding_gen)
        matcher.find_similar([reference], content_type=ContentType.BOOK, limit=10)

        embedding_gen.generate_content_embedding.assert_not_called()
