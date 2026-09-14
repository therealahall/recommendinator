from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from src.ingestion.plugin_base import DevicePollResult, DevicePollStatus
from src.storage.manager import StorageManager
from tests.factories import MALFORMED_IDS
from tests.fakes.source_plugins import (
    FAKE_AUTH_URL,
    FAKE_AUTHORIZATION,
    FAKE_CODE,
    FAKE_REFRESH_TOKEN,
    FakeDeviceCodeFlow,
)

from .conftest import _invoke_with_mocks

USER_ID = 1
TOKEN = "refresh_token"

pytestmark = pytest.mark.usefixtures("registry_with_oauth_fakes")


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "test.db")


def _configure(
    storage: StorageManager, source_id: str, plugin: str, enabled: bool = True
) -> None:
    config = {"client_id": "cid"} if plugin == "fake_device" else {}
    storage.sources.upsert(USER_ID, source_id, plugin, config, enabled=enabled)


def _token(storage: StorageManager, source_id: str) -> str | None:
    return storage.credentials.get(USER_ID, source_id, TOKEN)


def _auth(
    cli_runner: CliRunner, storage: StorageManager, *args: str, input_text: str = ""
) -> Any:
    return _invoke_with_mocks(
        cli_runner, ["auth", *args], storage, input_text=input_text
    )


