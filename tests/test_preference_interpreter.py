"""Tests for the pattern-based preference interpreter."""

import pytest

from src.recommendations.preference_interpreter import (
    GENRE_ALIASES,
    InterpretedPreference,
    PatternBasedInterpreter,
    PatternConfidence,
    _normalize_content_type,
    _normalize_genre,
    _normalize_length,
)


class TestNormalizeGenre:
    """Tests for genre normalization."""

    def test_canonical_genre_unchanged(self) -> None:
        """Canonical genre names should remain unchanged."""
        assert _normalize_genre("science fiction") == "science fiction"
        assert _normalize_genre("horror") == "horror"
        assert _normalize_genre("fantasy") == "fantasy"

    def test_alias_mapped_to_canonical(self) -> None:
        """Aliases should map to their canonical form."""
        assert _normalize_genre("sci-fi") == "science fiction"
        assert _normalize_genre("scifi") == "science fiction"
        assert _normalize_genre("sf") == "science fiction"

    def test_case_insensitive(self) -> None:
        """Genre normalization should be case-insensitive."""
        assert _normalize_genre("SCI-FI") == "science fiction"
        assert _normalize_genre("Horror") == "horror"
        assert _normalize_genre("FANTASY") == "fantasy"

    def test_unknown_genre_returned_as_is(self) -> None:
        """Unknown genres should be returned lowercased."""
        assert _normalize_genre("obscure genre") == "obscure genre"
        assert _normalize_genre("CUSTOM GENRE") == "custom genre"

    def test_whitespace_handling(self) -> None:
        """Leading/trailing whitespace should be stripped."""
        assert _normalize_genre("  horror  ") == "horror"
        assert _normalize_genre("  sci-fi  ") == "science fiction"


class TestNormalizeContentType:
    """Tests for content type normalization."""

    def test_canonical_type_unchanged(self) -> None:
        """Canonical content types should remain unchanged."""
        assert _normalize_content_type("book") == "book"
        assert _normalize_content_type("movie") == "movie"
        assert _normalize_content_type("tv_show") == "tv_show"
        assert _normalize_content_type("video_game") == "video_game"

    def test_alias_mapped_to_canonical(self) -> None:
        """Aliases should map to their canonical form."""
        assert _normalize_content_type("books") == "book"
        assert _normalize_content_type("movies") == "movie"
        assert _normalize_content_type("film") == "movie"
        assert _normalize_content_type("tv") == "tv_show"
        assert _normalize_content_type("games") == "video_game"

    def test_case_insensitive(self) -> None:
        """Content type normalization should be case-insensitive."""
        assert _normalize_content_type("BOOK") == "book"
        assert _normalize_content_type("Movies") == "movie"

    def test_unknown_type_returns_none(self) -> None:
        """Unknown content types should return None."""
        assert _normalize_content_type("unknown") is None
        assert _normalize_content_type("random") is None


class TestNormalizeLength:
    """Tests for length preference normalization."""

    def test_canonical_length_unchanged(self) -> None:
        """Canonical lengths should remain unchanged."""
        assert _normalize_length("short") == "short"
        assert _normalize_length("medium") == "medium"
        assert _normalize_length("long") == "long"

    def test_alias_mapped_to_canonical(self) -> None:
        """Aliases should map to their canonical form."""
        assert _normalize_length("quick") == "short"
        assert _normalize_length("brief") == "short"
        assert _normalize_length("lengthy") == "long"
        assert _normalize_length("epic") == "long"
        assert _normalize_length("moderate") == "medium"

    def test_unknown_length_returns_none(self) -> None:
        """Unknown lengths should return None."""
        assert _normalize_length("unknown") is None


class TestInterpretedPreference:
    """Tests for the InterpretedPreference dataclass."""

    def test_is_empty_when_no_preferences(self) -> None:
        """is_empty should return True when no preferences set."""
        pref = InterpretedPreference()
        assert pref.is_empty()

    def test_is_empty_false_with_genre_boosts(self) -> None:
        """is_empty should return False with genre boosts."""
        pref = InterpretedPreference(genre_boosts={"horror": 1.0})
        assert not pref.is_empty()

    def test_is_empty_false_with_genre_penalties(self) -> None:
        """is_empty should return False with genre penalties."""
        pref = InterpretedPreference(genre_penalties={"horror": 1.0})
        assert not pref.is_empty()

    def test_is_empty_false_with_content_type_filters(self) -> None:
        """is_empty should return False with content type filters."""
        pref = InterpretedPreference(content_type_filters={"book"})
        assert not pref.is_empty()

    def test_merge_combines_genre_boosts(self) -> None:
        """Merge should combine genre boosts from both preferences."""
        pref1 = InterpretedPreference(genre_boosts={"horror": 0.8})
        pref2 = InterpretedPreference(genre_boosts={"comedy": 0.9})
        merged = pref1.merge_with(pref2)
        assert merged.genre_boosts == {"horror": 0.8, "comedy": 0.9}

    def test_merge_later_takes_precedence(self) -> None:
        """Later preferences should override earlier ones for same key."""
        pref1 = InterpretedPreference(genre_boosts={"horror": 0.5})
        pref2 = InterpretedPreference(genre_boosts={"horror": 1.0})
        merged = pref1.merge_with(pref2)
        assert merged.genre_boosts == {"horror": 1.0}

    def test_merge_combines_content_type_filters(self) -> None:
        """Merge should union content type filters."""
        pref1 = InterpretedPreference(content_type_filters={"book"})
        pref2 = InterpretedPreference(content_type_filters={"movie"})
        merged = pref1.merge_with(pref2)
        assert merged.content_type_filters == {"book", "movie"}

    def test_merge_uses_lower_confidence(self) -> None:
        """Merge should use the lower confidence level."""
        pref1 = InterpretedPreference(confidence=PatternConfidence.HIGH)
        pref2 = InterpretedPreference(confidence=PatternConfidence.MEDIUM)
        merged = pref1.merge_with(pref2)
        assert merged.confidence == PatternConfidence.MEDIUM


