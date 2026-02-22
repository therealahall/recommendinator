"""Tests for recommendation and blurb prompt templates.

Covers content type grouping, anti-hallucination guardrails, and
regression tests for LLM misclassification / fabricated reviews.
"""

from src.conversation.engine import COMPACT_SYSTEM_PROMPT
from src.llm.prompts import (
    build_blurb_prompt,
    build_blurb_system_prompt,
    build_recommendation_prompt,
    build_recommendation_system_prompt,
)
from src.llm.tone import STYLE_RULES
from src.models.content import ConsumptionStatus, ContentItem, ContentType


def _make_item(
    title: str,
    content_type: ContentType,
    rating: int | None = None,
    review: str | None = None,
    author: str | None = None,
    status: ConsumptionStatus = ConsumptionStatus.COMPLETED,
) -> ContentItem:
    """Create a ContentItem for testing."""
    return ContentItem(
        id=f"test-{title.lower().replace(' ', '-')}",
        title=title,
        content_type=content_type,
        status=status,
        rating=rating,
        review=review,
        author=author,
    )


# ---------------------------------------------------------------------------
# Helpers for building standard item sets
# ---------------------------------------------------------------------------


def _make_books(count: int, rating: int = 5) -> list[ContentItem]:
    return [
        _make_item(
            f"Book {index}",
            ContentType.BOOK,
            rating=rating,
            author=f"Author {index}",
        )
        for index in range(count)
    ]


def _make_movies(count: int, rating: int = 5) -> list[ContentItem]:
    return [
        _make_item(f"Movie {index}", ContentType.MOVIE, rating=rating)
        for index in range(count)
    ]


def _make_tv_shows(count: int, rating: int = 5) -> list[ContentItem]:
    return [
        _make_item(f"TV Show {index}", ContentType.TV_SHOW, rating=rating)
        for index in range(count)
    ]


def _make_unconsumed(
    count: int, content_type: ContentType = ContentType.BOOK
) -> list[ContentItem]:
    return [
        _make_item(
            f"Candidate {index}",
            content_type=content_type,
            status=ConsumptionStatus.UNREAD,
        )
        for index in range(count)
    ]


# ===========================================================================
# Content type grouping tests
# ===========================================================================


