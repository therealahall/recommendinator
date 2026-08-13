"""Passwords and sessions for the single web account.

Claiming the instance is an UPDATE of user 1, never an INSERT: the whole
library hangs off that row. Sessions are stored as digests.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import sqlite3
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.storage import accounts
from src.storage.accounts import (
    SESSION_LIFETIME,
    AccountAlreadyClaimedError,
    account_is_claimed,
    claim_account,
    create_session,
    lookup_session,
    purge_expired_sessions,
    revoke_all_sessions,
    revoke_session,
    set_password,
    verify_password,
)
from src.storage.manager import StorageManager
from src.storage.schema import _SCHEMA_VERSION, create_schema, create_user

_ACCOUNTS_MODULE = Path(accounts.__file__)

# The two tables as the build before the account columns wrote them: no
# password columns on ``users``, and the ``content_items`` shape that build
# reached after its own ALTERs.
_USERS_BEFORE_THE_PASSWORD_COLUMNS = """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        display_name TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        settings TEXT
    )
"""

_CONTENT_ITEMS_BEFORE_THE_PASSWORD_COLUMNS = """
    CREATE TABLE content_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL DEFAULT 1 REFERENCES users(id) ON DELETE CASCADE,
        external_id TEXT,
        title TEXT NOT NULL,
        normalized_title TEXT,
        sort_title TEXT,
        search_text TEXT,
        content_type TEXT NOT NULL,
        status TEXT NOT NULL,
        ignored BOOLEAN DEFAULT 0,
        rating INTEGER,
        review TEXT,
        date_completed DATE,
        source TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, external_id, content_type)
    )
"""

_THE_LIBRARY = [("goodreads-1", "The Dispossessed"), ("steam-2", "Disco Elysium")]


def _open(db_path: Path) -> None:
    """Run ``create_schema`` over its own connection, as the app does."""
    conn = sqlite3.connect(db_path)
    try:
        create_schema(conn)
    finally:
        conn.close()


def _seed_the_library(conn: sqlite3.Connection) -> None:
    """Write ``_THE_LIBRARY`` under user 1, the id every content row carries."""
    conn.executemany(
        """INSERT INTO content_items
               (user_id, external_id, title, content_type, status)
           VALUES (1, ?, ?, 'book', 'completed')""",
        _THE_LIBRARY,
    )
    conn.commit()


def _library_of(conn: sqlite3.Connection) -> list[tuple[Any, ...]]:
    """Every content row's owner and title, oldest first."""
    return conn.execute(
        "SELECT user_id, title FROM content_items ORDER BY id"
    ).fetchall()


def _stored_password(conn: sqlite3.Connection) -> tuple[Any, Any]:
    """The account's ``(password_hash, password_salt)`` as stored."""
    row = conn.execute(
        "SELECT password_hash, password_salt FROM users WHERE id = 1"
    ).fetchone()
    return row[0], row[1]


def _session_row(conn: sqlite3.Connection, token: str) -> Any:
    """The stored ``(expires_at, last_seen_at)`` of *token*'s session."""
    return conn.execute(
        "SELECT expires_at, last_seen_at FROM sessions WHERE token_hash = ?",
        (hashlib.sha256(token.encode()).hexdigest(),),
    ).fetchone()


def _live_tokens(conn: sqlite3.Connection, tokens: list[str]) -> list[str]:
    """Whichever of *tokens* still resolve to a user."""
    return [token for token in tokens if lookup_session(conn, token) is not None]


def _session_opened_at(conn: sqlite3.Connection, moment: datetime) -> str:
    """Open a session at *moment*, so its window is the one that build set."""
    with patch.object(accounts, "utc_now", return_value=moment):
        return create_session(conn, 1)


