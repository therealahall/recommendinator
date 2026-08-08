"""Shared test factories: model instances and app fixtures with sane defaults."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import fields
from typing import Any
from unittest.mock import NonCallableMock, patch

from fastapi import FastAPI

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.web.app import create_app
from src.web.state import AppState, app_state


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

    storage.get_setting.side_effect = lambda key: store.get(key)
    storage.set_setting.side_effect = store.__setitem__
    storage.list_settings.side_effect = store.copy
    # An unstubbed method on a spec'd Mock returns a truthy Mock, which would
    # read as "a secret is already stored" — the opposite of an empty database.
    storage.get_credential.return_value = None
    storage.credential_row_exists.return_value = False
    storage.has_global_secret.return_value = False
    return store


@contextmanager
def booted_web_app(
    storage: Any,
    config: dict[str, Any],
    llm_components: tuple[Any, Any, Any] = (None, None, None),
    engine: Any = None,
) -> Iterator[FastAPI]:
    """Boot ``create_app`` over patched I/O boundaries, with ``storage``/``config``.

    The one supported way for a test to obtain the web app. An unpatched boot
    resolves whatever config file the process finds, opens the database that
    file names and runs the credential migration against it — re-encrypting
    real rows under the throwaway key the root conftest installs. Importing
    ``src.web.app:app`` does the same at collection time, before any fixture
    runs at all, which is why ``tests/test_web_app_import.py`` forbids it.

    A real temp-DB ``StorageManager`` is as welcome as a mock:
    ``back_mock_settings_store`` lets the settings and secret boot hooks run
    for real against an empty store and no-ops for storage that isolates
    itself. Anything this does not patch — ``configure_logging`` (already a
    no-op via the root conftest), the source migrations — a caller wraps around
    the call.

    No ``engine`` means no recommendation engine, so ``/api/recommendations``
    and its stream answer 503 until one is passed, and a truthy LLM client in
    ``llm_components`` wires the conversation engine with
    ``recommendation_engine=None``.

    ``app_state`` is a module-level singleton, so the boot starts from
    ``AppState()`` defaults and the caller's fields are restored afterwards.
    Both halves matter: restoring alone preserves whatever a previous test
    leaked into a field ``create_app`` never assigns, and a raise inside
    ``create_app`` would otherwise leave the singleton half-populated for the
    rest of the session.
    """
    saved = {f.name: getattr(app_state, f.name) for f in fields(app_state)}
    back_mock_settings_store(storage)
    defaults = AppState()
    try:
        # Field by field, never a rebind: the singleton is imported by name in
        # a dozen modules, all of which must keep seeing the current state.
        for field in fields(defaults):
            setattr(app_state, field.name, getattr(defaults, field.name))
        with (
            patch("src.web.app.load_config", return_value=config),
            patch("src.web.app.create_storage_manager", return_value=storage),
            patch("src.web.app.create_llm_components", return_value=llm_components),
            patch("src.web.app.create_recommendation_engine", return_value=engine),
            patch("src.web.app.migrate_config_credentials"),
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
