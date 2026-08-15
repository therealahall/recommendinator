"""Tests for normalized content length preferences."""

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

# ---------------------------------------------------------------------------
# get_length_value tests
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# classify_length tests
# ---------------------------------------------------------------------------


class TestClassifyLength:
    def test_long_book(self) -> None:
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": 800})
        assert classify_length(item) == LengthPreference.LONG

    def test_no_metadata_returns_none(self) -> None:
        item = make_item(content_type=ContentType.BOOK, metadata={})
        assert classify_length(item) is None

    def test_boundary_short_max(self) -> None:
        """Value exactly at short_max boundary is classified as short."""
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": 250})
        assert classify_length(item) == LengthPreference.SHORT

    def test_boundary_medium_max(self) -> None:
        """Value exactly at medium_max boundary is classified as medium."""
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": 500})
        assert classify_length(item) == LengthPreference.MEDIUM

    def test_boundary_video_game_short_max(self) -> None:
        """Ten average hours is the last short game, eleven the first medium one."""
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
        """Forty average hours is the last medium game, forty-one the first long one."""
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
        """A fractional average is truncated, never rounded, at both band edges."""
        item = make_item(
            content_type=ContentType.VIDEO_GAME,
            metadata={"average_playtime_hours": average},
        )
        assert classify_length(item) == expected

    def test_zero_average_is_a_short_game(self) -> None:
        """Zero is a length, not a blank, which is why RAWG never writes its 0.

        See ``test_enrich_game_with_no_playtime_writes_no_average`` in the RAWG
        provider tests for the other half of this pair.
        """
        item = make_item(
            content_type=ContentType.VIDEO_GAME,
            metadata={"average_playtime_hours": 0},
        )
        assert classify_length(item) == LengthPreference.SHORT


# ---------------------------------------------------------------------------
# score_length_match tests
# ---------------------------------------------------------------------------


class TestScoreLengthMatch:
    """Tests for the soft scoring function."""

    def test_any_preference_returns_1(self) -> None:
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": 1000})
        assert score_length_match(item, {"book": "any"}) == 1.0

    def test_no_preference_defaults_to_1(self) -> None:
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": 1000})
        assert score_length_match(item, {}) == 1.0

    def test_exact_match_returns_1(self) -> None:
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": 200})
        assert score_length_match(item, {"book": "short"}) == 1.0

    def test_adjacent_category_returns_07(self) -> None:
        """Medium item with short preference is adjacent (distance 1)."""
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": 350})
        assert score_length_match(item, {"book": "short"}) == 0.7

    def test_opposite_ends_returns_04(self) -> None:
        """Long item with short preference is opposite (distance 2)."""
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": 800})
        assert score_length_match(item, {"book": "short"}) == 0.4

    def test_no_metadata_returns_08(self) -> None:
        """Items without length metadata get benefit of the doubt."""
        item = make_item(content_type=ContentType.BOOK, metadata={})
        assert score_length_match(item, {"book": "short"}) == 0.8

    def test_different_content_type_not_affected(self) -> None:
        """A movie preference does not penalize a book."""
        item = make_item(content_type=ContentType.BOOK, metadata={"pages": 800})
        assert score_length_match(item, {"movie": "short"}) == 1.0

    @pytest.mark.parametrize("average", [None, "", "unknown", []])
    def test_game_with_unusable_rawg_playtime_returns_08(self, average: Any) -> None:
        """A blank or non-numeric average is no length, and is not an error."""
        item = make_item(
            content_type=ContentType.VIDEO_GAME,
            metadata={"average_playtime_hours": average},
        )
        assert classify_length(item) is None
        assert score_length_match(item, {"video_game": "long"}) == 0.8


# ---------------------------------------------------------------------------
# Regression tests
# ---------------------------------------------------------------------------


class TestVideoGameLengthRegression:
    """Video game length was read from the player's own playtime.

    Symptom: a roguelike with 300 logged hours classified as "long" and a
    100-hour JRPG abandoned after two hours classified as "short", so asking
    for short games returned the ones the user had barely played.

    Root cause: classification read ``playtime_hours``, which Steam fills with
    the user's own recorded playtime and the generic importers fill from the
    ``hours_played`` column.

    Fix: classification reads only ``average_playtime_hours``, RAWG's average
    across players, which describes the game rather than the player.
    """

    def test_heavily_played_game_is_not_long(self) -> None:
        item = make_item(
            content_type=ContentType.VIDEO_GAME,
            metadata={"playtime_minutes": 18000, "playtime_hours": 300.0},
        )
        assert get_length_value(item) is None
        assert classify_length(item) is None
        assert score_length_match(item, {"video_game": "short"}) == 0.8

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
        """The item Steam really builds carries no length, hand-built dicts aside.

        Steam is the source that reported the bug, so this takes its ingestion
        output rather than a fixture shaped like it: a new playtime key added
        there would fail here.
        """
        mock_get_games.return_value = [
            {"appid": 12345, "name": "Vampire Survivors", "playtime_forever": 18000}
        ]

        item = next(iter(_fetch_steam_games("test_key", steam_id="76561198000000000")))

        assert item.metadata["playtime_hours"] == 300.0
        assert get_length_value(item) is None
        assert score_length_match(item, {"video_game": "short"}) == 0.8
