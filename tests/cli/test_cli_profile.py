import json
from unittest.mock import patch

from click.testing import CliRunner

from src.cli.commands._profile import ANTI_FLAG
from src.recommendations.profile import AFFINITY_LIMIT
from src.web.api._profile import ProfileResponse
from tests.factories import make_storage_mock

from .conftest import _invoke_with_mocks


def _stored_profile() -> dict:
    return {
        "id": 1,
        "user_id": 1,
        "profile": {
            "genre_affinities": {"sci-fi": 4.5, "fantasy": 3.2, "gore": 1.5},
            "author_affinities": {"Terry Brooks": 4.0},
            "theme_preferences": ["space exploration", "time travel"],
            "anti_preferences": ["gore"],
            "cross_media_patterns": ["Generally rates books higher than games"],
            "generated_at": "2026-01-01T00:00:00",
        },
        "generated_at": "2026-01-01T00:00:00",
    }


class TestProfileShow:
    def test_show_profile_json(self, cli_runner: CliRunner) -> None:
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
        assert {"genre": "sci-fi", "score": 4.5, "anti": False} in parsed[
            "genre_affinities"
        ]
        assert parsed["author_affinities"] == [{"author": "Terry Brooks", "score": 4.0}]
        assert "space exploration" in parsed["theme_preferences"]
        assert parsed["has_content"] is True
        assert parsed["user_id"] == 1
        assert parsed["generated_at"] == "2026-01-01T00:00:00"

    def test_table_names_every_stored_preference(self, cli_runner: CliRunner) -> None:
        mock_storage = make_storage_mock()
        mock_storage.profiles.get.return_value = _stored_profile()

        result = _invoke_with_mocks(cli_runner, ["profile", "show"], mock_storage)

        assert result.exit_code == 0
        assert "sci-fi: 4.5" in result.output
        assert "Terry Brooks: 4.0" in result.output
        assert "space exploration" in result.output
        assert "Generally rates books higher than games" in result.output
        assert "2026-01-01T00:00:00" in result.output

    def test_a_disliked_genre_is_named_once_with_a_flag(
        self, cli_runner: CliRunner
    ) -> None:
        mock_storage = make_storage_mock()
        mock_storage.profiles.get.return_value = _stored_profile()

        result = _invoke_with_mocks(cli_runner, ["profile", "show"], mock_storage)

        assert f"gore: 1.5  {ANTI_FLAG}" in result.output
        assert result.output.count("gore") == 1

    def test_the_table_prints_the_whole_bounded_list_not_a_shorter_slice(
        self, cli_runner: CliRunner
    ) -> None:
        mock_storage = make_storage_mock()
        mock_storage.profiles.get.return_value = {
            "id": 1,
            "user_id": 1,
            "profile": {
                "genre_affinities": {
                    f"genre{index}": 5.0 for index in range(AFFINITY_LIMIT)
                }
            },
        }

        result = _invoke_with_mocks(cli_runner, ["profile", "show"], mock_storage)

        for index in range(AFFINITY_LIMIT):
            assert f"genre{index}: 5.0" in result.output

    def test_show_profile_no_profile_json(self, cli_runner: CliRunner) -> None:
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
        assert parsed["genre_affinities"] == []
        assert parsed["author_affinities"] == []
        assert parsed["theme_preferences"] == []
        assert parsed["has_content"] is False
        assert parsed["generated_at"] is None

    def test_a_stamped_but_vacuous_profile_reads_as_none_generated(
        self, cli_runner: CliRunner
    ) -> None:
        mock_storage = make_storage_mock()
        mock_storage.profiles.get.return_value = {
            "id": 1,
            "user_id": 1,
            "profile": {"generated_at": "2026-01-01T00:00:00"},
            "generated_at": "2026-01-01T00:00:00",
        }

        result = _invoke_with_mocks(cli_runner, ["profile", "show"], mock_storage)

        assert "No profile generated yet" in result.output


class TestProfileRegenerate:
    def test_regenerate_prints_the_profile_it_stored(self) -> None:
        mock_storage = make_storage_mock()
        mock_storage.profiles.get.return_value = _stored_profile()
        with patch("src.recommendations.profile.ProfileGenerator") as mock_pg_cls:
            result = _invoke_with_mocks(
                CliRunner(), ["profile", "regenerate"], mock_storage
            )

        mock_pg_cls.return_value.regenerate_and_save.assert_called_once_with(1)
        assert result.exit_code == 0
        assert "sci-fi: 4.5" in result.stdout
        assert "Terry Brooks: 4.0" in result.stdout
        assert "Analyzing your library..." in result.stderr
        assert "Analyzing your library..." not in result.stdout

    def test_regenerate_json_matches_the_web_response_shape(self) -> None:
        mock_storage = make_storage_mock()
        mock_storage.profiles.get.return_value = _stored_profile()
        with patch("src.recommendations.profile.ProfileGenerator"):
            result = _invoke_with_mocks(
                CliRunner(),
                ["profile", "regenerate", "--format", "json"],
                mock_storage,
            )

        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        assert set(parsed) == set(ProfileResponse.model_fields)
