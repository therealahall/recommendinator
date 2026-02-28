"""Tests for recommendation and blurb prompt templates.

Covers content type grouping, anti-hallucination guardrails, and
regression tests for LLM misclassification / fabricated reviews.
"""

from src.conversation.engine import COMPACT_SYSTEM_PROMPT, FULL_SYSTEM_PROMPT
from src.llm.prompts import (
    build_blurb_prompt,
    build_blurb_system_prompt,
    build_content_description,
    build_recommendation_prompt,
    build_recommendation_system_prompt,
    build_single_blurb_prompt,
)
from src.llm.tone import STYLE_RULES
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from tests.factories import make_item

# ---------------------------------------------------------------------------
# Helpers for building standard item sets
# ---------------------------------------------------------------------------


def _make_books(count: int, rating: int = 5) -> list[ContentItem]:
    return [
        make_item(
            f"Book {index}",
            ContentType.BOOK,
            rating=rating,
            author=f"Author {index}",
        )
        for index in range(count)
    ]


def _make_movies(count: int, rating: int = 5) -> list[ContentItem]:
    return [
        make_item(f"Movie {index}", ContentType.MOVIE, rating=rating)
        for index in range(count)
    ]


def _make_tv_shows(count: int, rating: int = 5) -> list[ContentItem]:
    return [
        make_item(f"TV Show {index}", ContentType.TV_SHOW, rating=rating)
        for index in range(count)
    ]


