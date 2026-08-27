"""Tests for genre normalization: compound splitting, subgenre preservation, and term variants."""

import pytest

from src.recommendations.genre_normalizer import (
    extract_and_normalize_genres,
    normalize_term,
    normalize_terms,
)


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


class TestExtractAndNormalizeGenres:
    @pytest.mark.parametrize("field", ["genres", "tags"])
    def test_json_array_string_yields_every_element(self, field: str) -> None:
        result = extract_and_normalize_genres({field: '["Science Fiction", "Fantasy"]'})
        assert result == ["science fiction", "fantasy"]

    @pytest.mark.parametrize("field", ["genres", "tags"])
    def test_comma_separated_string_yields_every_element_stripped(
        self, field: str
    ) -> None:
        result = extract_and_normalize_genres({field: " Science Fiction , Fantasy "})
        assert result == ["science fiction", "fantasy"]

    @pytest.mark.parametrize("field", ["genres", "tags"])
    def test_truncated_json_string_keeps_the_element_behind_the_bracket(
        self, field: str
    ) -> None:
        result = extract_and_normalize_genres({field: "[Drama, Fantasy"})
        assert result == ["drama", "fantasy"]

    def test_genre_list_yields_every_element(self) -> None:
        result = extract_and_normalize_genres({"genre": ["Science Fiction", "Fantasy"]})
        assert result == ["science fiction", "fantasy"]

    def test_genre_genres_and_tags_are_unioned_and_deduplicated(self) -> None:
        result = extract_and_normalize_genres(
            {
                "genre": "Drama",
                "genres": ["Fantasy", "Drama"],
                "tags": '["Fantasy", "Horror"]',
            }
        )
        assert result == ["drama", "fantasy", "horror"]

    @pytest.mark.parametrize(
        "metadata", [None, {}, {"genres": ""}, {"genres": []}, {"tags": []}]
    )
    def test_absent_or_empty_metadata_yields_no_terms(
        self, metadata: dict | None
    ) -> None:
        assert extract_and_normalize_genres(metadata) == []
