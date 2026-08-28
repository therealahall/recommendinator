from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.ingestion.sources.steam.steam import _fetch_steam_games
from src.models.content import ContentType
from src.recommendations.content_length import (
    LengthPreference,
    classify_length,
    get_length_value,
    score_length_match,
)
from tests.factories import make_item


def _unknown_game_score(preferences: dict[str, str]) -> float:
    """What a game carrying no length at all scores, whatever the tuning is."""
    return score_length_match(
        make_item(content_type=ContentType.VIDEO_GAME, metadata={}), preferences
    )


class TestGetLengthValue:
    def test_book_pages(self) -> None:
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": 300})
        assert get_length_value(item) == 300

    def test_book_num_pages(self) -> None:
        item = make_item(content_type=ContentType.BOOK, metadata={"num_pages": 150})
        assert get_length_value(item) == 150

    def test_video_game_reads_no_other_playtime_key(self) -> None:
        """Only RAWG's key counts: the other two spellings are not a game length."""
        item = make_item(
            content_type=ContentType.VIDEO_GAME,
            metadata={"playtime_hours": 300, "main_story_hours": 55},
        )
        assert get_length_value(item) is None

    def test_non_numeric_value_returns_none(self) -> None:
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": "unknown"})
        assert get_length_value(item) is None

    def test_string_numeric_value_converts(self) -> None:
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": "300"})
        assert get_length_value(item) == 300


class TestClassifyLength:
    def test_long_book(self) -> None:
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": 800})
        assert classify_length(item) == LengthPreference.LONG

    def test_no_metadata_returns_none(self) -> None:
        item = make_item(content_type=ContentType.BOOK, metadata={})
        assert classify_length(item) is None

    def test_boundary_short_max(self) -> None:
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": 250})
        assert classify_length(item) == LengthPreference.SHORT

    def test_boundary_medium_max(self) -> None:
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": 500})
        assert classify_length(item) == LengthPreference.MEDIUM

    def test_boundary_video_game_short_max(self) -> None:
        short = make_item(
            content_type=ContentType.VIDEO_GAME,
            metadata={"average_playtime_hours": 10},
        )
        medium = make_item(
            content_type=ContentType.VIDEO_GAME,
            metadata={"average_playtime_hours": 11},
        )
        assert classify_length(short) == LengthPreference.SHORT
        assert classify_length(medium) == LengthPreference.MEDIUM

    def test_boundary_video_game_medium_max(self) -> None:
        medium = make_item(
            content_type=ContentType.VIDEO_GAME,
            metadata={"average_playtime_hours": 40},
        )
        long_game = make_item(
            content_type=ContentType.VIDEO_GAME,
            metadata={"average_playtime_hours": 41},
        )
        assert classify_length(medium) == LengthPreference.MEDIUM
        assert classify_length(long_game) == LengthPreference.LONG

    @pytest.mark.parametrize(
        ("average", "expected"),
        [(10.9, LengthPreference.SHORT), (40.9, LengthPreference.MEDIUM)],
    )
    def test_fractional_average_truncates_toward_the_shorter_band(
        self, average: float, expected: LengthPreference
    ) -> None:
        item = make_item(
            content_type=ContentType.VIDEO_GAME,
            metadata={"average_playtime_hours": average},
        )
        assert classify_length(item) == expected

    def test_zero_average_is_a_short_game(self) -> None:
        """Zero is a length, not a blank, which is why RAWG never writes its 0."""
        item = make_item(
            content_type=ContentType.VIDEO_GAME,
            metadata={"average_playtime_hours": 0},
        )
        assert classify_length(item) == LengthPreference.SHORT


class TestScoreLengthMatch:
    def test_any_preference_returns_1(self) -> None:
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": 1000})
        assert score_length_match(item, {"book": "any"}) == 1.0

    def test_no_preference_defaults_to_1(self) -> None:
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": 1000})
        assert score_length_match(item, {}) == 1.0

    def test_exact_match_returns_1(self) -> None:
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": 200})
        assert score_length_match(item, {"book": "short"}) == 1.0

    def test_score_falls_as_the_length_moves_further_from_the_preference(self) -> None:
        short = make_item(content_type=ContentType.BOOK, metadata={"pages": 200})
        adjacent = make_item(content_type=ContentType.BOOK, metadata={"pages": 350})
        opposite = make_item(content_type=ContentType.BOOK, metadata={"pages": 800})

        assert (
            score_length_match(short, {"book": "short"})
            > score_length_match(adjacent, {"book": "short"})
            > score_length_match(opposite, {"book": "short"})
        )

    def test_no_metadata_scores_between_a_match_and_a_mismatch(self) -> None:
        unknown = make_item(content_type=ContentType.BOOK, metadata={})
        matching = make_item(content_type=ContentType.BOOK, metadata={"pages": 200})
        mismatched = make_item(content_type=ContentType.BOOK, metadata={"pages": 800})

        assert (
            score_length_match(matching, {"book": "short"})
            > score_length_match(unknown, {"book": "short"})
            > score_length_match(mismatched, {"book": "short"})
        )

    def test_different_content_type_not_affected(self) -> None:
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": 800})
        assert score_length_match(item, {"movie": "short"}) == 1.0

    @pytest.mark.parametrize("average", [None, "", "unknown", []])
    def test_game_with_unusable_rawg_playtime_has_no_length(self, average: Any) -> None:
        item = make_item(
            content_type=ContentType.VIDEO_GAME,
            metadata={"average_playtime_hours": average},
        )
        assert classify_length(item) is None
        assert score_length_match(item, {"video_game": "long"}) == _unknown_game_score(
            {"video_game": "long"}
        )


class TestVideoGameLengthRegression:
    """Video game length was read from the player's own playtime."""

    def test_heavily_played_game_is_not_long(self) -> None:
        item = make_item(
            content_type=ContentType.VIDEO_GAME,
            metadata={"playtime_minutes": 18000, "playtime_hours": 300.0},
        )
        assert get_length_value(item) is None
        assert classify_length(item) is None
        assert score_length_match(item, {"video_game": "short"}) == _unknown_game_score(
            {"video_game": "short"}
        )

    def test_rawg_average_wins_over_own_playtime(self) -> None:
        item = make_item(
            content_type=ContentType.VIDEO_GAME,
            metadata={"playtime_hours": 300.0, "average_playtime_hours": 6},
        )
        assert classify_length(item) == LengthPreference.SHORT

    @patch("src.ingestion.sources.steam.steam.get_owned_games")
    def test_steam_ingestion_writes_no_key_the_scorer_reads(
        self, mock_get_games: MagicMock
    ) -> None:
        """Steam is the source that reported the bug, so this takes its ingestion
        output rather than a fixture shaped like it: a new playtime key added there
        would fail here."""
        mock_get_games.return_value = [
            {"appid": 12345, "name": "Vampire Survivors", "playtime_forever": 18000}
        ]

        item = next(iter(_fetch_steam_games("test_key", steam_id="76561198000000000")))

        assert item.metadata["playtime_hours"] == 300.0
        assert get_length_value(item) is None
        assert score_length_match(item, {"video_game": "short"}) == _unknown_game_score(
            {"video_game": "short"}
        )
