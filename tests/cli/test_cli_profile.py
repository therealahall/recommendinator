"""Tests for CLI profile commands."""

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from src.conversation.profile import ProfileGenerator
from src.storage.manager import StorageManager

from .conftest import _invoke_with_mocks


class TestProfileShow:
    """Tests for profile show command."""

    def test_show_profile_table(self, cli_runner: CliRunner) -> None:
        """Test showing profile in table format."""
        # Storage returns a wrapper dict with the profile under a "profile" key.
        profile_record = {
            "id": 1,
            "user_id": 1,
            "profile": {
                "genre_affinities": {"sci-fi": 4.5, "fantasy": 3.2},
                "theme_preferences": ["space exploration", "time travel"],
                "anti_preferences": ["gore", "romance"],
                "cross_media_patterns": ["Enjoys adaptations"],
            },
            "generated_at": "2026-01-01T00:00:00",
        }
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_preference_profile.return_value = profile_record
        result = _invoke_with_mocks(cli_runner, ["profile", "show"], mock_storage)

        assert result.exit_code == 0
        assert "sci-fi" in result.output
        assert "fantasy" in result.output
        assert "space exploration" in result.output
        assert "Generated: 2026-01-01T00:00:00" in result.output

    def test_show_profile_json(self, cli_runner: CliRunner) -> None:
        """Test showing profile in JSON format."""
        profile_record = {
            "id": 1,
            "user_id": 1,
            "profile": {
                "genre_affinities": {"sci-fi": 4.5, "fantasy": 3.2},
                "theme_preferences": ["space exploration", "time travel"],
                "anti_preferences": ["gore"],
                "cross_media_patterns": [],
            },
            "generated_at": "2026-01-01T00:00:00",
        }
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_preference_profile.return_value = profile_record
        result = _invoke_with_mocks(
            cli_runner,
            ["profile", "show", "--format", "json"],
            mock_storage,
        )

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["genre_affinities"]["sci-fi"] == 4.5
        assert "space exploration" in parsed["theme_preferences"]
        assert parsed["user_id"] == 1
        assert parsed["generated_at"] == "2026-01-01T00:00:00"

    def test_show_profile_no_profile(self, cli_runner: CliRunner) -> None:
        """Test showing profile when none exists."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_preference_profile.return_value = None
        result = _invoke_with_mocks(cli_runner, ["profile", "show"], mock_storage)

        assert result.exit_code == 0
        assert "no profile" in result.output.lower()

    def test_show_profile_no_profile_json(self, cli_runner: CliRunner) -> None:
        """Empty profile in JSON mode emits the full ProfileResponse shape.

        Bug: the CLI used to print "No profile generated yet" on stdout even
        for --format json, which broke parity with the web API's empty-state
        response. The JSON path now emits an empty ProfileResponse so parsers
        on the web side see a consistent shape.
        """
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_preference_profile.return_value = None
        result = _invoke_with_mocks(
            cli_runner,
            ["profile", "show", "--format", "json"],
            mock_storage,
        )

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert set(parsed.keys()) == {
            "user_id",
            "genre_affinities",
            "theme_preferences",
            "anti_preferences",
            "cross_media_patterns",
            "generated_at",
        }
        assert parsed["user_id"] == 1
        assert parsed["genre_affinities"] == {}
        assert parsed["theme_preferences"] == []
        assert parsed["generated_at"] is None


class TestProfileRegenerate:
    """Tests for profile regenerate command."""

    def test_regenerate_profile(self, cli_runner: CliRunner) -> None:
        """Test regenerating profile."""
        mock_storage = MagicMock(spec=StorageManager)
        with patch("src.cli.commands.ProfileGenerator") as mock_pg_cls:
            mock_pg = MagicMock(spec=ProfileGenerator)
            mock_profile = MagicMock()
            mock_profile.genre_affinities = {"sci-fi": 4.5}
            mock_profile.theme_preferences = ["space"]
            mock_profile.anti_preferences = []
            mock_profile.cross_media_patterns = []
            mock_pg.regenerate_and_save.return_value = mock_profile
            mock_pg_cls.return_value = mock_pg
            result = _invoke_with_mocks(
                cli_runner, ["profile", "regenerate"], mock_storage
            )

        assert result.exit_code == 0
        assert "Profile regenerated with 1 genre affinities." in result.output
