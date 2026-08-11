"""Tests for CLI auth commands."""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from src.storage.manager import StorageManager
from src.web.trakt_auth import DevicePollResult, DevicePollStatus, TraktAuthError

from .conftest import _invoke_with_mocks

USER_ID = 1


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    """A real credential database, so the auth commands resolve real sources."""
    return StorageManager(sqlite_path=tmp_path / "test.db")


def _sources(**plugins: str) -> dict[str, Any]:
    """A config whose ``inputs`` declare each source id on its plugin."""
    return {
        "inputs": {
            source_id: {"plugin": plugin, "enabled": True}
            for source_id, plugin in plugins.items()
        }
    }


class TestAuthStatus:
    """Tests for auth status command."""

    def test_auth_status_no_sources_configured(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        result = _invoke_with_mocks(cli_runner, ["auth", "status"], storage)

        assert result.exit_code == 0
        assert "No OAuth sources are configured" in result.output

    def test_auth_status_shows_every_oauth_source(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        """Non-OAuth sources are configured too, and stay off the list."""
        storage.save_credential(USER_ID, "gog_work", "refresh_token", "token")
        config = _sources(
            gog_work="gog", epic_work="epic_games", my_books="calibre_web"
        )

        result = _invoke_with_mocks(cli_runner, ["auth", "status"], storage, config)

        assert result.exit_code == 0
        assert "  epic_work (epic_games): enabled, not connected" in result.output
        assert "  gog_work (gog): enabled, connected" in result.output
        assert "my_books" not in result.output

    def test_auth_status_includes_trakt_once_its_secret_is_saved(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        """Trakt is enabled by having the client credentials the flow needs."""
        config = _sources(trakt_work="trakt")
        config["inputs"]["trakt_work"]["client_id"] = "cid"

        result = _invoke_with_mocks(cli_runner, ["auth", "status"], storage, config)

        assert "  trakt_work (trakt): not enabled, not connected" in result.output

        storage.save_credential(USER_ID, "trakt_work", "client_secret", "secret")
        storage.save_credential(USER_ID, "trakt_work", "refresh_token", "token")
        result = _invoke_with_mocks(cli_runner, ["auth", "status"], storage, config)

        assert "  trakt_work (trakt): enabled, connected" in result.output


class TestAuthStatusShowsADisabledSourcesTokenRegression:
    """Reported: disabling a source hid the token it still holds.

    Cause: the token was only read inside the enabled check, the same
    conflation the web status dropped. Fix: both are asked, and printed.
    """

    @pytest.mark.parametrize(
        ("source_id", "plugin"), [("gog_work", "gog"), ("epic_work", "epic_games")]
    )
    def test_a_disabled_source_still_reports_its_token(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        source_id: str,
        plugin: str,
    ) -> None:
        storage.save_credential(USER_ID, source_id, "refresh_token", "still-live")
        config = _sources(**{source_id: plugin})
        config["inputs"][source_id]["enabled"] = False

        result = _invoke_with_mocks(cli_runner, ["auth", "status"], storage, config)

        assert f"  {source_id} ({plugin}): not enabled, connected" in result.output


class TestAuthStatusSeesADatabaseBackedSourceRegression:
    """Reported: a source added from the Data tab was absent from ``auth status``.

    Cause: the enablement checks were called without storage, so they read
    config.yaml alone. Fix: every check is asked with the database too.
    """

    @pytest.mark.parametrize(
        ("source_id", "plugin"), [("gog_db", "gog"), ("epic_db", "epic_games")]
    )
    def test_a_db_only_source_is_listed_and_enabled(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        source_id: str,
        plugin: str,
    ) -> None:
        storage.upsert_source_config(USER_ID, source_id, plugin, {}, enabled=True)
        storage.save_credential(USER_ID, source_id, "refresh_token", "token")

        result = _invoke_with_mocks(cli_runner, ["auth", "status"], storage)

        assert f"  {source_id} ({plugin}): enabled, connected" in result.output


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
        assert "'gog' is not an enabled gog source" in result.output

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
        mock_save.assert_called_once_with(
            mock_storage, "test-token", source_id="gog", user_id=1
        )

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
        mock_save.assert_called_once_with(
            mock_storage, "epic-token", source_id="epic_games", user_id=1
        )

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
            patch("src.cli.commands.time.sleep") as mock_sleep,
        ):
            result = _invoke_with_mocks(
                cli_runner,
                ["auth", "connect", "--source", "trakt"],
                mock_storage,
            )

        assert result.exit_code == 0
        assert "ABCD1234" in result.output
        assert "connected successfully" in result.output.lower()
        mock_save.assert_called_once_with(
            mock_storage, "trakt-refresh", source_id="trakt", user_id=1
        )
        # The poll loop waits the cadence Trakt returned before each poll.
        mock_sleep.assert_called_once_with(flow["interval"])

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
            patch("src.cli.commands.time.sleep"),
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
            patch("src.cli.commands.time.sleep") as mock_sleep,
        ):
            result = _invoke_with_mocks(
                cli_runner,
                ["auth", "connect", "--source", "trakt"],
                mock_storage,
            )

        assert result.exit_code == 0
        assert "connected successfully" in result.output.lower()
        mock_save.assert_called_once_with(
            mock_storage, "trakt-refresh", source_id="trakt", user_id=1
        )
        # PENDING does not change the cadence: every sleep uses the base interval.
        assert mock_sleep.call_args_list == [
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
        sleep_intervals: list[float] = []
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
            patch(
                "src.cli.commands.time.sleep",
                side_effect=lambda seconds: sleep_intervals.append(seconds),
            ),
        ):
            result = _invoke_with_mocks(
                cli_runner,
                ["auth", "connect", "--source", "trakt"],
                mock_storage,
            )

        assert result.exit_code == 0
        mock_save.assert_called_once_with(
            mock_storage, "trakt-refresh", source_id="trakt", user_id=1
        )
        # The first sleep uses the returned interval (5); after SLOW_DOWN the
        # backoff adds 5 seconds per the Trakt device-flow spec before the next
        # poll, matching the frontend's +5s increment.
        assert sleep_intervals[0] == 5
        assert sleep_intervals[1] == 10

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
            patch("src.cli.commands.time.sleep"),
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
            patch("src.cli.commands.time.sleep"),
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

        ``time.monotonic`` is patched so the very first deadline check is already
        past expiry — the poll loop body never runs and no real waiting occurs.
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
            patch("src.cli.commands.time.sleep") as mock_sleep,
            # First call sets the deadline (0 + 600); the loop check then sees a
            # time already beyond it, so the body is skipped entirely.
            patch("src.cli.commands.time.monotonic", side_effect=[0.0, 10_000.0]),
        ):
            result = _invoke_with_mocks(
                cli_runner,
                ["auth", "connect", "--source", "trakt"],
                mock_storage,
            )

        assert result.exit_code != 0
        assert "timed out" in result.output.lower()
        mock_poll.assert_not_called()
        mock_sleep.assert_not_called()
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
            patch("src.cli.commands.time.sleep", side_effect=KeyboardInterrupt),
        ):
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


