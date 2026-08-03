"""Tests for sorting utilities."""

from pathlib import Path

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.sqlite_db import SQLiteDB
from src.utils.sorting import (
    FUZZY_MATCH_THRESHOLD,
    _best_window_ratio,
    get_sort_title,
    matches_search,
    normalize_for_search,
    titles_similar,
)


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

    def test_up_does_not_match_upgrade_regression(self) -> None:
        assert titles_similar("Up", "Upgrade") is False

    def test_it_does_not_match_spirit_regression(self) -> None:
        assert titles_similar("It", "Spirit") is False

    def test_the_does_not_match_theater_regression(self) -> None:
        # "The" alone normalizes to "the" (the strip regex needs trailing
        # whitespace), so this exercises the mid-word boundary check, not
        # article stripping: "the" must not match inside "theater".
        assert titles_similar("The", "Theater") is False

    def test_her_does_not_match_where_regression(self) -> None:
        assert titles_similar("Her", "Where") is False

    def test_a_does_not_match_cars_regression(self) -> None:
        assert titles_similar("A", "Cars") is False

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

    def test_lowercases_and_strips_articles(self) -> None:
        assert normalize_for_search("The Matrix") == "matrix"

    def test_strips_punctuation(self) -> None:
        # Hyphens, parentheses, and the like collapse to single spaces so a
        # search term and a title normalize onto equal footing.
        assert normalize_for_search("Sci-Fi (1988)") == "sci fi 1988"

    def test_collapses_whitespace(self) -> None:
        assert (
            normalize_for_search("Spider-Man:  Homecoming") == "spider man homecoming"
        )

    def test_empty_string(self) -> None:
        assert normalize_for_search("") == ""

    def test_punctuation_only(self) -> None:
        assert normalize_for_search("!!!") == ""


class TestMatchesSearch:
    """Tests for the three matching tiers of matches_search."""

    def test_exact_match(self) -> None:
        assert matches_search("Die Hard", "die hard") is True

    def test_exact_match_ignores_articles(self) -> None:
        assert matches_search("The Matrix", "matrix") is True

    def test_partial_substring_match(self) -> None:
        assert matches_search("Die Hard (1988)", "Die Hard") is True

    def test_fuzzy_typo_match(self) -> None:
        # Hard PM requirement: "Die Heard" must match "Die Hard (1988)".
        # After normalization this is "die heard" vs the "die hard " window of
        # "die hard 1988", which scores ~0.89, above FUZZY_MATCH_THRESHOLD.
        assert matches_search("Die Hard (1988)", "Die Heard") is True

    def test_fuzzy_match_on_non_article_first_token(self) -> None:
        """Fuzzy matching works on a longer multi-token title.

        "Apocalypse Now" keeps both tokens, and the single-character typo
        "Apocalipse Now" scores ~0.93, comfortably above threshold.
        """
        assert matches_search("Apocalypse Now", "Apocalipse Now") is True

    def test_fuzzy_below_threshold_does_not_match(self) -> None:
        """A typo whose ratio falls below threshold is rejected.

        "Inception" vs "Insepton" scores ~0.75, below FUZZY_MATCH_THRESHOLD
        (0.80), so it must not match.  This pins that the threshold genuinely
        rejects near-misses rather than waving everything through.
        """
        assert _best_window_ratio("insepton", "inception") < FUZZY_MATCH_THRESHOLD
        assert matches_search("Inception", "Insepton") is False

    def test_short_query_fuzzy_false_positive(self) -> None:
        """QA probe: a 3-letter query fuzzy-matches a 1-letter-different word.

        This characterizes (does not condemn) a known property of a low
        difflib threshold: "cat" vs "bat" scores 0.667 and is rejected, but
        "cat" vs "car" / "cot" (also one letter off) likewise score 0.667.
        At length 3 a single substitution never reaches 0.80, so short-query
        false positives via substitution do not occur. A 4-letter query with
        one substitution, however, scores 0.75 and is still rejected. This
        pins that the threshold does not silently surface near-miss noise for
        short terms.
        """
        assert matches_search("Bat", "cat") is False
        assert matches_search("Cot", "cat") is False
        assert matches_search("Card", "cart") is False

    def test_unrelated_does_not_match(self) -> None:
        assert matches_search("The Matrix", "Die Heard") is False

    def test_empty_needle_does_not_match(self) -> None:
        assert matches_search("Die Hard", "") is False

    def test_empty_haystack_does_not_match(self) -> None:
        assert matches_search("", "Die Hard") is False