def _every_byte_sqlite_wrote(db_path: Path) -> bytes:
    """The database file and whatever sidecars WAL mode left beside it.

    A row still in the write-ahead log is not in the ``.db`` file, so reading
    that alone would let a leaked token pass unseen.
    """
    return b"".join(
        sidecar.read_bytes()
        for sidecar in sorted(db_path.parent.glob(f"{db_path.name}*"))
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """A database with the current schema and nobody claiming it."""
    path = tmp_path / "accounts.db"
    _open(path)
    return path


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    """A connection to that database, as the storage layer opens one."""
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def claimed(conn: sqlite3.Connection) -> sqlite3.Connection:
    """That database, claimed by ``owner`` with the password ``correct horse``."""
    claim_account(conn, "owner", "The Owner", "correct horse")
    return conn


class TestClaimingTheInstance:
    """The claim renames user 1; a second user row would orphan the library.

    Every content, credential and source-config row is keyed ``user_id = 1``,
    so a session pointed at a new row opens on an empty library.
    """

    def test_claiming_updates_user_one_and_leaves_the_library_reachable(
        self, conn: sqlite3.Connection
    ) -> None:
        """The id is unchanged, the rows are still there, and there is one user."""
        _seed_the_library(conn)
        before = _library_of(conn)

        account = claim_account(conn, "owner", "The Owner", "correct horse")

        assert before == [(1, "The Dispossessed"), (1, "Disco Elysium")]
        assert account["id"] == 1
        assert account["username"] == "owner"
        assert account["display_name"] == "The Owner"
        assert _library_of(conn) == before
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1

    def test_an_instance_is_unclaimed_until_it_has_a_password(
        self, conn: sqlite3.Connection
    ) -> None:
        """What the web layer reads to decide between login and first-run setup."""
        assert account_is_claimed(conn) is False

        claim_account(conn, "owner", None, "correct horse")

        assert account_is_claimed(conn) is True

    def test_a_second_claim_is_refused_and_writes_nothing(
        self, claimed: sqlite3.Connection
    ) -> None:
        """Otherwise the setup form hands the library to whoever finds it."""
        before = _stored_password(claimed)

        with pytest.raises(AccountAlreadyClaimedError):
            claim_account(claimed, "intruder", None, "hunter2")

        assert _stored_password(claimed) == before
        assert verify_password(claimed, "intruder", "hunter2") is None
        assert verify_password(claimed, "owner", "correct horse") is not None

    def test_a_database_with_no_account_row_has_nothing_to_claim(
        self, conn: sqlite3.Connection
    ) -> None:
        """The other way the guarded UPDATE matches no row: it must not INSERT."""
        conn.execute("DELETE FROM users WHERE id = 1")
        conn.commit()

        with pytest.raises(AccountAlreadyClaimedError):
            claim_account(conn, "owner", None, "correct horse")

        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


class TestHowThePasswordIsStored:
    """scrypt from the standard library, under a salt drawn per account."""

    def test_the_stored_hash_is_scrypt_over_the_stored_salt(
        self, claimed: sqlite3.Connection
    ) -> None:
        """Recomputed from the declared cost parameters, so a change shows up."""
        stored_hash, stored_salt = _stored_password(claimed)

        expected = hashlib.scrypt(
            b"correct horse",
            salt=bytes.fromhex(stored_salt),
            n=accounts._SCRYPT_N,
            r=accounts._SCRYPT_R,
            p=accounts._SCRYPT_P,
            dklen=accounts._SCRYPT_DKLEN,
        ).hex()

        assert stored_hash == expected

    def test_the_password_itself_is_nowhere_in_the_database_file(
        self, claimed: sqlite3.Connection, db_path: Path
    ) -> None:
        """A digest, not a reversible secret: no key recovers this one."""
        claimed.commit()
        stored_hash, _ = _stored_password(claimed)

        written = db_path.read_bytes()
        assert stored_hash.encode() in written
        assert b"correct horse" not in written

    def test_the_same_password_set_twice_gets_a_new_salt(
        self, claimed: sqlite3.Connection
    ) -> None:
        """A salt drawn once and reused would make equal passwords equal hashes."""
        first = _stored_password(claimed)

        set_password(claimed, 1, "correct horse")

        second = _stored_password(claimed)
        assert second[1] != first[1]
        assert second[0] != first[0]
        assert verify_password(claimed, "owner", "correct horse") is not None

    def test_the_salt_is_the_declared_number_of_random_bytes(
        self, claimed: sqlite3.Connection
    ) -> None:
        """Hex in the column, bytes to scrypt."""
        _, stored_salt = _stored_password(claimed)

        assert len(bytes.fromhex(stored_salt)) == accounts._SALT_BYTES

    def test_the_module_imports_nothing_outside_the_standard_library(self) -> None:
        """The hashing must not cost a dependency: bcrypt and passlib are absent.

        ``cryptography`` would work and is already a direct dependency, but the
        stdlib is one import fewer for the same primitive.
        """
        tree = ast.parse(_ACCOUNTS_MODULE.read_text(encoding="utf-8"))
        roots = {
            (node.module or "").split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        } | {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }

        assert "hashlib" in roots
        assert roots <= sys.stdlib_module_names | {"src", "__future__"}


class TestVerifyingAPassword:
    """A wrong password and an unknown username must be indistinguishable."""

    @staticmethod
    def _attempt(
        conn: sqlite3.Connection, username: str, password: str
    ) -> tuple[Any, int, int]:
        """Return the result, the scrypt runs it cost, and the salt length."""
        with patch.object(
            accounts, "_derive_key", wraps=accounts._derive_key
        ) as derive:
            result = verify_password(conn, username, password)
        return result, derive.call_count, len(derive.call_args.args[1])

    def test_the_right_password_returns_the_account(
        self, claimed: sqlite3.Connection
    ) -> None:
        """The user dict the web layer puts in the session."""
        account = verify_password(claimed, "owner", "correct horse")

        assert account is not None
        assert account["id"] == 1
        assert account["username"] == "owner"

    def test_a_wrong_password_and_an_unknown_username_cost_the_same(
        self, claimed: sqlite3.Connection
    ) -> None:
        """One scrypt run over a salt of the same size, so neither is the faster.

        Asserted structurally rather than by wall clock, which measures the
        machine.
        """
        missing = self._attempt(claimed, "nobody", "correct horse")
        wrong = self._attempt(claimed, "owner", "hunter2")

        assert missing == wrong
        assert missing == (None, 1, accounts._SALT_BYTES)

    def test_an_unclaimed_account_cannot_be_logged_into(
        self, conn: sqlite3.Connection
    ) -> None:
        """The username exists and the hash is NULL, and it costs a run too."""
        assert self._attempt(conn, "default", "") == (None, 1, accounts._SALT_BYTES)


class TestSessions:
    """The stored token is a digest, and the window rolls forward on use."""

    def test_the_token_is_never_written_to_the_database(
        self, claimed: sqlite3.Connection, db_path: Path
    ) -> None:
        """Somebody reading the file gets no live login out of it."""
        token = create_session(claimed, 1)
        digest = hashlib.sha256(token.encode()).hexdigest()

        written = db_path.read_bytes()
        assert claimed.execute("SELECT token_hash FROM sessions").fetchall() == [
            (digest,)
        ]
        assert digest.encode() in written
        assert token.encode() not in written

    def test_the_token_carries_thirty_two_random_bytes(
        self, claimed: sqlite3.Connection
    ) -> None:
        """``secrets.token_urlsafe(32)``, and never twice the same."""
        tokens = [create_session(claimed, 1) for _ in range(2)]
        decoded = [
            base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
            for token in tokens
        ]

        assert [len(raw) for raw in decoded] == [32, 32]
        assert tokens[0] != tokens[1]

    def test_a_live_session_resolves_to_its_user(
        self, claimed: sqlite3.Connection
    ) -> None:
        """What every authenticated request does with the cookie."""
        token = create_session(claimed, 1)

        account = lookup_session(claimed, token)

        assert account is not None
        assert account["id"] == 1

    def test_an_unknown_token_resolves_to_nobody(
        self, claimed: sqlite3.Connection
    ) -> None:
        """A forged cookie is a miss, not an error."""
        create_session(claimed, 1)

        assert lookup_session(claimed, "not-a-token") is None

    def test_an_expired_session_is_refused_and_left_expired(
        self, claimed: sqlite3.Connection
    ) -> None:
        """A lapsed session must not be revived by the lookup that rejects it."""
        token = _session_opened_at(claimed, datetime(2026, 1, 1, tzinfo=UTC))

        assert lookup_session(claimed, token) is None
        assert _session_row(claimed, token)[0] == "2026-01-31T00:00:00+00:00"

    def test_a_lookup_rolls_the_expiry_forward_and_stamps_last_seen(
        self, claimed: sqlite3.Connection
    ) -> None:
        """The window is rolling, so only an idle session lapses."""
        opened = datetime(2026, 1, 1, tzinfo=UTC)
        used = opened + timedelta(days=20)
        token = _session_opened_at(claimed, opened)

        with patch.object(accounts, "utc_now", return_value=used):
            account = lookup_session(claimed, token)

        assert account is not None
        assert _session_row(claimed, token) == (
            (used + SESSION_LIFETIME).isoformat(timespec="seconds"),
            used.isoformat(timespec="seconds"),
        )


class TestRevokingAndPurging:
    """Both delete rows, so both are asserted against sessions they must keep."""

    @pytest.fixture
    def population(self, claimed: sqlite3.Connection) -> dict[str, list[str]]:
        """Two live sessions for the account, one for a second user, one lapsed."""
        create_user(claimed, username="second", display_name="Second")
        return {
            "live": [create_session(claimed, 1) for _ in range(2)],
            "other_user": [create_session(claimed, 2)],
            "lapsed": [_session_opened_at(claimed, datetime(2026, 1, 1, tzinfo=UTC))],
        }

    def test_the_population_starts_with_four_sessions(
        self, claimed: sqlite3.Connection, population: dict[str, list[str]]
    ) -> None:
        """Anchor: every assertion below is vacuous over an empty table."""
        assert sum(len(tokens) for tokens in population.values()) == 4
        assert claimed.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 4

    def test_revoking_one_session_leaves_the_others(
        self, claimed: sqlite3.Connection, population: dict[str, list[str]]
    ) -> None:
        """Signing out of one browser is not signing out of the others."""
        revoke_session(claimed, population["live"][0])

        assert _live_tokens(claimed, population["live"]) == [population["live"][1]]
        assert _live_tokens(claimed, population["other_user"]) == (
            population["other_user"]
        )

    def test_revoking_all_of_one_users_sessions_spares_the_other_user(
        self, claimed: sqlite3.Connection, population: dict[str, list[str]]
    ) -> None:
        """A password change ends this account's sessions, not the whole table."""
        revoke_all_sessions(claimed, 1)

        assert _live_tokens(claimed, population["live"]) == []
        assert _live_tokens(claimed, population["other_user"]) == (
            population["other_user"]
        )
        assert claimed.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1

    def test_purging_removes_the_lapsed_sessions_and_only_those(
        self, claimed: sqlite3.Connection, population: dict[str, list[str]]
    ) -> None:
        """The count it reports is the count it deleted."""
        deleted = purge_expired_sessions(claimed)

        assert deleted == 1
        assert _live_tokens(claimed, population["lapsed"]) == []
        assert _live_tokens(claimed, population["live"]) == population["live"]

    def test_purging_a_table_with_nothing_lapsed_deletes_nothing(
        self, claimed: sqlite3.Connection
    ) -> None:
        """The counterpart of the count above, over live rows alone."""
        tokens = [create_session(claimed, 1) for _ in range(2)]

        assert purge_expired_sessions(claimed) == 0
        assert _live_tokens(claimed, tokens) == tokens


class TestUpgradingADatabaseWrittenBeforeTheAccountColumns:
    """A database at the previous version gains the columns and keeps its rows.

    Nothing here is guarded — the ALTER and the CREATE are unconditional — but
    the version moves and the library must come through untouched.
    """

    @pytest.fixture
    def upgraded(self, tmp_path: Path) -> Iterator[sqlite3.Connection]:
        """Build the previous shape, seed it, then open it with this build."""
        db_path = tmp_path / "before-accounts.db"
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(_USERS_BEFORE_THE_PASSWORD_COLUMNS)
            conn.execute(_CONTENT_ITEMS_BEFORE_THE_PASSWORD_COLUMNS)
            conn.execute(
                "INSERT INTO users (id, username, display_name)"
                " VALUES (1, 'default', 'Default User')"
            )
            _seed_the_library(conn)
            conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION - 1}")
            conn.commit()
        finally:
            conn.close()

        _open(db_path)

        connection = sqlite3.connect(db_path)
        try:
            yield connection
        finally:
            connection.close()

    def test_the_library_and_the_user_survive_the_upgrade(
        self, upgraded: sqlite3.Connection
    ) -> None:
        """The rows an operator already has, read back after the migration."""
        assert _library_of(upgraded) == [(1, "The Dispossessed"), (1, "Disco Elysium")]
        assert upgraded.execute(
            "SELECT id, username, display_name FROM users"
        ).fetchall() == [(1, "default", "Default User")]

    def test_the_upgraded_database_is_unclaimed_and_ready_to_be_claimed(
        self, upgraded: sqlite3.Connection
    ) -> None:
        """NULL columns are the unclaimed state, and claiming keeps the library."""
        assert account_is_claimed(upgraded) is False

        claim_account(upgraded, "owner", "The Owner", "correct horse")

        assert verify_password(upgraded, "owner", "correct horse") is not None
        assert _library_of(upgraded) == [(1, "The Dispossessed"), (1, "Disco Elysium")]

    def test_the_upgrade_leaves_an_empty_sessions_table_and_the_new_version(
        self, upgraded: sqlite3.Connection
    ) -> None:
        """The table is created by the same open that stamps the version."""
        assert upgraded.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert upgraded.execute("PRAGMA user_version").fetchone()[0] == _SCHEMA_VERSION


