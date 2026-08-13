"""The ``account`` group, the break-glass path to a lost web password.

The CLI reaches storage directly rather than over HTTP, so it is the only
surface that can reset the one account with no server and no session.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner

from src.cli.commands._account import account
from src.storage.manager import StorageManager
from tests.cli.conftest import _invoke_with_mocks

_NEW_PASSWORD = "a longer passphrase"
_OLD_PASSWORD = "correct horse"

#: What an operator types at the prompt and its confirmation.
_TYPED_TWICE = f"{_NEW_PASSWORD}\n{_NEW_PASSWORD}\n"

_SET_PASSWORD = ["account", "set-password"]


@pytest.fixture
def storage(tmp_path: Path) -> StorageManager:
    """A manager on its own database, with nobody claiming the instance."""
    return StorageManager(sqlite_path=tmp_path / "account.db")


@pytest.fixture
def claimed(storage: StorageManager) -> StorageManager:
    """That instance, claimed by ``owner`` with ``correct horse``."""
    storage.claim_account("owner", "The Owner", _OLD_PASSWORD)
    return storage


def _run(
    runner: CliRunner,
    storage: StorageManager,
    args: list[str],
    input_text: str | None = None,
) -> Any:
    return _invoke_with_mocks(runner, args, storage, input_text=input_text)


def _option_spellings(command: click.Command) -> set[str]:
    return {opt for param in command.params for opt in param.opts}


class TestResettingThePasswordWithNoServerRunning:
    """The acceptance path: the stored hash verifies afterwards."""

    def test_the_new_password_verifies_and_the_old_one_stops(
        self, cli_runner: CliRunner, claimed: StorageManager
    ) -> None:
        result = _run(cli_runner, claimed, _SET_PASSWORD, _TYPED_TWICE)

        assert result.exit_code == 0, result.output
        assert "Password set. Every session has been signed out." in result.output
        assert claimed.verify_password("owner", _NEW_PASSWORD) is not None
        assert claimed.verify_password("owner", _OLD_PASSWORD) is None

    def test_every_session_is_revoked(
        self, cli_runner: CliRunner, claimed: StorageManager
    ) -> None:
        """A browser someone else left signed in dies with the old password."""
        tokens = [claimed.create_session(1) for _ in range(2)]
        assert [claimed.lookup_session(token) for token in tokens] != [None, None]

        result = _run(cli_runner, claimed, _SET_PASSWORD, _TYPED_TWICE)

        assert result.exit_code == 0, result.output
        assert [claimed.lookup_session(token) for token in tokens] == [None, None]

    def test_the_password_stamp_moves(
        self, cli_runner: CliRunner, claimed: StorageManager
    ) -> None:
        """``account show`` reports it, so the write has to touch it."""
        before = claimed.describe_account(1)

        _run(cli_runner, claimed, _SET_PASSWORD, _TYPED_TWICE)

        after = claimed.describe_account(1)
        assert before is not None and after is not None
        assert before["password_updated_at"] is not None
        assert after["password_updated_at"] >= before["password_updated_at"]


class TestThePasswordIsNeverAnArgvValue:
    """An argv password lands in the shell history and in the process table."""

    def test_no_command_in_the_group_accepts_a_password_option(self) -> None:
        spellings = {
            name: _option_spellings(command)
            for name, command in account.commands.items()
        }

        assert set(spellings) == {"show", "set-password", "set-name"}
        assert [name for name, opts in spellings.items() if "--password" in opts] == []

    def test_the_typed_password_is_never_echoed(
        self, cli_runner: CliRunner, claimed: StorageManager
    ) -> None:
        """``hide_input``: a visible prompt echoes what was typed, into the
        terminal and into whatever recorded the session."""
        result = _run(cli_runner, claimed, _SET_PASSWORD, _TYPED_TWICE)

        assert result.exit_code == 0, result.output
        # Anchors the absence below: both prompts really did run.
        assert "New password" in result.output
        assert "Repeat for confirmation" in result.output
        assert _NEW_PASSWORD not in result.output

    def test_the_prompt_stays_off_the_data_channel(
        self, claimed: StorageManager
    ) -> None:
        """``--format json`` is pipeable, so the prompt goes to stderr."""
        result = _run(
            CliRunner(mix_stderr=False),
            claimed,
            [*_SET_PASSWORD, "--format", "json"],
            _TYPED_TWICE,
        )

        assert result.exit_code == 0, result.stderr
        assert json.loads(result.stdout) == claimed.describe_account(1)
        assert "New password" in result.stderr

    def test_a_mismatched_confirmation_stores_neither_attempt(
        self, cli_runner: CliRunner, claimed: StorageManager
    ) -> None:
        """The confirmation is what catches a typo in something nobody can see.

        The mismatch is reported and both prompts come round again, so the
        password that lands is the one typed twice.
        """
        result = _run(
            cli_runner,
            claimed,
            _SET_PASSWORD,
            f"one thing\nanother thing\n{_TYPED_TWICE}",
        )

        assert result.exit_code == 0, result.output
        assert "do not match" in result.output
        assert claimed.verify_password("owner", "one thing") is None
        assert claimed.verify_password("owner", _NEW_PASSWORD) is not None


class TestAnUnclaimedInstanceIsRefused:
    """Claiming happens in the browser; a reset must not half-create an account."""

    def test_set_password_refuses_and_writes_nothing(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        before = storage.describe_account(1)
        assert before is not None and before["claimed"] is False

        result = _run(cli_runner, storage, _SET_PASSWORD, _TYPED_TWICE)

        assert result.exit_code != 0
        assert "unclaimed" in result.output
        assert storage.describe_account(1) == before
        assert storage.verify_password("default", _NEW_PASSWORD) is None

    def test_show_reports_the_unclaimed_state_rather_than_failing(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        result = _run(cli_runner, storage, ["account", "show"])

        assert result.exit_code == 0, result.output
        assert "Claimed: no" in result.output
        assert "Password changed: never" in result.output


class TestShowingTheAccount:
    def test_the_table_names_the_account_and_its_password_age(
        self, cli_runner: CliRunner, claimed: StorageManager
    ) -> None:
        record = claimed.describe_account(1)
        assert record is not None

        result = _run(cli_runner, claimed, ["account", "show"])

        assert result.exit_code == 0, result.output
        assert "Username: owner" in result.output
        assert "Display name: The Owner" in result.output
        assert "Claimed: yes" in result.output
        assert f"Password changed: {record['password_updated_at']}" in result.output

    def test_a_user_id_nobody_carries_is_refused(
        self, cli_runner: CliRunner, claimed: StorageManager
    ) -> None:
        result = _run(cli_runner, claimed, ["account", "show", "--user", "7"])

        assert result.exit_code != 0
        assert "No user with id 7." in result.output


class TestRenamingTheAccount:
    def test_only_the_name_that_was_passed_is_written(
        self, cli_runner: CliRunner, claimed: StorageManager
    ) -> None:
        """A username-only rename must not erase the display name."""
        result = _run(
            cli_runner, claimed, ["account", "set-name", "--username", "keeper"]
        )

        record = claimed.describe_account(1)
        assert result.exit_code == 0, result.output
        assert "Account updated. Username: keeper." in result.output
        assert record is not None
        assert (record["username"], record["display_name"]) == ("keeper", "The Owner")

    def test_the_renamed_account_still_logs_in(
        self, cli_runner: CliRunner, claimed: StorageManager
    ) -> None:
        """The username is the login, so the password has to follow it."""
        _run(cli_runner, claimed, ["account", "set-name", "--username", "keeper"])

        assert claimed.verify_password("keeper", _OLD_PASSWORD) is not None
        assert claimed.verify_password("owner", _OLD_PASSWORD) is None

    def test_an_empty_display_name_clears_it(
        self, cli_runner: CliRunner, claimed: StorageManager
    ) -> None:
        result = _run(
            cli_runner, claimed, ["account", "set-name", "--display-name", ""]
        )

        record = claimed.describe_account(1)
        assert result.exit_code == 0, result.output
        assert record is not None
        assert (record["username"], record["display_name"]) == ("owner", None)

    def test_a_blank_username_is_refused_and_writes_nothing(
        self, cli_runner: CliRunner, claimed: StorageManager
    ) -> None:
        """An empty ``--username`` is a shell accident, and it would leave the
        login form with no name that works."""
        before = claimed.describe_account(1)

        result = _run(cli_runner, claimed, ["account", "set-name", "--username", "  "])

        assert result.exit_code != 0
        assert "--username cannot be blank." in result.output
        assert claimed.describe_account(1) == before

    def test_passing_neither_name_is_refused(
        self, cli_runner: CliRunner, claimed: StorageManager
    ) -> None:
        before = claimed.describe_account(1)

        result = _run(cli_runner, claimed, ["account", "set-name"])

        assert result.exit_code != 0
        assert "Pass --username, --display-name, or both." in result.output
        assert claimed.describe_account(1) == before


class TestTheJsonViewIsTheRecordStorageHolds:
    """Every command emits what ``describe_account`` returns, unshaped.

    Compared against storage rather than a literal, so a field the record
    grows cannot reach one command's document and miss another's.
    """

    @pytest.mark.parametrize(
        ("args", "input_text"),
        [
            (["account", "show", "--format", "json"], None),
            ([*_SET_PASSWORD, "--format", "json"], _TYPED_TWICE),
            (
                [
                    "account",
                    "set-name",
                    "--display-name",
                    "Renamed",
                    "--format",
                    "json",
                ],
                None,
            ),
        ],
        ids=["show", "set-password", "set-name"],
    )
    def test_the_document_is_the_stored_record(
        self, claimed: StorageManager, args: list[str], input_text: str | None
    ) -> None:
        result = _run(CliRunner(mix_stderr=False), claimed, args, input_text)

        assert result.exit_code == 0, result.stderr
        assert json.loads(result.stdout) == claimed.describe_account(1)

    def test_the_record_carries_every_field_the_group_prints(
        self, claimed: StorageManager
    ) -> None:
        """Anchors the comparisons above, which an empty record satisfies."""
        assert set(claimed.describe_account(1) or {}) == {
            "id",
            "username",
            "display_name",
            "claimed",
            "password_updated_at",
        }

    @pytest.mark.parametrize(
        ("args", "input_text", "expected"),
        [
            (_SET_PASSWORD, _TYPED_TWICE, "Password set."),
            (
                ["account", "set-name", "--display-name", "Renamed"],
                None,
                "Account updated.",
            ),
        ],
        ids=["set-password", "set-name"],
    )
    def test_the_table_branch_prints_prose_instead_of_the_document(
        self,
        cli_runner: CliRunner,
        claimed: StorageManager,
        args: list[str],
        input_text: str | None,
        expected: str,
    ) -> None:
        result = _run(cli_runner, claimed, args, input_text)

        assert result.exit_code == 0, result.output
        assert expected in result.output
        assert "password_updated_at" not in result.output


class TestAFailedWriteSaysWhatTheWebSays:
    """The database's own words go to the log, and to the terminal only on ask."""

    @staticmethod
    def _refuse_every_write(
        monkeypatch: pytest.MonkeyPatch, storage: StorageManager
    ) -> None:
        def refuse(*_args: object, **_kwargs: object) -> None:
            raise sqlite3.OperationalError("attempt to write a readonly database")

        monkeypatch.setattr(storage, "set_password", refuse)
        monkeypatch.setattr(storage, "update_user_identity", refuse)

    @pytest.mark.parametrize(
        ("args", "input_text", "action"),
        [
            (_SET_PASSWORD, _TYPED_TWICE, "set the password"),
            (
                ["account", "set-name", "--username", "keeper"],
                None,
                "rename the account",
            ),
        ],
        ids=["set-password", "set-name"],
    )
    def test_the_refusal_names_the_log_while_the_log_holds_the_detail(
        self,
        cli_runner: CliRunner,
        claimed: StorageManager,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        args: list[str],
        input_text: str | None,
        action: str,
    ) -> None:
        self._refuse_every_write(monkeypatch, claimed)

        with caplog.at_level(logging.ERROR, logger="src.cli.commands._account"):
            result = _run(cli_runner, claimed, args, input_text)

        assert result.exit_code != 0
        assert f"Could not {action}. Check logs for details." in result.output
        assert "readonly database" not in result.output
        assert "readonly database" in caplog.text

    @pytest.mark.parametrize(
        ("args", "input_text"),
        [
            ([*_SET_PASSWORD, "--verbose"], _TYPED_TWICE),
            (["account", "set-name", "--username", "keeper", "--verbose"], None),
        ],
        ids=["set-password", "set-name"],
    )
    def test_verbose_adds_the_underlying_error(
        self,
        cli_runner: CliRunner,
        claimed: StorageManager,
        monkeypatch: pytest.MonkeyPatch,
        args: list[str],
        input_text: str | None,
    ) -> None:
        """For the operator whose log file is unreadable."""
        self._refuse_every_write(monkeypatch, claimed)

        result = _run(cli_runner, claimed, args, input_text)

        assert result.exit_code != 0
        assert "attempt to write a readonly database" in result.output
