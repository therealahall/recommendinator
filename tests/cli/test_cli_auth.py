"""Tests for CLI auth commands."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from src.storage.manager import StorageManager

from .conftest import _invoke_with_mocks


class TestAuthStatus:
    """Tests for auth status command."""

    def test_auth_status_no_sources_enabled(self, cli_runner: CliRunner) -> None:
        """Test auth status when no OAuth sources are enabled."""
        mock_storage = MagicMock(spec=StorageManager)
        with (
            patch("src.cli.commands.is_gog_enabled", return_value=False),
            patch("src.cli.commands.is_epic_enabled", return_value=False),
        ):
            result = _invoke_with_mocks(cli_runner, ["auth", "status"], mock_storage)

        assert result.exit_code == 0
        assert "No OAuth sources are enabled" in result.output

    def test_auth_status_shows_sources(self, cli_runner: CliRunner) -> None:
        """Test auth status lists available OAuth sources."""
        mock_storage = MagicMock(spec=StorageManager)
        config = {
            "inputs": [
                {"source": "gog", "enabled": True},
                {"source": "epic_games", "enabled": True},
            ],
        }
        with (
            patch("src.cli.commands.is_gog_enabled", return_value=True),
            patch("src.cli.commands.has_gog_token", return_value=True),
            patch("src.cli.commands.is_epic_enabled", return_value=True),
            patch("src.cli.commands.has_epic_token", return_value=False),
        ):
            result = _invoke_with_mocks(
                cli_runner, ["auth", "status"], mock_storage, config=config
            )

        assert result.exit_code == 0
        assert "gog: connected" in result.output
        assert "epic: not connected" in result.output


class TestAuthConnect:
    """Tests for auth connect command."""

    def test_connect_source_not_enabled(self, cli_runner: CliRunner) -> None:
        """Test connecting a source that is not enabled in config."""
        mock_storage = MagicMock(spec=StorageManager)
        with patch("src.cli.commands.is_gog_enabled", return_value=False):
            result = _invoke_with_mocks(
                cli_runner,
                ["auth", "connect", "--source", "gog"],
                mock_storage,
            )

        assert result.exit_code != 0
        assert "not enabled in config" in result.output

    def test_connect_gog(self, cli_runner: CliRunner) -> None:
        """Test connecting GOG account."""
        mock_storage = MagicMock(spec=StorageManager)
        config = {"inputs": [{"source": "gog", "enabled": True}]}
        # Auth codes must be >=20 chars to pass extract_code_from_input validation
        auth_code = "test-auth-code-abc123xyz"
        with (
            patch("src.cli.commands.is_gog_enabled", return_value=True),
            patch(
                "src.cli.commands.get_gog_auth_url",
                return_value="https://auth.gog.com/auth?client_id=test",
            ),
            patch(
                "src.cli.commands.exchange_gog_code",
                return_value={"refresh_token": "test-token"},
            ),
            patch("src.cli.commands.save_gog_token") as mock_save,
            patch("webbrowser.open"),
        ):
            result = _invoke_with_mocks(
                cli_runner,
                ["auth", "connect", "--source", "gog"],
                mock_storage,
                config=config,
                input_text=f"{auth_code}\n",
            )

        assert result.exit_code == 0
        assert "connected successfully" in result.output.lower()
        mock_save.assert_called_once_with(mock_storage, "test-token", user_id=1)

    def test_connect_gog_no_browser(self, cli_runner: CliRunner) -> None:
        """Test --no-browser suppresses webbrowser.open."""
        mock_storage = MagicMock(spec=StorageManager)
        auth_code = "test-auth-code-abc123xyz"
        with (
            patch("src.cli.commands.is_gog_enabled", return_value=True),
            patch(
                "src.cli.commands.get_gog_auth_url",
                return_value="https://auth.gog.com/auth?client_id=test",
            ),
            patch(
                "src.cli.commands.exchange_gog_code",
                return_value={"refresh_token": "test-token"},
            ),
            patch("src.cli.commands.save_gog_token"),
            patch("webbrowser.open") as mock_open,
        ):
            result = _invoke_with_mocks(
                cli_runner,
                ["auth", "connect", "--source", "gog", "--no-browser"],
                mock_storage,
                input_text=f"{auth_code}\n",
            )

        assert result.exit_code == 0
        mock_open.assert_not_called()

    def test_connect_exchange_fails(self, cli_runner: CliRunner) -> None:
        """Test that connect handles exchange exceptions gracefully."""
        mock_storage = MagicMock(spec=StorageManager)
        auth_code = "test-auth-code-abc123xyz"
        with (
            patch("src.cli.commands.is_gog_enabled", return_value=True),
            patch(
                "src.cli.commands.get_gog_auth_url",
                return_value="https://auth.gog.com",
            ),
            patch(
                "src.cli.commands.exchange_gog_code",
                side_effect=RuntimeError("network error"),
            ),
            patch("webbrowser.open"),
        ):
            result = _invoke_with_mocks(
                cli_runner,
                ["auth", "connect", "--source", "gog"],
                mock_storage,
                input_text=f"{auth_code}\n",
            )

        assert result.exit_code != 0
        assert "Failed to connect gog" in result.output

    def test_connect_epic(self, cli_runner: CliRunner) -> None:
        """Test connecting Epic account (exercises the Epic branch)."""
        mock_storage = MagicMock(spec=StorageManager)
        auth_code = "test-auth-code-abc123xyz"
        with (
            patch("src.cli.commands.is_epic_enabled", return_value=True),
            patch(
                "src.cli.commands.get_epic_auth_url",
                return_value="https://www.epicgames.com/id/authorize",
            ),
            patch(
                "src.cli.commands.exchange_epic_code",
                return_value={"refresh_token": "epic-token"},
            ),
            patch("src.cli.commands.save_epic_token") as mock_save,
            patch("webbrowser.open"),
        ):
            result = _invoke_with_mocks(
                cli_runner,
                ["auth", "connect", "--source", "epic"],
                mock_storage,
                input_text=f"{auth_code}\n",
            )

        assert result.exit_code == 0
        mock_save.assert_called_once_with(mock_storage, "epic-token", user_id=1)

    def test_connect_no_refresh_token(self, cli_runner: CliRunner) -> None:
        """Test that connect aborts when exchange returns no refresh token."""
        mock_storage = MagicMock(spec=StorageManager)
        auth_code = "test-auth-code-abc123xyz"
        with (
            patch("src.cli.commands.is_gog_enabled", return_value=True),
            patch(
                "src.cli.commands.get_gog_auth_url",
                return_value="https://auth.gog.com",
            ),
            patch(
                "src.cli.commands.exchange_gog_code",
                return_value={"access_token": "only"},
            ),
            patch("webbrowser.open"),
        ):
            result = _invoke_with_mocks(
                cli_runner,
                ["auth", "connect", "--source", "gog"],
                mock_storage,
                input_text=f"{auth_code}\n",
            )

        assert result.exit_code != 0
        assert "No refresh token received" in result.output


class TestAuthDisconnect:
    """Tests for auth disconnect command."""

    def test_disconnect_gog(self, cli_runner: CliRunner) -> None:
        """Test disconnecting GOG account."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.delete_credential.return_value = True
        result = _invoke_with_mocks(
            cli_runner,
            ["auth", "disconnect", "--source", "gog", "--yes"],
            mock_storage,
        )

        assert result.exit_code == 0
        assert "disconnected" in result.output.lower()
        mock_storage.delete_credential.assert_called_once_with(
            1, "gog", "refresh_token"
        )

    def test_disconnect_without_yes(self, cli_runner: CliRunner) -> None:
        """Test aborting disconnect when user declines confirmation."""
        mock_storage = MagicMock(spec=StorageManager)
        result = _invoke_with_mocks(
            cli_runner,
            ["auth", "disconnect", "--source", "gog"],
            mock_storage,
            input_text="n\n",
        )

        assert "Aborted" in result.output
        mock_storage.delete_credential.assert_not_called()

    def test_disconnect_epic(self, cli_runner: CliRunner) -> None:
        """Test disconnecting Epic Games uses source_id 'epic_games'."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.delete_credential.return_value = True
        result = _invoke_with_mocks(
            cli_runner,
            ["auth", "disconnect", "--source", "epic", "--yes"],
            mock_storage,
        )

        assert result.exit_code == 0
        mock_storage.delete_credential.assert_called_once_with(
            1, "epic_games", "refresh_token"
        )

    def test_disconnect_no_active_connection(self, cli_runner: CliRunner) -> None:
        """Disconnect exits non-zero when no credential existed to delete.

        Mirrors the web `DELETE /api/{source}/token` 404 response — both
        interfaces signal "nothing to disconnect" as an error, not success.
        """
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.delete_credential.return_value = False
        result = _invoke_with_mocks(
            cli_runner,
            ["auth", "disconnect", "--source", "gog", "--yes"],
            mock_storage,
        )

        assert result.exit_code != 0
        assert "No active gog connection" in result.output