class TestTheStorageManagerSurface:
    """The methods the web layer calls, over a manager rather than a connection."""

    @pytest.fixture
    def storage(self, tmp_path: Path) -> StorageManager:
        """A manager on its own database, unclaimed."""
        return StorageManager(sqlite_path=tmp_path / "manager.db")

    def test_a_claim_and_a_login_round_trip(self, storage: StorageManager) -> None:
        """Setup, then sign in, then sign out — the whole surface of a session."""
        assert storage.account_is_claimed() is False

        account = storage.claim_account("owner", "The Owner", "correct horse")
        assert account["id"] == 1
        assert storage.account_is_claimed() is True
        assert storage.verify_password("owner", "hunter2") is None

        signed_in = storage.verify_password("owner", "correct horse")
        assert signed_in is not None

        token = storage.create_session(signed_in["id"])
        assert storage.lookup_session(token) == signed_in

        storage.revoke_session(token)
        assert storage.lookup_session(token) is None

    def test_a_password_change_can_end_every_session(
        self, storage: StorageManager
    ) -> None:
        """What the settings page does: new password, every browser signed out."""
        storage.claim_account("owner", None, "correct horse")
        tokens = [storage.create_session(1) for _ in range(2)]

        storage.set_password(1, "a longer passphrase")
        storage.revoke_all_sessions(1)

        assert storage.verify_password("owner", "a longer passphrase") is not None
        assert [storage.lookup_session(token) for token in tokens] == [None, None]
        assert storage.purge_expired_sessions() == 0

    def test_neither_secret_reaches_the_files_the_manager_writes(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        """The manager runs in WAL mode, so the ``.db`` file alone proves little.

        Checked over every file SQLite left, with the two digests as the anchor
        that the rows really are in the bytes being searched.
        """
        storage.claim_account("owner", None, "correct horse")
        token = storage.create_session(1)
        digest = hashlib.sha256(token.encode()).hexdigest()
        with storage.connection() as conn:
            stored_hash, _ = _stored_password(conn)

        written = _every_byte_sqlite_wrote(tmp_path / "manager.db")

        assert stored_hash.encode() in written
        assert digest.encode() in written
        assert b"correct horse" not in written
        assert token.encode() not in written


class TestReopeningTheDatabase:
    """A restart re-runs ``create_schema`` over the claimed database.

    The ALTERs, the CREATE and the default-user INSERT all run again, so this
    is the path that could quietly un-claim an instance.
    """

    def test_a_restart_keeps_the_password_the_username_and_the_session(
        self, claimed: sqlite3.Connection, db_path: Path
    ) -> None:
        """Otherwise every restart signs the operator out, or worse re-opens setup."""
        _seed_the_library(claimed)
        token = create_session(claimed, 1)
        before = _stored_password(claimed)
        claimed.close()

        _open(db_path)

        reopened = sqlite3.connect(db_path)
        try:
            assert _stored_password(reopened) == before
            assert account_is_claimed(reopened) is True
            assert lookup_session(reopened, token) is not None
            assert verify_password(reopened, "owner", "correct horse") is not None
            assert _library_of(reopened) == [
                (1, "The Dispossessed"),
                (1, "Disco Elysium"),
            ]
            assert reopened.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        finally:
            reopened.close()


class TestTheExpiryBoundary:
    """``lookup_session`` and ``purge_expired_sessions`` compare the same stamp.

    They must agree on the instant itself, or a session outlives the purge that
    should have taken it — or is purged while it still logs someone in.
    """

    def test_at_the_expiry_instant_the_session_is_refused_and_purgeable(
        self, claimed: sqlite3.Connection
    ) -> None:
        """Two sessions opened together, used a second apart, end up differently.

        The one used with a second to spare rolls its window forward and
        survives the purge; the one used at the instant itself does neither.
        """
        opened = datetime(2026, 1, 1, tzinfo=UTC)
        used_in_time, used_too_late = (
            _session_opened_at(claimed, opened) for _ in "ab"
        )
        expiry = opened + SESSION_LIFETIME
        assert claimed.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 2

        with patch.object(
            accounts, "utc_now", return_value=expiry - timedelta(seconds=1)
        ):
            assert lookup_session(claimed, used_in_time) is not None

        with patch.object(accounts, "utc_now", return_value=expiry):
            assert lookup_session(claimed, used_too_late) is None
            assert purge_expired_sessions(claimed) == 1
            assert lookup_session(claimed, used_in_time) is not None


class TestPasswordsThatAreNotASCII:
    """A passphrase is whatever the operator typed, in whatever script."""

    @pytest.mark.parametrize(
        "password",
        ["übergrüßen 日本語 🎲", " leading and trailing ", "x" * 4096],
    )
    def test_a_password_round_trips_and_a_near_miss_does_not(
        self, conn: sqlite3.Connection, password: str
    ) -> None:
        """Encoded to UTF-8 for scrypt, so no byte of it may be lost on the way."""
        claim_account(conn, "owner", None, password)

        assert verify_password(conn, "owner", password) is not None
        assert verify_password(conn, "owner", password.strip() + "!") is None


class TestWhatDoesNotDeleteASession:
    """The two revocations, aimed at things they must not match."""

    @pytest.fixture
    def population(self, claimed: sqlite3.Connection) -> dict[int, list[str]]:
        """Two sessions for the account and one for a second user."""
        create_user(claimed, username="second", display_name="Second")
        return {
            1: [create_session(claimed, 1) for _ in range(2)],
            2: [create_session(claimed, 2)],
        }

    def test_the_population_starts_with_three_sessions(
        self, claimed: sqlite3.Connection, population: dict[int, list[str]]
    ) -> None:
        """Anchor: revoking nothing from an empty table proves nothing."""
        assert claimed.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 3

    def test_revoking_for_a_user_with_no_sessions_deletes_nothing(
        self, claimed: sqlite3.Connection, population: dict[int, list[str]]
    ) -> None:
        """A user id matching no row must not fall through to matching every row."""
        create_user(claimed, username="third", display_name="Third")

        revoke_all_sessions(claimed, 3)

        assert _live_tokens(claimed, population[1]) == population[1]
        assert _live_tokens(claimed, population[2]) == population[2]

    def test_revoking_an_unknown_token_deletes_nothing(
        self, claimed: sqlite3.Connection, population: dict[int, list[str]]
    ) -> None:
        """A stale cookie signing out must not disturb the live browsers."""
        revoke_session(claimed, "not-a-token")

        assert _live_tokens(claimed, population[1]) == population[1]
        assert _live_tokens(claimed, population[2]) == population[2]

    def test_deleting_a_user_takes_only_that_users_sessions(
        self, claimed: sqlite3.Connection, population: dict[int, list[str]]
    ) -> None:
        """The sessions row's ``ON DELETE CASCADE``, which needs the pragma to fire."""
        claimed.execute("DELETE FROM users WHERE id = 2")
        claimed.commit()

        assert _live_tokens(claimed, population[2]) == []
        assert _live_tokens(claimed, population[1]) == population[1]


class TestUsernamesThatDoNotMatch:
    """A near-miss username must cost what a wrong password costs."""

    @pytest.mark.parametrize("username", ["Owner", "owner ", "owner' OR 1=1 --", ""])
    def test_a_near_miss_username_is_refused_at_the_same_cost(
        self, claimed: sqlite3.Connection, username: str
    ) -> None:
        """No login, and no oracle telling an attacker which name is the real one."""
        attempt = TestVerifyingAPassword._attempt(claimed, username, "correct horse")

        assert attempt == (None, 1, accounts._SALT_BYTES)
