"""Tests for the one-time settings-table cleanup migration.

An earlier iteration of the database-backed config seeded the ``settings``
table on every boot: both dotted-leaf rows (``features.ai_enabled``) and stale
whole-section JSON-blob rows (``features`` -> a dict). Seed-on-boot has since
been removed — the table now holds only leaves a user explicitly sets via the
settings UI/CLI. Because that feature is unreleased, every pre-existing row is a
seed artifact, so ``create_schema`` clears the table exactly once on upgrade.

The migration is guarded by SQLite's ``PRAGMA user_version`` so it runs on the
first upgrade and never again: a leaf a user sets after the upgrade must survive
every subsequent init.
"""

import sqlite3
from pathlib import Path

from src.storage.manager import StorageManager
from src.storage.schema import _SCHEMA_VERSION, create_schema


def _user_version(path: Path) -> int:
    """Read the persisted ``PRAGMA user_version`` for a database file."""
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def _seed_pre_upgrade_db(path: Path) -> None:
    """Write a pre-upgrade DB: seeded ``settings`` rows at ``user_version`` 0.

    Faithfully reproduces a database created by the old seed-on-boot code —
    a fully-populated settings table with no version bump (``user_version`` 0).
    Builds the real schema, then reseeds and rewinds the version so the next
    init sees exactly what an upgrading operator's database looks like.
    """
    conn = sqlite3.connect(path)
    try:
        create_schema(conn)
        conn.execute("DELETE FROM settings")
        conn.executemany(
            "INSERT INTO settings (key, value_json) VALUES (?, ?)",
            [
                # Stale whole-section JSON-blob row from the earliest design.
                ("features", '{"ai_enabled": false}'),
                # Auto-seeded dotted-leaf rows from the later design.
                ("features.ai_enabled", "false"),
                ("web.port", "18473"),
                ("recommendations.default_count", "5"),
            ],
        )
        # Rewind to the pre-upgrade version so the migration re-runs on init.
        conn.execute("PRAGMA user_version = 0")
        conn.commit()
    finally:
        conn.close()


class TestSettingsCleanupMigration:
    """The upgrade clears seeded settings rows exactly once."""

    def test_upgrade_clears_seeded_rows_and_advances_version(
        self, tmp_path: Path
    ) -> None:
        """A seeded pre-upgrade DB is emptied and its version advances on init."""
        db_path = tmp_path / "test.db"
        _seed_pre_upgrade_db(db_path)
        assert _user_version(db_path) == 0

        storage = StorageManager(sqlite_path=db_path)

        assert storage.list_settings() == {}
        assert _user_version(db_path) == _SCHEMA_VERSION

    def test_user_edit_after_upgrade_survives_reinit(self, tmp_path: Path) -> None:
        """A leaf set after the upgrade is never re-cleared on later inits.

        This is the one-time guarantee: once the version is current the DELETE
        must not fire again, or genuine user edits would be wiped on reboot.
        """
        db_path = tmp_path / "test.db"
        storage = StorageManager(sqlite_path=db_path)
        storage.set_setting("web.port", 9999)

        reopened = StorageManager(sqlite_path=db_path)

        assert reopened.get_setting("web.port") == 9999
        assert reopened.list_settings() == {"web.port": 9999}

    def test_fresh_install_is_noop_and_advances_version(self, tmp_path: Path) -> None:
        """A brand-new DB has an empty settings table and the bumped version."""
        db_path = tmp_path / "test.db"

        storage = StorageManager(sqlite_path=db_path)

        assert storage.list_settings() == {}
        assert _user_version(db_path) == _SCHEMA_VERSION

    def test_second_init_does_not_reclear_after_upgrade(self, tmp_path: Path) -> None:
        """Re-running the seeded-upgrade path leaves post-upgrade edits intact."""
        db_path = tmp_path / "test.db"
        _seed_pre_upgrade_db(db_path)

        # First init performs the one-time clear and advances the version.
        StorageManager(sqlite_path=db_path)
        # A real user edit lands after the feature ships.
        StorageManager(sqlite_path=db_path).set_setting("features.ai_enabled", True)

        reopened = StorageManager(sqlite_path=db_path)

        assert reopened.get_setting("features.ai_enabled") is True
