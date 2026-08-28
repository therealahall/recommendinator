from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import click
import pytest
import requests
from click.testing import CliRunner

from src.cli.commands._account import (
    PASSWORD_WRITE_FAILED,
    RENAME_FAILED,
    SESSION_SWEEP_FAILED,
)
from src.cli.commands._complete import COMPLETE_FAILED
from src.cli.commands._profile import PROFILE_LOAD_FAILED, PROFILE_REGENERATE_FAILED
from src.cli.commands._update import SYNC_FAILED
from src.cli.main import cli
from src.models.content import (
    MAX_REVIEW_LENGTH,
    ConsumptionStatus,
    ContentItem,
    ContentType,
)
from src.models.user_preferences import UserPreferenceConfig
from src.sources.service import SOURCE_ID_RULE, SOURCE_MISCONFIGURED_DETAIL
from src.storage.accounts import AccountStore
from src.storage.manager import StorageManager
from tests.factories import make_storage_mock
from tests.fakes.source_plugins import FakeFilePlugin

from .conftest import _invoke_with_mocks

_FAULT = "no such table: content_items"


def _source_config() -> dict[str, Any]:
    return {
        "inputs": {"books": {"plugin": "fake_file", "enabled": True, "path": "b.csv"}}
    }


def _assert_generic(result: Any, message: str) -> None:
    assert result.exit_code != 0
    assert f"{message}. Check logs for details." in result.output
    assert _FAULT not in result.output


def _assert_verbose(result: Any, message: str) -> None:
    assert result.exit_code != 0
    assert f"{message}: OperationalError: {_FAULT}" in result.output


class TestCompleteHidesTheWriteThatFailed:
    def test_the_refusal_is_generic_and_the_log_holds_the_reason(
        self, cli_runner: CliRunner, caplog: pytest.LogCaptureFixture
    ) -> None:
        storage = make_storage_mock()
        storage.complete_content_item.side_effect = sqlite3.OperationalError(_FAULT)

        with caplog.at_level(logging.ERROR, logger="src.cli._shared"):
            result = _invoke_with_mocks(
                cli_runner, ["complete", "--type", "book", "--title", "Dune"], storage
            )

        _assert_generic(result, COMPLETE_FAILED)
        assert _FAULT in caplog.text

    def test_verbose_adds_the_reason_and_still_no_traceback(
        self, cli_runner: CliRunner
    ) -> None:
        storage = make_storage_mock()
        storage.complete_content_item.side_effect = sqlite3.OperationalError(_FAULT)

        result = _invoke_with_mocks(
            cli_runner,
            ["--verbose", "complete", "--type", "book", "--title", "Dune"],
            storage,
        )

        _assert_verbose(result, COMPLETE_FAILED)
        assert "Traceback" not in result.output