class TestUnicodeSearch:
    """Regression tests for library search over non-Latin scripts.

    Bug reported: a title written in a non-Latin script (Japanese, Cyrillic,
    Arabic) could never be found by searching the library, from either the web
    UI or the CLI. Searching any substring of it returned nothing too, so the
    item was invisible to search forever.

    Root cause: normalize_for_search collapsed every character outside the
    ASCII class ``[0-9a-z]`` to a space, so a title (or a search term) with no
    ASCII alphanumerics normalized to the empty string, and matches_search
    returns False as soon as either side normalizes to empty.

    Fix: the normalization pattern now collapses only non-word characters,
    which Python's ``\\w`` scopes to every script, so letters outside ASCII
    survive normalization and only punctuation and symbols become spaces.
    """

    def test_non_latin_title_matches_itself_regression(self) -> None:
        """Titles with no ASCII letters match their own text, exact or partial."""
        assert matches_search("進撃の巨人", "進撃の巨人") is True
        assert matches_search("Метро 2033", "Метро") is True
        assert matches_search("ألف ليلة وليلة", "ألف ليلة") is True

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

    def test_unrelated_terms_in_the_same_script_fail_every_tier(self) -> None:
        """Non-ASCII input is matched, not waved through.

        A negative control rather than a regression test — it passed before the
        fix too, when both sides normalized to the empty string and
        matches_search bailed out. What it pins is the fix's boundary: now that
        non-Latin text survives normalization, unrelated strings in the same
        script must still fail all three tiers rather than colliding.
        """
        assert matches_search("進撃の巨人", "千と千尋の神隠し") is False
        assert matches_search("Метро 2033", "Война и мир") is False
        assert matches_search("ألف ليلة وليلة", "رحلة إلى الشمس") is False

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
            assert matches_search(title, title) is True

    def test_non_latin_partial_and_case_differing_terms_match_regression(self) -> None:
        """A substring term and a case-differing term reach the item too.

        The report called out that searching any substring of a non-Latin
        title also returned nothing, so partial terms are part of the defect
        rather than an extra. Cyrillic and Greek also have case, which the
        lowercasing in get_sort_title has to fold for non-ASCII letters.
        """
        assert matches_search("進撃の巨人", "巨人") is True
        assert matches_search("백년의 고독", "고독") is True
        assert matches_search("Метро 2033", "МЕТРО") is True
        assert matches_search("Οδύσσεια", "ΟΔΥΣΣΕΙΑ") is True

    def test_punctuation_around_non_latin_still_collapses_regression(self) -> None:
        """Non-Latin letters survive while punctuation and symbols still fold.

        The fix must widen the kept set without losing the punctuation
        normalization the ASCII class provided, including the full-width and
        CJK punctuation that shows up in the titles this bug was about.
        """
        assert normalize_for_search("進撃の巨人！ 【完全版】") == "進撃の巨人 完全版"
        assert matches_search("進撃の巨人 (Attack on Titan)", "進撃の巨人") is True
        assert matches_search("進撃の巨人 (Attack on Titan)", "Attack on Titan") is True

    def test_terms_in_a_different_script_fail_every_tier(self) -> None:
        """A term in one script never matches a title in another.

        The second negative control, and likewise not a regression test: the
        first pairs strings within a script, which leaves open the possibility
        that the wider character class made unrelated scripts collide through
        the fuzzy tier.
        """
        assert matches_search("Die Hard", "進撃の巨人") is False
        assert matches_search("進撃の巨人", "Die Hard") is False
        assert matches_search("Метро 2033", "進撃の巨人") is False

    def test_normalization_keeps_letters_and_digits_only_regression(self) -> None:
        """States the boundary of the widened class, so it is not read as "keep all".

        Python's ``\\w`` covers letters and digits in every script but not
        combining marks (categories Mn/Mc) or symbols, so a decomposed string
        keeps only its base letters and a title made purely of symbols still
        normalizes to the empty string. Composed non-Latin text -- the
        reported defect -- is unaffected. Folding decomposed and composed
        forms together would need unicodedata.normalize, which this fix
        deliberately does not do.

        The first two assertions use the composed and the decomposed spelling
        of e-acute, which look identical on screen: the composed one is a
        single letter and survives, the decomposed one is a letter plus a
        combining mark and loses the mark. Their differing expectations are
        what tells them apart.
        """
        assert normalize_for_search("é") == "é"
        assert normalize_for_search("é") == "e"
        assert normalize_for_search("★☆") == ""

    def test_non_latin_creator_is_findable_via_storage_regression(
        self, tmp_path: Path
    ) -> None:
        """The creator half of the search matches non-Latin text too.

        _matches_item runs the same normalization over the author/director/
        creators/developer field, so the defect hid every non-Latin creator
        name as well as every non-Latin title.
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


class TestBestWindowRatio:
    """Tests for the _best_window_ratio fuzzy helper."""

    def test_die_heard_clears_threshold(self) -> None:
        """The required typo match clears the threshold with margin.

        Normalized "die heard" against "die hard 1988" scores ~0.89 (best
        window "die heard" vs "die hard "), above FUZZY_MATCH_THRESHOLD, which
        is why "Die Heard" matches "Die Hard (1988)".
        """
        ratio = _best_window_ratio(
            normalize_for_search("Die Heard"), normalize_for_search("Die Hard (1988)")
        )
        assert ratio > FUZZY_MATCH_THRESHOLD

    def test_die_hardy_also_clears_threshold(self) -> None:
        """A different one-letter variant scores the same as "Die Heard".

        "die hardy" against "die hard 1988" also scores ~0.89, so the
        threshold cannot distinguish it from "Die Heard"; both are accepted.
        """
        ratio = _best_window_ratio(
            normalize_for_search("Die Hardy"), normalize_for_search("Die Hard (1988)")
        )
        assert ratio > FUZZY_MATCH_THRESHOLD

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
        assert matches_search("Akira", "Akira Kurosawa") is False
