"""Tests for embedding generation."""

from unittest.mock import Mock, patch

import pytest

from src.llm.client import OllamaClient
from src.llm.embeddings import EmbeddingGenerator
from src.models.content import ConsumptionStatus, ContentItem, ContentType


@pytest.fixture
def mock_ollama_client():
    """Create a mock Ollama client."""
    with patch("src.llm.embeddings.OllamaClient") as mock_client_class:
        mock_client = Mock(spec=OllamaClient)
        mock_client_class.return_value = mock_client
        yield mock_client


def test_generate_content_embedding(mock_ollama_client):
    """Test generating embedding for content item."""
    mock_ollama_client.generate_embedding.return_value = [0.1, 0.2, 0.3]

    item = ContentItem(
        id="123",
        title="Test Book",
        author="Test Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.COMPLETED,
        rating=4,
        review="Great book!",
    )

    generator = EmbeddingGenerator(mock_ollama_client)
    embedding = generator.generate_content_embedding(item)

    assert embedding == [0.1, 0.2, 0.3]
    mock_ollama_client.generate_embedding.assert_called_once()
    call_text = mock_ollama_client.generate_embedding.call_args[0][0]
    assert "Test Book" in call_text
    assert "Test Author" in call_text
    assert "Great book!" in call_text


def test_generate_review_embedding(mock_ollama_client):
    """Test generating embedding for review text."""
    mock_ollama_client.generate_embedding.return_value = [0.1, 0.2, 0.3]

    generator = EmbeddingGenerator(mock_ollama_client)
    embedding = generator.generate_review_embedding("This is a great book!")

    assert embedding == [0.1, 0.2, 0.3]
    mock_ollama_client.generate_embedding.assert_called_once_with(
        "This is a great book!"
    )


def test_generate_review_embedding_empty(mock_ollama_client):
    """Test generating embedding for empty review raises error."""
    generator = EmbeddingGenerator(mock_ollama_client)

    with pytest.raises(ValueError, match="Review text cannot be empty"):
        generator.generate_review_embedding("")


def test_generate_embeddings_batch(mock_ollama_client):
    """Test batch embedding generation."""
    mock_ollama_client.generate_embedding.side_effect = [
        [0.1, 0.2],
        [0.3, 0.4],
        [0.5, 0.6],
    ]

    items = [
        ContentItem(
            id=f"item_{i}",
            title=f"Item {i}",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        for i in range(3)
    ]

    generator = EmbeddingGenerator(mock_ollama_client)
    embeddings = generator.generate_embeddings_batch(items, batch_size=2)

    assert len(embeddings) == 3
    assert embeddings[0] == [0.1, 0.2]
    assert embeddings[1] == [0.3, 0.4]
    assert embeddings[2] == [0.5, 0.6]


def test_generate_embeddings_batch_error_propagation(mock_ollama_client):
    """Test that generate_embeddings_batch re-raises errors from the client.

    When generate_content_embedding fails for an item in the batch, the
    exception must propagate to the caller rather than being silently
    swallowed. Previously only the success path was tested.
    """
    # First item succeeds, second item fails
    mock_ollama_client.generate_embedding.side_effect = [
        [0.1, 0.2],
        RuntimeError("Embedding generation failed: connection refused"),
    ]

    items = [
        ContentItem(
            id="item_0",
            title="Item 0",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        ),
        ContentItem(
            id="item_1",
            title="Item 1",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        ),
    ]

    generator = EmbeddingGenerator(mock_ollama_client)

    with pytest.raises(RuntimeError, match="connection refused"):
        generator.generate_embeddings_batch(items, batch_size=5)
