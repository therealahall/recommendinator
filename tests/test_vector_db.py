"""Tests for ChromaDB vector database manager."""

import ast
import logging
import tempfile
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

import chromadb
import pytest

from src.storage import vector_db
from src.storage.vector_db import VectorDB
from src.utils.text import LINE_BREAKS


@pytest.fixture
def temp_vector_db(tmp_path: Path) -> VectorDB:
    """Create a temporary vector database for testing."""
    db_path = tmp_path / "vector_db"
    return VectorDB(db_path, collection_name="test_collection")


def test_add_and_get_embedding(temp_vector_db: VectorDB) -> None:
    """Test adding and retrieving an embedding."""
    content_id = "test_123"
    embedding = [0.1, 0.2, 0.3, 0.4, 0.5]
    metadata = {"content_type": "book", "title": "Test Book"}

    temp_vector_db.add_embedding(content_id, embedding, metadata)

    retrieved = temp_vector_db.get_embedding(content_id)
    assert retrieved is not None
    assert len(retrieved) == 5
    # Use approximate equality for floating-point comparison
    assert len(retrieved) == len(embedding)
    for r, e in zip(retrieved, embedding, strict=True):
        assert abs(r - e) < 1e-6


def test_update_embedding(temp_vector_db: VectorDB) -> None:
    """Test updating an existing embedding."""
    content_id = "test_123"
    embedding1 = [0.1, 0.2, 0.3]
    embedding2 = [0.4, 0.5, 0.6]

    temp_vector_db.add_embedding(content_id, embedding1)
    temp_vector_db.add_embedding(content_id, embedding2)

    retrieved = temp_vector_db.get_embedding(content_id)
    assert retrieved is not None
    # Use approximate equality for floating-point comparison
    assert len(retrieved) == len(embedding2)
    for r, e in zip(retrieved, embedding2, strict=True):
        assert abs(r - e) < 1e-6


def test_search_similar(temp_vector_db: VectorDB) -> None:
    """Test searching for similar embeddings."""
    # Add some test embeddings
    embeddings = [
        ([0.1, 0.2, 0.3], "item_1", {"content_type": "book"}),
        ([0.2, 0.3, 0.4], "item_2", {"content_type": "book"}),
        ([0.9, 0.8, 0.7], "item_3", {"content_type": "movie"}),
    ]

    for embedding, content_id, metadata in embeddings:
        temp_vector_db.add_embedding(content_id, embedding, metadata)

    # Search for similar to first embedding
    query_embedding = [0.15, 0.25, 0.35]  # Similar to item_1 and item_2
    results = temp_vector_db.search_similar(query_embedding, n_results=2)

    assert len(results) == 2
    assert any(result["content_id"] in ["item_1", "item_2"] for result in results)


def test_search_with_filters(temp_vector_db: VectorDB) -> None:
    """Test searching with content type filter."""
    embeddings = [
        ([0.1, 0.2, 0.3], "book_1", {"content_type": "book"}),
        ([0.2, 0.3, 0.4], "book_2", {"content_type": "book"}),
        ([0.9, 0.8, 0.7], "movie_1", {"content_type": "movie"}),
    ]

    for embedding, content_id, metadata in embeddings:
        temp_vector_db.add_embedding(content_id, embedding, metadata)

    query_embedding = [0.15, 0.25, 0.35]
    results = temp_vector_db.search_similar(
        query_embedding, n_results=10, content_type="book"
    )

    assert all(
        result.get("metadata", {}).get("content_type") == "book" for result in results
    )


def test_delete_embedding(temp_vector_db: VectorDB) -> None:
    """Test deleting an embedding."""
    content_id = "test_123"
    embedding = [0.1, 0.2, 0.3]

    temp_vector_db.add_embedding(content_id, embedding)
    assert temp_vector_db.has_embedding(content_id) is True

    deleted = temp_vector_db.delete_embedding(content_id)
    assert deleted is True
    assert temp_vector_db.has_embedding(content_id) is False


def test_has_embedding(temp_vector_db: VectorDB) -> None:
    """Test checking if embedding exists."""
    content_id = "test_123"
    embedding = [0.1, 0.2, 0.3]

    assert temp_vector_db.has_embedding(content_id) is False

    temp_vector_db.add_embedding(content_id, embedding)
    assert temp_vector_db.has_embedding(content_id) is True


