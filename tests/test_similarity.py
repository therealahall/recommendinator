"""Tests for SimilarityMatcher vector similarity matching."""

from unittest.mock import Mock

import pytest

from src.llm.embeddings import EmbeddingGenerator
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.recommendations.similarity import SimilarityMatcher
from src.storage.manager import StorageManager
from tests.factories import make_item


@pytest.fixture
def mock_storage() -> Mock:
    """Create a mock storage manager."""
    storage = Mock(spec=StorageManager)
    storage.vector_db = Mock()
    storage.vector_db.has_embedding = Mock(return_value=False)
    storage.vector_db.get_embedding = Mock(return_value=None)
    return storage


@pytest.fixture
def mock_embedding_gen() -> Mock:
    """Create a mock embedding generator."""
    gen = Mock(spec=EmbeddingGenerator)
    gen.generate_content_embedding = Mock(return_value=[0.1, 0.2, 0.3])
    return gen


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
        mock_storage.get_content_items.return_value = stored_items

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
        mock_storage.get_content_items.return_value = []

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
        mock_storage.get_content_items.return_value = []

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
        mock_storage.get_content_items.return_value = []

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
        mock_storage.get_content_items.return_value = []

        matcher.find_similar([ref], content_type=ContentType.MOVIE, limit=10)

        call_kwargs = mock_storage.search_similar.call_args.kwargs
        assert call_kwargs["n_results"] == 10
        assert call_kwargs["content_type"] == ContentType.MOVIE

    def test_passes_user_id_to_get_content_items(
        self, matcher: SimilarityMatcher, mock_storage: Mock
    ) -> None:
        """find_similar passes user_id when fetching items for lookup."""
        ref = make_item(item_id="ref1", title="Reference Book")

        mock_storage.search_similar.return_value = [
            {"content_id": "a", "score": 0.8},
        ]
        mock_storage.get_content_items.return_value = [
            make_item(item_id="a", title="Book A")
        ]

        matcher.find_similar([ref], user_id=42)

        call_kwargs = mock_storage.get_content_items.call_args.kwargs
        assert call_kwargs["user_id"] == 42


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
        mock_storage.get_content_items.assert_not_called()


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
        mock_storage.get_content_items.return_value = []

        matcher.find_similar([ref])

        # Should generate embedding but not save (no content_id)
        mock_embedding_gen.generate_content_embedding.assert_called_once_with(ref)
        mock_storage.save_content_item.assert_not_called()

    def test_handles_vector_db_none(self, mock_embedding_gen: Mock) -> None:
        """find_similar generates embeddings when vector_db is None."""
        storage = Mock(spec=StorageManager)
        storage.vector_db = None
        storage.search_similar.return_value = []
        storage.get_content_items.return_value = []

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
        mock_storage.get_content_items.return_value = [
            make_item(item_id="a", title="Book A"),
            make_item(item_id="b", title="Book B"),
            make_item(item_id="c", title="Book C"),
        ]

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
        mock_storage.get_content_items.return_value = [
            make_item(item_id="a", title="Book A"),
            make_item(item_id="b", title="Book B"),
        ]

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
        mock_storage.get_content_items.return_value = []

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
        mock_storage.get_content_items.return_value = [
            make_item(item_id="a", title="Book A")
        ]

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
        mock_storage.get_content_items.return_value = [
            make_item(item_id="a", title="Book A")
        ]

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
