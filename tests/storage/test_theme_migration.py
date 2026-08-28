from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.storage.manager import StorageManager
from src.storage.schema import create_schema


def _seed_pre_upgrade_db(path: Path, settings: dict) -> None:
    conn = sqlite3.connect(path)
    try:
        create_schema(conn)
        conn.execute("DROP TABLE user_ui_settings")
        conn.execute(
            "UPDATE users SET settings = ? WHERE id = 1", (json.dumps(settings),)
        )
        conn.execute("PRAGMA user_version = 18")
        conn.commit()
    finally:
        conn.close()


def test_a_stored_theme_survives_the_upgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "upgrade.db"
    _seed_pre_upgrade_db(
        db_path,
        {"preference_config": {"theme": "snowstorm", "series_in_order": False}},
    )

    storage = StorageManager(sqlite_path=db_path)

    assert storage.ui_settings.get_theme(1) == "snowstorm"
    assert storage.get_user_preference_config(1).series_in_order is False


def test_a_user_who_picked_no_theme_upgrades_to_none(tmp_path: Path) -> None:
    db_path = tmp_path / "default.db"
    _seed_pre_upgrade_db(db_path, {"preference_config": {"theme": ""}})

    assert StorageManager(sqlite_path=db_path).ui_settings.get_theme(1) == ""


def test_a_settings_blob_the_step_cannot_read_does_not_block_the_upgrade(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "junk.db"
    _seed_pre_upgrade_db(db_path, {"preference_config": "not a dict"})

    assert StorageManager(sqlite_path=db_path).ui_settings.get_theme(1) == ""


def test_the_move_does_not_re_run_after_the_upgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "once.db"
    _seed_pre_upgrade_db(db_path, {"preference_config": {"theme": "snowstorm"}})
    StorageManager(sqlite_path=db_path).ui_settings.set_theme(1, "nord")

    assert StorageManager(sqlite_path=db_path).ui_settings.get_theme(1) == "nord"