class TestTheAccountWritesHideTheFaultToo:
    _PASSWORD = "a longer passphrase"

    @staticmethod
    def _claimed(tmp_path: Path) -> StorageManager:
        storage = StorageManager(sqlite_path=tmp_path / "account.db")
        storage.accounts.claim("owner", "The Owner", "correct horse")
        return storage

    def test_a_failed_rename_is_generic_and_the_log_holds_the_reason(
        self, cli_runner: CliRunner, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with (
            caplog.at_level(logging.ERROR, logger="src.cli._shared"),
            patch.object(
                StorageManager,
                "update_user_identity",
                side_effect=sqlite3.OperationalError(_FAULT),
            ),
        ):
            result = _invoke_with_mocks(
                cli_runner,
                ["account", "set-name", "--username", "keeper"],
                self._claimed(tmp_path),
            )

        _assert_generic(result, RENAME_FAILED)
        assert _FAULT in caplog.text

    @pytest.mark.parametrize(
        ("method", "message", "password_changed"),
        [
            ("set_password", PASSWORD_WRITE_FAILED, False),
            ("revoke_all_sessions", SESSION_SWEEP_FAILED, True),
        ],
    )
    def test_each_step_refuses_in_its_own_words_and_reports_the_password(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        method: str,
        message: str,
        password_changed: bool,
    ) -> None:
        storage = self._claimed(tmp_path)
        with (
            caplog.at_level(logging.ERROR, logger="src.cli._shared"),
            patch.object(
                AccountStore, method, side_effect=sqlite3.OperationalError(_FAULT)
            ),
        ):
            result = _invoke_with_mocks(
                cli_runner,
                ["account", "set-password"],
                storage,
                input_text=f"{self._PASSWORD}\n{self._PASSWORD}\n",
            )

        _assert_generic(result, message)
        assert _FAULT in caplog.text
        both = (PASSWORD_WRITE_FAILED, SESSION_SWEEP_FAILED)
        assert [step for step in both if step in result.output] == [message]
        changed = storage.accounts.verify_password("owner", self._PASSWORD) is not None
        assert changed is password_changed


class TestProfileHidesTheStorageFault:
    def test_show_refuses_in_the_webs_words(self, cli_runner: CliRunner) -> None:
        storage = make_storage_mock()
        storage.profiles.get.side_effect = sqlite3.OperationalError(_FAULT)

        result = _invoke_with_mocks(cli_runner, ["profile", "show"], storage)

        _assert_generic(result, PROFILE_LOAD_FAILED)

    def test_regenerate_refuses_in_the_webs_words(self, cli_runner: CliRunner) -> None:
        storage = make_storage_mock()
        storage.get_signal_items.side_effect = sqlite3.OperationalError(_FAULT)

        result = _invoke_with_mocks(cli_runner, ["profile", "regenerate"], storage)

        _assert_generic(result, PROFILE_REGENERATE_FAILED)


@pytest.mark.usefixtures("registry_with_source_fakes")
class TestUpdateHidesTheSyncFault:
    def test_verbose_still_withholds_a_token_the_fault_quotes(
        self, cli_runner: CliRunner
    ) -> None:
        token = "sk-live-9f3c2a"
        with patch(
            "src.cli.commands._update.execute_multi_source_sync",
            side_effect=requests.ConnectionError(
                f"HTTPConnectionPool: /v1/library?api_key={token} refused"
            ),
        ):
            result = _invoke_with_mocks(
                cli_runner,
                ["--verbose", "update"],
                make_storage_mock(),
                config=_source_config(),
            )

        assert result.exit_code != 0
        assert f"{SYNC_FAILED}: ConnectionError" in result.output
        assert token not in result.output


@pytest.mark.usefixtures("registry_with_source_fakes")
class TestUpdateNamesTheSettingRatherThanThePath:
    def _run_with_validation_error(
        self, cli_runner: CliRunner, args: list[str], reason: str
    ) -> Any:
        with patch.object(FakeFilePlugin, "validate_config", return_value=[reason]):
            return _invoke_with_mocks(
                cli_runner,
                args,
                make_storage_mock(),
                config=_source_config(),
            )

    @pytest.mark.parametrize("args", [["update"], ["update", "--source", "books"]])
    def test_a_quoted_field_is_named_and_the_path_is_not_regression(
        self, cli_runner: CliRunner, tmp_path: Path, args: list[str]
    ) -> None:
        secret_path = str(tmp_path / "books.csv")

        result = self._run_with_validation_error(
            cli_runner, args, f"'path' is not readable: {secret_path}"
        )

        assert "check its 'path' setting" in result.output
        assert secret_path not in result.output

    def test_prose_naming_no_field_gets_the_unqualified_refusal(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        secret_path = str(tmp_path / "books.csv")

        result = self._run_with_validation_error(
            cli_runner, ["update"], f"File not found: {secret_path}"
        )

        assert SOURCE_MISCONFIGURED_DETAIL in result.output
        assert secret_path not in result.output

    def test_the_reason_still_reaches_the_log(
        self, cli_runner: CliRunner, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="src.cli.commands._update"):
            self._run_with_validation_error(
                cli_runner, ["update"], "'path' is not readable"
            )

        assert "'path' is not readable" in caplog.text


class TestVerboseIsAnsweredByEveryCommandThatRefusesRegression:
    _CODE = "test-auth-code-abc123xyz\n"

    def test_auth_connect_puts_the_exchanges_reason_on_the_terminal(
        self, cli_runner: CliRunner
    ) -> None:
        with (
            patch("src.cli.commands._auth.is_gog_enabled", return_value=True),
            patch(
                "src.cli.commands._auth.get_gog_auth_url",
                return_value="https://auth.gog.com",
            ),
            patch(
                "src.cli.commands._auth.exchange_gog_code",
                side_effect=RuntimeError(_FAULT),
            ),
            patch("webbrowser.open"),
        ):
            result = _invoke_with_mocks(
                cli_runner,
                ["--verbose", "auth", "connect", "--source", "gog"],
                make_storage_mock(),
                input_text=self._CODE,
            )

        assert result.exit_code != 0
        assert _FAULT in result.output


class TestVerboseRendersAFaultTheTerminalCannotEncode:
    @pytest.mark.parametrize(
        ("raw", "rendered"),
        [
            ("no such table: \udcff", "no such table: \\udcff"),
            (
                "no such table: \x07\r\n injected",
                "no such table: \\u0007\\r\\n injected",
            ),
        ],
        ids=["lone-surrogate", "control-characters"],
    )
    def test_the_reason_is_escaped_rather_than_raising(
        self, cli_runner: CliRunner, raw: str, rendered: str
    ) -> None:
        storage = make_storage_mock()
        storage.complete_content_item.side_effect = sqlite3.OperationalError(raw)

        result = _invoke_with_mocks(
            cli_runner,
            ["--verbose", "complete", "--type", "book", "--title", "Dune"],
            storage,
        )

        assert result.exit_code != 0
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert f"{COMPLETE_FAILED}: OperationalError: {rendered}" in result.output


class TestACustomRuleIsShownSanitizedRegression:
    _RAW = "avoid horror\udcff\x07"
    _CLEANED = "avoid horror"

    def _storage_holding(self, rules: list[str]) -> MagicMock:
        storage = make_storage_mock()
        preferences = UserPreferenceConfig(custom_rules=list(rules))

        def merge(_user_id: int, apply: Any) -> UserPreferenceConfig:
            apply(preferences)
            return preferences

        storage.get_user_preference_config.return_value = preferences
        storage.merge_user_preference_config.side_effect = merge
        return storage

    def test_add_echoes_the_cleaned_rule_regression(
        self, cli_runner: CliRunner
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["preferences", "custom-rules", "add", self._RAW],
            self._storage_holding([]),
        )

        assert result.exit_code == 0, result.output
        assert f"Added rule: '{self._CLEANED}'" in result.output

    def test_list_echoes_the_cleaned_rule_regression(
        self, cli_runner: CliRunner
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["preferences", "custom-rules", "list"],
            self._storage_holding([self._RAW]),
        )

        assert result.exit_code == 0, result.output
        assert f"0: {self._CLEANED}" in result.output

    def test_remove_echoes_the_cleaned_rule_regression(
        self, cli_runner: CliRunner
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["preferences", "custom-rules", "remove", "0"],
            self._storage_holding([self._RAW]),
        )

        assert result.exit_code == 0, result.output
        assert f"Removed rule: '{self._CLEANED}'" in result.output

    @pytest.mark.parametrize("output_format", ["table", "json"])
    def test_preferences_get_still_renders_the_rule_regression(
        self, cli_runner: CliRunner, output_format: str
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["preferences", "get", "--format", output_format],
            self._storage_holding([self._RAW]),
        )

        assert result.exit_code == 0, result.output
        assert self._CLEANED in result.output
        assert "\udcff" not in result.output
        assert "\x07" not in result.output

    def test_interpret_echoes_the_text_it_parsed_regression(
        self, cli_runner: CliRunner
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["preferences", "custom-rules", "interpret", self._RAW],
            self._storage_holding([]),
        )

        assert result.exit_code == 0, result.output
        assert f"Rule: '{self._CLEANED}'" in result.output


class TestArgvTextIsStoredWithoutItsSurrogatesRegression:
    _RAW = "loved it\udcff"
    _CLEANED = "loved it"

    @pytest.mark.parametrize(
        ("option", "field", "stored"),
        [
            ("--review", "review", _CLEANED),
            ("--description", "description", _CLEANED),
            ("--genre", "genres", [_CLEANED]),
            ("--tag", "tags", [_CLEANED]),
        ],
    )
    def test_library_edit_stores_the_stripped_value_regression(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
        option: str,
        field: str,
        stored: object,
    ) -> None:
        storage, db_id = self._seeded_library(tmp_path)

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", str(db_id), option, self._RAW],
            storage,
        )
        assert result.exit_code == 0, result.output

        shown = _invoke_with_mocks(
            cli_runner,
            ["library", "show", "--id", str(db_id), "--format", "json"],
            storage,
        )
        assert json.loads(shown.output)[field] == stored

    def test_library_edit_refuses_a_review_that_strips_to_nothing(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage, db_id = self._seeded_library(tmp_path)
        storage.update_item_from_ui(
            db_id=db_id, status="completed", review="worth it", user_id=1
        )

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", str(db_id), "--review", "\udcff"],
            storage,
        )

        shown = _invoke_with_mocks(
            cli_runner,
            ["library", "show", "--id", str(db_id), "--format", "json"],
            storage,
        )
        assert json.loads(shown.output)["review"] == "worth it"
        assert result.exit_code != 0

    def test_source_create_refuses_a_surrogate_in_its_id(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "source.db")

        result = _invoke_with_mocks(
            cli_runner, ["source", "create", self._RAW, "fake_file"], storage
        )

        assert result.exit_code != 0
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert SOURCE_ID_RULE in result.output

    def test_source_create_refuses_a_surrogate_in_its_plugin_name(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "source.db")

        result = _invoke_with_mocks(
            cli_runner, ["source", "create", "books", self._RAW], storage
        )

        assert result.exit_code != 0
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert "Unknown plugin" in result.output

    @pytest.mark.parametrize(
        "command", ["show", "schema", "migrate", "enable", "disable"]
    )
    def test_an_unknown_source_id_is_named_back_without_its_surrogate(
        self, cli_runner: CliRunner, tmp_path: Path, command: str
    ) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "source.db")

        result = _invoke_with_mocks(cli_runner, ["source", command, self._RAW], storage)

        assert result.exit_code != 0
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert "Unknown source" in result.output

    def test_complete_stores_the_stripped_title_and_review_regression(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "complete.db")

        result = _invoke_with_mocks(
            cli_runner,
            ["complete", "--type", "book", "--title", self._RAW, "--review", self._RAW],
            storage,
        )

        assert result.exit_code == 0, result.output
        assert f"Marked '{self._CLEANED}' as completed" in result.output
        stored = storage.get_content_items(content_type=ContentType.BOOK)
        assert [(item.title, item.review) for item in stored] == [
            (self._CLEANED, self._CLEANED)
        ]

    @staticmethod
    def _seeded_library(tmp_path: Path) -> tuple[StorageManager, int]:
        storage = StorageManager(sqlite_path=tmp_path / "library.db")
        db_id = storage.save_content_item(
            ContentItem(
                id="book-1",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
            ),
            user_id=1,
        )
        return storage, db_id


