from pathlib import Path

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.sqlite_db import SQLiteDB
from src.utils.sorting import (
    FUZZY_MATCH_THRESHOLD,
    _best_window_ratio,
    build_search_text,
    get_sort_title,
    normalize_for_search,
    search_text_matches,
    titles_similar,
)


def title_matches(title: str, term: str) -> bool:
    """Match a term against a title the way a library search does."""
    return search_text_matches(
        build_search_text(title, None), normalize_for_search(term)
    )


class TestGetSortTitle:
    def test_strips_leading_the(self) -> None:
        assert get_sort_title("The Lord of the Rings") == "lord of the rings"

    def test_empty_string(self) -> None:
        assert get_sort_title("") == ""

    def test_article_without_following_space_not_stripped(self) -> None:
        # "Theater" starts with "The" but shouldn't be stripped
        assert get_sort_title("Theater") == "theater"


class TestSortTitleArticleRegression:
    def test_i_am_legend_stays_intact_regression(self) -> None:
        """Bug reported: Italian article "i" in ARTICLES caused titles starting with
        "I " (English pronoun) to be incorrectly stripped."""
        assert get_sort_title("I Am Legend") == "i am legend"
        assert get_sort_title("I, Robot") == "i, robot"

    def test_l_apostrophe_was_unreachable_regression(self) -> None:
        """The regex requires \\s+ after the article, but "l'" uses an apostrophe
        (no space), so it could never match."""
        assert get_sort_title("L'Étranger") == "l'étranger"


class TestNonEnglishArticleStrippingRegression:
    """Regression tests for issue #77: non-English articles wrongly stripped."""

    def test_die_hard_sorts_under_d_regression(self) -> None:
        assert get_sort_title("Die Hard") == "die hard"

    def test_el_camino_sorts_under_e_regression(self) -> None:
        assert get_sort_title("El Camino") == "el camino"

    def test_english_the_still_stripped_regression(self) -> None:
        assert get_sort_title("The Matrix") == "matrix"


class TestTitlesSimilar:
    def test_article_stripped_match(self) -> None:
        assert titles_similar("The Matrix", "Matrix") is True

    def test_substring_containment(self) -> None:
        assert titles_similar("Blade Runner", "Blade Runner 2049") is True

    def test_completely_different_titles(self) -> None:
        assert titles_similar("Star Wars", "The Godfather") is False

    def test_empty_first_title(self) -> None:
        assert titles_similar("", "Anything") is False


class TestTitlesSimilarWordBoundaryRegression:
    """Bug reported: titles_similar matched a short title that appeared INSIDE an
    unrelated word (e.g. "An" matched "Antique", "Up" matched "Upgrade")."""

    def test_an_does_not_match_antique_regression(self) -> None:
        assert titles_similar("An", "Antique") is False

    def test_phrase_prefix_still_matches_regression(self) -> None:
        assert titles_similar("Blade Runner", "Blade Runner 2049") is True

    def test_hyphen_is_a_word_boundary_regression(self) -> None:
        assert titles_similar("Spider", "Spider-Man") is True

    def test_later_boundary_occurrence_matches_regression(self) -> None:
        """ "it" occurs mid-word inside "spirit" (rejected) and again as a standalone
        word (accepted), exercising the loop-continuation path."""
        assert titles_similar("It", "Spirit It") is True

    def test_whitespace_only_title_does_not_match_regression(self) -> None:
        """A title that normalizes to empty must not match (no infinite loop)."""
        assert titles_similar("   ", "Spirited Away") is False


class TestNormalizeForSearch:
    def test_strips_punctuation(self) -> None:
        # Hyphens, parentheses, and the like collapse to single spaces so a
        # search term and a title normalize onto equal footing.
        assert normalize_for_search("Sci-Fi (1988)") == "sci fi 1988"

    def test_punctuation_only(self) -> None:
        assert normalize_for_search("!!!") == ""


class TestTheMatchingTiers:
    """Every search runs all three: ``search_text_matches`` is the match, and
    ``title_matches`` above hands it the same stored text the read hands it."""

    def test_exact_match(self) -> None:
        assert title_matches("Die Hard", "die hard") is True

    def test_partial_substring_match(self) -> None:
        assert title_matches("Die Hard (1988)", "Die Hard") is True

    def test_fuzzy_typo_match(self) -> None:
        # Hard PM requirement: "Die Heard" must match "Die Hard (1988)".
        # After normalization this is "die heard" vs the "die hard " window of
        # "die hard 1988", which scores ~0.89, above FUZZY_MATCH_THRESHOLD.
        assert title_matches("Die Hard (1988)", "Die Heard") is True

    def test_fuzzy_below_threshold_does_not_match(self) -> None:
        """ "Inception" vs "Insepton" scores ~0.75, below FUZZY_MATCH_THRESHOLD
        (0.80), so it must not match."""
        assert _best_window_ratio("insepton", "inception") < FUZZY_MATCH_THRESHOLD
        assert title_matches("Inception", "Insepton") is False

    def test_empty_needle_does_not_match(self) -> None:
        assert title_matches("Die Hard", "") is False


