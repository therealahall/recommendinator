"""Password credentials and browser sessions for the one web account.

Neither secret is reversible, so neither goes through ``encryption.py``: a
password is stored as a scrypt digest under a random per-account salt, a
session token as its SHA-256 digest.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta
from hmac import compare_digest
from typing import TypedDict

from src.storage.schema import UserDict, get_default_user_id, get_user_by_id
from src.utils.dates import utc_now

# scrypt work factors. 128 * N * r is 16 MiB per verification, inside the
# 32 MiB ``maxmem`` the stdlib defaults to and unnoticeable on one login.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 64

_SALT_BYTES = 16

# Stands in for a salt when the username matches no row, so a miss costs the
# same scrypt run as a wrong password and cannot be timed apart from one.
_ABSENT_ACCOUNT_SALT = b"\x00" * _SALT_BYTES

_SESSION_TOKEN_BYTES = 32

#: The shortest password this instance accepts, wherever one is set. Long
#: enough that the scrypt cost above is what an attacker meets rather than an
#: exhaustive search.
MIN_PASSWORD_LENGTH = 12

#: Names the rule, because both interfaces render this at the user.
PASSWORD_TOO_SHORT = f"Password must be at least {MIN_PASSWORD_LENGTH} characters long."

#: The longest username or display name either interface stores.
MAX_ACCOUNT_NAME_LENGTH = 100

#: How long a session stays valid. Rolling: every ``lookup_session`` pushes
#: the expiry out by this much again, so only an idle session lapses.
SESSION_LIFETIME = timedelta(days=30)


class AccountRecord(TypedDict):
    """The account as an operator asks after it: who it is, and its password's age.

    Neither the hash nor the salt is in it, so it is safe to render.
    """

    id: int
    username: str
    display_name: str | None
    claimed: bool
    password_updated_at: str | None


class AccountAlreadyClaimedError(RuntimeError):
    """There is no unclaimed account row to write.

    A second claim would hand the library to whoever asked for it. Changing
    a password is :func:`set_password`.
    """


class PasswordTooShortError(ValueError):
    """The offered password is under :data:`MIN_PASSWORD_LENGTH`.

    Raised where the password is written rather than at each interface, so
    the CLI and the web inherit one floor and a third caller cannot miss it.
    """


def _derive_key(plaintext: str, salt: bytes) -> str:
    """Return the scrypt digest of *plaintext* under *salt*, hex-encoded."""
    return hashlib.scrypt(
        plaintext.encode(),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    ).hex()


def _token_hash(token: str) -> str:
    """Return the stored form of a session token: its SHA-256 digest."""
    return hashlib.sha256(token.encode()).hexdigest()


def _utc_text(moment: datetime) -> str:
    """Render *moment* as ISO 8601 text at second resolution.

    Session expiry is compared in SQL, and ``isoformat`` drops the
    microseconds field when it is zero — one stamp in a million that sorts
    short.
    """
    return moment.isoformat(timespec="seconds")


def _password_columns(plaintext: str) -> tuple[str, str, str]:
    """Return the ``(hash, salt, updated_at)`` triple for a new password.

    Raises:
        PasswordTooShortError: *plaintext* is under the floor. Every write
            path runs through here, which is what makes the floor one rule.
    """
    if len(plaintext) < MIN_PASSWORD_LENGTH:
        raise PasswordTooShortError(PASSWORD_TOO_SHORT)
    salt = secrets.token_bytes(_SALT_BYTES)
    return _derive_key(plaintext, salt), salt.hex(), _utc_text(utc_now())


def describe_account(conn: sqlite3.Connection, user_id: int) -> AccountRecord | None:
    """Report *user_id*'s names and the state of its password.

    Returns:
        None when no ``users`` row carries *user_id*.
    """
    cursor = conn.cursor()
    cursor.execute(
        """SELECT username, display_name, password_hash, password_updated_at
             FROM users
            WHERE id = ?""",
        (user_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {
        "id": user_id,
        "username": row[0],
        "display_name": row[1],
        "claimed": row[2] is not None,
        "password_updated_at": row[3],
    }


def account_is_claimed(conn: sqlite3.Connection) -> bool:
    """Report whether anyone has set a password on this instance.

    Returns:
        True once the account carries a password hash.
    """
    account = describe_account(conn, get_default_user_id())
    return account is not None and account["claimed"]


def claim_account(
    conn: sqlite3.Connection,
    username: str,
    display_name: str | None,
    plaintext_password: str,
) -> UserDict:
    """Claim the instance: name the account and give it a password.

    Returns:
        The claimed account.

    Raises:
        AccountAlreadyClaimedError: The account already has a password.
        PasswordTooShortError: The password is under the floor.
    """
    password_hash, salt, updated_at = _password_columns(plaintext_password)
    cursor = conn.cursor()
    # Both invariants in one WHERE: the row the whole library is keyed to is
    # updated rather than a second user inserted, and a claimed instance
    # cannot be claimed again, whatever raced the caller here.
    cursor.execute(
        """UPDATE users
              SET username = ?, display_name = ?, password_hash = ?,
                  password_salt = ?, password_updated_at = ?
            WHERE id = ? AND password_hash IS NULL""",
        (
            username,
            display_name,
            password_hash,
            salt,
            updated_at,
            get_default_user_id(),
        ),
    )
    claimed = (
        get_user_by_id(conn, get_default_user_id()) if cursor.rowcount == 1 else None
    )
    if claimed is None:
        conn.rollback()
        raise AccountAlreadyClaimedError("There is no unclaimed account to claim.")
    conn.commit()
    return claimed


def set_password(conn: sqlite3.Connection, user_id: int, plaintext: str) -> None:
    """Replace *user_id*'s password with *plaintext*.

    Raises:
        PasswordTooShortError: The password is under the floor; nothing is
            written.
    """
    password_hash, salt, updated_at = _password_columns(plaintext)
    cursor = conn.cursor()
    cursor.execute(
        """UPDATE users
              SET password_hash = ?, password_salt = ?, password_updated_at = ?
            WHERE id = ?""",
        (password_hash, salt, updated_at, user_id),
    )
    conn.commit()


def verify_password(
    conn: sqlite3.Connection, username: str, plaintext: str
) -> UserDict | None:
    """Return the user *plaintext* logs *username* in as, or None.

    An unknown username, and an unclaimed account, are hashed against
    :data:`_ABSENT_ACCOUNT_SALT` and matched against nothing, so a rejection
    costs the same scrypt run and cannot enumerate usernames.
    """
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, password_hash, password_salt FROM users WHERE username = ?",
        (username,),
    )
    row = cursor.fetchone()
    stored_hash = row[1] if row else None
    salt = bytes.fromhex(row[2]) if row and row[2] else _ABSENT_ACCOUNT_SALT
    if not compare_digest(_derive_key(plaintext, salt), stored_hash or ""):
        return None
    return get_user_by_id(conn, row[0])


def create_session(conn: sqlite3.Connection, user_id: int) -> str:
    """Open a session for *user_id* and return its token.

    The one moment the token exists in plaintext: the caller hands it to the
    browser, and only its digest is stored.
    """
    token = secrets.token_urlsafe(_SESSION_TOKEN_BYTES)
    now = utc_now()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO sessions
               (token_hash, user_id, created_at, expires_at, last_seen_at)
           VALUES (?, ?, ?, ?, ?)""",
        (
            _token_hash(token),
            user_id,
            _utc_text(now),
            _utc_text(now + SESSION_LIFETIME),
            _utc_text(now),
        ),
    )
    conn.commit()
    return token


