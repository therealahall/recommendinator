from src.recommendations.genre_clusters import (
    cluster_overlap,
    cluster_similarity,
    get_clusters_for_terms,
)


class TestGetClustersForTerms:
    def test_space_warfare_in_multiple_clusters(self) -> None:
        clusters = get_clusters_for_terms(["space warfare"])
        assert "science_fiction" in clusters
        assert "war_military" in clusters
        assert "space_opera" in clusters

    def test_unknown_term_returns_empty(self) -> None:
        assert get_clusters_for_terms(["xyzzy_not_a_genre"]) == set()


class TestClusterOverlap:
    def test_book_space_warfare_tv_war_share_cluster(self) -> None:
        score = cluster_overlap(["space warfare"], ["war"])
        assert score > 0.0

    def test_comedy_vs_horror_zero_overlap(self) -> None:
        score = cluster_overlap(["comedy"], ["horror"])
        assert score == 0.0

    def test_identical_terms_perfect_overlap(self) -> None:
        score = cluster_overlap(["science fiction"], ["science fiction"])
        assert score == 1.0

    def test_empty_terms_zero_overlap(self) -> None:
        assert cluster_overlap([], ["science fiction"]) == 0.0
        assert cluster_overlap(["science fiction"], []) == 0.0
        assert cluster_overlap([], []) == 0.0


class TestClusterSimilarity:
    def test_it_agrees_with_deriving_the_memberships_inline(self) -> None:
        terms_a = ["science fiction", "war"]
        terms_b = ["space warfare"]

        assert cluster_similarity(
            get_clusters_for_terms(terms_a), get_clusters_for_terms(terms_b)
        ) == cluster_overlap(terms_a, terms_b)


class TestNewClusters:
    def test_cross_content_cosmic_horror_book_vs_game(self) -> None:
        score = cluster_overlap(
            ["cosmic horror", "mystery"], ["eldritch", "survival horror"]
        )
        assert score > 0.0
