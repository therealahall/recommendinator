"""Tests for CLI profile commands."""

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from src.recommendations.profile import ProfileGenerator
from src.web.api import ProfileResponse
from tests.factories import make_storage_mock

from .conftest import _invoke_with_mocks


def _stored_profile() -> dict:
    """A ``profiles.get`` record carrying one entry in every profile field."""
    return {
        "id": 1,
        "user_id": 1,
        "profile": {
            "genre_affinities": {"sci-fi": 4.5, "fantasy": 3.2},
            "theme_preferences": ["space exploration", "time travel"],
            "anti_preferences": ["gore"],
            "cross_media_patterns": ["Generally rates books higher than games"],
        },
        "generated_at": "2026-01-01T00:00:00",
    }


class TestProfileShow:
    """Tests for profile show command."""

    def test_show_profile_json(self, cli_runner: CliRunner) -> None:
        """Test showing profile in JSON format."""
        profile_record = _stored_profile()
        mock_storage = make_storage_mock()
        mock_storage.profiles.get.return_value = profile_record
        result = _invoke_with_mocks(
            cli_runner,
            ["profile", "show", "--format", "json"],
            mock_storage,
        )

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert set(parsed) == set(ProfileResponse.model_fields)
        assert parsed["genre_affinities"]["sci-fi"] == 4.5
        assert "space exploration" in parsed["theme_preferences"]
        assert parsed["user_id"] == 1
        assert parsed["generated_at"] == "2026-01-01T00:00:00"

    def test_table_names_every_stored_preference(self, cli_runner: CliRunner) -> None:
        mock_storage = make_storage_mock()
        mock_storage.profiles.get.return_value = _stored_profile()

        result = _invoke_with_mocks(cli_runner, ["profile", "show"], mock_storage)

        assert result.exit_code == 0
        assert "sci-fi" in result.output
        assert "4.5" in result.output
        assert "space exploration" in result.output
        assert "gore" in result.output
        assert "Generally rates books higher than games" in result.output
        assert "2026-01-01T00:00:00" in result.output

    def test_show_profile_no_profile_json(self, cli_runner: CliRunner) -> None:
        """Empty profile in JSON mode emits the full ProfileResponse shape."""
        mock_storage = make_storage_mock()
        mock_storage.profiles.get.return_value = None
        result = _invoke_with_mocks(
            cli_runner,
            ["profile", "show", "--format", "json"],
            mock_storage,
        )

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert set(parsed) == set(ProfileResponse.model_fields)
        assert parsed["user_id"] == 1
        assert parsed["genre_affinities"] == {}
        assert parsed["theme_preferences"] == []
        assert parsed["generated_at"] is None


class TestProfileRegenerate:
    """Tests for profile regenerate command."""

    def test_regenerate_profile(self) -> None:
        """The result lands on stdout and the progress line on stderr.

        Chatter shares the channel `recommend` uses for it, so a run whose
        result is piped is not interleaved with what it was doing.
        """
        mock_storage = make_storage_mock()
        with patch("src.cli.commands._profile.ProfileGenerator") as mock_pg_cls:
            mock_pg = MagicMock(spec=ProfileGenerator)
            mock_profile = MagicMock()
            mock_profile.genre_affinities = {"sci-fi": 4.5}
            mock_profile.theme_preferences = ["space"]
            mock_profile.anti_preferences = []
            mock_profile.cross_media_patterns = []
            mock_pg.regenerate_and_save.return_value = mock_profile
            mock_pg_cls.return_value = mock_pg
            result = _invoke_with_mocks(
                CliRunner(), ["profile", "regenerate"], mock_storage
            )

        assert result.exit_code == 0
        assert "Profile regenerated with 1 genre affinities." in result.stdout
        assert "Analyzing your library..." in result.stderr
        assert "Analyzing your library..." not in result.stdout
