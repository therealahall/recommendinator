"""Tests for semantic genre cluster matching."""

from src.recommendations.genre_clusters import (
    cluster_overlap,
    cluster_similarity,
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

    def test_unknown_term_returns_empty(self) -> None:
        """Terms not in any cluster should return empty set."""
        assert get_clusters_for_terms(["xyzzy_not_a_genre"]) == set()


class TestClusterOverlap:
    """Tests for Jaccard similarity of cluster memberships."""

    def test_book_space_warfare_tv_war_share_cluster(self) -> None:
        """A book with 'space warfare' and TV with 'war' should share war_military."""
        score = cluster_overlap(["space warfare"], ["war"])
        assert score > 0.0

    def test_comedy_vs_horror_zero_overlap(self) -> None:
        """'comedy' and 'horror' should have zero cluster overlap."""
        score = cluster_overlap(["comedy"], ["horror"])
        assert score == 0.0

    def test_identical_terms_perfect_overlap(self) -> None:
        """Identical term lists should have 1.0 overlap."""
        score = cluster_overlap(["science fiction"], ["science fiction"])
        assert score == 1.0

    def test_empty_terms_zero_overlap(self) -> None:
        """Empty term list should give 0.0 overlap."""
        assert cluster_overlap([], ["science fiction"]) == 0.0
        assert cluster_overlap(["science fiction"], []) == 0.0
        assert cluster_overlap([], []) == 0.0


class TestClusterSimilarity:
    """Tests for comparing two already-derived cluster memberships.

    ``cluster_overlap`` derives both memberships and delegates here, so a
    caller comparing one item against many can derive each membership once.
    The two must agree on every input.
    """

    def test_it_agrees_with_deriving_the_memberships_inline(self) -> None:
        """The same terms score the same whichever entry point is used."""
        terms_a = ["science fiction", "war"]
        terms_b = ["space warfare"]

        assert cluster_similarity(
            get_clusters_for_terms(terms_a), get_clusters_for_terms(terms_b)
        ) == cluster_overlap(terms_a, terms_b)


class TestNewClusters:
    """Tests for newly added cluster categories."""

    def test_cross_content_cosmic_horror_book_vs_game(self) -> None:
        """A cosmic horror book and a Lovecraftian game should overlap via clusters."""
        score = cluster_overlap(
            ["cosmic horror", "mystery"], ["eldritch", "survival horror"]
        )
        assert score > 0.0
