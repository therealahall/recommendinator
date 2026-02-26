"""Tests for recommendation generation."""

from unittest.mock import Mock, patch

import pytest

from src.llm.client import OllamaClient
from src.llm.recommendations import RecommendationGenerator
from src.models.content import ConsumptionStatus, ContentItem, ContentType

# ===========================================================================
# Shared fixtures
# ===========================================================================


@pytest.fixture
def mock_ollama_client() -> Mock:
    """Create a mock Ollama client, patching the import in recommendations."""
    with patch("src.llm.recommendations.OllamaClient") as mock_client_class:
        mock_client = Mock(spec=OllamaClient)
        mock_client_class.return_value = mock_client
        yield mock_client


# ===========================================================================
# Parsing regression tests
# ===========================================================================


class TestParsingYearsInReasoningRegression:
    """Regression tests for years/numbers in reasoning text breaking the parser.

    Bug reported: When requesting 5 book recommendations, only 4 were returned.

    Root cause: The regex pattern ``(\\d+)\\.\\s+`` used to split numbered list
    items was not anchored to line starts. Years like "1984. " or ratings like
    "5. " appearing mid-sentence in the reasoning text caused spurious splits,
    corrupting the recommendation list. This could:
    - Eat the next recommendation's title line (losing a recommendation)
    - Create phantom entries from years that don't match any unconsumed item
    - Truncate reasoning at the spurious split point

    Fix: Anchored the pattern to line starts with ``^(\\d{1,2})\\.\\s+`` and
    ``re.MULTILINE``, and restricted to 1–2 digit numbers so that years
    (1984, 2019) at the start of reasoning lines don't cause splits.
    """

    @pytest.fixture()
    def generator(self) -> RecommendationGenerator:
        return RecommendationGenerator(Mock(spec=OllamaClient))

    @pytest.fixture()
    def unconsumed_five(self) -> list[ContentItem]:
        letters = "ABCDE"
        return [
            ContentItem(
                id=str(i),
                title=f"Book {letters[i]}",
                author=f"Author {letters[i]}",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            )
            for i in range(5)
        ]

    def test_year_in_reasoning_does_not_split_regression(
        self, generator: RecommendationGenerator, unconsumed_five: list[ContentItem]
    ) -> None:
        """Regression: '1984. ' mid-sentence must not start a new list item."""
        response = (
            "1. **Book A** by Author A\n"
            "Published in 1984. This dystopian novel delivers the same bleak atmosphere.\n\n"
            "2. **Book B** by Author B\n"
            "Great character development."
        )
        recs = generator._parse_recommendations(response, unconsumed_five)

        assert len(recs) == 2
        assert recs[0]["title"] == "Book A"
        assert "1984" in recs[0]["reasoning"]
        assert recs[1]["title"] == "Book B"

    def test_rating_in_reasoning_does_not_split_regression(
        self, generator: RecommendationGenerator, unconsumed_five: list[ContentItem]
    ) -> None:
        """Regression: 'a 5. This' mid-sentence must not start a new list item."""
        response = (
            "1. **Book A** by Author A\n"
            "You rated the prequel a 5. This sequel continues the story.\n\n"
            "2. **Book B** by Author B\n"
            "Compelling narrative.\n\n"
            "3. **Book C** by Author C\n"
            "Excellent worldbuilding."
        )
        recs = generator._parse_recommendations(response, unconsumed_five)

        assert len(recs) == 3
        assert recs[0]["title"] == "Book A"
        assert "rated the prequel a 5" in recs[0]["reasoning"]
        assert recs[1]["title"] == "Book B"
        assert recs[2]["title"] == "Book C"

    def test_full_five_recommendations_with_years_regression(
        self, generator: RecommendationGenerator, unconsumed_five: list[ContentItem]
    ) -> None:
        """Regression: all 5 recommendations must be returned when reasoning mentions years."""
        response = (
            "1. **Book A** by Author A\n"
            "Published in 1984. You'll love the themes.\n\n"
            "2. **Book B** by Author B\n"
            "Reminiscent of classics from the 2000s. A hidden gem.\n\n"
            "3. **Book C** by Author C\n"
            "Since you gave Dune a 5/5, this epic sci-fi from 2019. Perfect match.\n\n"
            "4. **Book D** by Author D\n"
            "Great mystery vibes.\n\n"
            "5. **Book E** by Author E\n"
            "Excellent finale to the series."
        )
        recs = generator._parse_recommendations(response, unconsumed_five)

        assert len(recs) == 5
        titles = [r["title"] for r in recs]
        assert titles == ["Book A", "Book B", "Book C", "Book D", "Book E"]

    def test_year_at_start_of_reasoning_line_does_not_split_regression(
        self, generator: RecommendationGenerator, unconsumed_five: list[ContentItem]
    ) -> None:
        """Regression: '1984. ' at the START of a reasoning line must not create a phantom item."""
        response = (
            "1. **Book A** by Author A\n"
            "1984. Was a major influence on this book.\n\n"
            "2. **Book B** by Author B\n"
            "Great read."
        )
        recs = generator._parse_recommendations(response, unconsumed_five)

        assert len(recs) == 2, f"Expected 2 recommendations, got {len(recs)}"
        assert recs[0]["title"] == "Book A"
        assert recs[1]["title"] == "Book B"


