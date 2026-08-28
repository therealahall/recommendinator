import pytest

from src.recommendations.preference_interpreter import (
    InterpretedPreference,
    PatternBasedInterpreter,
    PatternConfidence,
    _normalize_content_type,
    _normalize_genre,
    _normalize_length,
)


class TestNormalizeGenre:
    def test_alias_mapped_to_canonical(self) -> None:
        assert _normalize_genre("sci-fi") == "science fiction"
        assert _normalize_genre("scifi") == "science fiction"
        assert _normalize_genre("sf") == "science fiction"

    def test_unknown_genre_returned_as_is(self) -> None:
        assert _normalize_genre("obscure genre") == "obscure genre"
        assert _normalize_genre("CUSTOM GENRE") == "custom genre"


class TestNormalizeContentType:
    def test_alias_mapped_to_canonical(self) -> None:
        assert _normalize_content_type("books") == "book"
        assert _normalize_content_type("movies") == "movie"
        assert _normalize_content_type("film") == "movie"
        assert _normalize_content_type("tv") == "tv_show"
        assert _normalize_content_type("games") == "video_game"

    def test_unknown_type_returns_none(self) -> None:
        assert _normalize_content_type("unknown") is None
        assert _normalize_content_type("random") is None


class TestNormalizeLength:
    def test_alias_mapped_to_canonical(self) -> None:
        assert _normalize_length("quick") == "short"
        assert _normalize_length("brief") == "short"
        assert _normalize_length("lengthy") == "long"
        assert _normalize_length("epic") == "long"
        assert _normalize_length("moderate") == "medium"

    def test_unknown_length_returns_none(self) -> None:
        assert _normalize_length("unknown") is None


class TestInterpretedPreference:
    def test_is_empty_when_no_preferences(self) -> None:
        pref = InterpretedPreference()
        assert pref.is_empty()

    def test_is_empty_false_with_genre_boosts(self) -> None:
        pref = InterpretedPreference(genre_boosts={"horror": 1.0})
        assert not pref.is_empty()

    def test_merge_combines_genre_boosts(self) -> None:
        pref1 = InterpretedPreference(genre_boosts={"horror": 0.8})
        pref2 = InterpretedPreference(genre_boosts={"comedy": 0.9})
        merged = pref1.merge_with(pref2)
        assert merged.genre_boosts == {"horror": 0.8, "comedy": 0.9}

    def test_merge_later_takes_precedence(self) -> None:
        pref1 = InterpretedPreference(genre_boosts={"horror": 0.5})
        pref2 = InterpretedPreference(genre_boosts={"horror": 1.0})
        merged = pref1.merge_with(pref2)
        assert merged.genre_boosts == {"horror": 1.0}

    def test_merge_uses_lower_confidence(self) -> None:
        pref1 = InterpretedPreference(confidence=PatternConfidence.HIGH)
        pref2 = InterpretedPreference(confidence=PatternConfidence.MEDIUM)
        merged = pref1.merge_with(pref2)
        assert merged.confidence == PatternConfidence.MEDIUM


class TestPatternBasedInterpreter:
    @pytest.fixture
    def interpreter(self) -> PatternBasedInterpreter:
        return PatternBasedInterpreter()

    def test_avoid_genre(self, interpreter: PatternBasedInterpreter) -> None:
        result = interpreter.interpret("avoid horror")
        assert "horror" in result.genre_penalties
        assert result.confidence == PatternConfidence.HIGH

    def test_tired_of_genre(self, interpreter: PatternBasedInterpreter) -> None:
        result = interpreter.interpret("tired of sci-fi")
        assert "science fiction" in result.genre_penalties

    def test_prefer_genre(self, interpreter: PatternBasedInterpreter) -> None:
        result = interpreter.interpret("prefer sci-fi")
        assert "science fiction" in result.genre_boosts
        assert result.confidence == PatternConfidence.HIGH

    def test_only_books(self, interpreter: PatternBasedInterpreter) -> None:
        result = interpreter.interpret("only books")
        assert "book" in result.content_type_filters
        assert result.confidence == PatternConfidence.HIGH

    def test_no_movies_creates_exclusion(
        self, interpreter: PatternBasedInterpreter
    ) -> None:
        result = interpreter.interpret("no movies")
        assert "movie" in result.content_type_exclusions

    def test_short_books(self, interpreter: PatternBasedInterpreter) -> None:
        result = interpreter.interpret("short books")
        assert result.length_preferences.get("book") == "short"

    def test_quick_games(self, interpreter: PatternBasedInterpreter) -> None:
        result = interpreter.interpret("quick games")
        assert result.length_preferences.get("video_game") == "short"

    def test_unrecognized_rule(self, interpreter: PatternBasedInterpreter) -> None:
        result = interpreter.interpret("random gibberish that matches nothing")
        assert result.is_empty()
        assert result.confidence == PatternConfidence.NONE

    def test_punctuation_stripped(self, interpreter: PatternBasedInterpreter) -> None:
        result = interpreter.interpret("avoid horror!")
        assert "horror" in result.genre_penalties

        result2 = interpreter.interpret("prefer sci-fi.")
        assert "science fiction" in result2.genre_boosts

    def test_interpret_all_merges_rules(
        self, interpreter: PatternBasedInterpreter
    ) -> None:
        rules = ["avoid horror", "prefer comedy", "only books"]
        result = interpreter.interpret_all(rules)

        assert "horror" in result.genre_penalties
        assert "comedy" in result.genre_boosts
        assert "book" in result.content_type_filters


class TestNothingCutFromARuleCarriesItsRawTextRegression:
    """Reported: the engine's log line for interpreted rules went missing."""

    @pytest.mark.parametrize("raw", ["\udc80", "\x1b", "\x00", '"'])
    def test_a_penalised_genre_holds_none_of_it(self, raw: str) -> None:
        result = PatternBasedInterpreter().interpret(f"avoid {raw}horror")

        assert result.genre_penalties == {"horror": 1.0}
        assert result.original_rule == "avoid horror"
