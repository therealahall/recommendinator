"""Tests for genre normalization: compound splitting, subgenre preservation, and term variants."""

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


class TestNormalizeTermIndividual:
    """Verify individual term normalization still works after compound changes."""

    def test_sci_fi_normalizes_to_science_fiction(self) -> None:
        assert normalize_term("sci-fi") == "science fiction"

    def test_excluded_term_returns_none(self) -> None:
        assert normalize_term("fiction") is None


class TestSubgenrePreservation:
    """Tests that meaningful subgenres are preserved, not collapsed to parent genres.

    Previously, 'dark fantasy' was normalized to just 'fantasy', losing the
    'dark' qualifier that distinguishes it for cross-content matching.
    """

    def test_dark_fantasy_preserved(self) -> None:
        assert normalize_term("dark fantasy") == "dark fantasy"

    def test_cosmic_horror_from_lovecraftian(self) -> None:
        """'lovecraftian' should normalize to 'cosmic horror'."""
        assert normalize_term("lovecraftian") == "cosmic horror"

    def test_fantasy_fiction_still_normalizes(self) -> None:
        """'fantasy fiction' should still collapse to 'fantasy' (noise word removal)."""
        assert normalize_term("fantasy fiction") == "fantasy"