# ===========================================================================
# Parser fallback path tests
# ===========================================================================


def test_parse_recommendations_falls_back_to_title_search_when_no_numbered_list() -> (
    None
):
    """When the LLM returns prose instead of a numbered list, the parser falls
    back to scanning for item titles in the response text."""
    generator = RecommendationGenerator(Mock(spec=OllamaClient))
    unconsumed = [
        ContentItem(
            id="1",
            title="Book A",
            author="Author A",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        ),
    ]
    response = "I think Book A would be a great choice for you."
    result = generator._parse_recommendations(response, unconsumed)

    assert len(result) == 1
    assert result[0]["title"] == "Book A"
    assert result[0]["reasoning"] == "Recommended based on your preferences"


# ===========================================================================
# Integration tests
# ===========================================================================


def test_generate_recommendations(mock_ollama_client: Mock) -> None:
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

    assert len(recommendations) == 2
    assert recommendations[0]["title"] == "Book A"
    assert recommendations[1]["title"] == "Book B"


def test_generate_recommendations_no_unconsumed(mock_ollama_client: Mock) -> None:
    """Test recommendation generation with no unconsumed items."""
    generator = RecommendationGenerator(mock_ollama_client)
    recommendations = generator.generate_recommendations(
        ContentType.BOOK, [], [], count=5
    )

    assert recommendations == []


def test_generate_recommendations_fewer_than_requested(
    mock_ollama_client: Mock,
) -> None:
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

    # Should return exactly 1 (only available item)
    assert len(recommendations) == 1


def test_generate_recommendations_raises_on_llm_failure_regression(
    mock_ollama_client: Mock,
) -> None:
    """Regression: LLM failure must raise RuntimeError, not the raw exception."""
    mock_ollama_client.generate_text.side_effect = ConnectionError("Ollama unreachable")

    unconsumed = [
        ContentItem(
            id="1",
            title="Book A",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
    ]

    generator = RecommendationGenerator(mock_ollama_client)
    with pytest.raises(RuntimeError, match="Recommendation generation failed"):
        generator.generate_recommendations(ContentType.BOOK, [], unconsumed, count=5)


def test_generate_blurbs(mock_ollama_client: Mock) -> None:
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

    assert len(blurbs) == 2
    assert blurbs[0]["title"] == "Book A"
    assert blurbs[1]["title"] == "Book B"


def test_generate_blurbs_empty_selection(mock_ollama_client: Mock) -> None:
    """Test blurb generation with no selected items returns empty list."""
    generator = RecommendationGenerator(mock_ollama_client)
    blurbs = generator.generate_blurbs(
        content_type=ContentType.BOOK,
        selected_items=[],
        consumed_items=[],
    )

    assert blurbs == []
    mock_ollama_client.generate_text.assert_not_called()


def test_generate_blurbs_unmatched_titles(mock_ollama_client: Mock) -> None:
    """Test blurb generation when LLM returns titles that don't match selected items.

    When the LLM hallucinates or returns titles that don't correspond to any
    of the selected items, the parser still returns entries but with
    ``item: None`` since no match can be found in the selected items list.
    """
    mock_response = """1. **Unknown Book** by Mystery Author
This is a fascinating read you'll love.

2. **Mystery Title** by Another Author
Great themes that match your taste."""
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

    # The parser returns entries for each numbered item the LLM produced,
    # but none match the selected items, so every entry has item=None.
    assert len(blurbs) == 2
    assert blurbs[0]["title"] == "Unknown Book"
    assert blurbs[0]["item"] is None
    assert blurbs[1]["title"] == "Mystery Title"
    assert blurbs[1]["item"] is None

    # Verify the LLM was still called with the prompt
    mock_ollama_client.generate_text.assert_called_once()


def test_generate_blurbs_raises_on_llm_failure_regression(
    mock_ollama_client: Mock,
) -> None:
    """Regression: LLM failure during blurb generation must raise RuntimeError."""
    mock_ollama_client.generate_text.side_effect = ConnectionError("Ollama unreachable")

    selected = [
        ContentItem(
            id="1",
            title="Book A",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
    ]

    generator = RecommendationGenerator(mock_ollama_client)
    with pytest.raises(RuntimeError, match="Blurb generation failed"):
        generator.generate_blurbs(
            content_type=ContentType.BOOK,
            selected_items=selected,
            consumed_items=[],
        )
