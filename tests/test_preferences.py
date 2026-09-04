from itertools import product

import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.recommendations.preferences import PreferenceAnalyzer, UserPreferences

RATINGS = [1, 2, 3, 4, 5]


def _rated_book(item_id, author, rating, genre):
    return ContentItem(
        id=item_id,
        title=f"Book {item_id}",
        author=author,
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.COMPLETED,
        rating=rating,
        metadata={"genre": genre},
    )


def test_preference_analyzer_basic():
    analyzer = PreferenceAnalyzer(min_rating=4)

    items = [
        ContentItem(
            id="1",
            title="Book 1",
            author="Author A",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        ),
        ContentItem(
            id="2",
            title="Book 2",
            author="Author A",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
        ),
        ContentItem(
            id="3",
            title="Book 3",
            author="Author B",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=3,
        ),
    ]

    preferences = analyzer.analyze(items)

    assert preferences.total_items == 3
    assert preferences.average_rating == 4.0
    assert "author a" in preferences.preferred_authors
    assert preferences.preferred_authors["author a"] == 1.0


def test_user_preferences_get_author_score():
    preferences = UserPreferences(
        preferred_authors={"author a": 0.8, "author b": 0.5},
        preferred_genres={},
        average_rating=4.5,
        total_items=10,
    )

    assert preferences.get_author_score("Author A") == 0.8
    assert preferences.get_author_score("Author B") == 0.5
    assert preferences.get_author_score("Unknown Author") == 0.0
    assert preferences.get_author_score(None) == 0.0


def test_preference_analyzer_cross_content_type():
    analyzer = PreferenceAnalyzer(min_rating=4)

    items = [
        ContentItem(
            id="1",
            title="Dune",
            author="Frank Herbert",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genre": "Science Fiction"},
        ),
        ContentItem(
            id="2",
            title="Mass Effect",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
            metadata={"genres": ["Action", "RPG", "Science Fiction"]},
        ),
        ContentItem(
            id="3",
            title="The Expanse",
            content_type=ContentType.TV_SHOW,
            status=ConsumptionStatus.COMPLETED,
            rating=4,
            metadata={"genre": "Science Fiction"},
        ),
    ]

    preferences = analyzer.analyze(items)

    assert preferences.total_items == 3
    assert "science fiction" in preferences.preferred_genres
    assert "frank herbert" in preferences.preferred_authors
    assert preferences.preferred_genres["science fiction"] == 1.0


class TestScoreNormalisationBounds:
    """Symptom: with recommendations.min_rating_for_preference below 4, generating
    recommendations either crashed (500 from GET /api/recommendations) or, worse,
    silently inverted taste, so the worst rated author scored a perfect match."""

    def test_min_rating_three_with_all_three_star_items_does_not_raise_regression(
        self,
    ):
        analyzer = PreferenceAnalyzer(min_rating=3)

        preferences = analyzer.analyze(
            [
                _rated_book("1", "Author A", 3, "Science Fiction"),
                _rated_book("2", "Author B", 3, "Fantasy"),
            ]
        )

        assert preferences.preferred_authors == {"author a": 1.0, "author b": 1.0}
        assert preferences.preferred_genres == {
            "science fiction": 1.0,
            "fantasy": 1.0,
        }

    def test_min_rating_one_does_not_invert_preference_regression(self):
        analyzer = PreferenceAnalyzer(min_rating=1)

        preferences = analyzer.analyze(
            [
                _rated_book("1", "Author A", 1, "Science Fiction"),
                _rated_book("2", "Author B", 2, "Fantasy"),
            ]
        )

        score_a = preferences.get_author_score("Author A")
        score_b = preferences.get_author_score("Author B")
        assert score_b > score_a
        assert -1.0 <= score_a <= 1.0
        assert -1.0 <= score_b <= 1.0

    @pytest.mark.parametrize("min_rating", RATINGS)
    @pytest.mark.parametrize(("rating_a", "rating_b"), list(product(RATINGS, repeat=2)))
    def test_scores_stay_in_range_for_every_min_rating(
        self, min_rating, rating_a, rating_b
    ):
        analyzer = PreferenceAnalyzer(min_rating=min_rating)

        preferences = analyzer.analyze(
            [
                _rated_book("1", "Author A", rating_a, "Science Fiction"),
                _rated_book("2", "Author B", rating_b, "Science Fiction"),
            ]
        )

        assert -1.0 <= preferences.get_author_score("Author A") <= 1.0
        assert -1.0 <= preferences.get_author_score("Author B") <= 1.0
        assert -1.0 <= preferences.get_genre_score("Science Fiction") <= 1.0

    @pytest.mark.parametrize("min_rating", RATINGS)
    def test_liked_ratings_rank_in_rating_order_at_every_min_rating(self, min_rating):
        analyzer = PreferenceAnalyzer(min_rating=min_rating)
        liked = [r for r in RATINGS if r >= min_rating]

        preferences = analyzer.analyze(
            [
                _rated_book(str(rating), f"Author {rating}", rating, "Science Fiction")
                for rating in liked
            ]
        )

        scores = [preferences.get_author_score(f"Author {r}") for r in liked]
        assert scores == sorted(scores)
        assert scores[-1] == 1.0

    def test_unrated_items_leave_both_buckets_empty(self):
        analyzer = PreferenceAnalyzer(min_rating=4)

        preferences = analyzer.analyze(
            [
                ContentItem(
                    id="1",
                    title="Book 1",
                    author="Author A",
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.COMPLETED,
                    metadata={"genre": "Science Fiction"},
                )
            ]
        )

        assert preferences.preferred_authors == {}
        assert preferences.disliked_authors == {}
        assert preferences.preferred_genres == {}
        assert preferences.average_rating == 0.0
        assert preferences.total_items == 1
        assert preferences.get_author_score("Author A") == 0.0