class TestPatternBasedInterpreter:
    """Tests for the PatternBasedInterpreter class."""

    @pytest.fixture
    def interpreter(self) -> PatternBasedInterpreter:
        """Create a fresh interpreter for each test."""
        return PatternBasedInterpreter()

    # --- Avoid/Penalty patterns ---

    def test_avoid_genre(self, interpreter: PatternBasedInterpreter) -> None:
        """'avoid X' should create a genre penalty."""
        result = interpreter.interpret("avoid horror")
        assert "horror" in result.genre_penalties
        assert result.confidence == PatternConfidence.HIGH

    def test_no_genre(self, interpreter: PatternBasedInterpreter) -> None:
        """'no X' should create a genre penalty."""
        result = interpreter.interpret("no horror")
        assert "horror" in result.genre_penalties
        assert result.confidence == PatternConfidence.HIGH

    def test_skip_genre(self, interpreter: PatternBasedInterpreter) -> None:
        """'skip X' should create a genre penalty."""
        result = interpreter.interpret("skip romance")
        assert "romance" in result.genre_penalties

    def test_dont_want_genre(self, interpreter: PatternBasedInterpreter) -> None:
        """'don't want X' should create a genre penalty."""
        result = interpreter.interpret("don't want horror")
        assert "horror" in result.genre_penalties

    def test_hate_genre(self, interpreter: PatternBasedInterpreter) -> None:
        """'hate X' should create a genre penalty."""
        result = interpreter.interpret("I hate horror")
        assert "horror" in result.genre_penalties
        assert result.confidence == PatternConfidence.HIGH

    def test_tired_of_genre(self, interpreter: PatternBasedInterpreter) -> None:
        """'tired of X' should create a genre penalty."""
        result = interpreter.interpret("tired of sci-fi")
        assert "science fiction" in result.genre_penalties

    def test_burnt_out_on_genre(self, interpreter: PatternBasedInterpreter) -> None:
        """'burnt out on X' should create a genre penalty."""
        result = interpreter.interpret("burnt out on fantasy")
        assert "fantasy" in result.genre_penalties

    # --- Prefer/Boost patterns ---

    def test_prefer_genre(self, interpreter: PatternBasedInterpreter) -> None:
        """'prefer X' should create a genre boost."""
        result = interpreter.interpret("prefer sci-fi")
        assert "science fiction" in result.genre_boosts
        assert result.confidence == PatternConfidence.HIGH

    def test_love_genre(self, interpreter: PatternBasedInterpreter) -> None:
        """'love X' should create a genre boost."""
        result = interpreter.interpret("I love horror")
        assert "horror" in result.genre_boosts
        assert result.confidence == PatternConfidence.HIGH

    def test_more_of_genre(self, interpreter: PatternBasedInterpreter) -> None:
        """'more of X' should create a genre boost."""
        result = interpreter.interpret("want more of fantasy")
        assert "fantasy" in result.genre_boosts

    def test_give_me_genre(self, interpreter: PatternBasedInterpreter) -> None:
        """'give me X' should create a genre boost."""
        result = interpreter.interpret("give me more comedy")
        assert "comedy" in result.genre_boosts

    def test_in_the_mood_for(self, interpreter: PatternBasedInterpreter) -> None:
        """'in the mood for X' should create a genre boost."""
        result = interpreter.interpret("in the mood for mystery")
        assert "mystery" in result.genre_boosts

    # --- Content type filters ---

    def test_only_books(self, interpreter: PatternBasedInterpreter) -> None:
        """'only books' should filter to book content type."""
        result = interpreter.interpret("only books")
        assert "book" in result.content_type_filters
        assert result.confidence == PatternConfidence.HIGH

    def test_only_movies(self, interpreter: PatternBasedInterpreter) -> None:
        """'only movies' should filter to movie content type."""
        result = interpreter.interpret("only movies")
        assert "movie" in result.content_type_filters

    def test_only_tv(self, interpreter: PatternBasedInterpreter) -> None:
        """'only tv' should filter to tv_show content type."""
        result = interpreter.interpret("only tv")
        assert "tv_show" in result.content_type_filters

    def test_just_games(self, interpreter: PatternBasedInterpreter) -> None:
        """'just games' should filter to video_game content type."""
        result = interpreter.interpret("just games")
        assert "video_game" in result.content_type_filters

    def test_no_movies_creates_exclusion(
        self, interpreter: PatternBasedInterpreter
    ) -> None:
        """'no movies' should exclude movie content type."""
        result = interpreter.interpret("no movies")
        assert "movie" in result.content_type_exclusions

    # --- Length preferences ---

    def test_short_books(self, interpreter: PatternBasedInterpreter) -> None:
        """'short books' should set length preference."""
        result = interpreter.interpret("short books")
        assert result.length_preferences.get("book") == "short"

    def test_long_movies(self, interpreter: PatternBasedInterpreter) -> None:
        """'long movies' should set length preference."""
        result = interpreter.interpret("long movies")
        assert result.length_preferences.get("movie") == "long"

    def test_quick_games(self, interpreter: PatternBasedInterpreter) -> None:
        """'quick games' should set short length preference."""
        result = interpreter.interpret("quick games")
        assert result.length_preferences.get("video_game") == "short"

    def test_epic_books(self, interpreter: PatternBasedInterpreter) -> None:
        """'epic books' should set long length preference."""
        result = interpreter.interpret("epic books")
        assert result.length_preferences.get("book") == "long"

    # --- Genre aliases ---

    def test_scifi_normalized_to_science_fiction(
        self, interpreter: PatternBasedInterpreter
    ) -> None:
        """Sci-fi aliases should normalize to 'science fiction'."""
        result = interpreter.interpret("prefer scifi")
        assert "science fiction" in result.genre_boosts

        result2 = interpreter.interpret("avoid sf")
        assert "science fiction" in result2.genre_penalties

    # --- Edge cases ---

    def test_empty_rule(self, interpreter: PatternBasedInterpreter) -> None:
        """Empty rules should return empty result with no confidence."""
        result = interpreter.interpret("")
        assert result.is_empty()
        assert result.confidence == PatternConfidence.NONE

    def test_unrecognized_rule(self, interpreter: PatternBasedInterpreter) -> None:
        """Unrecognized rules should return empty result."""
        result = interpreter.interpret("random gibberish that matches nothing")
        assert result.is_empty()
        assert result.confidence == PatternConfidence.NONE

    def test_punctuation_stripped(self, interpreter: PatternBasedInterpreter) -> None:
        """Trailing punctuation should be stripped from extracted values."""
        result = interpreter.interpret("avoid horror!")
        assert "horror" in result.genre_penalties

        result2 = interpreter.interpret("prefer sci-fi.")
        assert "science fiction" in result2.genre_boosts

    def test_case_insensitive_patterns(
        self, interpreter: PatternBasedInterpreter
    ) -> None:
        """Patterns should match case-insensitively."""
        result = interpreter.interpret("AVOID HORROR")
        assert "horror" in result.genre_penalties

        result2 = interpreter.interpret("Prefer Sci-Fi")
        assert "science fiction" in result2.genre_boosts

    # --- Multiple rules ---

    def test_interpret_all_merges_rules(
        self, interpreter: PatternBasedInterpreter
    ) -> None:
        """interpret_all should merge multiple rules."""
        rules = ["avoid horror", "prefer comedy", "only books"]
        result = interpreter.interpret_all(rules)

        assert "horror" in result.genre_penalties
        assert "comedy" in result.genre_boosts
        assert "book" in result.content_type_filters

    def test_interpret_all_empty_list(
        self, interpreter: PatternBasedInterpreter
    ) -> None:
        """interpret_all with empty list should return empty result."""
        result = interpreter.interpret_all([])
        assert result.is_empty()
        assert result.confidence == PatternConfidence.NONE

    def test_interpret_all_combines_original_rules(
        self, interpreter: PatternBasedInterpreter
    ) -> None:
        """interpret_all should combine original rule text."""
        rules = ["avoid horror", "prefer comedy"]
        result = interpreter.interpret_all(rules)
        assert "horror" in result.original_rule
        assert "comedy" in result.original_rule


class TestGenreAliasesCoverage:
    """Tests to ensure genre aliases are reasonable."""

    def test_all_aliases_are_lowercase(self) -> None:
        """All genre aliases should be lowercase."""
        for canonical, aliases in GENRE_ALIASES.items():
            assert (
                canonical == canonical.lower()
            ), f"Canonical '{canonical}' not lowercase"
            for alias in aliases:
                assert alias == alias.lower(), f"Alias '{alias}' not lowercase"

    def test_no_duplicate_aliases(self) -> None:
        """No alias should appear in multiple canonical groups."""
        all_aliases: dict[str, str] = {}
        for canonical, aliases in GENRE_ALIASES.items():
            for alias in aliases:
                if alias in all_aliases:
                    pytest.fail(
                        f"Alias '{alias}' appears in both "
                        f"'{all_aliases[alias]}' and '{canonical}'"
                    )
                all_aliases[alias] = canonical
