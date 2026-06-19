"""Tests for sorting utilities."""

from src.utils.sorting import get_sort_title, titles_similar


class TestGetSortTitle:
    """Tests for get_sort_title function."""

    def test_strips_leading_the(self) -> None:
        """Test that 'The' is stripped from the beginning."""
        assert get_sort_title("The Lord of the Rings") == "lord of the rings"

    def test_strips_leading_a(self) -> None:
        """Test that 'A' is stripped from the beginning."""
        assert get_sort_title("A Tale of Two Cities") == "tale of two cities"

    def test_strips_leading_an(self) -> None:
        """Test that 'An' is stripped from the beginning."""
        assert get_sort_title("An American in Paris") == "american in paris"

    def test_preserves_article_in_middle(self) -> None:
        """Test that articles in the middle are preserved."""
        result = get_sort_title("The Lord of the Rings")
        assert "of the rings" in result

    def test_case_insensitive(self) -> None:
        """Test that article stripping is case insensitive."""
        assert get_sort_title("THE MATRIX") == "matrix"
        assert get_sort_title("the matrix") == "matrix"
        assert get_sort_title("The Matrix") == "matrix"

    def test_returns_lowercase(self) -> None:
        """Test that result is always lowercase for consistent sorting."""
        assert get_sort_title("STAR WARS") == "star wars"
        assert get_sort_title("Star Wars") == "star wars"

    def test_no_article(self) -> None:
        """Test titles without leading articles."""
        assert get_sort_title("Star Wars") == "star wars"
        assert get_sort_title("1984") == "1984"
        assert get_sort_title("Blade Runner") == "blade runner"

    def test_empty_string(self) -> None:
        """Test empty string input."""
        assert get_sort_title("") == ""

    def test_whitespace_only(self) -> None:
        """Test whitespace-only input."""
        assert get_sort_title("   ") == ""

    def test_strips_leading_whitespace(self) -> None:
        """Test that leading whitespace is handled."""
        assert get_sort_title("  The Matrix") == "matrix"

    def test_article_without_following_space_not_stripped(self) -> None:
        """Test that 'The' not followed by space is not stripped."""
        # "Theater" starts with "The" but shouldn't be stripped
        assert get_sort_title("Theater") == "theater"

    def test_french_articles_not_stripped(self) -> None:
        """Test French articles are NOT stripped (English-only, see #77)."""
        assert get_sort_title("Les Misérables") == "les misérables"
        assert get_sort_title("Le Petit Prince") == "le petit prince"
        assert get_sort_title("La Vie en Rose") == "la vie en rose"

    def test_spanish_articles_not_stripped(self) -> None:
        """Test Spanish articles are NOT stripped (English-only, see #77)."""
        assert get_sort_title("El Mariachi") == "el mariachi"
        assert get_sort_title("Los Tres Amigos") == "los tres amigos"

    def test_german_articles_not_stripped(self) -> None:
        """Test German articles are NOT stripped (English-only, see #77)."""
        assert get_sort_title("Der Untergang") == "der untergang"
        assert get_sort_title("Die Hard") == "die hard"
        assert get_sort_title("Das Boot") == "das boot"

    def test_single_word_starting_with_article_letters(self) -> None:
        """Test that single words starting with article letters aren't mangled."""
        assert get_sort_title("Anastasia") == "anastasia"
        assert get_sort_title("Angel") == "angel"
        assert get_sort_title("Them") == "them"


class TestSortTitleArticleRegression:
    """Regression tests for article stripping bugs."""

    def test_i_am_legend_stays_intact_regression(self) -> None:
        """Regression test: "I Am Legend" should not be stripped.

        Bug reported: Italian article "i" in ARTICLES caused titles starting
        with "I " (English pronoun) to be incorrectly stripped.

        Root cause: "i" was included as an Italian plural article, but it
        collides with the very common English word "I".

        Fix: Removed "i" from ARTICLES frozenset (later superseded by the full
        English-only narrowing in #77, which removed every non-English article).
        """
        assert get_sort_title("I Am Legend") == "i am legend"
        assert get_sort_title("I, Robot") == "i, robot"

    def test_l_apostrophe_was_unreachable_regression(self) -> None:
        """Regression test: "l'" was dead code in ARTICLES.

        The regex requires \\s+ after the article, but "l'" uses an
        apostrophe (no space), so it could never match. Removed as dead code.
        Titles like "L'Étranger" are unaffected (were never stripped).
        """
        assert get_sort_title("L'Étranger") == "l'étranger"


