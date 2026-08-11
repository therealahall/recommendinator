"""Tests for reporting and sweeping credentials stranded under a plugin name."""

import logging
from pathlib import Path
from typing import Any

import pytest

from src.storage.credential_orphans import (
    delete_orphaned_credentials,
    warn_about_orphaned_credentials,
)
from src.storage.manager import StorageManager

_STRANDED = "stranded-by-an-upgrade"


def _yaml_inputs(**sources: str) -> dict[str, Any]:
    """A config whose ``inputs`` map each given source id to its plugin."""
    return {
        "inputs": {
            source_id: {"plugin": plugin, "enabled": True}
            for source_id, plugin in sources.items()
        }
    }


def _insert_user(storage: StorageManager, user_id: int) -> None:
    """Insert a users row so the credentials FK constraint is satisfied."""
    with storage.connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, username) VALUES (?, ?)",
            (user_id, f"user{user_id}"),
        )
        conn.commit()


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    storage = StorageManager(sqlite_path=tmp_path / "test.db")
    storage.save_credential(1, "gog", "refresh_token", _STRANDED)
    return storage


class TestWarningAboutAStrandedCredential:
    """The operator hears which source to reconnect, and loses no token to it."""

    def test_both_sources_sharing_the_plugin_are_told_and_neither_is_given_it(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Nothing records which of them rotated it, so guessing is out."""
        with caplog.at_level(logging.WARNING):
            warn_about_orphaned_credentials(storage, "gog", "gog_work")
            warn_about_orphaned_credentials(storage, "gog", "gog_home")

        assert [record.levelno for record in caplog.records] == [logging.WARNING] * 2
        assert storage.get_credential(1, "gog", "refresh_token") == _STRANDED
        assert storage.get_credential(1, "gog_work", "refresh_token") is None
        assert storage.get_credential(1, "gog_home", "refresh_token") is None

    def test_every_stranded_key_is_named(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        storage.save_credential(1, "gog", "api_key", "also-stranded")

        with caplog.at_level(logging.WARNING):
            warn_about_orphaned_credentials(storage, "gog", "gog_work")

        assert "api_key, refresh_token" in caplog.messages[0]

    def test_a_source_id_that_forges_a_log_entry_is_escaped(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Source ids are typed into config.yaml and the web source form."""
        with caplog.at_level(logging.WARNING):
            warn_about_orphaned_credentials(storage, "gog", "gog\nWARNING forged")

        message = caplog.records[0].getMessage()
        assert "\\n" in message
        assert "\n" not in message

    def test_another_users_stranded_row_is_not_this_users_problem(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            warn_about_orphaned_credentials(storage, "gog", "gog_work", user_id=2)

        assert storage.get_credential(1, "gog", "refresh_token") == _STRANDED
        assert caplog.records == []


class TestALiveNamesakeRowIsNotCalledStrandedRegression:
    """Reported: the sweep guards a namesake source's own row; the warning does not.

    Cause: only ``delete_orphaned_credentials`` asks who is left. Fix: the
    warning asks too, so it stops calling a live credential a leftover.
    """

    def test_the_namesake_sources_own_row_is_not_reported_as_stranded(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        storage.upsert_source_config(1, "gog", "gog", {}, enabled=True)

        with caplog.at_level(logging.WARNING):
            warn_about_orphaned_credentials(storage, "gog", "gog_work")

        assert caplog.records == []

        # Anchor: without the namesake the same call does speak, so the silence
        # above is the guard rather than a call that never reaches a log.
        storage.delete_source_config(1, "gog")
        with caplog.at_level(logging.WARNING):
            warn_about_orphaned_credentials(storage, "gog", "gog_work")

        assert len(caplog.records) == 1


class TestBothPathsAskTheSameNamesakeQuestion:
    """A source called ``gog`` reads that row whatever plugin it runs.

    Split predicates were the reported defect, so the two paths are held to one
    case neither can answer by accident.
    """

    def test_a_namesake_on_another_plugin_silences_the_warning(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        storage.upsert_source_config(1, "gog", "steam", {}, enabled=True)

        with caplog.at_level(logging.WARNING):
            warn_about_orphaned_credentials(storage, "gog", "gog_work")

        assert caplog.records == []

        # Anchor: the same call speaks once the namesake goes, so the silence
        # above is the guard and not a call that reaches no log at all.
        storage.delete_source_config(1, "gog")
        with caplog.at_level(logging.WARNING):
            warn_about_orphaned_credentials(storage, "gog", "gog_work")

        assert len(caplog.records) == 1

    def test_a_namesake_on_another_plugin_keeps_the_row_on_delete(
        self, storage: StorageManager
    ) -> None:
        """No source runs ``gog`` any more, so only the namesake saves it."""
        storage.upsert_source_config(1, "gog", "steam", {}, enabled=True)

        delete_orphaned_credentials(storage, "gog", {})

        assert storage.get_credential(1, "gog", "refresh_token") == _STRANDED

    def test_an_unmigrated_namesake_keeps_the_row_on_delete(
        self, storage: StorageManager
    ) -> None:
        """The sweep gets a config, so a YAML-only namesake counts there."""
        delete_orphaned_credentials(storage, "gog", _yaml_inputs(gog="steam"))

        assert storage.get_credential(1, "gog", "refresh_token") == _STRANDED


class TestSweepingAStrandedCredentialOnDelete:
    """The row goes only once no configured source could ever read it."""

    def test_it_goes_when_no_source_is_left_on_the_plugin(
        self, storage: StorageManager
    ) -> None:
        delete_orphaned_credentials(
            storage, "gog", _yaml_inputs(my_books="calibre_web")
        )

        assert storage.get_credential(1, "gog", "refresh_token") is None

    def test_a_disabled_sibling_still_keeps_it(self, storage: StorageManager) -> None:
        """A disabled source is reconnected by enabling it, not by re-adding it."""
        storage.upsert_source_config(1, "gog_work", "gog", {}, enabled=False)

        delete_orphaned_credentials(storage, "gog", {})

        assert storage.get_credential(1, "gog", "refresh_token") == _STRANDED

    def test_no_other_sources_credentials_are_swept_with_it(
        self, storage: StorageManager
    ) -> None:
        storage.save_credential(1, "gog_home", "refresh_token", "live")

        delete_orphaned_credentials(storage, "gog", {})

        assert storage.get_credential(1, "gog", "refresh_token") is None
        assert storage.get_credential(1, "gog_home", "refresh_token") == "live"

    def test_another_users_row_under_the_same_plugin_survives(
        self, storage: StorageManager
    ) -> None:
        _insert_user(storage, 2)
        storage.save_credential(2, "gog", "refresh_token", "another-users")

        delete_orphaned_credentials(storage, "gog", {})

        assert storage.get_credential(1, "gog", "refresh_token") is None
        assert storage.get_credential(2, "gog", "refresh_token") == "another-users"