class TestContentTypeGrouping:
    """Tests that consumed items are split into same-type and cross-type groups."""

    def test_same_type_only_when_five_or_more(self) -> None:
        """When >= 5 same-type items exist, cross-type items are excluded."""
        books = _make_books(6)
        movies = _make_movies(3)
        consumed = books + movies
        unconsumed = _make_unconsumed(5)

        prompt = build_recommendation_prompt(
            content_type=ContentType.BOOK,
            consumed_items=consumed,
            unconsumed_items=unconsumed,
        )

        assert "Here are books they love:" in prompt
        for book in books:
            assert book.title in prompt
        # Cross-type movies should not appear
        for movie in movies:
            assert movie.title not in prompt
        assert "From other types" not in prompt

    def test_cross_type_included_when_fewer_than_five_same_type(self) -> None:
        """When < 5 same-type items exist, cross-type items fill remaining slots."""
        books = _make_books(3)
        movies = _make_movies(4)
        consumed = books + movies
        unconsumed = _make_unconsumed(5)

        prompt = build_recommendation_prompt(
            content_type=ContentType.BOOK,
            consumed_items=consumed,
            unconsumed_items=unconsumed,
        )

        assert "Here are books they love:" in prompt
        assert "From other types they've enjoyed:" in prompt
        # All books present
        for book in books:
            assert book.title in prompt
        # Movies present with type label
        for movie in movies:
            assert movie.title in prompt
            assert f"[movie] **{movie.title}**" in prompt

    def test_same_type_items_have_no_type_label(self) -> None:
        """Same-type items should NOT have a [type] prefix."""
        books = _make_books(6)
        unconsumed = _make_unconsumed(5)

        prompt = build_recommendation_prompt(
            content_type=ContentType.BOOK,
            consumed_items=books,
            unconsumed_items=unconsumed,
        )

        # Should not have [book] prefix
        assert "[book]" not in prompt

    def test_cross_type_items_keep_type_label(self) -> None:
        """Cross-type items should retain the [type] prefix."""
        books = _make_books(2)
        movies = _make_movies(2)
        consumed = books + movies
        unconsumed = _make_unconsumed(5)

        prompt = build_recommendation_prompt(
            content_type=ContentType.BOOK,
            consumed_items=consumed,
            unconsumed_items=unconsumed,
        )

        for movie in movies:
            assert f"[movie] **{movie.title}**" in prompt

    def test_cross_type_capped_to_remaining_slots(self) -> None:
        """Cross-type items should not exceed 10 total items minus same-type count."""
        books = _make_books(3)
        movies = _make_movies(20)
        consumed = books + movies
        unconsumed = _make_unconsumed(5)

        prompt = build_recommendation_prompt(
            content_type=ContentType.BOOK,
            consumed_items=consumed,
            unconsumed_items=unconsumed,
        )

        # 3 books + at most 7 movies = 10 total
        movie_lines = [line for line in prompt.splitlines() if "[movie]" in line]
        assert len(movie_lines) == 7

    def test_same_type_capped_to_ten(self) -> None:
        """Same-type items should be capped at 10 even when more exist."""
        books = _make_books(15)
        unconsumed = _make_unconsumed(5)

        prompt = build_recommendation_prompt(
            content_type=ContentType.BOOK,
            consumed_items=books,
            unconsumed_items=unconsumed,
        )

        # Only 10 of 15 books should appear
        book_count = sum(1 for book in books if book.title in prompt)
        assert book_count == 10

    def test_no_context_when_no_high_rated_items(self) -> None:
        """When no items are rated >= 4, no context section should appear."""
        low_rated = _make_books(3, rating=2)
        unconsumed = _make_unconsumed(5)

        prompt = build_recommendation_prompt(
            content_type=ContentType.BOOK,
            consumed_items=low_rated,
            unconsumed_items=unconsumed,
        )

        assert "Here are" not in prompt
        assert "From other types" not in prompt

    def test_only_cross_type_when_no_same_type_items(self) -> None:
        """When no same-type items exist, cross-type items appear correctly."""
        movies = _make_movies(3)
        unconsumed = _make_unconsumed(5)

        prompt = build_recommendation_prompt(
            content_type=ContentType.BOOK,
            consumed_items=movies,
            unconsumed_items=unconsumed,
        )

        assert "From other types they've enjoyed:" in prompt
        for movie in movies:
            assert f"[movie] **{movie.title}**" in prompt
        # No same-type header should appear
        assert "Here are books they love:" not in prompt

    def test_header_uses_content_type_name(self) -> None:
        """Header should say 'tv shows they love' for TV show recommendations."""
        shows = _make_tv_shows(6)
        unconsumed = _make_unconsumed(5, content_type=ContentType.TV_SHOW)

        prompt = build_recommendation_prompt(
            content_type=ContentType.TV_SHOW,
            consumed_items=shows,
            unconsumed_items=unconsumed,
        )

        assert "Here are tv shows they love:" in prompt

    def test_exactly_five_same_type_uses_same_type_only(self) -> None:
        """Edge case: exactly 5 same-type items should exclude cross-type."""
        books = _make_books(5)
        movies = _make_movies(3)
        consumed = books + movies
        unconsumed = _make_unconsumed(5)

        prompt = build_recommendation_prompt(
            content_type=ContentType.BOOK,
            consumed_items=consumed,
            unconsumed_items=unconsumed,
        )

        assert "From other types" not in prompt
        for movie in movies:
            assert movie.title not in prompt


# ===========================================================================
# Anti-hallucination guardrail tests
# ===========================================================================


class TestAntiHallucinationGuardrails:
    """Tests that prompts contain anti-hallucination rules.

    Note: user-prompt guardrails for recommendation prompts are covered by
    TestHallucinatedReviewsRegression. This class covers system prompts
    and blurb prompts.
    """

    def test_recommendation_system_prompt_has_data_accuracy_section(self) -> None:
        """System prompt should contain the Data Accuracy section."""
        system_prompt = build_recommendation_system_prompt(ContentType.BOOK)

        assert "## Data Accuracy" in system_prompt
        assert "NEVER invent quotes" in system_prompt
        assert "A book is NOT a show" in system_prompt

    def test_blurb_system_prompt_has_anti_hallucination(self) -> None:
        """Blurb system prompt should contain anti-hallucination instruction."""
        system_prompt = build_blurb_system_prompt(ContentType.MOVIE)

        assert "NEVER invent quotes or reviews" in system_prompt

    def test_blurb_prompt_has_anti_hallucination_rule(self) -> None:
        """Blurb prompt rules should include anti-hallucination instruction."""
        selected = _make_books(2)
        consumed = _make_books(3)

        prompt = build_blurb_prompt(
            content_type=ContentType.BOOK,
            selected_items=selected,
            consumed_items=consumed,
        )

        assert "Do NOT invent quotes or opinions" in prompt


