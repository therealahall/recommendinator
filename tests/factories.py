"""Shared test factories: model instances and app fixtures with sane defaults."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import fields
from typing import Any
from unittest.mock import DEFAULT, MagicMock, Mock, NonCallableMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.user_preferences import UserPreferenceConfig
from src.storage.accounts import AccountStore
from src.storage.credentials import CredentialStore
from src.storage.global_secrets import SecretStore
from src.storage.schema import UserDict, get_default_user_id
from src.storage.settings_store import SettingsStore
from src.storage.source_configs import SourceConfigStore
from src.web.app import create_app
from src.web.auth import SESSION_COOKIE
from src.web.state import AppState, app_state

# The one session token a mocked StorageManager recognises. Real storage mints
# its own, so nothing outside this module may assume the value.
_MOCK_SESSION_TOKEN = "test-session-000102030405060708090a0b"

#: Who ``authenticated_client`` is signed in as, against mocked storage.
SESSION_USER: UserDict = {
    "id": get_default_user_id(),
    "username": "tester",
    "display_name": "Tester",
    "created_at": "2026-01-01T00:00:00",
    "settings": None,
}

# Source ids both interfaces must refuse. `^…$` is end-of-line in Python's
# ``re``, not end-of-string, so a trailing newline is the payload that
# separates a full-match check from a search.
MALFORMED_IDS = ["Not An Id", "gog\n", "1gog", "../gog", "gog work", "gög", ""]

_SUB_STORES: dict[str, type] = {
    "accounts": AccountStore,
    "credentials": CredentialStore,
    "secrets": SecretStore,
    "settings": SettingsStore,
    "sources": SourceConfigStore,
}


def spec_sub_stores(storage: Any) -> None:
    """Spec each stubbed sub-store, so a rename cannot orphan a stub.

    ``Mock(spec=StorageManager)`` checks top-level names only, and
    ``create_autospec`` cannot help: the sub-stores are ``cached_property``,
    so each child would be spec'd against the descriptor. Idempotent, so
    pre-stubbing survives.
    """
    if not isinstance(storage, NonCallableMock):
        return
    for name, store in _SUB_STORES.items():
        if not isinstance(getattr(storage, name), store):
            setattr(storage, name, MagicMock(spec=store))


def back_mock_session_store(storage: Any) -> None:
    """Teach a mocked StorageManager the one token tests present.

    Unstubbed, ``lookup_session`` returns a truthy Mock — under which every
    cookie, and every guess, authenticates. A no-op for real storage.
    """
    if isinstance(storage, NonCallableMock):
        spec_sub_stores(storage)
        storage.accounts.lookup_session.side_effect = lambda token: (
            SESSION_USER if token == _MOCK_SESSION_TOKEN else None
        )
        # A count, because boot logs it with ``%d``.
        _default_return(storage.accounts.purge_expired_sessions, 0)


def issue_session(storage: Any) -> str:
    """Return a session token *storage* will recognise, minting one if real."""
    back_mock_session_store(storage)
    if isinstance(storage, NonCallableMock):
        return _MOCK_SESSION_TOKEN
    return str(storage.accounts.create_session(get_default_user_id()))


def authenticated_client(app: FastAPI, **kwargs: Any) -> TestClient:
    """Return a ``TestClient`` carrying a live session cookie.

    The session is opened against the booted app's storage, which is what
    ``app_state`` holds inside :func:`booted_web_app`.
    """
    return TestClient(
        app, cookies={SESSION_COOKIE: issue_session(app_state.storage)}, **kwargs
    )


def _default_return(method: Any, value: Any) -> None:
    """Stand *method* up as empty storage, unless the caller stubbed it.

    This runs at boot, which is after the test has configured its storage, so
    assigning unconditionally would silently undo the stub it came to test.
    """
    if method.side_effect is None and method._mock_return_value is DEFAULT:
        method.return_value = value


def back_mock_settings_store(storage: Any) -> dict[str, Any]:
    """Make a mocked StorageManager behave like an empty settings/secret store.

    Lets the real ``migrate_config_settings`` and ``migrate_config_secrets`` boot
    hooks run against a mocked StorageManager without leaking state across
    tests: the settings store starts empty, so the DB overlay is a no-op and
    config resolves from const/YAML, and nothing is stored in the credentials
    table, so a YAML secret takes the "migrate it" branch of the sweep rather
    than the "a stored secret already wins" one. Returns the backing dict so a
    test can pre-set leaves or assert what was written.

    A real ``StorageManager`` (temp-DB) already isolates itself, so this is a
    no-op for non-mock storage.
    """
    store: dict[str, Any] = {}
    if not isinstance(storage, NonCallableMock):
        return store

    spec_sub_stores(storage)
    storage.settings.get.side_effect = lambda key: store.get(key)
    storage.settings.set.side_effect = store.__setitem__
    storage.settings.list.side_effect = store.copy
    # An unstubbed method on a spec'd Mock returns a truthy Mock, which reads
    # as "already stored" — the opposite of an empty database. For
    # ``sources.get`` that means the sweep discards every sensitive field
    # the test's config declared.
    _default_return(storage.credentials.get, None)
    _default_return(storage.credentials.exists, False)
    _default_return(storage.secrets.has, False)
    _default_return(storage.sources.get, None)
    return store


def back_mock_preference_store(
    storage: Any, stored: UserPreferenceConfig | None = None
) -> Mock:
    """Make a mocked StorageManager hold one real ``UserPreferenceConfig``.

    Both interfaces hand their edit to ``merge_user_preference_config``, and a
    bare Mock returns a Mock, proving nothing about what the edit did. Returns
    that mock, for asserting it was not called.
    """
    existing = stored if stored is not None else UserPreferenceConfig()

    def merge(
        _user_id: int, apply: Callable[[UserPreferenceConfig], None]
    ) -> UserPreferenceConfig:
        apply(existing)
        return existing

    storage.get_user_preference_config = Mock(return_value=existing)
    storage.merge_user_preference_config = Mock(side_effect=merge)
    return storage.merge_user_preference_config


@contextmanager
def booted_web_app(
    storage: Any,
    config: dict[str, Any],
    engine: Any = None,
    migrate_credentials: bool = False,
) -> Iterator[FastAPI]:
    """Boot ``create_app`` over patched I/O boundaries, with ``storage``/``config``.

    The one supported way for a test to obtain the web app. An unpatched boot
    resolves whatever config file the process finds, opens the database that
    file names and runs the credential migration against it — re-encrypting
    real rows under the throwaway key the root conftest installs. A
    module-level ``src.web.app:app`` import does the same at collection time,
    before any fixture runs at all, so no test may take one.

    A real temp-DB ``StorageManager`` is as welcome as a mock:
    ``back_mock_settings_store`` lets the settings and secret boot hooks run
    for real against an empty store and no-ops for storage that isolates
    itself. Anything this does not patch — ``src.utils.logging`` (already a
    no-op via the root conftest), the source migrations — a caller wraps around
    the call.

    No ``engine`` means no recommendation engine, so ``/api/recommendations``
    and its stream answer 503 until one is passed.

    ``app_state`` is a module-level singleton, so the boot starts from
    ``AppState()`` defaults and the caller's fields are restored afterwards.
    Both halves matter: restoring alone preserves whatever a previous test
    leaked into a field ``create_app`` never assigns, and a raise inside
    ``create_app`` would otherwise leave the singleton half-populated for the
    rest of the session.
    """
    saved = {f.name: getattr(app_state, f.name) for f in fields(app_state)}
    back_mock_settings_store(storage)
    back_mock_session_store(storage)
    defaults = AppState()
    # Stubbed by default so a test's config keeps the secrets it declared; a
    # test about what startup does to a file-held one asks for the real pass.
    credential_migration: Any = (
        nullcontext()
        if migrate_credentials
        else patch("src.web.app.migrate_config_credentials")
    )
    try:
        # Field by field, never a rebind: the singleton is imported by name in
        # a dozen modules, all of which must keep seeing the current state.
        for field in fields(defaults):
            setattr(app_state, field.name, getattr(defaults, field.name))
        with (
            patch("src.web.app.load_config", return_value=config),
            patch("src.web.app.create_storage_manager", return_value=storage),
            patch("src.web.app.create_recommendation_engine", return_value=engine),
            credential_migration,
            # Resolved independently of the patched loader, so unpatched this
            # binds the path of whatever config file the machine has — which a
            # reload would then read for real. The raise takes create_app's own
            # not-found branch, landing on config/example.yaml.
            patch("src.web.app.resolve_config_path", side_effect=FileNotFoundError),
        ):
            app = create_app()
        yield app
    finally:
        for key, value in saved.items():
            setattr(app_state, key, value)


def make_item(
    title: str = "Test Item",
    content_type: ContentType = ContentType.BOOK,
    status: ConsumptionStatus = ConsumptionStatus.COMPLETED,
    item_id: str | None = None,
    db_id: int | None = None,
    rating: int | None = None,
    author: str | None = None,
    review: str | None = None,
    metadata: dict[str, Any] | None = None,
    genres: str | None = None,
    source: str | None = None,
) -> ContentItem:
    """Create a ``ContentItem`` with minimal boilerplate.

    Parameters
    ----------
    genres:
        Shorthand — sets ``metadata["genre"]`` when provided.
    """
    effective_metadata: dict[str, Any] = metadata.copy() if metadata else {}
    if genres:
        effective_metadata["genre"] = genres

    return ContentItem(
        id=item_id,
        db_id=db_id,
        title=title,
        content_type=content_type,
        status=status,
        rating=rating,
        author=author,
        review=review,
        metadata=effective_metadata,
        source=source,
    )
