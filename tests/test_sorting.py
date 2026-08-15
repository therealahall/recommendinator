"""Tests for sorting utilities."""

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
    """Match a term against a title the way a library search does.

    Runs ``search_text_matches`` over a stored ``build_search_text`` whose
    creator half is empty, which is the whole of the production match for an
    item with no creator: storage keeps that column pre-normalized and
    normalizes the term once per request, then offers each half to every tier.
    """
    return search_text_matches(
        build_search_text(title, None), normalize_for_search(term)
    )


class TestGetSortTitle:
    """Tests for get_sort_title function."""

    def test_strips_leading_the(self) -> None:
        """Test that 'The' is stripped from the beginning."""
        assert get_sort_title("The Lord of the Rings") == "lord of the rings"

    def test_empty_string(self) -> None:
        """Test empty string input."""
        assert get_sort_title("") == ""

    def test_article_without_following_space_not_stripped(self) -> None:
        """Test that 'The' not followed by space is not stripped."""
        # "Theater" starts with "The" but shouldn't be stripped
        assert get_sort_title("Theater") == "theater"


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

    def test_el_camino_sorts_under_e_regression(self) -> None:
        """ "El Camino" must keep the Spanish article "el"."""
        assert get_sort_title("El Camino") == "el camino"

    def test_english_the_still_stripped_regression(self) -> None:
        """English articles must still be stripped after the narrowing."""
        assert get_sort_title("The Matrix") == "matrix"


class TestTitlesSimilar:
    """Tests for titles_similar function."""

    def test_article_stripped_match(self) -> None:
        assert titles_similar("The Matrix", "Matrix") is True

    def test_substring_containment(self) -> None:
        assert titles_similar("Blade Runner", "Blade Runner 2049") is True

    def test_completely_different_titles(self) -> None:
        assert titles_similar("Star Wars", "The Godfather") is False

    def test_empty_first_title(self) -> None:
        assert titles_similar("", "Anything") is False


class TestTitlesSimilarWordBoundaryRegression:
    """Regression tests for intra-word substring false positives.

    Bug reported: titles_similar matched a short title that appeared INSIDE
    an unrelated word (e.g. "An" matched "Antique", "Up" matched "Upgrade").

    Root cause: the function used raw character substring containment
    (`t1_norm in t2_norm or t2_norm in t1_norm`), so any normalized title
    that happened to occur mid-word in another title was treated as similar.

    Fix: substring containment now must align on word boundaries — the shorter
    normalized title must be bounded by the string start/end or a
    non-alphanumeric character on each side.
    """

    def test_an_does_not_match_antique_regression(self) -> None:
        assert titles_similar("An", "Antique") is False

    def test_phrase_prefix_still_matches_regression(self) -> None:
        """A real phrase prefix bounded by whitespace still matches."""
        assert titles_similar("Blade Runner", "Blade Runner 2049") is True

    def test_hyphen_is_a_word_boundary_regression(self) -> None:
        """A non-alphanumeric separator (hyphen) counts as a boundary."""
        assert titles_similar("Spider", "Spider-Man") is True

    def test_later_boundary_occurrence_matches_regression(self) -> None:
        """The scan continues past a mid-word hit to a later boundary hit.

        "it" occurs mid-word inside "spirit" (rejected) and again as a
        standalone word (accepted), exercising the loop-continuation path.
        """
        assert titles_similar("It", "Spirit It") is True

    def test_whitespace_only_title_does_not_match_regression(self) -> None:
        """A title that normalizes to empty must not match (no infinite loop)."""
        assert titles_similar("   ", "Spirited Away") is False


class TestNormalizeForSearch:
    """Tests for normalize_for_search function."""

    def test_strips_punctuation(self) -> None:
        # Hyphens, parentheses, and the like collapse to single spaces so a
        # search term and a title normalize onto equal footing.
        assert normalize_for_search("Sci-Fi (1988)") == "sci fi 1988"

    def test_punctuation_only(self) -> None:
        assert normalize_for_search("!!!") == ""


class TestTheMatchingTiers:
    """The three tiers a library search runs, over a stored title.

    Every search runs all three: ``search_text_matches`` is the match, and
    ``title_matches`` above hands it the same stored text the read hands it.
    """

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
        """A typo whose ratio falls below threshold is rejected.

        "Inception" vs "Insepton" scores ~0.75, below FUZZY_MATCH_THRESHOLD
        (0.80), so it must not match.  This pins that the threshold genuinely
        rejects near-misses rather than waving everything through.
        """
        assert _best_window_ratio("insepton", "inception") < FUZZY_MATCH_THRESHOLD
        assert title_matches("Inception", "Insepton") is False

    def test_empty_needle_does_not_match(self) -> None:
        assert title_matches("Die Hard", "") is False