# ===========================================================================
# Blurb prompt type label tests
# ===========================================================================


class TestBlurbPromptTypeLabels:
    """Tests that build_blurb_prompt includes type labels on favorites."""

    def test_favorites_have_type_labels(self) -> None:
        """Each favorite in the blurb prompt should have a [type] prefix."""
        books = _make_books(2)
        movies = _make_movies(2)
        consumed = books + movies
        selected = _make_books(2)

        prompt = build_blurb_prompt(
            content_type=ContentType.BOOK,
            selected_items=selected,
            consumed_items=consumed,
        )

        for book in books:
            assert f"[book] **{book.title}**" in prompt
        for movie in movies:
            assert f"[movie] **{movie.title}**" in prompt

    def test_tv_show_type_label_format(self) -> None:
        """TV show favorites should use 'tv show' (space, not underscore) label."""
        shows = _make_tv_shows(2)
        selected = _make_tv_shows(1)

        prompt = build_blurb_prompt(
            content_type=ContentType.TV_SHOW,
            selected_items=selected,
            consumed_items=shows,
        )

        assert "[tv show]" in prompt
        assert "[tv_show]" not in prompt


# ===========================================================================
# Review text preservation tests
# ===========================================================================


class TestReviewTextPreservation:
    """Tests that reviews are included when present, but omitted when absent."""

    def test_review_included_when_present(self) -> None:
        """Items with reviews should have the review text in the prompt."""
        book_with_review = _make_item(
            "Reviewed Book",
            ContentType.BOOK,
            rating=5,
            review="Absolutely loved this!",
        )
        unconsumed = _make_unconsumed(3)

        prompt = build_recommendation_prompt(
            content_type=ContentType.BOOK,
            consumed_items=[book_with_review],
            unconsumed_items=unconsumed,
        )

        assert 'Review: "Absolutely loved this!"' in prompt

    def test_review_omitted_when_absent(self) -> None:
        """Items without reviews should not have any Review text."""
        book_without_review = _make_item("No Review Book", ContentType.BOOK, rating=5)
        unconsumed = _make_unconsumed(3)

        prompt = build_recommendation_prompt(
            content_type=ContentType.BOOK,
            consumed_items=[book_without_review],
            unconsumed_items=unconsumed,
        )

        assert "No Review Book" in prompt
        assert "Review:" not in prompt


# ===========================================================================
# Regression tests
# ===========================================================================


class TestContentTypeMisclassificationRegression:
    """Regression tests for LLM content type confusion.

    Bug reported: When recommending TV shows, the 3B model called books and
    movies "shows" because the prompt mixed all consumed content types together
    with only a small [type_label] prefix that the model ignored.

    Root cause: The prompt included cross-type items (books, movies) alongside
    same-type items (TV shows) with only a bracketed type label for
    disambiguation. The 3B model treated all items as the target type.

    Fix: Split consumed items into same-type and cross-type groups. When >= 5
    same-type items exist, exclude cross-type entirely. Same-type items no
    longer carry a redundant type label. Cross-type items appear in a separate
    section with an explicit header.
    """

    def test_tv_show_prompt_excludes_books_when_enough_shows_regression(
        self,
    ) -> None:
        """Regression: books should not appear in TV show prompt when >= 5 shows exist."""
        tv_shows = _make_tv_shows(6)
        books = _make_books(4)
        consumed = tv_shows + books
        unconsumed = _make_unconsumed(5, content_type=ContentType.TV_SHOW)

        prompt = build_recommendation_prompt(
            content_type=ContentType.TV_SHOW,
            consumed_items=consumed,
            unconsumed_items=unconsumed,
        )

        # Books must not appear — they caused the model to call books "shows"
        for book in books:
            assert book.title not in prompt
        # TV shows must be present without type label
        for show in tv_shows:
            assert show.title in prompt
        assert "[tv show]" not in prompt

    def test_book_prompt_separates_movies_when_few_books_regression(self) -> None:
        """Regression: cross-type movies should appear in separate section."""
        books = _make_books(2)
        movies = _make_movies(3)
        consumed = books + movies
        unconsumed = _make_unconsumed(5)

        prompt = build_recommendation_prompt(
            content_type=ContentType.BOOK,
            consumed_items=consumed,
            unconsumed_items=unconsumed,
        )

        # Books in same-type section without label
        assert "Here are books they love:" in prompt
        assert "[book]" not in prompt
        # Movies in cross-type section with label
        assert "From other types they've enjoyed:" in prompt
        for movie in movies:
            assert f"[movie] **{movie.title}**" in prompt


