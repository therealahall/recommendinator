"""The ``account`` group, the break-glass path to a lost web password.

The CLI reaches storage directly rather than over HTTP, so it is the only
surface that can reset the one account with no server and no session.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from src.storage.accounts import (
    ACCOUNT_NAME_BLANK,
    ACCOUNT_NAME_TOO_LONG,
    MAX_ACCOUNT_NAME_LENGTH,
    MIN_PASSWORD_LENGTH,
    PASSWORD_TOO_SHORT,
)
from src.storage.manager import StorageManager
from src.storage.schema import create_user
from tests.cli.conftest import _invoke_with_mocks

_NEW_PASSWORD = "a longer passphrase"
_OLD_PASSWORD = "correct horse"

#: What an operator types at the prompt and its confirmation.
_TYPED_TWICE = f"{_NEW_PASSWORD}\n{_NEW_PASSWORD}\n"

_SET_PASSWORD = ["account", "set-password"]

#: Before any session this suite opens, so a row stamped with it has lapsed.
_LONG_AGO = "2000-01-01T00:00:00"


@pytest.fixture
def storage(tmp_path: Path) -> StorageManager:
    """A manager on its own database, with nobody claiming the instance."""
    return StorageManager(sqlite_path=tmp_path / "account.db")


@pytest.fixture
def claimed(storage: StorageManager) -> StorageManager:
    """That instance, claimed by ``owner`` with ``correct horse``."""
    storage.accounts.claim("owner", "The Owner", _OLD_PASSWORD)
    return storage


def _run(
    runner: CliRunner,
    storage: StorageManager,
    args: list[str],
    input_text: str | None = None,
) -> Any:
    return _invoke_with_mocks(runner, args, storage, input_text=input_text)


def _stored_password(storage: StorageManager) -> Any:
    with storage.sqlite_db.connection() as conn:
        return conn.execute(
            "SELECT password_hash, password_salt FROM users WHERE id = 1"
        ).fetchone()


def _session_rows(storage: StorageManager) -> int:
    with storage.sqlite_db.connection() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])


class TestResettingThePasswordWithNoServerRunning:
    """The acceptance path: the stored hash verifies afterwards."""

    def test_the_new_password_verifies_and_the_old_one_stops(
        self, cli_runner: CliRunner, claimed: StorageManager
    ) -> None:
        result = _run(cli_runner, claimed, _SET_PASSWORD, _TYPED_TWICE)

        assert result.exit_code == 0, result.output
        assert "Password set. Every session has been signed out." in result.output
        assert claimed.accounts.verify_password("owner", _NEW_PASSWORD) is not None
        assert claimed.accounts.verify_password("owner", _OLD_PASSWORD) is None

    def test_every_session_is_revoked(
        self, cli_runner: CliRunner, claimed: StorageManager
    ) -> None:
        """A browser someone else left signed in dies with the old password."""
        tokens = [claimed.accounts.create_session(1) for _ in range(2)]
        assert [claimed.accounts.lookup_session(token) for token in tokens] != [
            None,
            None,
        ]

        result = _run(cli_runner, claimed, _SET_PASSWORD, _TYPED_TWICE)

        assert result.exit_code == 0, result.output
        assert [claimed.accounts.lookup_session(token) for token in tokens] == [
            None,
            None,
        ]

    def test_it_also_sweeps_the_sessions_that_have_already_lapsed(
        self, cli_runner: CliRunner, claimed: StorageManager
    ) -> None:
        """Nothing else deletes a lapsed row, so the table grew without bound.

        Revoking is scoped to the account being reset, which is why the sweep
        is only visible on a row that reset does not own.
        """
        with claimed.sqlite_db.connection() as conn:
            other = create_user(conn, "second")
            conn.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
                ("lapsed-digest", other, _LONG_AGO, _LONG_AGO, _LONG_AGO),
            )
            conn.commit()
        assert _session_rows(claimed) == 1

        result = _run(cli_runner, claimed, _SET_PASSWORD, _TYPED_TWICE)

        assert result.exit_code == 0, result.output
        assert _session_rows(claimed) == 0


class TestTheFloorTheWebFormAlsoKeeps:
    """Regression test: the CLI set a password the web would reject.

    Bug reported: docs/SECURITY.md says the minimum holds wherever one is set,
    naming ``account set-password``.
    Root cause: the group called ``set_password`` unchecked.
    Fix: apply ``MIN_PASSWORD_LENGTH`` here too.
    """

    def test_a_short_password_is_refused_and_the_hash_is_untouched_regression(
        self, cli_runner: CliRunner, claimed: StorageManager
    ) -> None:
        short = "x" * (MIN_PASSWORD_LENGTH - 1)
        before = _stored_password(claimed)

        result = _run(cli_runner, claimed, _SET_PASSWORD, f"{short}\n{short}\n")

        assert result.exit_code != 0
        assert PASSWORD_TOO_SHORT in result.output
        assert _stored_password(claimed) == before
        assert claimed.accounts.verify_password("owner", _OLD_PASSWORD) is not None
        assert claimed.accounts.verify_password("owner", short) is None

    def test_a_password_at_the_floor_is_accepted(
        self, cli_runner: CliRunner, claimed: StorageManager
    ) -> None:
        """Anchors the refusal: the boundary is ``<``, not ``<=``."""
        at_the_floor = "x" * MIN_PASSWORD_LENGTH

        result = _run(
            cli_runner, claimed, _SET_PASSWORD, f"{at_the_floor}\n{at_the_floor}\n"
        )

        assert result.exit_code == 0, result.output
        assert claimed.accounts.verify_password("owner", at_the_floor) is not None


class TestThePasswordIsNeverAnArgvValue:
    """An argv password lands in the shell history and in the process table."""

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
        assert json.loads(result.stdout) == claimed.accounts.describe(1)
        assert "New password" in result.stderr


class TestAnUnclaimedInstanceIsRefused:
    """Claiming happens in the browser; a reset must not half-create an account."""

    def test_set_password_refuses_and_writes_nothing(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        before = storage.accounts.describe(1)
        assert before is not None and before["claimed"] is False

        result = _run(cli_runner, storage, _SET_PASSWORD, _TYPED_TWICE)

        assert result.exit_code != 0
        assert "unclaimed" in result.output
        assert storage.accounts.describe(1) == before
        assert storage.accounts.verify_password("default", _NEW_PASSWORD) is None


class TestShowingTheAccount:
    def test_the_table_names_the_account_and_its_password_age(
        self, cli_runner: CliRunner, claimed: StorageManager
    ) -> None:
        record = claimed.accounts.describe(1)
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

        record = claimed.accounts.describe(1)
        assert result.exit_code == 0, result.output
        assert "Account updated. Username: keeper." in result.output
        assert record is not None
        assert (record["username"], record["display_name"]) == ("keeper", "The Owner")

    def test_the_renamed_account_still_logs_in(
        self, cli_runner: CliRunner, claimed: StorageManager
    ) -> None:
        """The username is the login, so the password has to follow it."""
        _run(cli_runner, claimed, ["account", "set-name", "--username", "keeper"])

        assert claimed.accounts.verify_password("keeper", _OLD_PASSWORD) is not None
        assert claimed.accounts.verify_password("owner", _OLD_PASSWORD) is None

    def test_a_blank_username_is_refused_and_writes_nothing(
        self, cli_runner: CliRunner, claimed: StorageManager
    ) -> None:
        """The storage door's own refusal, named after the option that broke it.

        The web renders the same sentence for the same value.
        """
        before = claimed.accounts.describe(1)

        result = _run(cli_runner, claimed, ["account", "set-name", "--username", "  "])

        assert result.exit_code != 0
        assert f"--username: {ACCOUNT_NAME_BLANK}" in result.output
        assert claimed.accounts.describe(1) == before

    @pytest.mark.parametrize("option", ["--username", "--display-name"])
    def test_a_name_past_the_column_is_refused_as_the_web_refuses_it(
        self, cli_runner: CliRunner, claimed: StorageManager, option: str
    ) -> None:
        """``UserUpdateRequest`` caps both at the same width, and 422s past it.

        Regression: the group carried its own copy of the cap and its own
        sentence, so the two interfaces could refuse different widths.
        """
        before = claimed.accounts.describe(1)

        result = _run(
            cli_runner,
            claimed,
            ["account", "set-name", option, "x" * (MAX_ACCOUNT_NAME_LENGTH + 1)],
        )

        assert result.exit_code != 0
        assert f"{option}: {ACCOUNT_NAME_TOO_LONG}" in result.output
        assert claimed.accounts.describe(1) == before

    def test_passing_neither_name_is_refused(
        self, cli_runner: CliRunner, claimed: StorageManager
    ) -> None:
        before = claimed.accounts.describe(1)

        result = _run(cli_runner, claimed, ["account", "set-name"])

        assert result.exit_code != 0
        assert "Pass --username, --display-name, or both." in result.output
        assert claimed.accounts.describe(1) == before


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
        assert json.loads(result.stdout) == claimed.accounts.describe(1)
