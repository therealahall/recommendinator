"""Tests for recommendation generation."""

from typing import Any
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
# Trademark symbol matching regression tests
# ===========================================================================


class TestTrademarkSymbolMatchingRegression:
    """Regression tests for trademark symbols breaking title matching.

    Bug reported: "Middle-earth™: Shadow of Mordor™ Game of the Year Edition"
    didn't get an AI description while other recommendations did.

    Root cause: _parse_recommendations() uses substring matching
    (``title_lower in item_title_lower``). The DB stores titles with trademark
    symbols (™, ®, ©) but the LLM returns titles without them, so
    "middle-earth" is not found in "middle-earth™" by Python's ``in`` operator.

    Fix: Strip trademark symbols (™®©) from both sides before comparison,
    matching the existing pattern used in sqlite_db.py and rawg.py.
    """

    @pytest.fixture()
    def generator(self) -> RecommendationGenerator:
        return RecommendationGenerator(Mock(spec=OllamaClient))

    def test_trademark_symbol_in_db_title_matches_llm_title(
        self, generator: RecommendationGenerator
    ) -> None:
        """Regression: DB title with ™ must match LLM title without it."""
        unconsumed = [
            ContentItem(
                id="1",
                title="Middle-earth™: Shadow of Mordor™ Game of the Year Edition",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
            ),
        ]
        response = (
            "1. **Middle-earth: Shadow of Mordor Game of the Year Edition**\n"
            "An excellent open-world action game set in Tolkien's universe."
        )
        recs = generator._parse_recommendations(response, unconsumed)

        assert len(recs) == 1
        assert recs[0]["item"] is not None
        assert recs[0]["item"].id == "1"
        assert "Tolkien" in recs[0]["reasoning"]

    def test_registered_symbol_in_db_title_matches_llm_title(
        self, generator: RecommendationGenerator
    ) -> None:
        """Regression: DB title with ® must match LLM title without it."""
        unconsumed = [
            ContentItem(
                id="1",
                title="DOOM® Eternal",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
            ),
        ]
        response = (
            "1. **DOOM Eternal**\n"
            "A fast-paced shooter that keeps the adrenaline high."
        )
        recs = generator._parse_recommendations(response, unconsumed)

        assert len(recs) == 1
        assert recs[0]["item"] is not None
        assert recs[0]["item"].id == "1"
        assert "adrenaline" in recs[0]["reasoning"]

    def test_copyright_symbol_in_db_title_matches_llm_title(
        self, generator: RecommendationGenerator
    ) -> None:
        """Regression: DB title with © must match LLM title without it."""
        unconsumed = [
            ContentItem(
                id="1",
                title="Some Game© Deluxe",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
            ),
        ]
        response = (
            "1. **Some Game Deluxe**\n" "A polished edition with all the DLC included."
        )
        recs = generator._parse_recommendations(response, unconsumed)

        assert len(recs) == 1
        assert recs[0]["item"] is not None
        assert recs[0]["item"].id == "1"
        assert "DLC" in recs[0]["reasoning"]

    def test_fallback_path_strips_trademarks(
        self, generator: RecommendationGenerator
    ) -> None:
        """Regression: fallback (prose) path must also strip trademark symbols."""
        unconsumed = [
            ContentItem(
                id="1",
                title="Middle-earth™: Shadow of Mordor™",
                content_type=ContentType.VIDEO_GAME,
                status=ConsumptionStatus.UNREAD,
            ),
        ]
        # No numbered list — triggers fallback path
        response = "I think Middle-earth: Shadow of Mordor would be a great choice."
        recs = generator._parse_recommendations(response, unconsumed)

        assert len(recs) == 1
        assert recs[0]["item"] is not None
        assert recs[0]["item"].id == "1"


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


class TestRecommendationGeneratorRegression:
    """Regression tests for RecommendationGenerator bugs."""

    def test_llm_failure_raises_runtime_error_regression(
        self,
        mock_ollama_client: Mock,
    ) -> None:
        """Regression: LLM failure must raise RuntimeError, not the raw exception.

        Bug reported: Raw ConnectionError leaked to callers instead of RuntimeError.
        Root cause: generate_recommendations didn't wrap LLM errors.
        Fix: Catch and re-raise as RuntimeError with descriptive message.
        """
        mock_ollama_client.generate_text.side_effect = ConnectionError(
            "Ollama unreachable"
        )

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
            generator.generate_recommendations(
                ContentType.BOOK, [], unconsumed, count=5
            )


def test_generate_single_blurb(mock_ollama_client: Mock) -> None:
    """Test single-item blurb generation returns stripped prose."""
    mock_ollama_client.generate_text.return_value = (
        "  You gave Favorite Book a 5/5 — Book A has the same space exploration vibe.  "
    )

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
    item = ContentItem(
        id="2",
        title="Book A",
        author="Author A",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )

    generator = RecommendationGenerator(mock_ollama_client)
    blurb = generator.generate_single_blurb(
        content_type=ContentType.BOOK,
        item=item,
        consumed_items=consumed,
    )

    assert (
        blurb
        == "You gave Favorite Book a 5/5 — Book A has the same space exploration vibe."
    )
    mock_ollama_client.generate_text.assert_called_once()