def test_count_embeddings(temp_vector_db: VectorDB) -> None:
    """Test counting embeddings."""
    assert temp_vector_db.count_embeddings() == 0

    for i in range(5):
        temp_vector_db.add_embedding(f"item_{i}", [0.1, 0.2, 0.3])

    assert temp_vector_db.count_embeddings() == 5


# ---------------------------------------------------------------------------
# search_similar with exclude_ids (8G)
# ---------------------------------------------------------------------------


def test_search_similar_with_exclude_ids(temp_vector_db: VectorDB) -> None:
    """Test that exclude_ids filters out specified content IDs from results."""
    embeddings = [
        ([0.1, 0.2, 0.3], "item_1", {"content_type": "book"}),
        ([0.15, 0.25, 0.35], "item_2", {"content_type": "book"}),
        ([0.2, 0.3, 0.4], "item_3", {"content_type": "book"}),
    ]

    for embedding, content_id, metadata in embeddings:
        temp_vector_db.add_embedding(content_id, embedding, metadata)

    # Search without exclude: all items should be returnable
    query = [0.12, 0.22, 0.32]
    results_all = temp_vector_db.search_similar(query, n_results=10)
    all_ids = {r["content_id"] for r in results_all}
    assert "item_1" in all_ids

    # Search with exclude_ids: item_1 should be filtered out
    results_filtered = temp_vector_db.search_similar(
        query, n_results=10, exclude_ids=["item_1"]
    )
    filtered_ids = {r["content_id"] for r in results_filtered}
    assert "item_1" not in filtered_ids
    assert len(results_filtered) < len(results_all)


def test_search_similar_exclude_ids_respects_n_results(
    temp_vector_db: VectorDB,
) -> None:
    """Test that exclude_ids + n_results still returns at most n_results items."""
    # Add enough items so filtering still leaves enough for n_results
    for i in range(10):
        temp_vector_db.add_embedding(
            f"item_{i}", [0.1 * (i + 1), 0.2, 0.3], {"content_type": "book"}
        )

    results = temp_vector_db.search_similar(
        [0.1, 0.2, 0.3],
        n_results=3,
        exclude_ids=["item_0", "item_1"],
    )

    assert len(results) <= 3
    result_ids = {r["content_id"] for r in results}
    assert "item_0" not in result_ids
    assert "item_1" not in result_ids


def test_search_similar_exclude_all_returns_empty(
    temp_vector_db: VectorDB,
) -> None:
    """Test that excluding all items returns an empty list."""
    temp_vector_db.add_embedding("only_item", [0.1, 0.2, 0.3], {"content_type": "book"})

    results = temp_vector_db.search_similar(
        [0.1, 0.2, 0.3],
        n_results=10,
        exclude_ids=["only_item"],
    )

    assert results == []


def test_search_similar_distances_none_returns_none_score() -> None:
    """Test that when ChromaDB returns distances=None, score is set to None.

    This uses a mock to simulate the edge case where distances are not
    returned by ChromaDB (e.g. when include=['metadatas'] is used or
    an unusual configuration).
    """
    mock_collection = MagicMock(spec=chromadb.Collection)
    mock_collection.query.return_value = {
        "ids": [["item_1", "item_2"]],
        "distances": None,
        "metadatas": [[{"content_type": "book"}, {"content_type": "movie"}]],
    }

    with patch("chromadb.PersistentClient") as mock_client_cls:
        mock_client = MagicMock(spec=chromadb.ClientAPI)
        mock_client.get_or_create_collection.return_value = mock_collection
        mock_client_cls.return_value = mock_client

        with tempfile.TemporaryDirectory() as tmp:
            db = VectorDB(Path(tmp) / "test_db")
            # Replace the collection with our mock
            db.collection = mock_collection

            results = db.search_similar([0.1, 0.2, 0.3], n_results=2)

    assert len(results) == 2
    assert results[0]["content_id"] == "item_1"
    assert results[0]["score"] is None
    assert results[1]["content_id"] == "item_2"
    assert results[1]["score"] is None


