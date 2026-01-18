"""Tests for ChromaDB vector database manager."""

from pathlib import Path

import pytest

from src.storage.vector_db import VectorDB


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
    for r, e in zip(retrieved, embedding):
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
    for r, e in zip(retrieved, embedding2):
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

    assert len(results) <= 2
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