class TestUnicodeSearch:
    """Bug reported: a title written in a non-Latin script (Japanese, Cyrillic,
    Arabic) could never be found by searching the library, from either the web UI
    or the CLI."""

    def test_non_latin_title_is_findable_via_storage_regression(
        self, tmp_path: Path
    ) -> None:
        db = SQLiteDB(tmp_path / "unicode_search.db")
        db.save_content_item(
            ContentItem(
                id="tv_aot",
                title="進撃の巨人",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.COMPLETED,
            )
        )
        db.save_content_item(
            ContentItem(
                id="tv_spirited",
                title="千と千尋の神隠し",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.COMPLETED,
            )
        )

        results = db.get_content_items(
            content_type=ContentType.TV_SHOW, search="進撃の巨人"
        )
        assert [item.title for item in results] == ["進撃の巨人"]

    def test_every_reported_script_is_searchable_regression(self) -> None:
        """The report listed CJK, Cyrillic, Greek, Hebrew, Arabic and Devanagari as
        falling through the old ASCII class, and its reproduction used a Korean
        title, so covering only Japanese/Cyrillic/Arabic would leave most of the
        reported surface unproven."""
        titles = [
            "進撃の巨人",  # Japanese
            "三体",  # Chinese
            "백년의 고독",  # Korean
            "Метро 2033",  # Cyrillic
            "Οδύσσεια",  # Greek
            "מלחמה ושלום",  # Hebrew
            "ألف ليلة وليلة",  # Arabic
            "गोदान",  # Devanagari
            "แฮร์รี่ พอตเตอร์",  # Thai
        ]
        for title in titles:
            assert normalize_for_search(title) != ""
            assert title_matches(title, title) is True

    def test_non_latin_partial_and_case_differing_terms_match_regression(self) -> None:
        """The report called out that searching any substring of a non-Latin title
        also returned nothing, so partial terms are part of the defect rather than
        an extra."""
        assert title_matches("進撃の巨人", "巨人") is True
        assert title_matches("백년의 고독", "고독") is True
        assert title_matches("Метро 2033", "МЕТРО") is True
        assert title_matches("Οδύσσεια", "ΟΔΥΣΣΕΙΑ") is True

    def test_terms_in_a_different_script_fail_every_tier(self) -> None:
        """The second negative control, and likewise not a regression test: the
        first pairs strings within a script, which leaves open the possibility that
        the wider character class made unrelated scripts collide through the fuzzy
        tier."""
        assert title_matches("Die Hard", "進撃の巨人") is False
        assert title_matches("進撃の巨人", "Die Hard") is False
        assert title_matches("Метро 2033", "進撃の巨人") is False

    def test_non_latin_creator_is_findable_via_storage_regression(
        self, tmp_path: Path
    ) -> None:
        """The stored search text normalizes the author/director/creators/developer
        field the same way it normalizes the title, so the defect hid every
        non-Latin creator name as well as every non-Latin title."""
        db = SQLiteDB(tmp_path / "unicode_creator.db")
        db.save_content_item(
            ContentItem(
                id="book_kafka_shore",
                title="Kafka on the Shore",
                author="村上春樹",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
            )
        )
        db.save_content_item(
            ContentItem(
                id="book_snow_country",
                title="Snow Country",
                author="川端康成",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
            )
        )

        results = db.get_content_items(search="村上春樹")
        assert [item.title for item in results] == ["Kafka on the Shore"]


class TestTheStoredSearchText:
    def test_a_row_with_no_stored_text_matches_nothing(self) -> None:
        """A NULL column is not a haystack, and matching it is not an error."""
        assert search_text_matches(None, normalize_for_search("die hard")) is False

    def test_a_term_matches_the_creator_half(self) -> None:
        text = build_search_text("Die Hard (1988)", "John McTiernan")

        assert search_text_matches(text, normalize_for_search("McTiernan")) is True

    def test_a_term_cannot_match_across_the_title_and_the_creator(self) -> None:
        """Their separator is a character search normalization can never produce, so
        a substring found in the joined text always lies inside one half."""
        text = build_search_text("Alpha", "Omega")

        assert search_text_matches(text, normalize_for_search("alpha")) is True
        assert search_text_matches(text, normalize_for_search("omega")) is True
        assert search_text_matches(text, normalize_for_search("alpha omega")) is False

    def test_an_item_with_no_creator_matches_on_its_title_alone(self) -> None:
        """A missing creator is an empty half, not a half that matches anything."""
        text = build_search_text("Untitled Manuscript", None)

        assert search_text_matches(text, normalize_for_search("manuscript")) is True
        assert search_text_matches(text, normalize_for_search("Tolkien")) is False


class TestBestWindowRatio:
    def test_needle_longer_than_haystack_uses_full_ratio(self) -> None:
        """With no window to slide, the helper compares the whole strings."""
        ratio = _best_window_ratio(
            normalize_for_search("Akira Kurosawa"), normalize_for_search("Akira")
        )
        assert ratio < FUZZY_MATCH_THRESHOLD
        assert title_matches("Akira", "Akira Kurosawa") is False