def test_generate_blurbs_per_item(mock_ollama_client: Mock) -> None:
    """Test per-item blurb generation returns dict keyed by item ID."""
    mock_ollama_client.generate_text.side_effect = [
        "Book A has the same space exploration vibe.",
        "Like your favorite, this one goes deep on character development.",
    ]

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

    items = [
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
    blurbs = generator.generate_blurbs_per_item(
        content_type=ContentType.BOOK,
        items_with_refs=[(items[0], []), (items[1], [])],
        consumed_items=consumed,
    )

    assert len(blurbs) == 2
    assert "2" in blurbs
    assert "3" in blurbs
    assert mock_ollama_client.generate_text.call_count == 2


def test_generate_blurbs_per_item_empty(mock_ollama_client: Mock) -> None:
    """Test per-item blurb generation with no items returns empty dict."""
    generator = RecommendationGenerator(mock_ollama_client)
    blurbs = generator.generate_blurbs_per_item(
        content_type=ContentType.BOOK,
        items_with_refs=[],
        consumed_items=[],
    )

    assert blurbs == {}
    mock_ollama_client.generate_text.assert_not_called()


def test_generate_blurbs_per_item_partial_failure(
    mock_ollama_client: Mock,
) -> None:
    """Test per-item blurb generation returns successful results on partial failure.

    Uses prompt-keyed side_effect so the outcome is deterministic regardless
    of thread execution order (Book A always succeeds, Book B always fails).
    """

    def _prompt_keyed_response(**kwargs: Any) -> str:
        prompt = kwargs.get("prompt", "")
        if "Book B" in prompt:
            raise ConnectionError("Ollama unreachable")
        return "Great match for your taste."

    mock_ollama_client.generate_text.side_effect = _prompt_keyed_response

    items = [
        ContentItem(
            id="1",
            title="Book A",
            author="Author A",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        ),
        ContentItem(
            id="2",
            title="Book B",
            author="Author B",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        ),
    ]

    generator = RecommendationGenerator(mock_ollama_client)
    blurbs = generator.generate_blurbs_per_item(
        content_type=ContentType.BOOK,
        items_with_refs=[(items[0], []), (items[1], [])],
        consumed_items=[],
    )

    # Book A succeeded, Book B failed — should still get the successful one
    assert len(blurbs) == 1
    assert "1" in blurbs
    assert blurbs["1"] == "Great match for your taste."


# ===========================================================================
# Single-item fast path tests (8D)
# ===========================================================================


class TestGenerateBlurbsPerItemSingleItemFastPath:
    """Tests for the single-item fast path in generate_blurbs_per_item.

    When ``len(items_with_refs) == 1``, the method skips
    ``ThreadPoolExecutor`` overhead and calls ``generate_single_blurb``
    directly in the current thread.
    """

    @pytest.fixture()
    def generator(self, mock_ollama_client: Mock) -> RecommendationGenerator:
        return RecommendationGenerator(mock_ollama_client)

    def test_single_item_uses_fast_path(
        self, generator: RecommendationGenerator, mock_ollama_client: Mock
    ) -> None:
        """Single item bypasses ThreadPoolExecutor and returns blurb directly."""
        mock_ollama_client.generate_text.return_value = "A perfect sci-fi pick."

        item = ContentItem(
            id="42",
            title="Book X",
            author="Author X",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
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
        refs = [
            ContentItem(
                id="ref1",
                title="Reference Book",
                author="Ref Author",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
            )
        ]

        blurbs = generator.generate_blurbs_per_item(
            content_type=ContentType.BOOK,
            items_with_refs=[(item, refs)],
            consumed_items=consumed,
        )

        assert blurbs == {"42": "A perfect sci-fi pick."}
        mock_ollama_client.generate_text.assert_called_once()

    def test_single_item_fast_path_handles_failure(
        self, generator: RecommendationGenerator, mock_ollama_client: Mock
    ) -> None:
        """Single-item fast path logs warning and returns empty on failure."""
        mock_ollama_client.generate_text.side_effect = RuntimeError("LLM down")

        item = ContentItem(
            id="42",
            title="Book X",
            author="Author X",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )

        blurbs = generator.generate_blurbs_per_item(
            content_type=ContentType.BOOK,
            items_with_refs=[(item, [])],
            consumed_items=[],
        )

        assert blurbs == {}

    def test_single_item_fast_path_does_not_use_thread_pool(
        self, generator: RecommendationGenerator, mock_ollama_client: Mock
    ) -> None:
        """Single-item fast path skips ThreadPoolExecutor entirely."""
        mock_ollama_client.generate_text.return_value = "Good match."

        item = ContentItem(
            id="42",
            title="Book X",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )

        with patch("src.llm.recommendations.ThreadPoolExecutor") as mock_pool_cls:
            blurbs = generator.generate_blurbs_per_item(
                content_type=ContentType.BOOK,
                items_with_refs=[(item, [])],
                consumed_items=[],
            )

        mock_pool_cls.assert_not_called()
        assert blurbs == {"42": "Good match."}