class TestUnicodeSearch:
    """Regression tests for library search over non-Latin scripts.

    Bug reported: a title written in a non-Latin script (Japanese, Cyrillic,
    Arabic) could never be found by searching the library, from either the web
    UI or the CLI. Searching any substring of it returned nothing too, so the
    item was invisible to search forever.

    Root cause: normalize_for_search collapsed every character outside the
    ASCII class ``[0-9a-z]`` to a space, so a title (or a search term) with no
    ASCII alphanumerics normalized to the empty string, and matching returns
    False as soon as either side normalizes to empty.

    Fix: the normalization pattern now collapses only non-word characters,
    which Python's ``\\w`` scopes to every script, so letters outside ASCII
    survive normalization and only punctuation and symbols become spaces.
    """

    def test_non_latin_title_is_findable_via_storage_regression(
        self, tmp_path: Path
    ) -> None:
        """The library read path surfaces a non-Latin title for its own term."""
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
        """All the scripts the report named survive normalization, not just three.

        The report listed CJK, Cyrillic, Greek, Hebrew, Arabic and Devanagari
        as falling through the old ASCII class, and its reproduction used a
        Korean title, so covering only Japanese/Cyrillic/Arabic would leave
        most of the reported surface unproven.
        """
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
        """A substring term and a case-differing term reach the item too.

        The report called out that searching any substring of a non-Latin
        title also returned nothing, so partial terms are part of the defect
        rather than an extra. Cyrillic and Greek also have case, which the
        lowercasing in get_sort_title has to fold for non-ASCII letters.
        """
        assert title_matches("進撃の巨人", "巨人") is True
        assert title_matches("백년의 고독", "고독") is True
        assert title_matches("Метро 2033", "МЕТРО") is True
        assert title_matches("Οδύσσεια", "ΟΔΥΣΣΕΙΑ") is True

    def test_terms_in_a_different_script_fail_every_tier(self) -> None:
        """A term in one script never matches a title in another.

        The second negative control, and likewise not a regression test: the
        first pairs strings within a script, which leaves open the possibility
        that the wider character class made unrelated scripts collide through
        the fuzzy tier.
        """
        assert title_matches("Die Hard", "進撃の巨人") is False
        assert title_matches("進撃の巨人", "Die Hard") is False
        assert title_matches("Метро 2033", "進撃の巨人") is False

    def test_non_latin_creator_is_findable_via_storage_regression(
        self, tmp_path: Path
    ) -> None:
        """The creator half of the search matches non-Latin text too.

        The stored search text normalizes the author/director/creators/
        developer field the same way it normalizes the title, so the defect
        hid every non-Latin creator name as well as every non-Latin title.
        """
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
    """The haystack a stored library search matches an item against.

    ``build_search_text`` holds the item's normalized title and creator in one
    column, and ``search_text_matches`` runs all three tiers back over each
    half. The two are one contract: what a search finds has to be what the
    same rules found when they ran over the loaded item.
    """

    def test_a_row_with_no_stored_text_matches_nothing(self) -> None:
        """A NULL column is not a haystack, and matching it is not an error.

        The candidate projection selects the column straight from the row, so
        a row written by something that does not fill it arrives here as None
        — the fill repairs such a row on the next open, and until then it must
        read as matching nothing rather than raising.
        """
        assert search_text_matches(None, normalize_for_search("die hard")) is False

    def test_a_term_matches_the_creator_half(self) -> None:
        """So does a creator match, which is why the creator is stored at all."""
        text = build_search_text("Die Hard (1988)", "John McTiernan")

        assert search_text_matches(text, normalize_for_search("McTiernan")) is True

    def test_a_term_cannot_match_across_the_title_and_the_creator(self) -> None:
        """The two halves are matched separately, never as one string.

        Their separator is a character search normalization can never produce,
        so a substring found in the joined text always lies inside one half.
        Without that, "alpha omega" would match an item whose title merely ends
        where its creator's name begins.
        """
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
    """Tests for the _best_window_ratio fuzzy helper."""

    def test_needle_longer_than_haystack_uses_full_ratio(self) -> None:
        """When the needle is longer than the haystack the fallback path runs.

        With no window to slide, the helper compares the whole strings.
        "akira kurosawa" (needle) is longer than the title "akira" (haystack),
        exercising the ``len(needle) >= len(haystack)`` branch; the partial
        overlap stays well below threshold.
        """
        ratio = _best_window_ratio(
            normalize_for_search("Akira Kurosawa"), normalize_for_search("Akira")
        )
        assert ratio < FUZZY_MATCH_THRESHOLD
        assert title_matches("Akira", "Akira Kurosawa") is False
