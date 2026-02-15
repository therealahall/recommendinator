"""Tests for semantic genre cluster matching."""

from src.recommendations.genre_clusters import (
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
