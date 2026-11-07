import pytest

from src.recommendations.genre_clusters import CLUSTERS, get_clusters_for_terms
from src.recommendations.genre_normalizer import (
    extract_and_normalize_genres,
    normalize_term,
    normalize_terms,
)


class TestCompoundGenreSplitting:
    def test_sci_fi_and_fantasy_splits(self) -> None:
        result = normalize_terms(["Sci-Fi & Fantasy"])
        assert "science fiction" in result
        assert "fantasy" in result

    def test_and_variant_splits(self) -> None:
        result = normalize_terms(["Action and Adventure"])
        assert "action" in result
        assert "adventure" in result

    def test_compound_with_existing_term_deduplicates(self) -> None:
        result = normalize_terms(["Sci-Fi & Fantasy", "Science Fiction"])
        assert result.count("science fiction") == 1
        assert "fantasy" in result

    def test_non_compound_term_unchanged(self) -> None:
        result = normalize_terms(["Drama"])
        assert result == ["drama"]


class TestNormalizeTermIndividual:
    @pytest.mark.parametrize(
        ("term", "canonical"),
        [
            ("Sci-Fi", "science fiction"),
            ("sci fi", "science fiction"),
            ("Post-Apocalyptic", "post-apocalyptic"),
            ("post apocalyptic", "post-apocalyptic"),
            ("Turn_Based", "turn-based"),
        ],
    )
    def test_a_separator_variant_resolves_to_its_canonical_term(
        self, term: str, canonical: str
    ) -> None:
        assert normalize_term(term) == canonical

    @pytest.mark.parametrize(
        "term",
        [
            "fiction",
            "Singleplayer",
            "Single-player",
            "Steam Trading Cards",
            "to-read",
            "Currently Reading",
            "read in 2019",
            "2019 reads",
            "2020-challenge",
        ],
    )
    def test_a_blocked_term_is_dropped(self, term: str) -> None:
        assert normalize_term(term) is None

    def test_an_unknown_term_passes_through_folded(self) -> None:
        assert normalize_term("Hope-Punk  Fiction") == "hope punk fiction"

    def test_every_cluster_term_survives_as_itself(self) -> None:
        cluster_terms = {term for terms in CLUSTERS.values() for term in terms}
        assert {term for term in cluster_terms if normalize_term(term) != term} == set()

    def test_a_game_tagged_only_flight_simulator_reaches_its_cluster(self) -> None:
        terms = extract_and_normalize_genres({"genres": ["Flight Simulator"]})
        assert get_clusters_for_terms(terms) == {"simulation_sandbox"}


class TestSubgenrePreservation:
    def test_dark_fantasy_preserved(self) -> None:
        assert normalize_term("dark fantasy") == "dark fantasy"

    def test_cosmic_horror_from_lovecraftian(self) -> None:
        assert normalize_term("lovecraftian") == "cosmic horror"

    def test_fantasy_fiction_still_normalizes(self) -> None:
        assert normalize_term("fantasy fiction") == "fantasy"


class TestExtractAndNormalizeGenres:
    @pytest.mark.parametrize("field", ["genres", "tags"])
    def test_json_array_string_yields_every_element(self, field: str) -> None:
        result = extract_and_normalize_genres({field: '["Science Fiction", "Fantasy"]'})
        assert result == ["science fiction", "fantasy"]

    @pytest.mark.parametrize("field", ["genres", "tags"])
    def test_comma_separated_string_yields_every_element_stripped(
        self, field: str
    ) -> None:
        result = extract_and_normalize_genres({field: " Science Fiction , Fantasy "})
        assert result == ["science fiction", "fantasy"]

    @pytest.mark.parametrize("field", ["genres", "tags"])
    def test_truncated_json_string_keeps_the_element_behind_the_bracket(
        self, field: str
    ) -> None:
        result = extract_and_normalize_genres({field: "[Drama, Fantasy"})
        assert result == ["drama", "fantasy"]

    def test_genre_list_yields_every_element(self) -> None:
        result = extract_and_normalize_genres({"genre": ["Science Fiction", "Fantasy"]})
        assert result == ["science fiction", "fantasy"]

    def test_genre_genres_and_tags_are_unioned_and_deduplicated(self) -> None:
        result = extract_and_normalize_genres(
            {
                "genre": "Drama",
                "genres": ["Fantasy", "Drama"],
                "tags": '["Fantasy", "Horror"]',
            }
        )
        assert result == ["drama", "fantasy", "horror"]

    @pytest.mark.parametrize(
        "metadata", [None, {}, {"genres": ""}, {"genres": []}, {"tags": []}]
    )
    def test_absent_or_empty_metadata_yields_no_terms(
        self, metadata: dict | None
    ) -> None:
        assert extract_and_normalize_genres(metadata) == []