class TestPreferredWeightAccumulation:
    """The reweighting that fixed the normalisation is a linear rescale of the old
    weights at the default threshold of 4, so these pin the accumulated and
    default-threshold results the engine's scorers consume."""

    @pytest.mark.parametrize("min_rating", RATINGS)
    def test_a_rating_exactly_at_the_threshold_is_preferred(self, min_rating):
        analyzer = PreferenceAnalyzer(min_rating=min_rating)

        preferences = analyzer.analyze(
            [_rated_book("1", "Author A", min_rating, "Science Fiction")]
        )

        assert preferences.preferred_authors == {"author a": 1.0}
        assert preferences.disliked_authors == {}
        assert preferences.get_author_score("Author A") == 1.0

    def test_default_threshold_keeps_the_four_to_five_star_ratio(self):
        """At min_rating 4, a 4 star author still scores half a 5 star one."""
        analyzer = PreferenceAnalyzer(min_rating=4)

        preferences = analyzer.analyze(
            [
                _rated_book("1", "Author A", 5, "Science Fiction"),
                _rated_book("2", "Author B", 4, "Fantasy"),
            ]
        )

        assert preferences.preferred_authors == {"author a": 1.0, "author b": 0.5}

    @pytest.mark.parametrize("min_rating", RATINGS)
    def test_many_repeats_of_one_author_stay_at_the_ceiling(self, min_rating):
        analyzer = PreferenceAnalyzer(min_rating=min_rating)

        preferences = analyzer.analyze(
            [
                _rated_book(str(index), "Author A", 5, "Science Fiction")
                for index in range(20)
            ]
            + [_rated_book("21", "Author B", min_rating, "Fantasy")]
        )

        assert preferences.get_author_score("Author A") == 1.0
        assert 0.0 < preferences.get_author_score("Author B") <= 1.0
        assert preferences.get_genre_score("Science Fiction") == 1.0

    def test_disliked_only_author_scores_negative_within_range(self):
        analyzer = PreferenceAnalyzer(min_rating=4)

        preferences = analyzer.analyze(
            [
                _rated_book("1", "Author A", 1, "Fantasy"),
                _rated_book("2", "Author A", 1, "Fantasy"),
                _rated_book("3", "Author B", 5, "Science Fiction"),
            ]
        )

        assert preferences.get_author_score("Author A") == -1.0
        assert preferences.get_genre_score("Fantasy") == -1.0

    def test_items_without_an_author_only_feed_the_genre_buckets(self):
        """A missing author is skipped rather than keyed under an empty name."""
        analyzer = PreferenceAnalyzer(min_rating=4)

        preferences = analyzer.analyze(
            [
                ContentItem(
                    id="1",
                    title="Mass Effect",
                    content_type=ContentType.VIDEO_GAME,
                    status=ConsumptionStatus.COMPLETED,
                    rating=5,
                    metadata={"genre": "Science Fiction"},
                )
            ]
        )

        assert preferences.preferred_authors == {}
        assert preferences.preferred_genres == {"science fiction": 1.0}
        assert preferences.get_author_score("") == 0.0


class TestCreatorNamespaceIsFlatAcrossTypes:
    """Storage populates ``ContentItem.author`` for every content type: a book's
    author, a film's director, a show's creator, a game's developer."""

    @staticmethod
    def _rated(item_id, *, creator, rating, content_type):
        return ContentItem(
            id=item_id,
            title=f"Title {item_id}",
            author=creator,
            content_type=content_type,
            status=ConsumptionStatus.COMPLETED,
            rating=rating,
            metadata={"genre": "Science Fiction"},
        )

    def test_every_types_creator_enters_the_preferred_namespace(self):
        analyzer = PreferenceAnalyzer(min_rating=4)

        preferences = analyzer.analyze(
            [
                self._rated(
                    "1",
                    creator="Frank Herbert",
                    rating=5,
                    content_type=ContentType.BOOK,
                ),
                self._rated(
                    "2",
                    creator="Denis Villeneuve",
                    rating=5,
                    content_type=ContentType.MOVIE,
                ),
                self._rated(
                    "3",
                    creator="Vince Gilligan",
                    rating=5,
                    content_type=ContentType.TV_SHOW,
                ),
                self._rated(
                    "4",
                    creator="Mobius Digital",
                    rating=5,
                    content_type=ContentType.VIDEO_GAME,
                ),
            ]
        )

        assert preferences.preferred_authors == {
            "frank herbert": 1.0,
            "denis villeneuve": 1.0,
            "vince gilligan": 1.0,
            "mobius digital": 1.0,
        }

    def test_one_name_on_two_types_is_one_entry_that_nets_out(self):
        analyzer = PreferenceAnalyzer(min_rating=4)

        preferences = analyzer.analyze(
            [
                self._rated(
                    "1", creator="Alex Garland", rating=5, content_type=ContentType.BOOK
                ),
                self._rated(
                    "2",
                    creator="Alex Garland",
                    rating=1,
                    content_type=ContentType.MOVIE,
                ),
            ]
        )

        assert preferences.preferred_authors == {"alex garland": 1.0}
        assert preferences.disliked_authors == {"alex garland": 1.0}
        assert preferences.get_author_score("Alex Garland") == 0.0