def _make_unconsumed(
    count: int, content_type: ContentType = ContentType.BOOK
) -> list[ContentItem]:
    return [
        make_item(
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

        # Only 10 of 15 books should appear — use **title** to avoid
        # substring matches (e.g. "Book 1" matching "Book 10")
        book_count = sum(1 for book in books if f"**{book.title}**" in prompt)
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
        assert "do NOT invent quotes, opinions, or facts" in system_prompt
        assert "A book is NOT a show" in system_prompt

    def test_blurb_system_prompt_has_anti_hallucination(self) -> None:
        """Blurb system prompt should contain anti-hallucination instruction."""
        system_prompt = build_blurb_system_prompt(ContentType.MOVIE)

        assert "NEVER invent quotes, opinions, or facts" in system_prompt

    def test_blurb_prompt_has_anti_hallucination_rule(self) -> None:
        """Blurb prompt rules should include anti-hallucination instruction."""
        selected = _make_books(2)
        consumed = _make_books(3)

        prompt = build_blurb_prompt(
            content_type=ContentType.BOOK,
            selected_items=selected,
            consumed_items=consumed,
        )

        assert "do NOT invent quotes, opinions, or facts" in prompt


# ===========================================================================
# Blurb prompt type label tests
# ===========================================================================


class TestBlurbPromptTypeLabels:
    """Tests that build_blurb_prompt uses type labels correctly.

    Same-type favorites should NOT have type labels (redundant).
    Cross-type favorites should have type labels for disambiguation.
    """

    def test_same_type_favorites_have_no_type_label(self) -> None:
        """Same-type favorites should NOT have a [type] prefix."""
        books = _make_books(2)
        selected = _make_books(2)

        prompt = build_blurb_prompt(
            content_type=ContentType.BOOK,
            selected_items=selected,
            consumed_items=books,
        )

        assert "[book]" not in prompt
        for book in books:
            assert f"**{book.title}**" in prompt

    def test_cross_type_favorites_have_type_labels(self) -> None:
        """Cross-type favorites should have a [type] prefix."""
        books = _make_books(2)
        movies = _make_movies(2)
        consumed = books + movies
        selected = _make_books(2)

        prompt = build_blurb_prompt(
            content_type=ContentType.BOOK,
            selected_items=selected,
            consumed_items=consumed,
        )

        # Same-type books should NOT have labels
        assert "[book]" not in prompt
        # Cross-type movies should have labels
        for movie in movies:
            assert f"[movie] **{movie.title}**" in prompt

    def test_tv_show_cross_type_label_format(self) -> None:
        """TV show cross-type favorites should use 'tv show' (space, not underscore) label."""
        books = _make_books(2)
        shows = _make_tv_shows(2)
        consumed = books + shows
        selected = _make_books(1)

        prompt = build_blurb_prompt(
            content_type=ContentType.BOOK,
            selected_items=selected,
            consumed_items=consumed,
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
        book_with_review = make_item(
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
        book_without_review = make_item("No Review Book", ContentType.BOOK, rating=5)
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
    Replaced with "Since you gave Firewatch a 5/5..." to demonstrate
    specificity via titles and ratings only.

    Bug reported (round 3): LLM fabricated "called it an absolute banger"
    and "raved about" — attributing sentiments user never expressed.

    Root cause (round 3): The "Since you gave Firewatch a 5/5..." example
    still demonstrated how to speak about user history, which the model
    extended to fabricated attributions. PERSONALITY_TRAITS phrasing like
    "Talk like you just discovered something incredible" encouraged
    invented analogies.

    Fix (round 3): Removed all example phrasings and speech-pattern
    suggestions. Added explicit NEVER rules against attributing quotes
    or sentiments.
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
        assert "mention titles" in prompt

    def test_anti_hallucination_in_user_prompt_regression(self) -> None:
        """Regression: user prompt must contain anti-fabrication guardrail."""
        consumed = _make_books(3)
        unconsumed = _make_unconsumed(5)

        prompt = build_recommendation_prompt(
            content_type=ContentType.BOOK,
            consumed_items=consumed,
            unconsumed_items=unconsumed,
        )

        assert "do NOT invent quotes, opinions, or facts" in prompt

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
        assert "do NOT invent quotes, opinions, or facts" in blurb_prompt

    def test_style_rules_no_fabricated_quote_example_regression(self) -> None:
        """Regression: STYLE_RULES must not suggest ways to speak or quote users.

        Bug reported (round 2): Despite anti-hallucination guardrails, the
        LLM fabricated "a gut punch of an ending" for Band of Brothers.

        Bug reported (round 3): LLM fabricated "called it an absolute
        banger" and "raved about" — attributing sentiments user never
        expressed. Style rules contained example phrasings that taught the
        model to invent user quotes.

        Root cause: STYLE_RULES contained example phrasings like 'Since you
        gave Firewatch a 5/5...' and 'mirror that language back' which
        taught the model to fabricate and attribute user quotes.

        Fix: Removed all example phrasings and speech-pattern suggestions.
        Added explicit rules against attributing quotes or sentiments.
        """
        # The rule itself must prohibit inventing quotes or opinions
        assert "NEVER invent quotes, opinions, or facts" in STYLE_RULES
        # Must not demonstrate the quote-attribution pattern via a worked example
        assert "called it" not in STYLE_RULES
        assert "gut punch" not in STYLE_RULES
        # Must not contain example phrasings that suggest ways to speak
        assert "Since you gave" not in STYLE_RULES
        assert "mirror" not in STYLE_RULES.lower()
        # Must still instruct specificity via titles
        assert "reference their actual titles" in STYLE_RULES

    def test_compact_system_prompt_no_fabricated_quote_example_regression(
        self,
    ) -> None:
        """Regression: COMPACT_SYSTEM_PROMPT must not demonstrate quoting or attribution.

        The compact prompt's few-shot example contained the "called it
        'a gut punch'" pattern and sentiment-attribution language like
        "emotional sucker-punch" and "hits harder than Firewatch's",
        teaching the model to fabricate quotes and attribute sentiments.
        """
        assert "called it" not in COMPACT_SYSTEM_PROMPT
        assert "gut punch" not in COMPACT_SYSTEM_PROMPT
        assert "sucker-punch" not in COMPACT_SYSTEM_PROMPT
        assert "hits harder" not in COMPACT_SYSTEM_PROMPT
        assert "hype machine" not in COMPACT_SYSTEM_PROMPT.lower()


# ===========================================================================
# Genre metadata in prompts
# ===========================================================================


class TestGenreMetadataInPrompts:
    """Tests that genre metadata appears in recommendation and blurb prompts."""

    def test_genres_in_same_type_consumed_items(self) -> None:
        """Same-type consumed items should include genre tags."""
        book = make_item(
            "Dune",
            ContentType.BOOK,
            rating=5,
            author="Frank Herbert",
            metadata={"genres": ["Science Fiction", "Adventure"]},
        )
        unconsumed = _make_unconsumed(3)

        prompt = build_recommendation_prompt(
            content_type=ContentType.BOOK,
            consumed_items=[book],
            unconsumed_items=unconsumed,
        )

        assert "[Science Fiction, Adventure]" in prompt

    def test_genres_in_cross_type_consumed_items(self) -> None:
        """Cross-type consumed items should include genre tags."""
        movie = make_item(
            "Blade Runner",
            ContentType.MOVIE,
            rating=5,
            metadata={"genres": ["Sci-Fi", "Thriller"]},
        )
        unconsumed = _make_unconsumed(3)

        prompt = build_recommendation_prompt(
            content_type=ContentType.BOOK,
            consumed_items=[movie],
            unconsumed_items=unconsumed,
        )

        assert "[Sci-Fi, Thriller]" in prompt

    def test_genres_in_candidate_items(self) -> None:
        """Candidate (unconsumed) items should include genre tags."""
        consumed = _make_books(3)
        candidate = make_item(
            "Neuromancer",
            ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            author="William Gibson",
            metadata={"genres": ["Cyberpunk", "Science Fiction"]},
        )

        prompt = build_recommendation_prompt(
            content_type=ContentType.BOOK,
            consumed_items=consumed,
            unconsumed_items=[candidate],
        )

        assert "[Cyberpunk, Science Fiction]" in prompt

    def test_genres_in_blurb_favorites(self) -> None:
        """Blurb prompt favorites should include genre tags."""
        favorite = make_item(
            "Foundation",
            ContentType.BOOK,
            rating=5,
            author="Isaac Asimov",
            metadata={"genres": ["Science Fiction", "Classic"]},
        )
        selected = _make_books(2)

        prompt = build_blurb_prompt(
            content_type=ContentType.BOOK,
            selected_items=selected,
            consumed_items=[favorite],
        )

        assert "[Science Fiction, Classic]" in prompt

    def test_genres_in_blurb_selected_items(self) -> None:
        """Blurb prompt selected items should include genre tags."""
        consumed = _make_books(3)
        selected = make_item(
            "Hyperion",
            ContentType.BOOK,
            author="Dan Simmons",
            metadata={"genres": ["Science Fiction", "Space Opera"]},
        )

        prompt = build_blurb_prompt(
            content_type=ContentType.BOOK,
            selected_items=[selected],
            consumed_items=consumed,
        )

        assert "[Science Fiction, Space Opera]" in prompt

    def test_no_empty_brackets_when_no_genres(self) -> None:
        """Items without genres should not produce empty brackets."""
        book = make_item("No Genre Book", ContentType.BOOK, rating=5)
        unconsumed = _make_unconsumed(3)

        prompt = build_recommendation_prompt(
            content_type=ContentType.BOOK,
            consumed_items=[book],
            unconsumed_items=unconsumed,
        )

        assert "[]" not in prompt

    def test_genres_capped_at_four(self) -> None:
        """Genre tags should include at most 4 genres."""
        book = make_item(
            "Many Genres",
            ContentType.BOOK,
            rating=5,
            metadata={
                "genres": [
                    "Fantasy",
                    "Adventure",
                    "Romance",
                    "Mystery",
                    "Horror",
                    "Thriller",
                ]
            },
        )
        unconsumed = _make_unconsumed(3)

        prompt = build_recommendation_prompt(
            content_type=ContentType.BOOK,
            consumed_items=[book],
            unconsumed_items=unconsumed,
        )

        assert "[Fantasy, Adventure, Romance, Mystery]" in prompt
        assert "Horror" not in prompt
        assert "Thriller" not in prompt

    def test_legacy_genre_string_fallback(self) -> None:
        """Items with legacy 'genre' string should still get genre tags."""
        book = make_item(
            "Legacy Book",
            ContentType.BOOK,
            rating=5,
            metadata={"genre": "Science Fiction, Fantasy"},
        )
        unconsumed = _make_unconsumed(3)

        prompt = build_recommendation_prompt(
            content_type=ContentType.BOOK,
            consumed_items=[book],
            unconsumed_items=unconsumed,
        )

        assert "[Science Fiction, Fantasy]" in prompt

    def test_genres_list_in_content_description(self) -> None:
        """Canonical 'genres' list format is included in content descriptions."""
        item = make_item(
            "Band of Brothers",
            ContentType.TV_SHOW,
            rating=5,
            metadata={"genres": ["Drama", "War", "History"]},
        )

        description = build_content_description(item)

        assert "Band of Brothers" in description
        assert "Genre: Drama, War, History" in description


class TestGenreMisclassificationRegression:
    """Regression tests for LLM genre misclassification.

    Bug reported: The LLM called Band of Brothers "hilarious" and confused
    Duckman with The Amazing Race because the recommendation prompts included
    only bare titles and ratings — no genre metadata. A 3B model cannot
    reliably infer that Band of Brothers is a WWII drama from its title alone.

    Root cause: ``build_recommendation_prompt()`` and ``build_blurb_prompt()``
    did not include genre metadata, unlike the conversation context system
    (``src/conversation/context.py``) which already included genres.

    Fix: Added ``format_genre_tag()`` to all prompt templates so the LLM
    sees genre context like ``[Drama, War]`` alongside each item.
    """

    def test_band_of_brothers_has_genre_context_regression(self) -> None:
        """Regression: Band of Brothers should include drama/war genre tags.

        Bug reported: LLM described Band of Brothers as "hilarious" because
        the prompt had only the title and a 5/5 rating — no genre metadata
        to indicate it is a WWII drama miniseries.
        """
        band_of_brothers = make_item(
            "Band of Brothers",
            ContentType.TV_SHOW,
            rating=5,
            metadata={"genres": ["Drama", "War", "History"]},
        )
        unconsumed = _make_unconsumed(3, content_type=ContentType.TV_SHOW)

        prompt = build_recommendation_prompt(
            content_type=ContentType.TV_SHOW,
            consumed_items=[band_of_brothers],
            unconsumed_items=unconsumed,
        )

        # The LLM must see genre context to avoid calling it "hilarious"
        assert "[Drama, War, History]" in prompt
        assert "Band of Brothers" in prompt


# ===========================================================================
# Cross-type review leaking regression tests
# ===========================================================================


class TestCrossTypeReviewLeakingRegression:
    """Regression tests for cross-content-type review leaking.

    Bug reported: Video game reviews/ratings appeared in movie recommendation
    blurbs because build_blurb_prompt() showed favorites from ALL content types
    without separation. The user rated video games highly but hadn't reviewed
    movies — the LLM referenced game ratings when writing movie blurbs.

    Root cause: build_blurb_prompt() lumped all favorites together with [type]
    labels but no separation. build_recommendation_prompt() included review
    text for cross-type items, giving the LLM wrong-type review content.

    Fix: Applied same content-type separation logic to build_blurb_prompt()
    (same-type vs cross-type grouping). Stripped review_text from cross-type
    items in build_recommendation_prompt().
    """

    def test_blurb_excludes_cross_type_when_enough_same_type_regression(
        self,
    ) -> None:
        """Regression: blurb should only show same-type favorites when >= 5 exist."""
        movies = _make_movies(6)
        games = [
            make_item(f"Game {index}", ContentType.VIDEO_GAME, rating=5)
            for index in range(4)
        ]
        consumed = movies + games
        selected = _make_movies(2)

        prompt = build_blurb_prompt(
            content_type=ContentType.MOVIE,
            selected_items=selected,
            consumed_items=consumed,
        )

        # Exactly 5 of the 6 movies should appear (capped at 5, order may vary
        # due to same-rating tier shuffling for variety)
        movie_count = sum(1 for movie in movies if f"**{movie.title}**" in prompt)
        assert movie_count == 5
        # Games should NOT appear
        for game in games:
            assert game.title not in prompt
        assert "From other types" not in prompt

    def test_blurb_includes_cross_type_in_separate_section_regression(
        self,
    ) -> None:
        """Regression: blurb should show cross-type in separate section when < 5 same-type."""
        movies = _make_movies(2)
        games = [
            make_item(f"Game {index}", ContentType.VIDEO_GAME, rating=5)
            for index in range(3)
        ]
        consumed = movies + games
        selected = _make_movies(2)

        prompt = build_blurb_prompt(
            content_type=ContentType.MOVIE,
            selected_items=selected,
            consumed_items=consumed,
        )

        assert "Their favorite movies:" in prompt
        assert "From other types:" in prompt
        # Movies in same-type section without type label prefix
        for movie in movies:
            assert f"[movie] **{movie.title}**" not in prompt
            assert f"**{movie.title}**" in prompt
        # Games in cross-type section with label
        for game in games:
            assert f"[video game] **{game.title}**" in prompt

    def test_recommendation_cross_type_items_have_no_review_text_regression(
        self,
    ) -> None:
        """Regression: cross-type items in recommendation prompt should NOT include review text."""
        book_with_review = make_item(
            "Great Book",
            ContentType.BOOK,
            rating=5,
            review="This book changed my life!",
        )
        unconsumed = _make_unconsumed(3, content_type=ContentType.MOVIE)

        prompt = build_recommendation_prompt(
            content_type=ContentType.MOVIE,
            consumed_items=[book_with_review],
            unconsumed_items=unconsumed,
        )

        # The book title should appear (cross-type fill)
        assert "Great Book" in prompt
        # But its review should NOT — prevents review misattribution
        assert "This book changed my life!" not in prompt
        assert "Review:" not in prompt

    def test_recommendation_same_type_items_keep_review_text_regression(
        self,
    ) -> None:
        """Regression: same-type items in recommendation prompt should keep review text."""
        movie_with_review = make_item(
            "Great Movie",
            ContentType.MOVIE,
            rating=5,
            review="Absolutely stunning visuals!",
        )
        unconsumed = _make_unconsumed(3, content_type=ContentType.MOVIE)

        prompt = build_recommendation_prompt(
            content_type=ContentType.MOVIE,
            consumed_items=[movie_with_review],
            unconsumed_items=unconsumed,
        )

        assert "Great Movie" in prompt
        assert 'Review: "Absolutely stunning visuals!"' in prompt


# ===========================================================================
# Spoiler prevention regression tests
# ===========================================================================


class TestSpoilerPreventionRegression:
    """Regression tests for spoiler prevention.

    Bug reported: The LLM revealed plot endings/twists ("the ending that
    pays homage to Beauty and the Beast"). No anti-spoiler instructions
    existed anywhere in the prompts.

    Root cause: The prompt system contained rules about hallucinating
    reviews and fabricating user quotes, but nothing prevented the LLM
    from discussing the content of the recommended items themselves.
    Spoilers are not fabrications — the LLM knew the content — but they
    are harmful regardless.

    Fix: Added anti-spoiler rules to STYLE_RULES (inherited by
    build_recommendation_system_prompt and FULL_SYSTEM_PROMPT),
    build_blurb_system_prompt, the build_blurb_prompt user prompt,
    and COMPACT_SYSTEM_PROMPT directly.
    """

    def test_style_rules_contain_anti_spoiler(self) -> None:
        """STYLE_RULES should contain anti-spoiler instruction."""
        assert "NEVER reveal plot twists" in STYLE_RULES

    def test_recommendation_system_prompt_has_anti_spoiler(self) -> None:
        """Recommendation system prompt should have anti-spoiler (via STYLE_RULES)."""
        system_prompt = build_recommendation_system_prompt(ContentType.BOOK)
        assert "NEVER reveal plot twists" in system_prompt

    def test_blurb_system_prompt_has_anti_spoiler(self) -> None:
        """Blurb system prompt should contain anti-spoiler instruction."""
        system_prompt = build_blurb_system_prompt(ContentType.MOVIE)
        assert "NEVER reveal plot twists" in system_prompt

    def test_blurb_user_prompt_has_anti_spoiler(self) -> None:
        """Blurb user prompt should contain anti-spoiler rule."""
        selected = _make_books(2)
        consumed = _make_books(3)

        prompt = build_blurb_prompt(
            content_type=ContentType.BOOK,
            selected_items=selected,
            consumed_items=consumed,
        )

        assert "Do NOT reveal plot twists" in prompt

    def test_compact_system_prompt_has_anti_spoiler(self) -> None:
        """COMPACT_SYSTEM_PROMPT should contain anti-spoiler instruction."""
        assert "NEVER reveal plot twists" in COMPACT_SYSTEM_PROMPT


# ===========================================================================
# Sentiment inference regression tests
# ===========================================================================


class TestRatingsRemovedFromPromptsRegression:
    """Regression: numeric ratings must NOT appear in recommendation/blurb prompts.

    Bug reported (round 7): Despite multiple anti-hallucination fixes, the
    LLM continued to fabricate ratings for recommended items (Dishonored 4/5,
    Dragon Age Origins 5/5, Eye of the Beholder 4/5) and parrot ratings for
    consumed items. The user does not want ratings re-exposed in blurbs.

    Root cause: Showing numeric ratings (5/5) in context items primed the LLM
    to attach ratings to everything, including recommendations it was inventing
    ratings for. The "mention titles and ratings" instruction actively
    encouraged this behavior.

    Fix: Removed all numeric ratings from prompt context items, Related lines,
    and instructions. The system still filters by rating >= 4 internally but
    presents items as "things they love" without numbers.

    Note: Conversation prompts (engine.py) intentionally retain ratings for
    the chat system's rating-prediction feature. This class covers only the
    recommendation and blurb prompt paths.
    """

    def test_consumed_items_have_no_rating_in_prompt_regression(self) -> None:
        """Regression: consumed items should NOT include (N/5) rating text."""
        consumed = _make_books(3)
        unconsumed = _make_unconsumed(5)

        prompt = build_recommendation_prompt(
            content_type=ContentType.BOOK,
            consumed_items=consumed,
            unconsumed_items=unconsumed,
        )

        assert "Book 0" in prompt
        assert "/5)" not in prompt

    def test_blurb_favorites_have_no_rating_regression(self) -> None:
        """Regression: blurb prompt favorites should NOT include (N/5) rating text."""
        selected = _make_books(2)
        consumed = _make_books(3)

        prompt = build_blurb_prompt(
            content_type=ContentType.BOOK,
            selected_items=selected,
            consumed_items=consumed,
        )

        assert "Book 0" in prompt
        assert "/5)" not in prompt

    def test_blurb_related_lines_have_no_rating_regression(self) -> None:
        """Regression: blurb Related: lines should NOT include (N/5) rating text."""
        ref = make_item("Ref Game", ContentType.VIDEO_GAME, rating=5)
        pick = make_item("New Game", ContentType.VIDEO_GAME)

        prompt = build_blurb_prompt(
            content_type=ContentType.VIDEO_GAME,
            selected_items=[pick],
            consumed_items=[ref],
            per_item_references=[[ref]],
        )

        assert "Related: Ref Game" in prompt
        assert "/5)" not in prompt

    def test_single_blurb_favorites_have_no_rating_regression(self) -> None:
        """Regression: single blurb favorites should NOT include (N/5) rating text."""
        item = make_item("New Book", ContentType.BOOK)
        consumed = _make_books(3)

        prompt = build_single_blurb_prompt(
            content_type=ContentType.BOOK,
            item=item,
            consumed_items=consumed,
        )

        assert "Book 0" in prompt
        assert "/5)" not in prompt

    def test_single_blurb_references_have_no_rating_regression(self) -> None:
        """Regression: single blurb Related: lines should NOT include (N/5)."""
        item = make_item("New Book", ContentType.BOOK)
        refs = [make_item("Ref A", ContentType.BOOK, rating=5)]

        prompt = build_single_blurb_prompt(
            content_type=ContentType.BOOK,
            item=item,
            consumed_items=[],
            references=refs,
        )

        assert "Related: Ref A" in prompt
        assert "/5)" not in prompt


# ===========================================================================
# Review misattribution regression tests
# ===========================================================================


class TestReviewMisattributionRegression:
    """Regression tests for review misattribution between items.

    Bug reported (round 5): The LLM attributed American Dirt's review
    ("a compelling read that I finished in 3 sessions") to Allanon's Quest.
    Both books appeared in the same-type favorites list with reviews on
    adjacent lines. The LLM grabbed the wrong review.

    Root cause: No explicit rule told the LLM that each review is tied to
    the item on the SAME line. The existing "ONLY quote reviews that appear
    above" rule prevented inventing reviews but not swapping them between
    items.

    Fix: Added explicit anti-misattribution rules to STYLE_RULES, the Data
    Accuracy section of build_recommendation_system_prompt, and the inline
    rules of build_recommendation_prompt and build_blurb_prompt.
    """

    def test_style_rules_contain_anti_misattribution(self) -> None:
        """STYLE_RULES should contain anti-misattribution instruction."""
        assert "belong to THAT item only" in STYLE_RULES

    def test_recommendation_system_prompt_has_anti_misattribution(self) -> None:
        """Recommendation system prompt should have anti-misattribution rule."""
        system_prompt = build_recommendation_system_prompt(ContentType.BOOK)
        assert "review belongs to the item on the same line" in system_prompt.lower()

    def test_recommendation_user_prompt_has_anti_misattribution(self) -> None:
        """Recommendation user prompt should have anti-misattribution rule."""
        consumed = _make_books(3)
        unconsumed = _make_unconsumed(5)

        prompt = build_recommendation_prompt(
            content_type=ContentType.BOOK,
            consumed_items=consumed,
            unconsumed_items=unconsumed,
        )

        assert "belongs to the item on the SAME line" in prompt

    def test_blurb_user_prompt_has_anti_misattribution(self) -> None:
        """Blurb user prompt should have anti-misattribution rule."""
        selected = _make_books(2)
        consumed = _make_books(3)

        prompt = build_blurb_prompt(
            content_type=ContentType.BOOK,
            selected_items=selected,
            consumed_items=consumed,
        )

        assert "belongs to the item on the SAME line" in prompt

    def test_blurb_system_prompt_has_anti_misattribution(self) -> None:
        """Blurb system prompt should have anti-misattribution instruction."""
        system_prompt = build_blurb_system_prompt(ContentType.BOOK)
        assert "NEVER misattribute reviews or authors" in system_prompt


# ===========================================================================
# Author hallucination regression tests
# ===========================================================================


class TestAuthorHallucinationRegression:
    """Regression tests for fabricated author connections.

    Bug reported (round 5): When recommending from Fire & Blood (George
    R.R. Martin, 5/5), the LLM described The Way of Kings (Brandon
    Sanderson) as "another of your author's works" and "inspired by George
    R.R. Martin's grandiose fantasy epics." The LLM fabricated an author
    connection that does not exist.

    Root cause: No explicit rule told the LLM that author names are distinct
    people and that shared authorship must not be claimed unless the names
    match exactly. The LLM treated two fantasy authors as interchangeable.

    Fix: Added explicit author-accuracy rules to STYLE_RULES, the Data
    Accuracy section of build_recommendation_system_prompt, and the inline
    rules of build_recommendation_prompt and build_blurb_prompt.
    """

    def test_style_rules_contain_author_accuracy(self) -> None:
        """STYLE_RULES should contain anti-misattribution covering authors."""
        assert "never attribute one item's details to another" in STYLE_RULES.lower()

    def test_recommendation_system_prompt_has_author_accuracy(self) -> None:
        """Recommendation system prompt should have author accuracy rule."""
        system_prompt = build_recommendation_system_prompt(ContentType.BOOK)
        assert "author names shown are identical" in system_prompt.lower()

    def test_recommendation_user_prompt_has_author_accuracy(self) -> None:
        """Recommendation user prompt should have author accuracy rule."""
        consumed = _make_books(3)
        unconsumed = _make_unconsumed(5)

        prompt = build_recommendation_prompt(
            content_type=ContentType.BOOK,
            consumed_items=consumed,
            unconsumed_items=unconsumed,
        )

        assert "do NOT claim two items share an author" in prompt

    def test_blurb_user_prompt_has_author_accuracy(self) -> None:
        """Blurb user prompt should have author accuracy rule."""
        selected = _make_books(2)
        consumed = _make_books(3)

        prompt = build_blurb_prompt(
            content_type=ContentType.BOOK,
            selected_items=selected,
            consumed_items=consumed,
        )

        assert "do NOT claim two items share an author" in prompt


# ===========================================================================
# General knowledge fabrication regression tests
# ===========================================================================


class TestGeneralKnowledgeFabricationRegression:
    """Regression tests for LLM using general knowledge to fabricate user opinions.

    Bug reported (round 6): The LLM described a user as "someone who found
    1984 to be a 'gut punch'" in a book recommendation, but the user's 1984
    entry had NO review. The phrase "gut punch" existed only in a video game
    review (Red Dead Redemption 2) that wasn't even in the recommendation
    context. The LLM fabricated the quote using its general knowledge of 1984
    as an emotionally impactful book.

    Root cause: Anti-hallucination rules prohibited inventing quotes and
    interpreting ratings as emotions, but did not explicitly prohibit using
    general knowledge to fabricate what someone thought or felt about an item.

    Fix (round 6): Added explicit "do NOT use general knowledge to fabricate"
    rules to all prompt paths.

    Fix (round 7): Consolidated into broader "do NOT invent quotes, opinions,
    or facts about items" rule which covers general knowledge fabrication.
    Conversation prompts retain the explicit general-knowledge wording.
    """

    def test_style_rules_ban_fabrication_regression(self) -> None:
        """STYLE_RULES should ban inventing facts (covers general knowledge fabrication)."""
        assert "NEVER invent quotes, opinions, or facts" in STYLE_RULES

    def test_recommendation_system_prompt_bans_fabrication_regression(
        self,
    ) -> None:
        """Recommendation system prompt should ban fabrication."""
        system_prompt = build_recommendation_system_prompt(ContentType.BOOK)
        assert "do NOT invent quotes, opinions, or facts" in system_prompt

    def test_recommendation_user_prompt_bans_fabrication_regression(
        self,
    ) -> None:
        """Recommendation user prompt should ban fabrication."""
        consumed = _make_books(3)
        unconsumed = _make_unconsumed(5)

        prompt = build_recommendation_prompt(
            content_type=ContentType.BOOK,
            consumed_items=consumed,
            unconsumed_items=unconsumed,
        )

        assert "do NOT invent quotes, opinions, or facts" in prompt

    def test_blurb_system_prompt_bans_fabrication_regression(self) -> None:
        """Blurb system prompt should ban fabrication."""
        system_prompt = build_blurb_system_prompt(ContentType.BOOK)
        assert "NEVER invent quotes, opinions, or facts" in system_prompt

    def test_blurb_user_prompt_bans_fabrication_regression(self) -> None:
        """Blurb user prompt should ban fabrication."""
        selected = _make_books(2)
        consumed = _make_books(3)

        prompt = build_blurb_prompt(
            content_type=ContentType.BOOK,
            selected_items=selected,
            consumed_items=consumed,
        )

        assert "do NOT invent quotes, opinions, or facts" in prompt

    def test_conversation_full_prompt_bans_general_knowledge_regression(self) -> None:
        """FULL_SYSTEM_PROMPT should ban general knowledge fabrication."""
        assert "general knowledge to fabricate" in FULL_SYSTEM_PROMPT.lower()

    def test_conversation_compact_prompt_bans_general_knowledge_regression(
        self,
    ) -> None:
        """COMPACT_SYSTEM_PROMPT should ban general knowledge fabrication."""
        assert "general knowledge to fabricate" in COMPACT_SYSTEM_PROMPT.lower()


# ===========================================================================
# Cross-type review transfer regression tests
# ===========================================================================


class TestCrossTypeReviewTransferRegression:
    """Regression tests for review language transferring across content types.

    Bug reported (round 6): A book recommendation described the user as
    "someone who found 1984 to be a 'gut punch'" — but the phrase "gut punch"
    came from a video game review (Red Dead Redemption 2), not from the book.
    The conversation context showed both items' reviews, and the LLM
    transferred the game review's language to the book recommendation.

    Root cause: The conversation system's context block shows reviews from
    ALL content types. When a video game review says "gut punch" and a book
    has no review, the LLM borrows the game review's phrasing and applies it
    to the book because both appear in the same context window.

    Fix: Added explicit cross-type review transfer rules to FULL_SYSTEM_PROMPT
    and COMPACT_SYSTEM_PROMPT: "A review written for one content type belongs
    to THAT item only — do NOT transfer review language or sentiments from a
    [Video Game] review to describe a [Book], or vice versa."
    """

    def test_full_prompt_bans_cross_type_review_transfer_regression(self) -> None:
        """FULL_SYSTEM_PROMPT should ban transferring reviews across content types."""
        assert "transfer review language" in FULL_SYSTEM_PROMPT.lower()

    def test_compact_prompt_bans_cross_type_review_transfer_regression(self) -> None:
        """COMPACT_SYSTEM_PROMPT should ban transferring reviews across content types."""
        assert "transfer review language" in COMPACT_SYSTEM_PROMPT.lower()


class TestPerItemReferencesRegression:
    """Regression tests for cross-genre comparison bug in blurb prompts.

    Bug reported: The LLM generated a blurb for Middle Earth: Shadow of War
    that compared it to Forza Horizon 4's "visceral action" — a racing game
    with no genre overlap. This happened because build_blurb_prompt() picked
    the first 5 high-rated favorites regardless of genre relevance, so the
    LLM connected Middle Earth to whichever favorites appeared first.

    Root cause: _find_contributing_reference_items() already computes
    genre-relevant references per candidate, but _enhance_with_llm() passed
    ALL consumed items to generate_blurbs(), and build_blurb_prompt()
    ignored per-item context entirely.

    Fix: Thread per_item_references from the engine through generate_blurbs()
    to build_blurb_prompt(), which now emits "Related:" lines after each pick
    so the LLM only draws comparisons to genuinely related items.
    """

    def test_related_line_includes_correct_refs_excludes_unrelated_regression(
        self,
    ) -> None:
        """Per-item references produce a Related line with correct items only.

        The Related line for an action RPG must include action/adventure
        references and must NOT include the racing game (Forza), even though
        Forza is a consumed favorite.
        """
        action_pick = make_item(
            "Middle Earth: Shadow of War",
            ContentType.VIDEO_GAME,
            metadata={"genres": ["action", "adventure"]},
        )
        ref_action_1 = make_item(
            "The Last of Us: Part 1",
            ContentType.VIDEO_GAME,
            rating=5,
            metadata={"genres": ["action", "adventure"]},
        )
        ref_action_2 = make_item(
            "God of War",
            ContentType.VIDEO_GAME,
            rating=5,
            metadata={"genres": ["action", "adventure"]},
        )
        forza = make_item(
            "Forza Horizon 4",
            ContentType.VIDEO_GAME,
            rating=5,
            metadata={"genres": ["racing"]},
        )

        consumed = [ref_action_1, ref_action_2, forza]
        per_item_refs = [[ref_action_1, ref_action_2]]

        prompt = build_blurb_prompt(
            content_type=ContentType.VIDEO_GAME,
            selected_items=[action_pick],
            consumed_items=consumed,
            per_item_references=per_item_refs,
        )

        # Related line now includes genre tags for accuracy
        assert "Related: The Last of Us: Part 1 [action, adventure]" in prompt
        assert "God of War [action, adventure]" in prompt
        related_line = next(
            line for line in prompt.split("\n") if line.strip().startswith("Related:")
        )
        assert "Forza" not in related_line

    def test_no_per_item_references_backward_compatible_regression(self) -> None:
        """Without per_item_references, prompt should work as before (no Related lines)."""
        pick = make_item(
            "Some Game",
            ContentType.VIDEO_GAME,
        )
        consumed = [
            make_item(f"Fav {index}", ContentType.VIDEO_GAME, rating=5)
            for index in range(5)
        ]

        prompt = build_blurb_prompt(
            content_type=ContentType.VIDEO_GAME,
            selected_items=[pick],
            consumed_items=consumed,
        )

        assert "Related:" not in prompt

    def test_empty_references_list_no_related_line_regression(self) -> None:
        """When per_item_references contains an empty list, no Related line appears."""
        pick = make_item("Some Game", ContentType.VIDEO_GAME)
        consumed = [
            make_item(f"Fav {index}", ContentType.VIDEO_GAME, rating=5)
            for index in range(5)
        ]

        prompt = build_blurb_prompt(
            content_type=ContentType.VIDEO_GAME,
            selected_items=[pick],
            consumed_items=consumed,
            per_item_references=[[]],
        )

        assert "Related:" not in prompt

    def test_prompt_rule_references_related_items_regression(self) -> None:
        """The prompt rule should direct the LLM to use Related items."""
        pick = make_item("Some Game", ContentType.VIDEO_GAME)
        ref = make_item("Ref Game", ContentType.VIDEO_GAME, rating=4)

        prompt = build_blurb_prompt(
            content_type=ContentType.VIDEO_GAME,
            selected_items=[pick],
            consumed_items=[ref],
            per_item_references=[[ref]],
        )

        assert "Related items when listed" in prompt

    def test_multiple_picks_each_get_own_related_line_regression(self) -> None:
        """Each pick gets its own Related line from its own references."""
        pick_action = make_item(
            "Action Game",
            ContentType.VIDEO_GAME,
            metadata={"genres": ["action"]},
        )
        pick_racing = make_item(
            "Racing Game",
            ContentType.VIDEO_GAME,
            metadata={"genres": ["racing"]},
        )
        ref_action = make_item("God of War", ContentType.VIDEO_GAME, rating=5)
        ref_racing = make_item("Gran Turismo", ContentType.VIDEO_GAME, rating=4)

        consumed = [ref_action, ref_racing]
        per_item_refs = [[ref_action], [ref_racing]]

        prompt = build_blurb_prompt(
            content_type=ContentType.VIDEO_GAME,
            selected_items=[pick_action, pick_racing],
            consumed_items=consumed,
            per_item_references=per_item_refs,
        )

        related_lines = [
            line.strip()
            for line in prompt.split("\n")
            if line.strip().startswith("Related:")
        ]
        assert (
            len(related_lines) == 2
        ), f"Expected 2 Related lines, found {len(related_lines)}: {related_lines}"
        assert (
            "God of War" in related_lines[0]
        ), f"Expected 'God of War' in first Related line, got: {related_lines[0]!r}"
        assert (
            "Gran Turismo" in related_lines[1]
        ), f"Expected 'Gran Turismo' in second Related line, got: {related_lines[1]!r}"


# ---------------------------------------------------------------------------
# build_single_blurb_prompt tests
# ---------------------------------------------------------------------------


class TestBuildSingleBlurbPrompt:
    """Tests for the single-item blurb prompt builder."""

    def test_includes_item_title_and_author(self) -> None:
        """Prompt includes the pick's title and author."""
        item = make_item("Dune", ContentType.BOOK, author="Frank Herbert")
        prompt = build_single_blurb_prompt(
            content_type=ContentType.BOOK,
            item=item,
            consumed_items=[],
        )
        assert "Pick: Dune by Frank Herbert" in prompt

    def test_no_numbered_list_instruction(self) -> None:
        """Prompt instructs raw prose, not a numbered list."""
        item = make_item("Dune", ContentType.BOOK)
        prompt = build_single_blurb_prompt(
            content_type=ContentType.BOOK,
            item=item,
            consumed_items=[],
        )
        assert "no title, no numbered list" in prompt

    def test_taste_context_from_same_type_favorites(self) -> None:
        """High-rated same-type items appear as taste context."""
        favorites = _make_books(3, rating=5)
        item = make_item("New Book", ContentType.BOOK)
        prompt = build_single_blurb_prompt(
            content_type=ContentType.BOOK,
            item=item,
            consumed_items=favorites,
        )
        assert "Their favorite books:" in prompt
        for fav in favorites:
            assert fav.title in prompt

    def test_cross_type_context_fills_remaining_slots(self) -> None:
        """Cross-type favorites fill remaining slots when < 5 same-type."""
        same = [make_item("Book 1", ContentType.BOOK, rating=5, author="A1")]
        cross = [
            make_item("Game 1", ContentType.VIDEO_GAME, rating=5),
            make_item("Movie 1", ContentType.MOVIE, rating=4),
        ]
        item = make_item("New Book", ContentType.BOOK)
        prompt = build_single_blurb_prompt(
            content_type=ContentType.BOOK,
            item=item,
            consumed_items=same + cross,
        )
        assert "From other types:" in prompt
        assert "Game 1" in prompt
        assert "Movie 1" in prompt

    def test_references_appear_as_related(self) -> None:
        """References without genre metadata render as plain titles in Related line."""
        item = make_item("New Book", ContentType.BOOK)
        refs = [
            make_item("Ref A", ContentType.BOOK, rating=5),
            make_item("Ref B", ContentType.BOOK, rating=4),
        ]
        prompt = build_single_blurb_prompt(
            content_type=ContentType.BOOK,
            item=item,
            consumed_items=[],
            references=refs,
        )
        # No genres on these refs, so no brackets appended
        assert "Related: Ref A, Ref B" in prompt

    def test_no_references_omits_related_line(self) -> None:
        """Without references, no Related line appears."""
        item = make_item("New Book", ContentType.BOOK)
        prompt = build_single_blurb_prompt(
            content_type=ContentType.BOOK,
            item=item,
            consumed_items=[],
            references=None,
        )
        assert "Related:" not in prompt

    def test_content_type_name_in_prompt(self) -> None:
        """Prompt uses the human-readable content type name."""
        item = make_item("Hades", ContentType.VIDEO_GAME)
        prompt = build_single_blurb_prompt(
            content_type=ContentType.VIDEO_GAME,
            item=item,
            consumed_items=[],
        )
        assert "video game pick" in prompt


# ---------------------------------------------------------------------------
# LLM guardrail tests (series names, genre accuracy, Related: tags, verbs)
# ---------------------------------------------------------------------------


class TestSeriesNameGuardrails:
    """Verify prompts instruct the LLM to use series names, not entry titles.

    Bug reported: Blurbs referred to "your favorite series, Harry Potter and
    the Order of the Phoenix" — treating an individual book title as the
    series name.  The series is "Harry Potter", not the individual entry.

    Fix: Favorites context now annotates series entries with "(Series Name
    series)" and prompt rules tell the LLM to use that series name.

    Note: Only blurb prompts are tested here.  Recommendation prompts
    (build_recommendation_prompt/system_prompt) do not include this
    guardrail because they select items, not write prose about them —
    series name confusion is a blurb-generation issue.
    """

    _SERIES_PHRASE = "series name"

    def test_blurb_system_prompt_has_series_guardrail(self) -> None:
        """Blurb system prompt instructs LLM to use series name."""
        prompt = build_blurb_system_prompt(ContentType.BOOK)
        assert self._SERIES_PHRASE in prompt.lower()

    def test_blurb_prompt_has_series_guardrail(self) -> None:
        """Blurb prompt rules include series name instruction."""
        selected = [make_item("Pick", ContentType.BOOK)]
        prompt = build_blurb_prompt(ContentType.BOOK, selected, consumed_items=[])
        assert self._SERIES_PHRASE in prompt.lower()

    def test_single_blurb_prompt_has_series_guardrail(self) -> None:
        """Single blurb prompt rules include series name instruction."""
        item = make_item("Pick", ContentType.BOOK)
        prompt = build_single_blurb_prompt(ContentType.BOOK, item, consumed_items=[])
        assert self._SERIES_PHRASE in prompt.lower()

    def test_series_annotation_appears_in_favorites_context(self) -> None:
        """Favorites with series metadata show '(Series series)' annotation."""
        favorites = [
            make_item(
                "Harry Potter and the Order of the Phoenix",
                ContentType.BOOK,
                rating=5,
                author="J.K. Rowling",
                metadata={"series_name": "Harry Potter", "series_number": 5},
            ),
        ]
        selected = [make_item("New Book", ContentType.BOOK)]
        prompt = build_blurb_prompt(
            ContentType.BOOK, selected, consumed_items=favorites
        )
        assert "(Harry Potter series)" in prompt

    def test_no_series_annotation_for_standalone_titles(self) -> None:
        """Standalone titles (no series metadata) get no annotation."""
        favorites = [
            make_item(
                "The Road",
                ContentType.BOOK,
                rating=5,
                author="Cormac McCarthy",
            ),
        ]
        selected = [make_item("New Book", ContentType.BOOK)]
        prompt = build_blurb_prompt(
            ContentType.BOOK, selected, consumed_items=favorites
        )
        assert "series)" not in prompt


class TestGenreAccuracyGuardrails:
    """Verify prompts instruct the LLM not to invent genre attributes.

    Bug reported: A blurb claimed the Shannara series (fantasy) involved
    "space warfare" when comparing it to a StarCraft recommendation.

    Fix: Prompt rules now explicitly forbid attributing genres, settings, or
    themes to referenced items that aren't listed in their genre brackets.

    Note: Only blurb prompts are tested here.  Recommendation prompts do not
    have Related: lines with genre brackets — they use a different format
    for candidates — so the genre-accuracy guardrail is blurb-specific.
    """

    _GENRE_PHRASE = "genre brackets"

    def test_blurb_system_prompt_has_genre_accuracy_guardrail(self) -> None:
        """Blurb system prompt warns against inventing genres."""
        prompt = build_blurb_system_prompt(ContentType.BOOK)
        assert self._GENRE_PHRASE in prompt.lower()

    def test_blurb_prompt_has_genre_accuracy_guardrail(self) -> None:
        """Blurb prompt rules include genre accuracy instruction."""
        selected = [make_item("Pick", ContentType.BOOK)]
        prompt = build_blurb_prompt(ContentType.BOOK, selected, consumed_items=[])
        assert self._GENRE_PHRASE in prompt.lower()

    def test_single_blurb_prompt_has_genre_accuracy_guardrail(self) -> None:
        """Single blurb prompt rules include genre accuracy instruction."""
        item = make_item("Pick", ContentType.BOOK)
        prompt = build_single_blurb_prompt(ContentType.BOOK, item, consumed_items=[])
        assert self._GENRE_PHRASE in prompt.lower()


class TestRelatedItemsIncludeGenreTags:
    """Verify Related: lines include genre tags so the LLM knows each
    reference's actual genre and doesn't invent settings or themes.

    Bug reported: A blurb claimed the Shannara series involved "space warfare"
    because the Related line only showed titles without genre context.
    """

    def test_blurb_prompt_related_line_includes_genres(self) -> None:
        """Related: line in build_blurb_prompt includes genre brackets."""
        selected = [make_item("Pick", ContentType.BOOK)]
        refs = [
            make_item(
                "Bloodfire Quest",
                ContentType.BOOK,
                rating=5,
                genres="Fantasy, Epic Fantasy",
            ),
        ]
        prompt = build_blurb_prompt(
            ContentType.BOOK,
            selected,
            consumed_items=[],
            per_item_references=[refs],
        )
        assert "Bloodfire Quest [Fantasy, Epic Fantasy]" in prompt

    def test_single_blurb_prompt_related_line_includes_genres(self) -> None:
        """Related: line in build_single_blurb_prompt includes genre brackets."""
        item = make_item("Pick", ContentType.BOOK)
        refs = [
            make_item(
                "The Black Elfstone",
                ContentType.BOOK,
                rating=5,
                genres="Fantasy, Epic Fantasy",
            ),
        ]
        prompt = build_single_blurb_prompt(
            ContentType.BOOK,
            item,
            consumed_items=[],
            references=refs,
        )
        assert "The Black Elfstone [Fantasy, Epic Fantasy]" in prompt


class TestAntiGenreTagInProseGuardrails:
    """Regression: genre tags like (Comedy) must not appear in blurb text.

    Bug reported: Blurbs included literal genre parentheticals in prose —
    e.g. 'You'll love "Legally Blonde" (Comedy)'.

    Fix: Prompt rules now forbid including genre tags in output text.
    """

    _RULE_PHRASE = "Do NOT include genre tags"

    def test_recommendation_prompt_has_anti_genre_tag_rule_regression(self) -> None:
        books = _make_books(3)
        unconsumed = _make_unconsumed(3)
        prompt = build_recommendation_prompt(ContentType.BOOK, books, unconsumed)
        assert self._RULE_PHRASE in prompt

    def test_recommendation_system_prompt_has_anti_genre_tag_rule_regression(
        self,
    ) -> None:
        prompt = build_recommendation_system_prompt(ContentType.BOOK)
        assert self._RULE_PHRASE in prompt

    def test_blurb_system_prompt_has_anti_genre_tag_rule_regression(self) -> None:
        prompt = build_blurb_system_prompt(ContentType.BOOK)
        assert self._RULE_PHRASE in prompt

    def test_blurb_prompt_has_anti_genre_tag_rule_regression(self) -> None:
        selected = [make_item("Pick", ContentType.BOOK)]
        prompt = build_blurb_prompt(ContentType.BOOK, selected, consumed_items=[])
        assert self._RULE_PHRASE in prompt

    def test_single_blurb_prompt_has_anti_genre_tag_rule_regression(self) -> None:
        item = make_item("Pick", ContentType.BOOK)
        prompt = build_single_blurb_prompt(ContentType.BOOK, item, consumed_items=[])
        assert self._RULE_PHRASE in prompt


class TestAntiCircularJustificationGuardrails:
    """Regression: LLM justified picks by citing user's experience with
    the pick's own series — circular reasoning.

    Bug reported: A Ranger's Apprentice recommendation said "just as you
    experienced in the (Ranger's Apprentice) series" — justifying a pick
    by referencing the user's experience with that same series.

    Fix: Prompt rules now forbid circular self-series justification.
    """

    _RULE_PHRASE = "circular"

    def test_recommendation_prompt_has_anti_circular_rule_regression(self) -> None:
        books = _make_books(3)
        unconsumed = _make_unconsumed(3)
        prompt = build_recommendation_prompt(ContentType.BOOK, books, unconsumed)
        assert self._RULE_PHRASE in prompt

    def test_recommendation_system_prompt_has_anti_circular_rule_regression(
        self,
    ) -> None:
        prompt = build_recommendation_system_prompt(ContentType.BOOK)
        assert self._RULE_PHRASE in prompt

    def test_blurb_system_prompt_has_anti_circular_rule_regression(self) -> None:
        prompt = build_blurb_system_prompt(ContentType.BOOK)
        assert self._RULE_PHRASE in prompt

    def test_blurb_prompt_has_anti_circular_rule_regression(self) -> None:
        selected = [make_item("Pick", ContentType.BOOK)]
        prompt = build_blurb_prompt(ContentType.BOOK, selected, consumed_items=[])
        assert self._RULE_PHRASE in prompt

    def test_single_blurb_prompt_has_anti_circular_rule_regression(self) -> None:
        item = make_item("Pick", ContentType.BOOK)
        prompt = build_single_blurb_prompt(ContentType.BOOK, item, consumed_items=[])
        assert self._RULE_PHRASE in prompt


class TestVarietyInstructionGuardrails:
    """Regression: blurbs opened with repetitive formulaic phrases.

    Bug reported: Three consecutive blurbs started with "You'll adore..."
    and nearly all used "much like" as the connector.

    Fix: Prompt rules now ban formulaic openers and repeated connectors.
    """

    _VARIETY_PHRASES = [
        "You'll adore",
        "You'll love",
        "If you enjoyed",
    ]

    def test_recommendation_prompt_has_variety_instruction_regression(self) -> None:
        books = _make_books(3)
        unconsumed = _make_unconsumed(3)
        prompt = build_recommendation_prompt(ContentType.BOOK, books, unconsumed)
        for phrase in self._VARIETY_PHRASES:
            assert phrase in prompt, f"Missing variety phrase: {phrase!r}"

    def test_blurb_system_prompt_has_variety_instruction_regression(self) -> None:
        prompt = build_blurb_system_prompt(ContentType.BOOK)
        for phrase in self._VARIETY_PHRASES:
            assert phrase in prompt, f"Missing variety phrase: {phrase!r}"

    def test_blurb_prompt_has_variety_instruction_regression(self) -> None:
        selected = [make_item("Pick", ContentType.BOOK)]
        prompt = build_blurb_prompt(ContentType.BOOK, selected, consumed_items=[])
        for phrase in self._VARIETY_PHRASES:
            assert phrase in prompt, f"Missing variety phrase: {phrase!r}"

    def test_single_blurb_prompt_has_variety_instruction_regression(self) -> None:
        item = make_item("Pick", ContentType.VIDEO_GAME)
        prompt = build_single_blurb_prompt(
            ContentType.VIDEO_GAME, item, consumed_items=[]
        )
        for phrase in self._VARIETY_PHRASES:
            assert phrase in prompt, f"Missing variety phrase: {phrase!r}"


class TestVerbGuardrails:
    """Verify all prompt functions include content-type verb guidance.

    Prevents the regression where blurbs used "watching" for video games.
    See TestVerbConfusionRegression in test_bug_regressions.py for the
    original incident.
    """

    _VERB_PHRASES = [
        "READ books",
        "WATCH movies and TV shows",
        "PLAY video games",
    ]

    def test_recommendation_prompt_has_verb_guidance(self) -> None:
        """build_recommendation_prompt includes verb guidance."""
        books = _make_books(3)
        unconsumed = _make_unconsumed(3)
        prompt = build_recommendation_prompt(ContentType.BOOK, books, unconsumed)
        for phrase in self._VERB_PHRASES:
            assert phrase in prompt

    def test_recommendation_system_prompt_has_verb_guidance(self) -> None:
        """build_recommendation_system_prompt includes verb guidance."""
        prompt = build_recommendation_system_prompt(ContentType.BOOK)
        for phrase in self._VERB_PHRASES:
            assert phrase in prompt

    def test_blurb_system_prompt_has_verb_guidance(self) -> None:
        """build_blurb_system_prompt includes verb guidance."""
        prompt = build_blurb_system_prompt(ContentType.MOVIE)
        for phrase in self._VERB_PHRASES:
            assert phrase in prompt

    def test_blurb_prompt_has_verb_guidance(self) -> None:
        """build_blurb_prompt includes verb guidance."""
        selected = [make_item("Pick", ContentType.BOOK)]
        prompt = build_blurb_prompt(ContentType.BOOK, selected, consumed_items=[])
        for phrase in self._VERB_PHRASES:
            assert phrase in prompt

    def test_single_blurb_prompt_has_verb_guidance(self) -> None:
        """build_single_blurb_prompt includes verb guidance."""
        item = make_item("Pick", ContentType.VIDEO_GAME)
        prompt = build_single_blurb_prompt(
            ContentType.VIDEO_GAME, item, consumed_items=[]
        )
        for phrase in self._VERB_PHRASES:
            assert phrase in prompt