class TestAuthStatus:
    def test_auth_status_no_sources_configured(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        result = _auth(cli_runner, storage, "status")

        assert result.exit_code == 0
        assert "No OAuth sources are configured" in result.output

    def test_every_source_whose_plugin_declares_a_flow_is_listed(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        storage.credentials.save(USER_ID, "paste_work", TOKEN, "token")
        _configure(storage, "paste_work", "fake_paste")
        _configure(storage, "device_work", "fake_device")
        _configure(storage, "books", "fake_file")

        result = _auth(cli_runner, storage, "status")

        assert result.exit_code == 0, result.output
        assert "  device_work (fake_device): enabled, not connected" in result.output
        assert "  paste_work (fake_paste): enabled, connected" in result.output
        assert "books" not in result.output

    def test_a_disabled_source_still_reports_its_token(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        storage.credentials.save(USER_ID, "paste_work", TOKEN, "still-live")
        _configure(storage, "paste_work", "fake_paste", enabled=False)

        result = _auth(cli_runner, storage, "status")

        assert "  paste_work (fake_paste): not enabled, connected" in result.output


class TestAPluginDeclaringAFlowConnectsWithNoCoreEditRegression:
    def test_a_code_paste_flow_connects_and_disconnects(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        _configure(storage, "paste_work", "fake_paste")
        named = ("--source", "fake_paste", "--source-id", "paste_work")

        connected = _auth(
            cli_runner,
            storage,
            "connect",
            *named,
            "--no-browser",
            input_text=f"{FAKE_CODE}\n",
        )

        assert connected.exit_code == 0, connected.output
        assert FAKE_AUTH_URL in connected.output
        assert _token(storage, "paste_work") == FAKE_REFRESH_TOKEN
        assert "paste_work (fake_paste): enabled, connected" in (
            _auth(cli_runner, storage, "status").output
        )

        revoked = _auth(cli_runner, storage, "disconnect", *named, "--yes")

        assert revoked.exit_code == 0, revoked.output
        assert _token(storage, "paste_work") is None

    def test_a_device_code_flow_connects_and_disconnects(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        _configure(storage, "device_work", "fake_device")
        named = ("--source", "fake_device", "--source-id", "device_work")

        with patch("src.cli.commands._auth.time.sleep") as slept:
            connected = _auth(cli_runner, storage, "connect", *named)

        assert connected.exit_code == 0, connected.output
        assert FAKE_AUTHORIZATION.user_code in connected.output
        slept.assert_called_once_with(FAKE_AUTHORIZATION.interval)
        assert _token(storage, "device_work") == FAKE_REFRESH_TOKEN

        revoked = _auth(cli_runner, storage, "disconnect", *named, "--yes")

        assert revoked.exit_code == 0, revoked.output
        assert _token(storage, "device_work") is None


class TestAuthConnect:
    def test_a_source_that_is_not_enabled_is_refused(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        result = _auth(cli_runner, storage, "connect", "--source", "fake_paste")

        assert result.exit_code != 0
        assert "Fake Paste is not enabled or set up for that source" in result.output
        assert "authentication failed" not in result.output

    def test_the_source_id_defaults_to_the_plugin_name(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        _configure(storage, "fake_paste", "fake_paste")

        result = _auth(
            cli_runner,
            storage,
            "connect",
            "--source",
            "fake_paste",
            "--no-browser",
            input_text=f"{FAKE_CODE}\n",
        )

        assert result.exit_code == 0, result.output
        assert _token(storage, "fake_paste") == FAKE_REFRESH_TOKEN

    def test_a_refused_code_stores_nothing(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        _configure(storage, "fake_paste", "fake_paste")

        result = _auth(
            cli_runner,
            storage,
            "connect",
            "--source",
            "fake_paste",
            "--no-browser",
            input_text="not-the-code\n",
        )

        assert result.exit_code != 0
        assert "Fake Paste authentication failed" in result.output
        assert _token(storage, "fake_paste") is None

    def test_a_denied_device_flow_stores_nothing(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        _configure(storage, "fake_device", "fake_device")

        with (
            patch.object(
                FakeDeviceCodeFlow,
                "poll",
                return_value=DevicePollResult(DevicePollStatus.DENIED),
            ),
            patch("src.cli.commands._auth.time.sleep"),
        ):
            result = _auth(cli_runner, storage, "connect", "--source", "fake_device")

        assert result.exit_code != 0
        assert "denied" in result.output.lower()
        assert _token(storage, "fake_device") is None

    def test_a_device_source_missing_its_setup_is_not_called_a_failed_sign_in(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        storage.sources.upsert(USER_ID, "fake_device", "fake_device", {}, enabled=True)

        result = _auth(cli_runner, storage, "connect", "--source", "fake_device")

        assert result.exit_code != 0
        assert "not enabled" in result.output
        assert "authentication failed" not in result.output

    @pytest.mark.parametrize(
        ("verb", "flag"), [("connect", "--no-browser"), ("disconnect", "--yes")]
    )
    def test_a_plugin_declaring_no_flow_names_the_ones_that_do(
        self, cli_runner: CliRunner, storage: StorageManager, verb: str, flag: str
    ) -> None:
        result = _auth(cli_runner, storage, verb, "--source", "fake_file", flag)

        assert result.exit_code != 0
        assert "--source must be one of: fake_device, fake_paste" in result.output


class TestAuthDisconnect:
    def test_disconnect_without_yes_asks_first(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        storage.credentials.save(USER_ID, "fake_paste", TOKEN, "token")
        _configure(storage, "fake_paste", "fake_paste")

        result = _auth(
            cli_runner,
            storage,
            "disconnect",
            "--source",
            "fake_paste",
            input_text="n\n",
        )

        assert "Aborted" in result.output
        assert _token(storage, "fake_paste") == "token"

    def test_disconnect_no_active_connection(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        _configure(storage, "fake_paste", "fake_paste")

        result = _auth(
            cli_runner, storage, "disconnect", "--source", "fake_paste", "--yes"
        )

        assert result.exit_code != 0
        assert "No active Fake Paste connection" in result.output

    def test_a_named_source_revokes_only_its_own_token(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        storage.credentials.save(USER_ID, "paste_work", TOKEN, "mine")
        storage.credentials.save(USER_ID, "fake_paste", TOKEN, "the-plugin-name-row")
        _configure(storage, "paste_work", "fake_paste")

        result = _auth(
            cli_runner,
            storage,
            "disconnect",
            "--source",
            "fake_paste",
            "--source-id",
            "paste_work",
            "--yes",
        )

        assert result.exit_code == 0, result.output
        assert _token(storage, "paste_work") is None
        assert _token(storage, "fake_paste") == "the-plugin-name-row"

    def test_an_id_another_plugin_owns_is_refused(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        storage.credentials.save(USER_ID, "device_work", TOKEN, "not-paste")
        _configure(storage, "device_work", "fake_device")

        result = _auth(
            cli_runner,
            storage,
            "disconnect",
            "--source",
            "fake_paste",
            "--source-id",
            "device_work",
            "--yes",
        )

        assert result.exit_code != 0
        assert "No active Fake Paste connection" in result.output
        assert _token(storage, "device_work") == "not-paste"

    @pytest.mark.parametrize("enabled", [True, False, None])
    def test_a_token_is_revocable_whatever_its_source_state(
        self, cli_runner: CliRunner, storage: StorageManager, enabled: bool | None
    ) -> None:
        storage.credentials.save(USER_ID, "paste_work", TOKEN, "still-live")
        if enabled is not None:
            _configure(storage, "paste_work", "fake_paste", enabled=enabled)

        result = _auth(
            cli_runner,
            storage,
            "disconnect",
            "--source",
            "fake_paste",
            "--source-id",
            "paste_work",
            "--yes",
        )

        assert result.exit_code == 0, result.output
        assert _token(storage, "paste_work") is None


class TestBothAuthVerbsValidateTheSourceId:
    _VERB_FLAGS = {"connect": ["--no-browser"], "disconnect": ["--yes"]}

    @pytest.mark.parametrize("verb", sorted(_VERB_FLAGS))
    @pytest.mark.parametrize("bad_id", MALFORMED_IDS)
    @pytest.mark.parametrize("plugin", ["fake_paste", "fake_device"])
    def test_a_malformed_id_is_refused_before_anything_reads_it(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        verb: str,
        bad_id: str,
        plugin: str,
    ) -> None:
        storage.credentials.save(USER_ID, plugin, TOKEN, "the-default-id")
        _configure(storage, plugin, plugin)

        result = _auth(
            cli_runner,
            storage,
            verb,
            "--source",
            plugin,
            "--source-id",
            bad_id,
            *self._VERB_FLAGS[verb],
        )

        assert result.exit_code != 0
        assert "--source-id must start with a lowercase letter" in result.output
        assert _token(storage, plugin) == "the-default-id"