class TestContentIdLoggingRegression:
    """Regression: a chromadb fault logged the content id with a traceback.

    For the CSV plugins the id is a column of an uploaded file, and a
    traceback is unboundable, multi-line, and the disclosure itself.
    """

    _FORGED = "book-1\nWARNING  | forged | line"

    @pytest.fixture
    def failing_db(self, temp_vector_db: VectorDB) -> VectorDB:
        """A db whose collection faults on every id-carrying call."""
        collection = MagicMock(spec=chromadb.Collection)
        collection.get.side_effect = RuntimeError("chromadb is unavailable")
        collection.delete.side_effect = RuntimeError("chromadb is unavailable")
        temp_vector_db.collection = collection
        return temp_vector_db

    @pytest.mark.parametrize(
        ("method", "expected"),
        [
            ("get_embedding", None),
            ("delete_embedding", False),
            ("has_embedding", False),
        ],
    )
    def test_a_faulting_call_logs_the_id_escaped_and_without_a_traceback(
        self,
        failing_db: VectorDB,
        caplog: pytest.LogCaptureFixture,
        method: str,
        expected: object,
    ) -> None:
        """The id is escaped and no frame of the fault is rendered."""
        with caplog.at_level(logging.ERROR, logger="src.storage.vector_db"):
            assert getattr(failing_db, method)(self._FORGED) == expected

        assert "book-1\\nWARNING" in caplog.text
        assert self._FORGED not in caplog.text
        assert not any(record.exc_info for record in caplog.records)


_FAULTS = [
    pytest.param(
        RuntimeError("connection refused"),
        "RuntimeError: connection refused",
        id="unreachable-server",
    ),
    pytest.param(
        ValueError("collection not found"),
        "ValueError: collection not found",
        id="missing-collection",
    ),
    pytest.param(
        OSError("No space left on device"),
        "OSError: No space left on device",
        id="full-disk",
    ),
    pytest.param(
        RuntimeError("chromadb is unavailable\nERROR    | forged | line"),
        "RuntimeError: chromadb is unavailable\\nERROR    | forged | line",
        id="forged-entry-in-the-message",
    ),
]

#: A sink missing from here is one whose fault nothing reads, which is why
#: every catching method in the parsed module is asserted against these ids.
_SINKS = [
    pytest.param(
        lambda db: db.get_embedding("book-1"),
        None,
        "Failed to get embedding for book-1",
        id="get_embedding",
    ),
    pytest.param(
        lambda db: db.delete_embedding("book-1"),
        False,
        "Failed to delete embedding for book-1",
        id="delete_embedding",
    ),
    pytest.param(
        lambda db: db.has_embedding("book-1"),
        False,
        "Failed to check embedding for book-1",
        id="has_embedding",
    ),
    pytest.param(
        lambda db: db.search_similar([0.1, 0.2, 0.3]),
        [],
        "Vector search failed",
        id="search_similar",
    ),
    pytest.param(
        lambda db: db.count_embeddings(),
        0,
        "Failed to count embeddings",
        id="count_embeddings",
    ),
]


