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

There are two version-guarded steps, and the difference matters. Version 1 wipes
the WHOLE table (every row was a seed artifact). Version 2 deletes only the five
keys in ``_ORPHANED_SETTING_KEYS`` — leaves that were briefly registry entries on
this branch and no longer are — and must spare everything else. A test that only
exercises version 0 cannot tell the two apart, because the version-1 wipe empties
the table before the version-2 prune runs.
"""

import sqlite3
from pathlib import Path

from src.settings.metadata import flat_defaults
from src.storage.manager import StorageManager
from src.storage.schema import (
    _ORPHANED_SETTING_KEYS,
    _SCHEMA_VERSION,
    create_schema,
)


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
                ("recommendations.max_count", "20"),
                ("recommendations.default_count", "5"),
            ],
        )
        # Rewind to the pre-upgrade version so the migration re-runs on init.
        conn.execute("PRAGMA user_version = 0")
        conn.commit()
    finally:
        conn.close()


def _seed_v1_db_with_orphans(path: Path) -> None:
    """Write a version-1 DB: the shape an earlier build of THIS branch produced.

    At version 1 the settings registry briefly included ``web.host``/``port``/
    ``debug`` and the ``ingestion`` section, so a developer on this branch can
    have rows for keys that are no longer registry leaves. Those rows are
    unreachable from the app — ``settings reset`` and ``DELETE /api/settings``
    both refuse a key with no registry entry — which is what version 2 prunes.
    """
    conn = sqlite3.connect(path)
    try:
        create_schema(conn)
        conn.execute("DELETE FROM settings")
        conn.executemany(
            "INSERT INTO settings (key, value_json) VALUES (?, ?)",
            [
                ("web.debug", "true"),
                ("web.host", '"0.0.0.0"'),
                ("web.port", "9000"),
                ("ingestion.conflict_strategy", '"last_write_wins"'),
                ("ingestion.source_priority", '["goodreads"]'),
                # A genuine user edit that must survive the prune.
                ("recommendations.default_count", "9"),
            ],
        )
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
    finally:
        conn.close()


class TestOrphanedSettingsPrune:
    """Version 2 prunes leaves that are no longer registry entries.

    Regression: the version-1 tests all rewind to ``user_version`` 0, so the
    ``version < 1`` whole-table clear wipes everything before the ``version < 2``
    branch can run — meaning the prune could be deleted outright and the suite
    would still pass. These drive the v1 boundary specifically.
    """

    def test_no_orphaned_key_is_a_live_registry_leaf(self) -> None:
        """The prune must never name a key the registry still owns.

        ``_ORPHANED_SETTING_KEYS`` drives an unrecoverable DELETE against real
        user rows on the v1→v2 upgrade. The sibling test pins that the five
        listed keys ARE deleted, so removing one fails — but nothing pinned the
        other direction: adding ``logging.file`` or ``web.allowed_origins`` to
        that tuple would silently destroy every user's stored value on next
        boot, and the whole suite would stay green.
        """
        live = set(flat_defaults()) & set(_ORPHANED_SETTING_KEYS)

        assert live == set(), f"prune would delete live registry leaves: {sorted(live)}"

    def test_v1_upgrade_prunes_orphans_and_keeps_user_leaves(
        self, tmp_path: Path
    ) -> None:
        """Orphaned keys go; a genuine user-set leaf stays.

        The second assertion is the load-bearing one — it proves version 2 is a
        targeted prune and not a second table-wide wipe of real user input.
        """
        db_path = tmp_path / "test.db"
        _seed_v1_db_with_orphans(db_path)

        storage = StorageManager(sqlite_path=db_path)

        assert storage.list_settings() == {"recommendations.default_count": 9}
        assert _user_version(db_path) == _SCHEMA_VERSION

    def test_prune_does_not_re_run_after_upgrade(self, tmp_path: Path) -> None:
        """The prune is one-time: an orphan key written back afterwards survives.

        Re-inserting an ORPHANED key is what makes this discriminate. Asserting
        on a non-orphan leaf would pass whether or not the version guard exists,
        because the key-specific DELETE would spare it either way — so the guard
        could be deleted and the suite would stay green.
        """
        db_path = tmp_path / "test.db"
        _seed_v1_db_with_orphans(db_path)
        storage = StorageManager(sqlite_path=db_path)
        assert storage.get_setting("web.debug") is None  # pruned on upgrade

        # set_setting is raw storage with no registry validation, so this
        # reproduces a row the migration would delete if it ever fired again.
        storage.set_setting("web.debug", True)

        assert StorageManager(sqlite_path=db_path).get_setting("web.debug") is True


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
        # A live registry leaf, deliberately: web.port is one of the keys the
        # version-2 prune now deletes, so it cannot stand for "a user edit".
        StorageManager(sqlite_path=db_path).set_setting(
            "recommendations.default_count", 9
        )

        reopened = StorageManager(sqlite_path=db_path)

        assert reopened.get_setting("recommendations.default_count") == 9
        assert reopened.list_settings() == {"recommendations.default_count": 9}

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
