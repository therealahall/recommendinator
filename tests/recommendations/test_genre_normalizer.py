"""Tests for compound genre splitting in the genre normalizer."""

from src.recommendations.genre_normalizer import normalize_term, normalize_terms


class TestCompoundGenreSplitting:
    """Tests for splitting compound genres like 'Sci-Fi & Fantasy'.

    Bug reported: TMDB returns compound genres like "Sci-Fi & Fantasy"
    and "Action & Adventure" which passed through normalization as-is
    (or were lossy-mapped to a single term), so they never matched
    individual terms like "science fiction" or "fantasy" from books.

    Fix: COMPOUND_SPLITS expands compound terms into their constituent
    parts before individual normalization.
    """

    def test_sci_fi_and_fantasy_splits(self) -> None:
        """'Sci-Fi & Fantasy' should split to both 'science fiction' and 'fantasy'."""
        result = normalize_terms(["Sci-Fi & Fantasy"])
        assert "science fiction" in result
        assert "fantasy" in result

    def test_action_and_adventure_splits(self) -> None:
        """'Action & Adventure' should split to both 'action' and 'adventure'."""
        result = normalize_terms(["Action & Adventure"])
        assert "action" in result
        assert "adventure" in result

    def test_war_and_politics_splits(self) -> None:
        """'War & Politics' should split to both 'war' and 'politics'."""
        result = normalize_terms(["War & Politics"])
        assert "war" in result
        assert "politics" in result

    def test_and_variant_splits(self) -> None:
        """'and' variant should also split."""
        result = normalize_terms(["Action and Adventure"])
        assert "action" in result
        assert "adventure" in result

    def test_compound_with_existing_term_deduplicates(self) -> None:
        """Compound split + existing term should deduplicate."""
        result = normalize_terms(["Sci-Fi & Fantasy", "Science Fiction"])
        assert result.count("science fiction") == 1
        assert "fantasy" in result

    def test_non_compound_term_unchanged(self) -> None:
        """Non-compound terms should pass through normally."""
        result = normalize_terms(["Drama"])
        assert result == ["drama"]

    def test_mixed_compound_and_simple(self) -> None:
        """Mix of compound and simple terms."""
        result = normalize_terms(["Sci-Fi & Fantasy", "Crime", "Action & Adventure"])
        assert "science fiction" in result
        assert "fantasy" in result
        assert "crime" in result
        assert "action" in result
        assert "adventure" in result


class TestNormalizeTermIndividual:
    """Verify individual term normalization still works after compound changes."""

    def test_sci_fi_normalizes_to_science_fiction(self) -> None:
        assert normalize_term("sci-fi") == "science fiction"

    def test_drama_passes_through(self) -> None:
        assert normalize_term("Drama") == "drama"

    def test_excluded_term_returns_none(self) -> None:
        assert normalize_term("fiction") is None