class TestHallucinatedReviewsRegression:
    """Regression tests for LLM fabricating user reviews.

    Bug reported (round 1): The LLM fabricated quotes like "You called
    Aladdin a 'gut punch of an ending'" for items that had NO user review.

    Root cause (round 1): The instruction "mention titles, ratings, and what
    they loved" encouraged the model to invent reasons even when no review
    existed.

    Fix (round 1): Changed instruction to "mention titles and ratings"
    (removed "what they loved"). Added explicit anti-hallucination rules in
    both the user prompt and system prompt forbidding fabricated quotes.

    Bug reported (round 2): Despite guardrails, LLM still fabricated "a gut
    punch of an ending" for Band of Brothers (5/5, no review). The model
    learned the quoting pattern from the STYLE_RULES example itself.

    Root cause (round 2): STYLE_RULES contained the example "Since you gave
    Firewatch a 5/5 and called it 'a gut punch of an ending'..." which
    taught the model the exact fabrication pattern. The model followed the
    demonstrated example over the prohibition rules.

    Fix (round 2): Removed the fabricated-quote example from STYLE_RULES.
    New example demonstrates specificity via titles and ratings only: "Since
    you gave Firewatch a 5/5..." not "since you like narrative games".
    """

    def test_no_what_they_loved_instruction_regression(self) -> None:
        """Regression: prompt must NOT contain 'what they loved' instruction."""
        consumed = _make_books(3)
        unconsumed = _make_unconsumed(5)

        prompt = build_recommendation_prompt(
            content_type=ContentType.BOOK,
            consumed_items=consumed,
            unconsumed_items=unconsumed,
        )

        assert "what they loved" not in prompt
        assert "mention titles and ratings" in prompt

    def test_anti_hallucination_in_user_prompt_regression(self) -> None:
        """Regression: user prompt must contain review-quoting guardrail."""
        consumed = _make_books(3)
        unconsumed = _make_unconsumed(5)

        prompt = build_recommendation_prompt(
            content_type=ContentType.BOOK,
            consumed_items=consumed,
            unconsumed_items=unconsumed,
        )

        assert "ONLY quote reviews that appear above" in prompt
        assert "do NOT invent one" in prompt

    def test_anti_hallucination_in_system_prompt_regression(self) -> None:
        """Regression: system prompt must forbid inventing quotes."""
        system_prompt = build_recommendation_system_prompt(ContentType.BOOK)

        assert "NEVER invent quotes" in system_prompt

    def test_anti_hallucination_in_blurb_prompts_regression(self) -> None:
        """Regression: blurb prompts must also forbid inventing quotes."""
        system_prompt = build_blurb_system_prompt(ContentType.BOOK)
        assert "NEVER invent quotes" in system_prompt

        selected = _make_books(2)
        consumed = _make_books(3)
        blurb_prompt = build_blurb_prompt(
            content_type=ContentType.BOOK,
            selected_items=selected,
            consumed_items=consumed,
        )
        assert "Do NOT invent quotes or opinions" in blurb_prompt

    def test_style_rules_no_fabricated_quote_example_regression(self) -> None:
        """Regression: STYLE_RULES must not demonstrate quoting a review.

        Bug reported (round 2): Despite anti-hallucination guardrails, the
        LLM fabricated "a gut punch of an ending" for Band of Brothers.

        Root cause: STYLE_RULES contained the example 'called it "a gut
        punch of an ending"' which taught the model to fabricate quotes.

        Fix: Replaced the example with one that references titles and
        ratings only, without demonstrating the quote-fabrication pattern.
        """
        # The rule itself must prohibit putting words in their mouth
        assert "NEVER put words in their mouth" in STYLE_RULES
        # Must not demonstrate the quote-attribution pattern via a worked example
        assert "called it" not in STYLE_RULES
        assert "gut punch" not in STYLE_RULES
        # The example should demonstrate specificity via ratings, not quotes
        assert "5/5" in STYLE_RULES

    def test_compact_system_prompt_no_fabricated_quote_example_regression(
        self,
    ) -> None:
        """Regression: COMPACT_SYSTEM_PROMPT must not demonstrate quoting a review.

        The compact prompt's few-shot example contained the same "called it
        'a gut punch'" pattern, teaching the model to fabricate quotes even
        when STYLE_RULES was clean.
        """
        assert "called it" not in COMPACT_SYSTEM_PROMPT
        assert "gut punch" not in COMPACT_SYSTEM_PROMPT
