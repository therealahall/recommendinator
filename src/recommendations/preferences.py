import logging
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from src.models.content import ContentItem
from src.recommendations.genre_normalizer import extract_and_normalize_genres

logger = logging.getLogger(__name__)


_NAME_SUFFIXES = frozenset(
    {
        "inc",
        "ltd",
        "llc",
        "co",
        "corp",
        "plc",
        "gmbh",
        "sa",
        "limited",
        "lp",
        "pty",
        "pty ltd",
        "ab",
        "oy",
        "jr",
        "sr",
        "ii",
        "iii",
    }
)


def creator_names(names: str) -> list[str]:
    """Storage joins a work's creators with commas, in whatever order its source
    listed them. A suffix stays on its name: split off, "Ltd." would match every
    other studio ending in it."""
    split: list[str] = []
    for part in names.split(","):
        name = part.strip()
        if not name:
            continue
        if split and name.replace(".", "").lower() in _NAME_SUFFIXES:
            split[-1] = f"{split[-1]}, {name}"
        else:
            split.append(name)
    return split


def split_creators(names: str) -> frozenset[str]:
    return frozenset(name.lower() for name in creator_names(names))


#: Ratings' worth of the user's own mean each genre is shrunk toward, so a genre
#: rated once or twice cannot claim a strong taste.
_GENRE_PRIOR_RATINGS = 2

#: Stars above or below the user's own mean that score a genre a full 1 or -1.
_GENRE_FULL_SCALE_STARS = 1.0


@dataclass(frozen=True)
class GenreTaste:
    #: In [-1, 1]: how far above or below the user's own mean the genre is rated.
    score: float
    #: How much it says about a candidate: more ratings say more, and a genre on
    #: most of the library says next to nothing.
    weight: float


def _genre_tastes(
    genre_ratings: dict[str, list[int]], mean: float, rated_items: int
) -> dict[str, GenreTaste]:
    tastes: dict[str, GenreTaste] = {}
    for genre, ratings in genre_ratings.items():
        count = len(ratings)
        deviation = (sum(ratings) - count * mean) / (count + _GENRE_PRIOR_RATINGS)
        tastes[genre] = GenreTaste(
            score=max(-1.0, min(1.0, deviation / _GENRE_FULL_SCALE_STARS)),
            weight=count / (count + _GENRE_PRIOR_RATINGS) * (1 - count / rated_items),
        )
    return tastes


class UserPreferences:
    def __init__(
        self,
        preferred_authors: dict[str, float],
        average_rating: float,
        total_items: int,
        disliked_authors: dict[str, float] | None = None,
        genre_tastes: dict[str, GenreTaste] | None = None,
    ) -> None:
        self.preferred_authors = preferred_authors
        self.average_rating = average_rating
        self.total_items = total_items
        self.disliked_authors = disliked_authors or {}
        self.genre_tastes = genre_tastes or {}

    def get_author_score(self, author: str | None) -> float:
        if not author:
            return 0.0
        return max(
            (
                self.preferred_authors.get(name, 0.0)
                - self.disliked_authors.get(name, 0.0)
                for name in split_creators(author)
            ),
            default=0.0,
        )

    def get_genre_score(self, genre: str | None) -> float:
        taste = self.genre_tastes.get(genre.lower()) if genre else None
        return taste.score if taste else 0.0


class PreferenceAnalyzer:
    def __init__(self, min_rating: int = 4) -> None:
        self.min_rating = min_rating

    def analyze(self, consumed_items: list[ContentItem]) -> UserPreferences:
        if not consumed_items:
            return UserPreferences({}, 0.0, 0)

        ratings = [item.rating for item in consumed_items if item.rating is not None]
        avg_rating = sum(ratings) / len(ratings) if ratings else 0.0

        author_ratings = [
            (creator, item.rating)
            for item in consumed_items
            if item.author and item.rating is not None
            for creator in split_creators(item.author)
        ]
        author_scores, disliked_authors = self._score_attributes(author_ratings)

        genre_ratings: dict[str, list[int]] = defaultdict(list)
        for item in consumed_items:
            if item.rating is not None:
                for genre in extract_and_normalize_genres(item.metadata):
                    genre_ratings[genre.lower()].append(item.rating)

        return UserPreferences(
            preferred_authors=author_scores,
            average_rating=avg_rating,
            total_items=len(consumed_items),
            disliked_authors=disliked_authors,
            genre_tastes=_genre_tastes(genre_ratings, avg_rating, len(ratings)),
        )

    def _score_attributes(
        self, attribute_ratings: Sequence[tuple[str, int | float]]
    ) -> tuple[dict[str, float], dict[str, float]]:
        preferred: dict[str, float] = {}
        disliked: dict[str, float] = {}

        for value, rating in attribute_ratings:
            if rating >= self.min_rating:
                weight = rating - self.min_rating + 1
                preferred[value] = preferred.get(value, 0.0) + weight
            else:
                weight = abs(rating - 3) / 2.0
                disliked[value] = disliked.get(value, 0.0) + weight

        # Every preferred weight is at least 1, so this maximum is never zero
        if preferred:
            max_score = max(preferred.values())
            preferred = {k: v / max_score for k, v in preferred.items()}

        # A rating of 3 weighs nothing, so this maximum can be zero
        if disliked:
            max_score = max(disliked.values())
            if max_score > 0:
                disliked = {k: v / max_score for k, v in disliked.items()}

        return preferred, disliked
