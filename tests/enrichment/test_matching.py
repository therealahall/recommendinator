import pytest

from src.enrichment.matching import best_match_index, normalize_title, title_similarity


class TestNormalizeTitle:
    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ("The Matrix", "Matrix"),
            ("Star Wars: Episode IV - A New Hope", "Star wars episode IV, a new hope"),
            ("WALL·E", "Wall E"),
        ],
    )
    def test_two_spellings_of_one_title_normalize_alike(
        self, left: str, right: str
    ) -> None:
        assert normalize_title(left) == normalize_title(right)


class TestTitleSimilarity:
    def test_a_title_that_normalizes_away_matches_nothing(self) -> None:
        assert title_similarity("...", "Blade Runner") == 0.0


class TestBestMatchIndex:
    def test_a_higher_ranked_sequel_does_not_stand_in_for_the_original(self) -> None:
        candidates = [(["Blade Runner 2049"], 2017), (["Blade Runner"], 1982)]

        assert best_match_index("Blade Runner", None, candidates) == 1

    def test_a_release_three_years_out_is_the_same_work_but_four_is_not(self) -> None:
        assert best_match_index("Some Movie", 1999, [(["Some Movie"], 2002)]) == 0
        assert best_match_index("Some Movie", 1999, [(["Some Movie"], 2003)]) is None

    def test_the_item_year_settles_a_tie_the_search_ranking_would_otherwise_win(
        self,
    ) -> None:
        title = "The Girl with the Dragon Tattoo"
        remake_below_the_original = [([title], 2009), ([title], 2011)]

        assert best_match_index(title, 2011, remake_below_the_original) == 1

    def test_a_dateless_candidate_does_not_outrank_one_dated_to_the_item_year(
        self,
    ) -> None:
        assert best_match_index("Dune", 2021, [(["Dune"], None), (["Dune"], 2021)]) == 1

    def test_a_franchise_prefixed_library_title_still_matches_the_bare_release(
        self,
    ) -> None:
        plex_naming = "Star Wars: Episode V - The Empire Strikes Back"

        assert (
            best_match_index(plex_naming, 1980, [(["The Empire Strikes Back"], 1980)])
            == 0
        )