def lookup_session(conn: sqlite3.Connection, token: str) -> UserDict | None:
    """Return the user *token* is signed in as, or None if it names no live one.

    A live session's expiry rolls forward by :data:`SESSION_LIFETIME` on
    every lookup, so only an idle one lapses.
    """
    token_hash = _token_hash(token)
    now = utc_now()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT user_id FROM sessions WHERE token_hash = ? AND expires_at > ?",
        (token_hash, _utc_text(now)),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    cursor.execute(
        "UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE token_hash = ?",
        (_utc_text(now), _utc_text(now + SESSION_LIFETIME), token_hash),
    )
    conn.commit()
    return get_user_by_id(conn, row[0])


def revoke_session(conn: sqlite3.Connection, token: str) -> None:
    """End the session *token* names."""
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
    conn.commit()


def revoke_other_sessions(
    conn: sqlite3.Connection, user_id: int, keep_token: str
) -> None:
    """End every session *user_id* holds except the one *keep_token* names.

    What a password change does: every other browser is signed out, and the
    one making the change is not made to sign in again.
    """
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
        (user_id, _token_hash(keep_token)),
    )
    conn.commit()


def revoke_all_sessions(conn: sqlite3.Connection, user_id: int) -> None:
    """End every session *user_id* holds, on every device."""
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    conn.commit()


def purge_expired_sessions(conn: sqlite3.Connection) -> int:
    """Delete the lapsed sessions.

    Returns:
        Number of rows deleted.
    """
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM sessions WHERE expires_at <= ?", (_utc_text(utc_now()),)
    )
    deleted = cursor.rowcount
    conn.commit()
    return deleted
