import sqlite3
from pathlib import Path

from src.settings.metadata import flat_defaults
from src.storage.manager import StorageManager
from src.storage.schema import (
    _ORPHANED_SETTING_KEYS,
    _ORPHANED_SETTING_PREFIXES,
    _SCHEMA_VERSION,
    create_schema,
)


def _user_version(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def _seed_pre_upgrade_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        create_schema(conn)
        conn.execute("DELETE FROM settings")
        conn.executemany(
            "INSERT INTO settings (key, value_json) VALUES (?, ?)",
            [
                ("recommendations", '{"max_count": 20}'),
                ("web.port", "18473"),
                ("recommendations.max_count", "20"),
                ("recommendations.default_count", "5"),
            ],
        )
        conn.execute("PRAGMA user_version = 0")
        conn.commit()
    finally:
        conn.close()


def _seed_v1_db_with_orphans(path: Path) -> None:
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
                ("recommendations.default_count", "9"),
            ],
        )
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
    finally:
        conn.close()


class TestOrphanedSettingsPrune:
    def test_no_orphaned_key_is_a_live_registry_leaf(self) -> None:
        live = set(flat_defaults()) & set(_ORPHANED_SETTING_KEYS)

        assert live == set(), f"prune would delete live registry leaves: {sorted(live)}"

    def test_v1_upgrade_prunes_orphans_and_keeps_user_leaves(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "test.db"
        _seed_v1_db_with_orphans(db_path)

        storage = StorageManager(sqlite_path=db_path)

        assert storage.settings.list() == {"recommendations.default_count": 9}
        assert _user_version(db_path) == _SCHEMA_VERSION

    def test_prune_does_not_re_run_after_upgrade(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        _seed_v1_db_with_orphans(db_path)
        storage = StorageManager(sqlite_path=db_path)
        assert storage.settings.get("web.debug") is None

        storage.settings.set("web.debug", True)

        assert StorageManager(sqlite_path=db_path).settings.get("web.debug") is True


def _seed_v2_db_with_ai_leaves(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        create_schema(conn)
        conn.execute("DELETE FROM settings")
        conn.executemany(
            "INSERT INTO settings (key, value_json) VALUES (?, ?)",
            [
                ("features.ai_enabled", "true"),
                ("ollama.model", '"mistral:7b"'),
                ("ollama.base_url", '"http://ollama:11434"'),
                ("conversation.llm.temperature", "0.7"),
                ("recommendations.default_count", "9"),
            ],
        )
        conn.execute("PRAGMA user_version = 2")
        conn.commit()
    finally:
        conn.close()


class TestTheAiRemovalPrunesItsOwnLeaves:
    def test_no_orphaned_prefix_covers_a_live_registry_leaf(self) -> None:
        live = {
            key for key in flat_defaults() if key.startswith(_ORPHANED_SETTING_PREFIXES)
        }

        assert live == set(), f"prune would delete live registry leaves: {sorted(live)}"

    def test_a_database_holding_them_boots_clean(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        _seed_v2_db_with_ai_leaves(db_path)

        storage = StorageManager(sqlite_path=db_path)

        assert storage.settings.list() == {"recommendations.default_count": 9}
        assert _user_version(db_path) == _SCHEMA_VERSION


class TestSettingsCleanupMigration:
    def test_upgrade_clears_seeded_rows_and_advances_version(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "test.db"
        _seed_pre_upgrade_db(db_path)
        assert _user_version(db_path) == 0

        storage = StorageManager(sqlite_path=db_path)

        assert storage.settings.list() == {}
        assert _user_version(db_path) == _SCHEMA_VERSION

    def test_second_init_does_not_reclear_after_upgrade(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        _seed_pre_upgrade_db(db_path)

        StorageManager(sqlite_path=db_path)
        StorageManager(sqlite_path=db_path).settings.set("enrichment.enabled", True)

        reopened = StorageManager(sqlite_path=db_path)

        assert reopened.settings.get("enrichment.enabled") is True