class TestConnectingASourceTheWebCanConnectRegression:
    """Reported: ``auth connect`` refused a source the browser connects fine.

    Cause: the enablement check was called with neither storage nor a source
    id, so a database-managed source read as absent however it was named.
    """

    @staticmethod
    def _connect(
        cli_runner: CliRunner,
        storage: StorageManager,
        config: dict[str, Any],
        *extra: str,
    ) -> Any:
        with (
            patch(
                "src.cli.commands.get_gog_auth_url", return_value="https://auth.gog.com"
            ),
            patch(
                "src.cli.commands.exchange_gog_code",
                return_value={"refresh_token": "fresh-token"},
            ),
        ):
            return _invoke_with_mocks(
                cli_runner,
                ["auth", "connect", "--source", "gog", "--no-browser", *extra],
                storage,
                config,
                input_text="an-authorization-code-long-enough\n",
            )

    def test_a_db_only_source_can_be_connected(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        storage.upsert_source_config(USER_ID, "gog", "gog", {}, enabled=True)

        result = self._connect(cli_runner, storage, {})

        assert result.exit_code == 0, result.output
        assert storage.get_credential(USER_ID, "gog", "refresh_token") == "fresh-token"

    def test_a_named_source_takes_its_own_token(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        result = self._connect(
            cli_runner, storage, _sources(gog_work="gog"), "--source-id", "gog_work"
        )

        assert result.exit_code == 0, result.output
        assert (
            storage.get_credential(USER_ID, "gog_work", "refresh_token")
            == "fresh-token"
        )
        assert storage.get_credential(USER_ID, "gog", "refresh_token") is None

    def test_a_named_trakt_source_resolves_its_own_client_credentials(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        """The device flow reads the client id off the source being connected."""
        config = _sources(trakt_work="trakt")
        config["inputs"]["trakt_work"]["client_id"] = "cid"
        storage.save_credential(USER_ID, "trakt_work", "client_secret", "secret")

        with (
            patch(
                "src.cli.commands.start_device_auth_flow",
                return_value={
                    "device_code": "dev123",
                    "user_code": "ABCD1234",
                    "verification_url": "https://trakt.tv/activate",
                    "expires_in": 600,
                    "interval": 5,
                },
            ),
            patch(
                "src.cli.commands.poll_device_token",
                return_value=DevicePollResult(DevicePollStatus.SUCCESS, "trakt-token"),
            ),
            patch("src.cli.commands.time.sleep"),
        ):
            result = _invoke_with_mocks(
                cli_runner,
                ["auth", "connect", "--source", "trakt", "--source-id", "trakt_work"],
                storage,
                config,
            )

        assert result.exit_code == 0, result.output
        assert (
            storage.get_credential(USER_ID, "trakt_work", "refresh_token")
            == "trakt-token"
        )


# Each ``--source`` choice, the plugin behind it and the id a bare invocation
# addresses. Epic is the one where the two differ.
PROVIDERS = [("gog", "gog"), ("epic", "epic_games"), ("trakt", "trakt")]


class TestAuthDisconnect:
    """Tests for auth disconnect command."""

    @pytest.mark.parametrize(("source", "plugin"), PROVIDERS)
    def test_disconnect_deletes_the_default_sources_token(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        source: str,
        plugin: str,
    ) -> None:
        storage.save_credential(USER_ID, plugin, "refresh_token", "token")
        config = _sources(**{plugin: plugin})

        result = _invoke_with_mocks(
            cli_runner,
            ["auth", "disconnect", "--source", source, "--yes"],
            storage,
            config,
        )

        assert result.exit_code == 0
        assert "disconnected" in result.output.lower()
        assert storage.get_credential(USER_ID, plugin, "refresh_token") is None

    def test_disconnect_without_yes(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        """Test aborting disconnect when user declines confirmation."""
        storage.save_credential(USER_ID, "gog", "refresh_token", "token")

        result = _invoke_with_mocks(
            cli_runner,
            ["auth", "disconnect", "--source", "gog"],
            storage,
            _sources(gog="gog"),
            input_text="n\n",
        )

        assert "Aborted" in result.output
        assert storage.get_credential(USER_ID, "gog", "refresh_token") == "token"

    def test_disconnect_no_active_connection(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        """Disconnect exits non-zero when no credential existed to delete.

        Mirrors the web `DELETE /api/{source}/token` 404 response — both
        interfaces signal "nothing to disconnect" as an error, not success.
        """
        result = _invoke_with_mocks(
            cli_runner,
            ["auth", "disconnect", "--source", "gog", "--yes"],
            storage,
            _sources(gog="gog"),
        )

        assert result.exit_code != 0
        assert "No active gog connection" in result.output


class TestDisconnectingASourceOfItsOwnNameRegression:
    """Reported: ``gog_work``'s refresh token could not be revoked from the CLI.

    Cause: deletion was keyed on the plugin name, which this release stopped
    storing tokens under. Fix: ``--source-id``, resolved as the web resolves it.
    """

    @pytest.mark.parametrize(("source", "plugin"), PROVIDERS)
    def test_a_named_source_can_revoke_its_own_token(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        source: str,
        plugin: str,
    ) -> None:
        storage.save_credential(USER_ID, f"{plugin}_work", "refresh_token", "mine")
        storage.save_credential(USER_ID, plugin, "refresh_token", "the-plugin-name-row")

        result = _invoke_with_mocks(
            cli_runner,
            [
                "auth",
                "disconnect",
                "--source",
                source,
                "--source-id",
                f"{plugin}_work",
                "--yes",
            ],
            storage,
            _sources(**{f"{plugin}_work": plugin}),
        )

        assert result.exit_code == 0
        assert (
            storage.get_credential(USER_ID, f"{plugin}_work", "refresh_token") is None
        )
        # The row under the plugin name belongs to whoever is named that, and
        # this call was never asked about it.
        assert (
            storage.get_credential(USER_ID, plugin, "refresh_token")
            == "the-plugin-name-row"
        )

    def test_a_disabled_source_can_still_revoke_its_token(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        """Disabling a source is how revoking its token starts."""
        storage.save_credential(USER_ID, "gog_work", "refresh_token", "still-live")
        config = _sources(gog_work="gog")
        config["inputs"]["gog_work"]["enabled"] = False

        result = _invoke_with_mocks(
            cli_runner,
            [
                "auth",
                "disconnect",
                "--source",
                "gog",
                "--source-id",
                "gog_work",
                "--yes",
            ],
            storage,
            config,
        )

        assert result.exit_code == 0
        assert storage.get_credential(USER_ID, "gog_work", "refresh_token") is None

    def test_an_id_another_plugin_owns_is_refused(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        """The id is a credential key, so the plugin behind it decides."""
        storage.save_credential(USER_ID, "trakt_work", "refresh_token", "not-gogs")

        result = _invoke_with_mocks(
            cli_runner,
            [
                "auth",
                "disconnect",
                "--source",
                "gog",
                "--source-id",
                "trakt_work",
                "--yes",
            ],
            storage,
            _sources(trakt_work="trakt"),
        )

        assert result.exit_code != 0
        assert "No active gog connection" in result.output
        assert (
            storage.get_credential(USER_ID, "trakt_work", "refresh_token") == "not-gogs"
        )


class TestRevokingATokenNoSourceClaimsRegression:
    """Reported: deleting ``inputs.gog`` left its refresh token undeletable.

    Cause: the gate read "no source claims this id" as "another plugin does".
    Fix: only another plugin's source puts an id out of reach.
    """

    @pytest.mark.parametrize(("source", "plugin"), PROVIDERS)
    def test_a_stranded_token_can_still_be_revoked(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        source: str,
        plugin: str,
    ) -> None:
        storage.save_credential(USER_ID, plugin, "refresh_token", "stranded")

        result = _invoke_with_mocks(
            cli_runner,
            ["auth", "disconnect", "--source", source, "--yes"],
            storage,
            _sources(),
        )

        assert result.exit_code == 0, result.output
        assert storage.get_credential(USER_ID, plugin, "refresh_token") is None


class TestStatusReportsOnlyATokenDisconnectCanDeleteRegression:
    """Reported: ``auth status`` said connected where disconnect said 404.

    Cause: the token was read off the resolved config, which layers the YAML
    entry in, while disconnect deletes the credential row. Fix: ask the row.
    """

    @pytest.mark.parametrize(("source", "plugin"), PROVIDERS)
    def test_a_yaml_only_token_reads_not_connected(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        source: str,
        plugin: str,
    ) -> None:
        source_id = f"{plugin}_work"
        config = _sources(**{source_id: plugin})
        config["inputs"][source_id]["refresh_token"] = "from-yaml"
        # Trakt is enabled by its client credentials rather than its token, and
        # the enabled half of the line must not move between the two reads.
        config["inputs"][source_id]["client_id"] = "cid"
        storage.save_credential(USER_ID, source_id, "client_secret", "secret")

        result = _invoke_with_mocks(cli_runner, ["auth", "status"], storage, config)

        assert f"  {source_id} ({plugin}): enabled, not connected" in result.output

        # The anchor: the same source reads connected once the token is
        # somewhere ``auth disconnect`` can reach it.
        storage.save_credential(USER_ID, source_id, "refresh_token", "in-the-db")
        result = _invoke_with_mocks(cli_runner, ["auth", "status"], storage, config)

        assert f"  {source_id} ({plugin}): enabled, connected" in result.output