class TestNonEnglishArticleStrippingRegression:
    """Regression tests for issue #77: non-English articles wrongly stripped.

    Bug reported: "Die Hard" sorted under H instead of D because the German
    article "die" ("the") was in the multilingual ARTICLES set and got
    stripped. Same trap for "Das Boot" (German "das"), "El Camino" (Spanish
    "el"), "Los Angeles" (Spanish "los"), etc.

    Root cause: ARTICLES spanned English, French, Spanish, German, and Italian.
    Many non-English articles collide with English words and proper nouns.

    Fix: Narrowed ARTICLES to English only ({"a", "an", "the"}). Locale-aware
    multilingual stripping is deferred to a future per-locale config.
    """

    def test_die_hard_sorts_under_d_regression(self) -> None:
        """ "Die Hard" must keep "die" so it sorts under D, not H."""
        assert get_sort_title("Die Hard") == "die hard"

    def test_das_boot_sorts_under_d_regression(self) -> None:
        """ "Das Boot" must keep the German article "das"."""
        assert get_sort_title("Das Boot") == "das boot"

    def test_el_camino_sorts_under_e_regression(self) -> None:
        """ "El Camino" must keep the Spanish article "el"."""
        assert get_sort_title("El Camino") == "el camino"

    def test_le_petit_prince_sorts_under_l_regression(self) -> None:
        """ "Le Petit Prince" must keep the French article "le"."""
        assert get_sort_title("Le Petit Prince") == "le petit prince"

    def test_il_postino_sorts_under_i_regression(self) -> None:
        """ "Il Postino" must keep the Italian article "il"."""
        assert get_sort_title("Il Postino") == "il postino"

    def test_english_the_still_stripped_regression(self) -> None:
        """English articles must still be stripped after the narrowing."""
        assert get_sort_title("The Matrix") == "matrix"


class TestSortTitleOrdering:
    """Tests for sorting behavior with get_sort_title."""

    def test_sorting_ignores_articles(self) -> None:
        """Test that sorting by sort_title ignores leading articles."""
        titles = [
            "The Lord of the Rings",
            "A Tale of Two Cities",
            "Star Wars",
            "An American in Paris",
            "Blade Runner",
        ]
        sorted_titles = sorted(titles, key=get_sort_title)

        # Expected order (alphabetically by sort key):
        # american in paris, blade runner, lord of the rings,
        # star wars, tale of two cities
        assert sorted_titles == [
            "An American in Paris",
            "Blade Runner",
            "The Lord of the Rings",
            "Star Wars",
            "A Tale of Two Cities",
        ]

    def test_sorting_with_numbers(self) -> None:
        """Test sorting with numeric titles."""
        titles = ["The Matrix", "1984", "2001: A Space Odyssey", "Avatar"]
        sorted_titles = sorted(titles, key=get_sort_title)

        # Numbers sort before letters in ASCII
        assert sorted_titles[0] == "1984"
        assert sorted_titles[1] == "2001: A Space Odyssey"


class TestTitlesSimilar:
    """Tests for titles_similar function."""

    def test_identical_titles(self) -> None:
        assert titles_similar("The Lord of the Rings", "The Lord of the Rings") is True

    def test_article_stripped_match(self) -> None:
        assert titles_similar("The Matrix", "Matrix") is True

    def test_substring_containment(self) -> None:
        assert titles_similar("Blade Runner", "Blade Runner 2049") is True

    def test_completely_different_titles(self) -> None:
        assert titles_similar("Star Wars", "The Godfather") is False

    def test_empty_first_title(self) -> None:
        assert titles_similar("", "Anything") is False

    def test_empty_second_title(self) -> None:
        assert titles_similar("Anything", "") is False

    def test_both_empty(self) -> None:
        assert titles_similar("", "") is False

    def test_case_insensitive(self) -> None:
        assert titles_similar("DUNE", "dune") is True

    def test_no_false_match_on_short_overlap(self) -> None:
        """Unrelated titles with no substring relationship should not match."""
        assert titles_similar("Portal", "Inception") is False
