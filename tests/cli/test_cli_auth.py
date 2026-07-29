"""Tests for CLI auth commands."""

import itertools
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from src.storage.manager import StorageManager
from src.web.trakt_auth import DevicePollResult, DevicePollStatus, TraktAuthError

from .conftest import _invoke_with_mocks


@contextmanager
def _patched_command_time(
    monotonic: Iterator[float] | None = None,
) -> Iterator[MagicMock]:
    """Replace ``src.cli.commands``'s own ``time`` reference for the block.

    Deliberately not ``patch("src.cli.commands.time.sleep")``: that target
    resolves through to the shared ``time`` module and sets the attribute
    *there*, so every thread in the process sees it for the duration. The Trakt
    tests below assert on exact call recordings (``assert_called_once_with``,
    whole ``call_args_list`` equality, ``assert_not_called``) and one of them
    injects ``KeyboardInterrupt`` — a background thread that merely sleeps
    during the block would corrupt the first three and take the exception in
    the last. Rebinding the module reference inside ``commands``'s namespace
    confines the replacement to the code under test, the shape
    ``tests/enrichment/test_rate_limiter.py`` already uses.

    Args:
        monotonic: Readings ``time.monotonic()`` returns in order. Defaults to
            a clock advancing one second per call, which leaves the poll loop's
            deadline comfortably in the future.

    Yields:
        The stand-in module. ``.sleep`` is the recorder the tests assert on.
    """
    with patch("src.cli.commands.time") as mock_time:
        mock_time.monotonic.side_effect = monotonic or itertools.count(0.0, 1.0)
        yield mock_time


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
            patch(
                "src.cli.commands.resolve_trakt_client_credentials",
                side_effect=TraktAuthError("not configured"),
            ),
        ):
            result = _invoke_with_mocks(
                cli_runner, ["auth", "status"], mock_storage, config=config
            )

        assert result.exit_code == 0
        assert "gog: connected" in result.output
        assert "epic: not connected" in result.output

    def test_auth_status_includes_trakt(self, cli_runner: CliRunner) -> None:
        """auth status lists Trakt when its client credentials are saved."""
        mock_storage = MagicMock(spec=StorageManager)
        with (
            patch("src.cli.commands.is_gog_enabled", return_value=False),
            patch("src.cli.commands.is_epic_enabled", return_value=False),
            patch(
                "src.cli.commands.resolve_trakt_client_credentials",
                return_value=("cid", "secret"),
            ),
            patch("src.cli.commands.is_trakt_connected", return_value=True),
        ):
            result = _invoke_with_mocks(cli_runner, ["auth", "status"], mock_storage)

        assert result.exit_code == 0
        assert "trakt: connected" in result.output

    def test_auth_status_trakt_not_connected(self, cli_runner: CliRunner) -> None:
        """auth status shows 'trakt: not connected' when creds saved but no token."""
        mock_storage = MagicMock(spec=StorageManager)
        with (
            patch("src.cli.commands.is_gog_enabled", return_value=False),
            patch("src.cli.commands.is_epic_enabled", return_value=False),
            patch(
                "src.cli.commands.resolve_trakt_client_credentials",
                return_value=("cid", "secret"),
            ),
            patch("src.cli.commands.is_trakt_connected", return_value=False),
        ):
            result = _invoke_with_mocks(cli_runner, ["auth", "status"], mock_storage)

        assert result.exit_code == 0
        assert "trakt: not connected" in result.output


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

    def test_connect_trakt_success(self, cli_runner: CliRunner) -> None:
        """Trakt device flow connects when the first poll approves."""
        mock_storage = MagicMock(spec=StorageManager)
        flow = {
            "device_code": "dev123",
            "user_code": "ABCD1234",
            "verification_url": "https://trakt.tv/activate",
            "expires_in": 600,
            "interval": 5,
        }
        with (
            patch(
                "src.cli.commands.resolve_trakt_client_credentials",
                return_value=("cid", "secret"),
            ),
            patch("src.cli.commands.start_device_auth_flow", return_value=flow),
            patch(
                "src.cli.commands.poll_device_token",
                return_value=DevicePollResult(
                    DevicePollStatus.SUCCESS, "trakt-refresh"
                ),
            ),
            patch("src.cli.commands.save_trakt_token") as mock_save,
            _patched_command_time() as mock_time,
        ):
            result = _invoke_with_mocks(
                cli_runner,
                ["auth", "connect", "--source", "trakt"],
                mock_storage,
            )

        assert result.exit_code == 0
        assert "ABCD1234" in result.output
        assert "connected successfully" in result.output.lower()
        mock_save.assert_called_once_with(mock_storage, "trakt-refresh", user_id=1)
        # The poll loop waits the cadence Trakt returned before each poll.
        mock_time.sleep.assert_called_once_with(flow["interval"])

    def test_connect_trakt_not_configured(self, cli_runner: CliRunner) -> None:
        """Trakt connect aborts when client credentials are missing."""
        mock_storage = MagicMock(spec=StorageManager)
        with patch(
            "src.cli.commands.resolve_trakt_client_credentials",
            side_effect=TraktAuthError("Trakt is not configured."),
        ):
            result = _invoke_with_mocks(
                cli_runner,
                ["auth", "connect", "--source", "trakt"],
                mock_storage,
            )

        assert result.exit_code != 0
        assert "not configured" in result.output

    def test_connect_trakt_denied(self, cli_runner: CliRunner) -> None:
        """Trakt connect aborts when the user denies the request."""
        mock_storage = MagicMock(spec=StorageManager)
        flow = {
            "device_code": "dev123",
            "user_code": "ABCD1234",
            "verification_url": "https://trakt.tv/activate",
            "expires_in": 600,
            "interval": 5,
        }
        with (
            patch(
                "src.cli.commands.resolve_trakt_client_credentials",
                return_value=("cid", "secret"),
            ),
            patch("src.cli.commands.start_device_auth_flow", return_value=flow),
            patch(
                "src.cli.commands.poll_device_token",
                return_value=DevicePollResult(DevicePollStatus.DENIED),
            ),
            patch("src.cli.commands.save_trakt_token") as mock_save,
            _patched_command_time(),
        ):
            result = _invoke_with_mocks(
                cli_runner,
                ["auth", "connect", "--source", "trakt"],
                mock_storage,
            )

        assert result.exit_code != 0
        assert "denied" in result.output.lower()
        mock_save.assert_not_called()

    def test_connect_trakt_pending_then_success(self, cli_runner: CliRunner) -> None:
        """Trakt connect keeps polling through PENDING until approval."""
        mock_storage = MagicMock(spec=StorageManager)
        flow = {
            "device_code": "dev123",
            "user_code": "ABCD1234",
            "verification_url": "https://trakt.tv/activate",
            "expires_in": 600,
            "interval": 5,
        }
        with (
            patch(
                "src.cli.commands.resolve_trakt_client_credentials",
                return_value=("cid", "secret"),
            ),
            patch("src.cli.commands.start_device_auth_flow", return_value=flow),
            patch(
                "src.cli.commands.poll_device_token",
                side_effect=[
                    DevicePollResult(DevicePollStatus.PENDING),
                    DevicePollResult(DevicePollStatus.SUCCESS, "trakt-refresh"),
                ],
            ),
            patch("src.cli.commands.save_trakt_token") as mock_save,
            _patched_command_time() as mock_time,
        ):
            result = _invoke_with_mocks(
                cli_runner,
                ["auth", "connect", "--source", "trakt"],
                mock_storage,
            )

        assert result.exit_code == 0
        assert "connected successfully" in result.output.lower()
        mock_save.assert_called_once_with(mock_storage, "trakt-refresh", user_id=1)
        # PENDING does not change the cadence: every sleep uses the base interval.
        assert mock_time.sleep.call_args_list == [
            ((flow["interval"],),),
            ((flow["interval"],),),
        ]

    def test_connect_trakt_slow_down_then_success(self, cli_runner: CliRunner) -> None:
        """Trakt connect backs off on SLOW_DOWN and still completes on approval."""
        mock_storage = MagicMock(spec=StorageManager)
        flow = {
            "device_code": "dev123",
            "user_code": "ABCD1234",
            "verification_url": "https://trakt.tv/activate",
            "expires_in": 600,
            "interval": 5,
        }
        with (
            patch(
                "src.cli.commands.resolve_trakt_client_credentials",
                return_value=("cid", "secret"),
            ),
            patch("src.cli.commands.start_device_auth_flow", return_value=flow),
            patch(
                "src.cli.commands.poll_device_token",
                side_effect=[
                    DevicePollResult(DevicePollStatus.SLOW_DOWN),
                    DevicePollResult(DevicePollStatus.SUCCESS, "trakt-refresh"),
                ],
            ),
            patch("src.cli.commands.save_trakt_token") as mock_save,
            _patched_command_time() as mock_time,
        ):
            result = _invoke_with_mocks(
                cli_runner,
                ["auth", "connect", "--source", "trakt"],
                mock_storage,
            )

        assert result.exit_code == 0
        mock_save.assert_called_once_with(mock_storage, "trakt-refresh", user_id=1)
        # The first sleep uses the returned interval (5); after SLOW_DOWN the
        # backoff adds 5 seconds per the Trakt device-flow spec before the next
        # poll, matching the frontend's +5s increment.
        assert mock_time.sleep.call_args_list == [((5,),), ((10,),)]

    def test_connect_trakt_expired(self, cli_runner: CliRunner) -> None:
        """Trakt connect aborts when the device code expires."""
        mock_storage = MagicMock(spec=StorageManager)
        flow = {
            "device_code": "dev123",
            "user_code": "ABCD1234",
            "verification_url": "https://trakt.tv/activate",
            "expires_in": 600,
            "interval": 5,
        }
        with (
            patch(
                "src.cli.commands.resolve_trakt_client_credentials",
                return_value=("cid", "secret"),
            ),
            patch("src.cli.commands.start_device_auth_flow", return_value=flow),
            patch(
                "src.cli.commands.poll_device_token",
                return_value=DevicePollResult(DevicePollStatus.EXPIRED),
            ),
            _patched_command_time(),
        ):
            result = _invoke_with_mocks(
                cli_runner,
                ["auth", "connect", "--source", "trakt"],
                mock_storage,
            )

        assert result.exit_code != 0
        assert "expired" in result.output.lower()

    def test_connect_trakt_success_without_refresh_token(
        self, cli_runner: CliRunner
    ) -> None:
        """Trakt connect aborts (no save) when SUCCESS carries no refresh token.

        Guards the explicit None check that replaced a stripped assert: an empty
        token must never be persisted.
        """
        mock_storage = MagicMock(spec=StorageManager)
        flow = {
            "device_code": "dev123",
            "user_code": "ABCD1234",
            "verification_url": "https://trakt.tv/activate",
            "expires_in": 600,
            "interval": 5,
        }
        with (
            patch(
                "src.cli.commands.resolve_trakt_client_credentials",
                return_value=("cid", "secret"),
            ),
            patch("src.cli.commands.start_device_auth_flow", return_value=flow),
            patch(
                "src.cli.commands.poll_device_token",
                return_value=DevicePollResult(DevicePollStatus.SUCCESS, None),
            ),
            patch("src.cli.commands.save_trakt_token") as mock_save,
            _patched_command_time(),
        ):
            result = _invoke_with_mocks(
                cli_runner,
                ["auth", "connect", "--source", "trakt"],
                mock_storage,
            )

        assert result.exit_code != 0
        assert "no refresh token" in result.output.lower()
        mock_save.assert_not_called()

    def test_connect_trakt_times_out(self, cli_runner: CliRunner) -> None:
        """Trakt connect aborts with a timeout message once the deadline passes.

        ``time.monotonic`` reads a clock that jumps far past ``expires_in``
        between calls, so the very first deadline check is already expired —
        the poll loop body never runs and no real waiting occurs.
        """
        mock_storage = MagicMock(spec=StorageManager)
        flow = {
            "device_code": "dev123",
            "user_code": "ABCD1234",
            "verification_url": "https://trakt.tv/activate",
            "expires_in": 600,
            "interval": 5,
        }
        with (
            patch(
                "src.cli.commands.resolve_trakt_client_credentials",
                return_value=("cid", "secret"),
            ),
            patch("src.cli.commands.start_device_auth_flow", return_value=flow),
            patch("src.cli.commands.poll_device_token") as mock_poll,
            patch("src.cli.commands.save_trakt_token") as mock_save,
            # Whichever call sets the deadline (t + 600), the next one is at
            # least t + 10_000, so the loop body is always skipped entirely.
            _patched_command_time(itertools.count(0.0, 10_000.0)) as mock_time,
        ):
            result = _invoke_with_mocks(
                cli_runner,
                ["auth", "connect", "--source", "trakt"],
                mock_storage,
            )

        assert result.exit_code != 0
        assert "timed out" in result.output.lower()
        mock_poll.assert_not_called()
        mock_time.sleep.assert_not_called()
        mock_save.assert_not_called()

    def test_connect_trakt_keyboard_interrupt(self, cli_runner: CliRunner) -> None:
        """Ctrl-C during the poll wait aborts cleanly with 'Cancelled.'."""
        mock_storage = MagicMock(spec=StorageManager)
        flow = {
            "device_code": "dev123",
            "user_code": "ABCD1234",
            "verification_url": "https://trakt.tv/activate",
            "expires_in": 600,
            "interval": 5,
        }
        with (
            patch(
                "src.cli.commands.resolve_trakt_client_credentials",
                return_value=("cid", "secret"),
            ),
            patch("src.cli.commands.start_device_auth_flow", return_value=flow),
            patch("src.cli.commands.poll_device_token") as mock_poll,
            patch("src.cli.commands.save_trakt_token") as mock_save,
            _patched_command_time() as mock_time,
        ):
            mock_time.sleep.side_effect = KeyboardInterrupt
            result = _invoke_with_mocks(
                cli_runner,
                ["auth", "connect", "--source", "trakt"],
                mock_storage,
            )

        assert result.exit_code != 0
        assert "cancelled" in result.output.lower()
        mock_poll.assert_not_called()
        mock_save.assert_not_called()

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

    def test_disconnect_trakt(self, cli_runner: CliRunner) -> None:
        """Test disconnecting Trakt uses source_id 'trakt'."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.delete_credential.return_value = True
        result = _invoke_with_mocks(
            cli_runner,
            ["auth", "disconnect", "--source", "trakt", "--yes"],
            mock_storage,
        )

        assert result.exit_code == 0
        mock_storage.delete_credential.assert_called_once_with(
            1, "trakt", "refresh_token"
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
