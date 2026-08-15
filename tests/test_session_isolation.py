"""The credential-key redirect the repository-root conftest installs.

Dropped, a real ``StorageManager`` writes its encryption key beside its
database — for a plugin test on the default path, the developer's ``data/``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.storage.manager import StorageManager


class TestCredentialKeyRedirectIsLoadBearing:
    """What the redirect prevents, shown by dropping it for one test.

    Dropping the variable the autouse fixture sets is the closest a test can get
    to running without the fixture, and it is what makes the plugin-local
    assertion discriminating: the key really does follow the database directory
    when nothing redirects it.
    """

    def test_without_the_redirect_the_key_lands_beside_the_database(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unredirected StorageManager writes its key into the database directory."""
        monkeypatch.delenv("RECOMMENDINATOR_KEY_PATH")
        database_dir = tmp_path / "data"

        storage = StorageManager(sqlite_path=database_dir / "library.db")
        storage.save_credential(1, "steam", "api_key", "unredirected-secret")

        assert (database_dir / ".credential_key").exists()
        assert not (tmp_path / ".credential_key").exists()

    def test_with_the_redirect_the_key_never_reaches_the_database_directory(
        self, tmp_path: Path
    ) -> None:
        """With the fixture in force the same call writes only to the redirect path."""
        database_dir = tmp_path / "data"

        storage = StorageManager(sqlite_path=database_dir / "library.db")
        storage.save_credential(1, "steam", "api_key", "redirected-secret")

        assert (tmp_path / ".credential_key").exists()
        assert not (database_dir / ".credential_key").exists()
