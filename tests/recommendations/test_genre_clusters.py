"""Tests for semantic genre cluster matching."""

from src.recommendations.genre_clusters import (
    CLUSTERS,
    cluster_overlap,
    get_clusters_for_terms,
)


class TestGetClustersForTerms:
    """Tests for mapping terms to cluster memberships."""

    def test_space_warfare_in_multiple_clusters(self) -> None:
        """'space warfare' should belong to sci-fi, war, and space_opera clusters."""
        clusters = get_clusters_for_terms(["space warfare"])
        assert "science_fiction" in clusters
        assert "war_military" in clusters
        assert "space_opera" in clusters

    def test_robots_in_sci_fi(self) -> None:
        """'robots' should be in the science_fiction cluster."""
        clusters = get_clusters_for_terms(["robots"])
        assert "science_fiction" in clusters

    def test_comedy_in_comedy_cluster(self) -> None:
        clusters = get_clusters_for_terms(["comedy"])
        assert "comedy_lighthearted" in clusters

    def test_empty_terms_returns_empty(self) -> None:
        assert get_clusters_for_terms([]) == set()

    def test_unknown_term_returns_empty(self) -> None:
        """Terms not in any cluster should return empty set."""
        assert get_clusters_for_terms(["xyzzy_not_a_genre"]) == set()

    def test_multiple_terms_union(self) -> None:
        """Multiple terms should return union of their clusters."""
        clusters = get_clusters_for_terms(["science fiction", "war"])
        assert "science_fiction" in clusters
        assert "war_military" in clusters


class TestClusterOverlap:
    """Tests for Jaccard similarity of cluster memberships."""

    def test_book_space_warfare_tv_war_share_cluster(self) -> None:
        """A book with 'space warfare' and TV with 'war' should share war_military."""
        score = cluster_overlap(["space warfare"], ["war"])
        assert score > 0.0

    def test_book_sci_fi_game_robots_share_cluster(self) -> None:
        """'science fiction' and 'robots' share the science_fiction cluster."""
        score = cluster_overlap(["science fiction"], ["robots"])
        assert score > 0.0

    def test_comedy_vs_horror_zero_overlap(self) -> None:
        """'comedy' and 'horror' should have zero cluster overlap."""
        score = cluster_overlap(["comedy"], ["horror"])
        assert score == 0.0

    def test_symmetry(self) -> None:
        """overlap(a, b) should equal overlap(b, a)."""
        score_ab = cluster_overlap(["science fiction", "war"], ["space warfare"])
        score_ba = cluster_overlap(["space warfare"], ["science fiction", "war"])
        assert score_ab == score_ba

    def test_identical_terms_perfect_overlap(self) -> None:
        """Identical term lists should have 1.0 overlap."""
        score = cluster_overlap(["science fiction"], ["science fiction"])
        assert score == 1.0

    def test_empty_terms_zero_overlap(self) -> None:
        """Empty term list should give 0.0 overlap."""
        assert cluster_overlap([], ["science fiction"]) == 0.0
        assert cluster_overlap(["science fiction"], []) == 0.0
        assert cluster_overlap([], []) == 0.0

    def test_drama_vs_drama_full_overlap(self) -> None:
        """Same single term should give 1.0 overlap."""
        score = cluster_overlap(["drama"], ["drama"])
        assert score == 1.0

    def test_drama_only_vs_sci_fi_low_overlap(self) -> None:
        """'drama' alone vs 'science fiction' alone should have low/zero overlap."""
        score = cluster_overlap(["drama"], ["science fiction"])
        # These are in different clusters, so no overlap
        assert score == 0.0


class TestExpandedClusters:
    """Tests for expanded cluster definitions covering new subgenres and themes."""

    def test_punk_subgenres_in_science_fiction(self) -> None:
        """All -punk subgenres should cluster with science fiction."""
        for term in ["biopunk", "dieselpunk", "solarpunk", "atompunk", "nanopunk"]:
            clusters = get_clusters_for_terms([term])
            assert "science_fiction" in clusters, f"{term} should be in science_fiction"

    def test_dark_fantasy_in_both_fantasy_and_horror(self) -> None:
        """'dark fantasy' bridges fantasy and horror clusters."""
        clusters = get_clusters_for_terms(["dark fantasy"])
        assert "fantasy" in clusters
        assert "horror_dark" in clusters

    def test_cosmic_horror_in_horror_dark(self) -> None:
        clusters = get_clusters_for_terms(["cosmic horror"])
        assert "horror_dark" in clusters

    def test_new_thriller_subgenres_in_crime_thriller(self) -> None:
        for term in ["psychological thriller", "domestic thriller", "legal thriller"]:
            clusters = get_clusters_for_terms([term])
            assert "crime_thriller" in clusters, f"{term} should be in crime_thriller"

    def test_romance_subgenres_in_romance(self) -> None:
        for term in ["romantic comedy", "slow burn", "enemies to lovers"]:
            clusters = get_clusters_for_terms([term])
            assert "romance" in clusters, f"{term} should be in romance"

    def test_western_subgenres_in_western(self) -> None:
        for term in ["spaghetti western", "neo-western", "weird western"]:
            clusters = get_clusters_for_terms([term])
            assert "western" in clusters, f"{term} should be in western"

    def test_space_western_bridges_western_and_sci_fi(self) -> None:
        """'space western' should bridge western and science fiction."""
        clusters = get_clusters_for_terms(["space western"])
        assert "western" in clusters
        assert "science_fiction" in clusters

    def test_game_genres_in_strategy_tactics(self) -> None:
        for term in ["tower defense", "city builder", "grand strategy", "4x"]:
            clusters = get_clusters_for_terms([term])
            assert (
                "strategy_tactics" in clusters
            ), f"{term} should be in strategy_tactics"

    def test_found_family_in_drama_emotional(self) -> None:
        clusters = get_clusters_for_terms(["found family"])
        assert "drama_emotional" in clusters


class TestNewClusters:
    """Tests for newly added cluster categories."""

    def test_supernatural_paranormal_cluster_exists(self) -> None:
        assert "supernatural_paranormal" in CLUSTERS

    def test_psychological_cluster_exists(self) -> None:
        assert "psychological" in CLUSTERS

    def test_nonfiction_documentary_cluster_exists(self) -> None:
        assert "nonfiction_documentary" in CLUSTERS

    def test_ghost_story_in_supernatural(self) -> None:
        clusters = get_clusters_for_terms(["ghost story"])
        assert "supernatural_paranormal" in clusters
        assert "horror_dark" in clusters

    def test_memoir_in_nonfiction(self) -> None:
        clusters = get_clusters_for_terms(["memoir"])
        assert "nonfiction_documentary" in clusters

    def test_true_crime_in_both_nonfiction_and_crime(self) -> None:
        """True crime bridges nonfiction and crime/thriller clusters."""
        clusters = get_clusters_for_terms(["true crime"])
        assert "nonfiction_documentary" in clusters
        assert "crime_thriller" in clusters

    def test_obsession_in_psychological(self) -> None:
        clusters = get_clusters_for_terms(["obsession"])
        assert "psychological" in clusters

    def test_cross_content_cosmic_horror_book_vs_game(self) -> None:
        """A cosmic horror book and a Lovecraftian game should overlap via clusters."""
        score = cluster_overlap(
            ["cosmic horror", "mystery"], ["eldritch", "survival horror"]
        )
        assert score > 0.0
