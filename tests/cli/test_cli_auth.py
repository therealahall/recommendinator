from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from src.auth.trakt import DevicePollResult, DevicePollStatus
from src.storage.manager import StorageManager
from tests.factories import MALFORMED_IDS, make_storage_mock

from .conftest import _invoke_with_mocks

USER_ID = 1


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "test.db")


def _configure(storage: StorageManager, **plugins: str) -> None:
    for sid, plugin in plugins.items():
        storage.sources.upsert(USER_ID, sid, plugin, {}, enabled=True)


class TestAuthStatus:
    def test_auth_status_no_sources_configured(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        result = _invoke_with_mocks(cli_runner, ["auth", "status"], storage)

        assert result.exit_code == 0
        assert "No OAuth sources are configured" in result.output

    def test_auth_status_shows_every_oauth_source(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        storage.credentials.save(USER_ID, "gog_work", "refresh_token", "token")
        _configure(
            storage, gog_work="gog", epic_work="epic_games", my_books="calibre_web"
        )

        result = _invoke_with_mocks(cli_runner, ["auth", "status"], storage)

        assert result.exit_code == 0
        assert "  epic_work (epic_games): enabled, not connected" in result.output
        assert "  gog_work (gog): enabled, connected" in result.output
        assert "my_books" not in result.output


class TestAuthStatusShowsADisabledSourcesTokenRegression:
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
        storage.credentials.save(USER_ID, source_id, "refresh_token", "still-live")
        storage.sources.upsert(USER_ID, source_id, plugin, {}, enabled=False)

        result = _invoke_with_mocks(cli_runner, ["auth", "status"], storage)

        assert f"  {source_id} ({plugin}): not enabled, connected" in result.output


class TestAuthStatusSeesADatabaseBackedSourceRegression:
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
        storage.sources.upsert(USER_ID, source_id, plugin, {}, enabled=True)
        storage.credentials.save(USER_ID, source_id, "refresh_token", "token")

        result = _invoke_with_mocks(cli_runner, ["auth", "status"], storage)

        assert f"  {source_id} ({plugin}): enabled, connected" in result.output


class TestAuthConnect:
    def test_connect_source_not_enabled(self, cli_runner: CliRunner) -> None:
        mock_storage = make_storage_mock()
        with patch("src.cli.commands._auth.is_gog_enabled", return_value=False):
            result = _invoke_with_mocks(
                cli_runner,
                ["auth", "connect", "--source", "gog"],
                mock_storage,
            )

        assert result.exit_code != 0
        assert "'gog' is not an enabled gog source" in result.output

    def test_connect_gog(self, cli_runner: CliRunner) -> None:
        mock_storage = make_storage_mock()
        auth_code = "test-auth-code-abc123xyz"
        with (
            patch("src.cli.commands._auth.is_gog_enabled", return_value=True),
            patch(
                "src.cli.commands._auth.get_gog_auth_url",
                return_value="https://auth.gog.com/auth?client_id=test",
            ),
            patch(
                "src.cli.commands._auth.exchange_gog_code",
                return_value={"refresh_token": "test-token"},
            ),
            patch("src.cli.commands._auth.save_gog_token") as mock_save,
            patch("webbrowser.open"),
        ):
            result = _invoke_with_mocks(
                cli_runner,
                ["auth", "connect", "--source", "gog"],
                mock_storage,
                input_text=f"{auth_code}\n",
            )

        assert result.exit_code == 0
        assert "connected successfully" in result.output.lower()
        mock_save.assert_called_once_with(
            mock_storage, "test-token", source_id="gog", user_id=1
        )

    def test_connect_trakt_success(self, cli_runner: CliRunner) -> None:
        mock_storage = make_storage_mock()
        flow = {
            "device_code": "dev123",
            "user_code": "ABCD1234",
            "verification_url": "https://trakt.tv/activate",
            "expires_in": 600,
            "interval": 5,
        }
        with (
            patch(
                "src.cli.commands._auth.resolve_trakt_client_credentials",
                return_value=("cid", "secret"),
            ),
            patch("src.cli.commands._auth.start_device_auth_flow", return_value=flow),
            patch(
                "src.cli.commands._auth.poll_device_token",
                return_value=DevicePollResult(
                    DevicePollStatus.SUCCESS, "trakt-refresh"
                ),
            ),
            patch("src.cli.commands._auth.save_trakt_token") as mock_save,
            patch("src.cli.commands._auth.time.sleep") as mock_sleep,
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
        mock_sleep.assert_called_once_with(flow["interval"])

    def test_connect_trakt_denied(self, cli_runner: CliRunner) -> None:
        mock_storage = make_storage_mock()
        flow = {
            "device_code": "dev123",
            "user_code": "ABCD1234",
            "verification_url": "https://trakt.tv/activate",
            "expires_in": 600,
            "interval": 5,
        }
        with (
            patch(
                "src.cli.commands._auth.resolve_trakt_client_credentials",
                return_value=("cid", "secret"),
            ),
            patch("src.cli.commands._auth.start_device_auth_flow", return_value=flow),
            patch(
                "src.cli.commands._auth.poll_device_token",
                return_value=DevicePollResult(DevicePollStatus.DENIED),
            ),
            patch("src.cli.commands._auth.save_trakt_token") as mock_save,
            patch("src.cli.commands._auth.time.sleep"),
        ):
            result = _invoke_with_mocks(
                cli_runner,
                ["auth", "connect", "--source", "trakt"],
                mock_storage,
            )

        assert result.exit_code != 0
        assert "denied" in result.output.lower()
        mock_save.assert_not_called()

    def test_connect_no_refresh_token(self, cli_runner: CliRunner) -> None:
        mock_storage = make_storage_mock()
        auth_code = "test-auth-code-abc123xyz"
        with (
            patch("src.cli.commands._auth.is_gog_enabled", return_value=True),
            patch(
                "src.cli.commands._auth.get_gog_auth_url",
                return_value="https://auth.gog.com",
            ),
            patch(
                "src.cli.commands._auth.exchange_gog_code",
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
    @staticmethod
    def _connect(cli_runner: CliRunner, storage: StorageManager, *extra: str) -> Any:
        with (
            patch(
                "src.cli.commands._auth.get_gog_auth_url",
                return_value="https://auth.gog.com",
            ),
            patch(
                "src.cli.commands._auth.exchange_gog_code",
                return_value={"refresh_token": "fresh-token"},
            ),
        ):
            return _invoke_with_mocks(
                cli_runner,
                ["auth", "connect", "--source", "gog", "--no-browser", *extra],
                storage,
                input_text="an-authorization-code-long-enough\n",
            )

    def test_a_db_only_source_can_be_connected(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        storage.sources.upsert(USER_ID, "gog", "gog", {}, enabled=True)

        result = self._connect(cli_runner, storage)

        assert result.exit_code == 0, result.output
        assert storage.credentials.get(USER_ID, "gog", "refresh_token") == "fresh-token"

    def test_a_named_source_takes_its_own_token(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        _configure(storage, gog_work="gog")

        result = self._connect(cli_runner, storage, "--source-id", "gog_work")

        assert result.exit_code == 0, result.output
        assert (
            storage.credentials.get(USER_ID, "gog_work", "refresh_token")
            == "fresh-token"
        )
        assert storage.credentials.get(USER_ID, "gog", "refresh_token") is None

    def test_a_named_trakt_source_resolves_its_own_client_credentials(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        storage.sources.upsert(
            USER_ID, "trakt_work", "trakt", {"client_id": "cid"}, enabled=True
        )
        storage.credentials.save(USER_ID, "trakt_work", "client_secret", "secret")

        with (
            patch(
                "src.cli.commands._auth.start_device_auth_flow",
                return_value={
                    "device_code": "dev123",
                    "user_code": "ABCD1234",
                    "verification_url": "https://trakt.tv/activate",
                    "expires_in": 600,
                    "interval": 5,
                },
            ),
            patch(
                "src.cli.commands._auth.poll_device_token",
                return_value=DevicePollResult(DevicePollStatus.SUCCESS, "trakt-token"),
            ),
            patch("src.cli.commands._auth.time.sleep"),
        ):
            result = _invoke_with_mocks(
                cli_runner,
                ["auth", "connect", "--source", "trakt", "--source-id", "trakt_work"],
                storage,
            )

        assert result.exit_code == 0, result.output
        assert (
            storage.credentials.get(USER_ID, "trakt_work", "refresh_token")
            == "trakt-token"
        )


PROVIDERS = [("gog", "gog"), ("epic", "epic_games"), ("trakt", "trakt")]


class TestAuthDisconnect:
    @pytest.mark.parametrize(("source", "plugin"), PROVIDERS)
    def test_disconnect_deletes_the_default_sources_token(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        source: str,
        plugin: str,
    ) -> None:
        storage.credentials.save(USER_ID, plugin, "refresh_token", "token")
        _configure(storage, **{plugin: plugin})

        result = _invoke_with_mocks(
            cli_runner,
            ["auth", "disconnect", "--source", source, "--yes"],
            storage,
        )

        assert result.exit_code == 0
        assert "disconnected" in result.output.lower()
        assert storage.credentials.get(USER_ID, plugin, "refresh_token") is None

    def test_disconnect_without_yes(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        storage.credentials.save(USER_ID, "gog", "refresh_token", "token")
        _configure(storage, gog="gog")

        result = _invoke_with_mocks(
            cli_runner,
            ["auth", "disconnect", "--source", "gog"],
            storage,
            input_text="n\n",
        )

        assert "Aborted" in result.output
        assert storage.credentials.get(USER_ID, "gog", "refresh_token") == "token"

    def test_disconnect_no_active_connection(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        _configure(storage, gog="gog")

        result = _invoke_with_mocks(
            cli_runner,
            ["auth", "disconnect", "--source", "gog", "--yes"],
            storage,
        )

        assert result.exit_code != 0
        assert "No active gog connection" in result.output


class TestDisconnectingASourceOfItsOwnNameRegression:
    @pytest.mark.parametrize(("source", "plugin"), PROVIDERS)
    def test_a_named_source_can_revoke_its_own_token(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        source: str,
        plugin: str,
    ) -> None:
        storage.credentials.save(USER_ID, f"{plugin}_work", "refresh_token", "mine")
        storage.credentials.save(
            USER_ID, plugin, "refresh_token", "the-plugin-name-row"
        )
        _configure(storage, **{f"{plugin}_work": plugin})

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
        )

        assert result.exit_code == 0
        assert (
            storage.credentials.get(USER_ID, f"{plugin}_work", "refresh_token") is None
        )
        assert (
            storage.credentials.get(USER_ID, plugin, "refresh_token")
            == "the-plugin-name-row"
        )

    def test_a_disabled_source_can_still_revoke_its_token(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        storage.credentials.save(USER_ID, "gog_work", "refresh_token", "still-live")
        storage.sources.upsert(USER_ID, "gog_work", "gog", {}, enabled=False)

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
        )

        assert result.exit_code == 0
        assert storage.credentials.get(USER_ID, "gog_work", "refresh_token") is None

    def test_an_id_another_plugin_owns_is_refused(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        storage.credentials.save(USER_ID, "trakt_work", "refresh_token", "not-gogs")
        _configure(storage, trakt_work="trakt")

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
        )

        assert result.exit_code != 0
        assert "No active gog connection" in result.output
        assert (
            storage.credentials.get(USER_ID, "trakt_work", "refresh_token")
            == "not-gogs"
        )


class TestRevokingATokenNoSourceClaimsRegression:
    @pytest.mark.parametrize(("source", "plugin"), PROVIDERS)
    def test_a_stranded_token_can_still_be_revoked(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        source: str,
        plugin: str,
    ) -> None:
        storage.credentials.save(USER_ID, plugin, "refresh_token", "stranded")

        result = _invoke_with_mocks(
            cli_runner,
            ["auth", "disconnect", "--source", source, "--yes"],
            storage,
        )

        assert result.exit_code == 0, result.output
        assert storage.credentials.get(USER_ID, plugin, "refresh_token") is None


class TestBothAuthVerbsValidateTheSourceId:
    _VERB_FLAGS = {"connect": ["--no-browser"], "disconnect": ["--yes"]}

    @pytest.mark.parametrize("verb", sorted(_VERB_FLAGS))
    @pytest.mark.parametrize("bad_id", MALFORMED_IDS)
    @pytest.mark.parametrize(("source", "plugin"), PROVIDERS)
    def test_a_malformed_id_is_refused_before_anything_reads_it(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        verb: str,
        bad_id: str,
        source: str,
        plugin: str,
    ) -> None:
        storage.credentials.save(USER_ID, plugin, "refresh_token", "the-default-id")
        _configure(storage, **{plugin: plugin})

        result = _invoke_with_mocks(
            cli_runner,
            [
                "auth",
                verb,
                "--source",
                source,
                "--source-id",
                bad_id,
                *self._VERB_FLAGS[verb],
            ],
            storage,
        )

        assert result.exit_code != 0
        assert "--source-id must start with a lowercase letter" in result.output
        assert storage.credentials.get(USER_ID, plugin, "refresh_token") == (
            "the-default-id"
        )
