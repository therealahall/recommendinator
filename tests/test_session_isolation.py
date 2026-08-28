from __future__ import annotations

from pathlib import Path

import pytest

from src.storage.manager import StorageManager


class TestCredentialKeyRedirectIsLoadBearing:
    def test_without_the_redirect_the_key_lands_beside_the_database(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("RECOMMENDINATOR_KEY_PATH")
        database_dir = tmp_path / "data"

        storage = StorageManager(sqlite_path=database_dir / "library.db")
        storage.credentials.save(1, "steam", "api_key", "unredirected-secret")

        assert (database_dir / ".credential_key").exists()
        assert not (tmp_path / ".credential_key").exists()

    def test_with_the_redirect_the_key_never_reaches_the_database_directory(
        self, tmp_path: Path
    ) -> None:
        database_dir = tmp_path / "data"

        storage = StorageManager(sqlite_path=database_dir / "library.db")
        storage.credentials.save(1, "steam", "api_key", "redirected-secret")

        assert (tmp_path / ".credential_key").exists()
        assert not (database_dir / ".credential_key").exists()