class TestTheSurrogateStripIsOneGate:
    @staticmethod
    def _bound(*argv: str) -> list[str]:
        seen: list[str] = []
        group = type(cli)(name="recommendinator")

        @group.command()
        @click.argument("values", nargs=-1)
        def take(values: tuple[str, ...]) -> None:
            seen.extend(values)

        result = CliRunner().invoke(group, ["take", *argv], catch_exceptions=False)

        assert result.exit_code == 0, result.stderr
        return seen

    def test_the_root_group_strips_every_token_before_parsing(self) -> None:
        assert self._bound("a\udcffb", "c\udcffd") == ["ab", "cd"]

    def test_text_the_locale_can_decode_survives_the_strip(self) -> None:
        kept = "Sublime — 日本語 🎉 café"

        assert self._bound(kept) == [kept]


class TestAGuardSeesTheValueStorageWillGetRegression:
    _ALL_UNDECODABLE = "\udcff\udcfe"

    @staticmethod
    def _library(tmp_path: Path) -> tuple[StorageManager, int]:
        storage = StorageManager(sqlite_path=tmp_path / "guards.db")
        db_id = storage.save_content_item(
            ContentItem(
                id="book-1",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
            ),
            user_id=1,
        )
        storage.update_item_from_ui(
            db_id=db_id, status="completed", review="worth it", user_id=1
        )
        return storage, db_id

    def test_library_edit_says_why_it_refused_the_review(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage, db_id = self._library(tmp_path)

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", str(db_id), "--review", self._ALL_UNDECODABLE],
            storage,
        )

        assert result.exit_code != 0
        assert "--review cannot be empty" in result.output
        assert "--clear-review" in result.output
        assert storage.get_content_item(db_id, user_id=1).review == "worth it"

    def test_complete_refuses_the_same_review_regression(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "complete.db")

        result = _invoke_with_mocks(
            cli_runner,
            ["complete", "--type", "book", "--title", "Dune"]
            + ["--review", self._ALL_UNDECODABLE],
            storage,
        )

        assert result.exit_code != 0
        assert "--review cannot be empty" in result.output
        assert storage.get_content_items(content_type=ContentType.BOOK) == []

    def test_the_length_cap_measures_the_review_that_gets_stored(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage, db_id = self._library(tmp_path)
        review = "a" * MAX_REVIEW_LENGTH + "\udcff" * 20

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", str(db_id), "--review", review],
            storage,
        )

        assert result.exit_code == 0, result.output
        assert storage.get_content_item(db_id, user_id=1).review == (
            "a" * MAX_REVIEW_LENGTH
        )

    def test_a_config_path_of_undecodable_bytes_is_refused_as_missing(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        config = tmp_path / "example\udcff.yaml"
        config.write_text("{}", encoding="utf-8", errors="surrogateescape")

        result = cli_runner.invoke(cli, ["--config", str(config), "status"])

        assert result.exit_code != 0
        assert "does not exist" in result.output
