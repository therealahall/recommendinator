"""Tests for sorting utilities."""

from src.utils.sorting import get_sort_title


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

    def test_french_articles(self) -> None:
        """Test French articles are stripped."""
        assert get_sort_title("Les Misérables") == "misérables"
        assert get_sort_title("Le Petit Prince") == "petit prince"
        assert get_sort_title("La Vie en Rose") == "vie en rose"

    def test_spanish_articles(self) -> None:
        """Test Spanish articles are stripped."""
        assert get_sort_title("El Mariachi") == "mariachi"
        assert get_sort_title("Los Tres Amigos") == "tres amigos"

    def test_german_articles(self) -> None:
        """Test German articles are stripped."""
        assert get_sort_title("Der Untergang") == "untergang"
        assert get_sort_title("Die Hard") == "hard"
        assert get_sort_title("Das Boot") == "boot"

    def test_single_word_starting_with_article_letters(self) -> None:
        """Test that single words starting with article letters aren't mangled."""
        assert get_sort_title("Anastasia") == "anastasia"
        assert get_sort_title("Angel") == "angel"
        assert get_sort_title("Them") == "them"


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