class TestAFaultingCallSaysWhyRegression:
    """Regression: every ChromaDB fault logged the same undiagnostic line.

    An unreachable server and a full disk were indistinguishable, and the two
    sinks carrying no id kept ``logger.exception`` and its absolute paths.
    """

    @pytest.mark.parametrize(("error", "rendered"), _FAULTS)
    @pytest.mark.parametrize(("call", "fallback", "sink"), _SINKS)
    def test_a_faulting_call_logs_one_line_naming_its_sink_and_the_exception(
        self,
        temp_vector_db: VectorDB,
        caplog: pytest.LogCaptureFixture,
        call: Callable[[VectorDB], object],
        fallback: object,
        sink: str,
        error: Exception,
        rendered: str,
    ) -> None:
        """One record: this sink's wording, the class, and no traceback."""
        collection = MagicMock(spec=chromadb.Collection)
        for chroma_call in ("get", "delete", "query", "count"):
            getattr(collection, chroma_call).side_effect = error
        temp_vector_db.collection = collection

        with caplog.at_level(logging.ERROR, logger="src.storage.vector_db"):
            assert call(temp_vector_db) == fallback

        assert [record.getMessage() for record in caplog.records] == [
            f"{sink}: {rendered}"
        ]
        assert not any(record.exc_info for record in caplog.records)

    def test_the_list_of_sinks_covers_every_handler_in_the_module(self) -> None:
        """``_SINKS`` is hand-written, so a sixth handler would go unswept.

        Named rather than counted: swapping one handler for another keeps a
        count equal while leaving the new sink unswept.
        """
        source = Path(vector_db.__file__).read_text(encoding="utf-8")
        catching = {
            node.name
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and any(isinstance(child, ast.ExceptHandler) for child in ast.walk(node))
        }

        assert catching == {param.id for param in _SINKS}

    def test_the_fault_kinds_render_different_lines(
        self, temp_vector_db: VectorDB, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Distinguishable, not merely present: one fault per outage kind.

        A sink that pasted every kind into one line would satisfy the
        per-fault assertion above; this is what says the operator can tell
        them apart.
        """
        lines = set()
        for error in (
            RuntimeError("connection refused"),
            ValueError("collection not found"),
            OSError("No space left on device"),
        ):
            collection = MagicMock(spec=chromadb.Collection)
            collection.get.side_effect = error
            temp_vector_db.collection = collection
            caplog.clear()

            with caplog.at_level(logging.ERROR, logger="src.storage.vector_db"):
                temp_vector_db.get_embedding("book-1")

            lines.add(caplog.records[0].getMessage())

        assert len(lines) == 3


class TestTheChromaFaultsOwnWordsCannotForgeALine:
    """The diagnostic is ChromaDB's text, and ChromaDB quotes the id back.

    So the message added beside the id carries whatever the id held, and needs
    the escaping the id already got. The id is a column of an imported file.
    """

    @pytest.mark.parametrize("breaker", [*LINE_BREAKS, "\0"])
    def test_a_break_in_the_message_stays_on_one_line(
        self,
        temp_vector_db: VectorDB,
        caplog: pytest.LogCaptureFixture,
        breaker: str,
    ) -> None:
        collection = MagicMock(spec=chromadb.Collection)
        collection.get.side_effect = RuntimeError(
            f"no such id: book-1{breaker}ERROR | forged | line"
        )
        temp_vector_db.collection = collection

        with caplog.at_level(logging.ERROR, logger="src.storage.vector_db"):
            assert temp_vector_db.get_embedding("book-1") is None

        assert len(caplog.records) == 1
        assert len(caplog.records[0].getMessage().splitlines()) == 1

    def test_a_lone_surrogate_in_the_message_is_escaped(
        self, temp_vector_db: VectorDB, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Unescaped it costs the whole entry, so the fault reports nothing."""
        collection = MagicMock(spec=chromadb.Collection)
        collection.delete.side_effect = RuntimeError("no such id: book-\udcff")
        temp_vector_db.collection = collection

        with caplog.at_level(logging.ERROR, logger="src.storage.vector_db"):
            assert temp_vector_db.delete_embedding("book-1") is False

        assert "book-\\udcff" in caplog.records[0].getMessage()


def test_search_similar_metadatas_none_returns_empty_dict() -> None:
    """Test that when ChromaDB returns metadatas=None, metadata is set to {}."""
    mock_collection = MagicMock(spec=chromadb.Collection)
    mock_collection.query.return_value = {
        "ids": [["item_1"]],
        "distances": [[0.1]],
        "metadatas": None,
    }

    with patch("chromadb.PersistentClient") as mock_client_cls:
        mock_client = MagicMock(spec=chromadb.ClientAPI)
        mock_client.get_or_create_collection.return_value = mock_collection
        mock_client_cls.return_value = mock_client

        with tempfile.TemporaryDirectory() as tmp:
            db = VectorDB(Path(tmp) / "test_db")
            db.collection = mock_collection

            results = db.search_similar([0.1, 0.2, 0.3], n_results=1)

    assert len(results) == 1
    assert results[0]["metadata"] == {}
    # Score should be computed: 1.0 - 0.1 = 0.9
    assert abs(results[0]["score"] - 0.9) < 1e-6
