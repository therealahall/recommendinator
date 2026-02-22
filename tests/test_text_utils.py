"""Tests for text formatting utilities."""

import pytest

from src.utils.text import humanize_source_id


class TestHumanizeSourceIdBasic:
    """Tests for basic snake_case to human-readable conversion."""

    @pytest.mark.parametrize(
        ("source_id", "expected"),
        [
            ("my_books", "My Books"),
            ("finished_movies", "Finished Movies"),
            ("personal_site_games", "Personal Site Games"),
            ("steam_wishlist", "Steam Wishlist"),
            ("currently_reading", "Currently Reading"),
        ],
    )
    def test_normal_snake_case_inputs(self, source_id: str, expected: str) -> None:
        """Normal snake_case source IDs are title-cased with spaces."""
        assert humanize_source_id(source_id) == expected


class TestHumanizeSourceIdAcronyms:
    """Tests for acronym uppercasing in humanize_source_id."""

    @pytest.mark.parametrize(
        ("source_id", "expected"),
        [
            ("tv", "TV"),
            ("gog", "GOG"),
            ("api", "API"),
            ("id", "ID"),
            ("csv", "CSV"),
            ("json", "JSON"),
        ],
    )
    def test_single_acronym(self, source_id: str, expected: str) -> None:
        """Known acronyms are fully uppercased instead of title-cased."""
        assert humanize_source_id(source_id) == expected

    @pytest.mark.parametrize(
        ("source_id", "expected"),
        [
            ("finished_tv_shows", "Finished TV Shows"),
            ("gog_wishlist", "GOG Wishlist"),
            ("api_key", "API Key"),
            ("source_id", "Source ID"),
            ("export_csv", "Export CSV"),
            ("import_json", "Import JSON"),
        ],
    )
    def test_acronym_within_multi_word_id(self, source_id: str, expected: str) -> None:
        """Acronyms within longer snake_case IDs are uppercased correctly."""
        assert humanize_source_id(source_id) == expected

    @pytest.mark.parametrize(
        ("source_id", "expected"),
        [
            ("gog_api", "GOG API"),
            ("tv_json", "TV JSON"),
            ("csv_api_id", "CSV API ID"),
        ],
    )
    def test_multiple_acronyms_in_one_id(self, source_id: str, expected: str) -> None:
        """Multiple acronyms in a single source ID are all uppercased."""
        assert humanize_source_id(source_id) == expected


class TestHumanizeSourceIdEdgeCases:
    """Tests for edge cases in humanize_source_id."""

    def test_empty_string(self) -> None:
        """Empty string input returns an empty string."""
        assert humanize_source_id("") == ""

    def test_single_word_no_underscores(self) -> None:
        """Single word without underscores is title-cased."""
        assert humanize_source_id("books") == "Books"

    def test_single_word_already_capitalized(self) -> None:
        """Single capitalized word is returned title-cased (unchanged)."""
        assert humanize_source_id("Books") == "Books"

    def test_multiple_consecutive_underscores(self) -> None:
        """Multiple consecutive underscores produce empty segments that capitalize to empty strings."""
        result = humanize_source_id("my__books")
        # split("_") on "my__books" gives ["my", "", "books"]
        # "".capitalize() returns "", so we get "My  Books" with double space
        assert result == "My  Books"

    def test_leading_underscore(self) -> None:
        """Leading underscore produces an empty first segment."""
        result = humanize_source_id("_private_source")
        # split("_") on "_private_source" gives ["", "private", "source"]
        assert result == " Private Source"

    def test_trailing_underscore(self) -> None:
        """Trailing underscore produces an empty last segment."""
        result = humanize_source_id("my_source_")
        # split("_") on "my_source_" gives ["my", "source", ""]
        assert result == "My Source "

    def test_all_underscores(self) -> None:
        """String of only underscores produces spaces."""
        result = humanize_source_id("___")
        # split("_") on "___" gives ["", "", "", ""]
        assert result == "   "

    def test_non_acronym_short_word(self) -> None:
        """Short words not in the acronym list are title-cased, not uppercased."""
        assert humanize_source_id("my_app") == "My App"

    def test_acronym_case_sensitivity(self) -> None:
        """Acronym lookup is case-sensitive; uppercase input is not matched."""
        # "TV" (uppercase) is not in the lookup dict (key is "tv" lowercase)
        # so it goes through .capitalize() which gives "Tv"
        assert humanize_source_id("TV") == "Tv"
        assert humanize_source_id("GOG") == "Gog"
