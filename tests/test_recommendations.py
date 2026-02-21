"""Tests for recommendation generation."""

from unittest.mock import Mock, patch

import pytest

from src.llm.client import OllamaClient
from src.llm.recommendations import RecommendationGenerator
from src.models.content import ConsumptionStatus, ContentItem, ContentType


@pytest.fixture
def mock_ollama_client():
    """Create a mock Ollama client."""
    with patch("src.llm.recommendations.OllamaClient") as mock_client_class:
        mock_client = Mock(spec=OllamaClient)
        mock_client_class.return_value = mock_client
        yield mock_client


def test_generate_recommendations(mock_ollama_client):
    """Test recommendation generation."""
    # Mock LLM response
    mock_response = """1. Book A by Author A
This matches your preference for science fiction.

2. Book B by Author B
Similar themes to your favorite books."""
    mock_ollama_client.generate_text.return_value = mock_response

    consumed = [
        ContentItem(
            id="1",
            title="Favorite Book",
            author="Author",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
    ]

    unconsumed = [
        ContentItem(
            id="2",
            title="Book A",
            author="Author A",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        ),
        ContentItem(
            id="3",
            title="Book B",
            author="Author B",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        ),
    ]

    generator = RecommendationGenerator(mock_ollama_client)
    recommendations = generator.generate_recommendations(
        ContentType.BOOK, consumed, unconsumed, count=2
    )

    assert len(recommendations) >= 1
    # Check that we got recommendations
    assert any(rec["title"] == "Book A" for rec in recommendations)


def test_generate_recommendations_no_unconsumed(mock_ollama_client):
    """Test recommendation generation with no unconsumed items."""
    generator = RecommendationGenerator(mock_ollama_client)
    recommendations = generator.generate_recommendations(
        ContentType.BOOK, [], [], count=5
    )

    assert recommendations == []


def test_generate_recommendations_fewer_than_requested(mock_ollama_client):
    """Test when fewer items available than requested."""
    mock_ollama_client.generate_text.return_value = "1. Book A\n   Good match."

    unconsumed = [
        ContentItem(
            id="1",
            title="Book A",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
    ]

    generator = RecommendationGenerator(mock_ollama_client)
    recommendations = generator.generate_recommendations(
        ContentType.BOOK, [], unconsumed, count=5
    )

    # Should only return 1 (available items)
    assert len(recommendations) <= 1


def test_generate_blurbs(mock_ollama_client):
    """Test blurb generation for pre-selected items."""
    mock_response = """1. **Book A** by Author A
You gave Favorite Book a 5/5 — Book A has the same space exploration vibe.

2. **Book B** by Author B
Like your favorite, this one goes deep on character development."""
    mock_ollama_client.generate_text.return_value = mock_response

    consumed = [
        ContentItem(
            id="1",
            title="Favorite Book",
            author="Author",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
    ]

    selected = [
        ContentItem(
            id="2",
            title="Book A",
            author="Author A",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        ),
        ContentItem(
            id="3",
            title="Book B",
            author="Author B",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        ),
    ]

    generator = RecommendationGenerator(mock_ollama_client)
    blurbs = generator.generate_blurbs(
        content_type=ContentType.BOOK,
        selected_items=selected,
        consumed_items=consumed,
    )

    assert len(blurbs) >= 1
    assert any(blurb["title"] == "Book A" for blurb in blurbs)


def test_generate_blurbs_empty_selection(mock_ollama_client):
    """Test blurb generation with no selected items returns empty list."""
    generator = RecommendationGenerator(mock_ollama_client)
    blurbs = generator.generate_blurbs(
        content_type=ContentType.BOOK,
        selected_items=[],
        consumed_items=[],
    )

    assert blurbs == []
    mock_ollama_client.generate_text.assert_not_called()
