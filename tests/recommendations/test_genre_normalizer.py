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


class TestNewCompoundSplits:
    """Tests for additional compound genre splits added for broader provider coverage."""

    def test_mystery_and_thriller_splits(self) -> None:
        result = normalize_terms(["Mystery & Thriller"])
        assert "mystery" in result
        assert "thriller" in result

    def test_crime_and_thriller_splits(self) -> None:
        result = normalize_terms(["Crime & Thriller"])
        assert "crime" in result
        assert "thriller" in result

    def test_horror_and_thriller_splits(self) -> None:
        result = normalize_terms(["Horror & Thriller"])
        assert "horror" in result
        assert "thriller" in result

    def test_drama_and_romance_splits(self) -> None:
        result = normalize_terms(["Drama & Romance"])
        assert "drama" in result
        assert "romance" in result

    def test_sci_fi_and_horror_splits(self) -> None:
        result = normalize_terms(["Sci-Fi & Horror"])
        assert "science fiction" in result
        assert "horror" in result


class TestSubgenrePreservation:
    """Tests that meaningful subgenres are preserved, not collapsed to parent genres.

    Previously, 'dark fantasy' was normalized to just 'fantasy', losing the
    'dark' qualifier that distinguishes it for cross-content matching.
    """

    def test_dark_fantasy_preserved(self) -> None:
        assert normalize_term("dark fantasy") == "dark fantasy"

    def test_urban_fantasy_preserved(self) -> None:
        assert normalize_term("urban fantasy") == "urban fantasy"

    def test_epic_fantasy_preserved(self) -> None:
        assert normalize_term("epic fantasy") == "epic fantasy"

    def test_high_fantasy_preserved(self) -> None:
        assert normalize_term("high fantasy") == "high fantasy"

    def test_supernatural_horror_preserved(self) -> None:
        assert normalize_term("supernatural horror") == "supernatural horror"

    def test_cosmic_horror_from_lovecraftian(self) -> None:
        """'lovecraftian' should normalize to 'cosmic horror'."""
        assert normalize_term("lovecraftian") == "cosmic horror"

    def test_fantasy_fiction_still_normalizes(self) -> None:
        """'fantasy fiction' should still collapse to 'fantasy' (noise word removal)."""
        assert normalize_term("fantasy fiction") == "fantasy"


class TestNewNormalizationVariants:
    """Tests for new normalization mappings added for variant term coverage."""

    def test_sci_fi_space_variant(self) -> None:
        assert normalize_term("sci fi") == "science fiction"

    def test_rom_com_normalizes(self) -> None:
        assert normalize_term("rom-com") == "romantic comedy"

    def test_romcom_normalizes(self) -> None:
        assert normalize_term("romcom") == "romantic comedy"

    def test_whodunnit_normalizes(self) -> None:
        assert normalize_term("whodunnit") == "whodunit"

    def test_hard_boiled_normalizes(self) -> None:
        assert normalize_term("hard-boiled") == "hardboiled"

    def test_alternative_history_normalizes(self) -> None:
        assert normalize_term("alternative history") == "alternate history"

    def test_tower_defence_normalizes(self) -> None:
        assert normalize_term("tower defence") == "tower defense"

    def test_post_apocalyptic_variants(self) -> None:
        assert normalize_term("post apocalyptic") == "post-apocalyptic"
        assert normalize_term("postapocalyptic") == "post-apocalyptic"


class TestNewAllowedTerms:
    """Smoke tests that key new terms pass through the normalizer."""

    def test_punk_subgenres_allowed(self) -> None:
        for term in ["biopunk", "dieselpunk", "solarpunk", "atompunk"]:
            assert normalize_term(term) == term, f"{term} should be allowed"

    def test_fantasy_subgenres_allowed(self) -> None:
        for term in ["grimdark", "litrpg", "isekai", "wuxia"]:
            assert normalize_term(term) == term, f"{term} should be allowed"

    def test_horror_subgenres_allowed(self) -> None:
        for term in ["cosmic horror", "body horror", "folk horror"]:
            assert normalize_term(term) == term, f"{term} should be allowed"

    def test_game_genres_allowed(self) -> None:
        for term in ["battle royale", "tower defense", "city builder", "visual novel"]:
            assert normalize_term(term) == term, f"{term} should be allowed"

    def test_tone_terms_allowed(self) -> None:
        for term in ["bittersweet", "whimsical", "brooding", "cerebral"]:
            assert normalize_term(term) == term, f"{term} should be allowed"

    def test_theme_terms_allowed(self) -> None:
        for term in ["moral ambiguity", "found family", "slow burn"]:
            assert normalize_term(term) == term, f"{term} should be allowed"

    def test_hyphenated_terms_in_allowed(self) -> None:
        """Terms with hyphens need explicit ALLOWED_TERMS entries."""
        for term in ["neo-western", "self-discovery", "post-cyberpunk", "fast-paced"]:
            assert normalize_term(term) == term, f"{term} should be allowed"
