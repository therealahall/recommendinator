import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage import schema
from src.storage.item_merges import MergeEvidence, absorb_item
from src.storage.schema import _SCHEMA_VERSION, create_schema
from src.storage.sqlite_db import SQLiteDB
from src.utils.series import split_series_from_title
from src.utils.sorting import get_sort_title, normalize_for_search, search_text_matches

_THE_DUPLICATE_PAIR = (
    (
        "steam-witcher",
        "The Witcher III: Wild Hunt",
        "the witcher iii: wild hunt",
        5,
        None,
    ),
    (
        "blog-witcher",
        "Witcher 3 - Wild Hunt",
        "witcher 3 - wild hunt",
        None,
        "Great writing",
    ),
)

_THE_PAIR_RENORMALIZED = [
    ("steam-witcher", "witcher 3 wild hunt", 5, None),
    ("blog-witcher", "witcher 3 wild hunt", None, "Great writing"),
]

_THE_PAIR_UNTOUCHED = [
    ("steam-witcher", "the witcher iii: wild hunt", 5, None),
    ("blog-witcher", "witcher 3 - wild hunt", None, "Great writing"),
]

_CONTENT_ITEMS_BEFORE_NORMALIZED_TITLE = """
    CREATE TABLE content_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL DEFAULT 1,
        external_id TEXT,
        title TEXT NOT NULL,
        content_type TEXT NOT NULL,
        status TEXT NOT NULL,
        rating INTEGER,
        review TEXT,
        date_completed DATE,
        source TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, external_id, content_type)
    )
"""

_USERS_TABLE = """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        display_name TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        settings TEXT
    )
"""


def _open(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        create_schema(conn)
    finally:
        conn.close()


def _user_version(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def _rewind_to(db_path: Path, version: int) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(f"PRAGMA user_version = {int(version)}")
        conn.commit()
    finally:
        conn.close()


def _merge_by_hand(db_path: Path, survivor: str, absorbed: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        ids = {
            row["external_id"]: int(row["content_item_id"])
            for row in cursor.execute(
                "SELECT external_id, content_item_id FROM content_item_external_ids"
            ).fetchall()
        }
        absorb_item(
            cursor,
            survivor_id=ids[survivor],
            absorbed_id=ids[absorbed],
            evidence=MergeEvidence.MANUAL,
        )
        conn.commit()
    finally:
        conn.close()


def _seed_the_duplicate_pair(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        for external_id, title, normalized_title, rating, review in _THE_DUPLICATE_PAIR:
            cursor = conn.execute(
                """INSERT INTO content_items
                   (user_id, title, normalized_title, content_type,
                    status, rating, review, source)
                   VALUES (1, ?, ?, 'video_game', 'completed', ?, ?, 'legacy')""",
                (title, normalized_title, rating, review),
            )
            _record_external_id(conn, cursor.lastrowid, external_id)
        conn.commit()
    finally:
        conn.close()


def _record_external_id(
    conn: sqlite3.Connection, db_id: int | None, external_id: str
) -> None:
    conn.execute(
        "INSERT INTO content_item_external_ids"
        " (content_item_id, user_id, source, external_id, content_type)"
        " SELECT id, user_id, 'legacy', ?, content_type"
        " FROM content_items WHERE id = ?",
        (external_id, db_id),
    )


def _drop_the_declines_table(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DROP TABLE content_item_duplicate_declines")
        conn.commit()
    finally:
        conn.close()


def _a_book(source: str, external_id: str, title: str) -> ContentItem:
    return ContentItem(
        id=external_id,
        title=title,
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
        source=source,
    )


def _content_rows(db_path: Path) -> list[tuple[Any, ...]]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT x.external_id, ci.normalized_title, ci.rating, ci.review"
            " FROM content_items ci"
            " LEFT JOIN content_item_external_ids x ON x.content_item_id = ci.id"
            " WHERE ci.merged_into IS NULL"
            " ORDER BY ci.id"
        ).fetchall()
    finally:
        conn.close()


def _merge_targets(db_path: Path) -> dict[str, str]:
    conn = sqlite3.connect(db_path)
    try:
        return dict(
            conn.execute(
                "SELECT x.external_id, live.external_id FROM content_items ci"
                " JOIN content_item_external_ids x ON x.content_item_id = ci.id"
                " JOIN content_item_external_ids live"
                "   ON live.content_item_id = COALESCE(ci.merged_into, ci.id)"
            ).fetchall()
        )
    finally:
        conn.close()


def _settings_rows(db_path: Path) -> dict[str, str]:
    conn = sqlite3.connect(db_path)
    try:
        return dict(conn.execute("SELECT key, value_json FROM settings").fetchall())
    finally:
        conn.close()


def _repair_runs_of_one_open(db_path: Path) -> int:
    with patch.object(
        schema,
        "_repair_legacy_content_rows",
        wraps=schema._repair_legacy_content_rows,
    ) as repair:
        SQLiteDB(db_path)
    return int(repair.call_count)


class TestTheVersionsAnUpgradingDatabaseCanBeAt:
    @pytest.mark.parametrize("stored_version", [0, 1, 2])
    def test_every_pre_upgrade_version_repairs_once_and_then_stops(
        self, tmp_path: Path, stored_version: int
    ) -> None:
        db_path = tmp_path / "upgrading.db"
        _open(db_path)
        _seed_the_duplicate_pair(db_path)
        _rewind_to(db_path, stored_version)

        assert _repair_runs_of_one_open(db_path) == 1
        assert _repair_runs_of_one_open(db_path) == 0
        assert _content_rows(db_path) == _THE_PAIR_RENORMALIZED
        assert _user_version(db_path) == _SCHEMA_VERSION

    def test_a_version_two_database_keeps_the_leaves_the_prune_already_spared(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "at-version-two.db"
        _open(db_path)
        conn = sqlite3.connect(db_path)
        try:
            conn.executemany(
                "INSERT INTO settings (key, value_json) VALUES (?, ?)",
                [("recommendations.default_count", "9"), ("web.debug", "true")],
            )
            conn.commit()
        finally:
            conn.close()
        _seed_the_duplicate_pair(db_path)
        _rewind_to(db_path, 2)

        _open(db_path)

        assert _settings_rows(db_path) == {
            "recommendations.default_count": "9",
            "web.debug": "true",
        }
        assert _content_rows(db_path) == _THE_PAIR_RENORMALIZED


class TestATableThatArrivedWithoutAVersionBump:
    @pytest.mark.parametrize("stored_version", list(range(9, _SCHEMA_VERSION + 1)))
    def test_a_library_stamped_before_the_declines_table_can_refuse_a_pair(
        self, tmp_path: Path, stored_version: int
    ) -> None:
        db_path = tmp_path / f"stamped-at-{stored_version}.db"
        _open(db_path)
        _drop_the_declines_table(db_path)
        _rewind_to(db_path, stored_version)

        db = SQLiteDB(db_path)
        kept = db.save_content_item(_a_book("calibre_web", "c:1", "Deadhouse Gates"))
        refused = db.save_content_item(
            _a_book("goodreads_rss", "2", "Deadhouse Gates (Malazan Book 2)")
        )

        assert [
            [copy.db_id for copy in one.copies]
            for one in db.list_duplicate_suggestions().suggestions
        ] == [[kept, refused]]
        assert db.decline_duplicate_suggestion(kept, [refused]) != []
        assert db.list_duplicate_suggestions().suggestions == []


class TestWhatAnOpenThatRaisedLeavesBehind:
    @staticmethod
    def _fail_the_last_pass(db_path: Path) -> None:
        with (
            patch.object(
                schema,
                "_migrate_stranded_detail_shapes",
                side_effect=OSError("disk failure"),
            ),
            pytest.raises(OSError),
        ):
            SQLiteDB(db_path)

    def _legacy_database(self, tmp_path: Path) -> Path:
        db_path = tmp_path / "legacy.db"
        _open(db_path)
        _seed_the_duplicate_pair(db_path)
        _rewind_to(db_path, 0)
        return db_path

    def test_a_failed_open_advances_neither_the_rows_nor_the_version(
        self, tmp_path: Path
    ) -> None:
        db_path = self._legacy_database(tmp_path)

        self._fail_the_last_pass(db_path)

        assert _content_rows(db_path) == _THE_PAIR_UNTOUCHED
        assert _user_version(db_path) == 0

    def test_the_retry_runs_the_whole_upgrade_rather_than_the_remainder(
        self, tmp_path: Path
    ) -> None:
        db_path = self._legacy_database(tmp_path)
        self._fail_the_last_pass(db_path)

        assert _repair_runs_of_one_open(db_path) == 1
        assert _content_rows(db_path) == _THE_PAIR_RENORMALIZED
        assert _user_version(db_path) == _SCHEMA_VERSION


class TestSchemaVersionRewindRegression:
    def test_a_version_above_this_build_is_left_where_it_is(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "from-a-later-build.db"
        _open(db_path)
        _rewind_to(db_path, _SCHEMA_VERSION + 1)

        _open(db_path)

        assert _user_version(db_path) == _SCHEMA_VERSION + 1


class TestWhatCountsAsADatabaseWithNoTables:
    def test_a_zero_byte_file_is_a_fresh_install(self, tmp_path: Path) -> None:
        db_path = tmp_path / "empty.db"
        db_path.touch()

        assert _repair_runs_of_one_open(db_path) == 0
        assert _user_version(db_path) == _SCHEMA_VERSION

    def test_a_half_created_database_is_not_read_as_current(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "half-created.db"
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(_USERS_TABLE)
            conn.commit()
        finally:
            conn.close()

        assert _repair_runs_of_one_open(db_path) == 1
        assert _user_version(db_path) == _SCHEMA_VERSION

    def test_a_database_predating_the_normalized_title_column_is_backfilled(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "ancient.db"
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(_CONTENT_ITEMS_BEFORE_NORMALIZED_TITLE)
            conn.executemany(
                """INSERT INTO content_items
                   (user_id, external_id, title, content_type, status,
                    rating, review, source)
                   VALUES (1, ?, ?, 'video_game', 'completed', ?, ?, 'legacy')""",
                [
                    (external_id, title, rating, review)
                    for external_id, title, _, rating, review in _THE_DUPLICATE_PAIR
                ],
            )
            conn.commit()
        finally:
            conn.close()

        _open(db_path)

        assert _content_rows(db_path) == _THE_PAIR_RENORMALIZED
        assert _user_version(db_path) == _SCHEMA_VERSION

    def test_a_file_that_is_not_a_database_raises_and_is_left_alone(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "not-a-database.db"
        db_path.write_bytes(b"recommendinator was never here\n" * 8)
        before = db_path.read_bytes()

        with pytest.raises(sqlite3.DatabaseError):
            SQLiteDB(db_path)

        assert db_path.read_bytes() == before


class TestUpgradingALibraryWrittenUnderTheOldTitleRules:
    _FERAL_GODS = (
        (
            "goodreads_rss",
            "57905101",
            "The Gate of the Feral Gods (Dungeon Crawler Carl, #4)",
            "gate of the feral gods dungeon crawler carl 4",
            "Matt Dinniman",
        ),
        (
            "calibre_web",
            "calibre:51a0e808",
            "The Gate of the Feral Gods",
            "gate of the feral gods",
            None,
        ),
    )

    _AUTHORLESS = (
        (
            "calibre_web",
            "calibre:0d1a4c19",
            "Beowulf",
            "beowulf",
            "Unknown",
        ),
    )

    @staticmethod
    def _seed_books(db_path: Path, rows: tuple[tuple[Any, ...], ...]) -> None:
        conn = sqlite3.connect(db_path)
        try:
            for source, external_id, title, normalized_title, author in rows:
                cursor = conn.execute(
                    """INSERT INTO content_items
                       (user_id, title, normalized_title, content_type,
                        status, source)
                       VALUES (1, ?, ?, 'book', 'unread', ?)""",
                    (title, normalized_title, source),
                )
                conn.execute(
                    """INSERT INTO content_item_external_ids
                       (content_item_id, user_id, source, external_id, content_type)
                       VALUES (?, 1, ?, ?, 'book')""",
                    (cursor.lastrowid, source, external_id),
                )
                conn.execute(
                    "INSERT INTO book_details (content_item_id, author)"
                    " VALUES (?, ?)",
                    (cursor.lastrowid, author),
                )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _groups(db_path: Path) -> dict[str, list[str]]:
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT live.normalized_title, x.external_id FROM content_items ci"
                " JOIN content_items live ON live.id = COALESCE(ci.merged_into, ci.id)"
                " JOIN content_item_external_ids x ON x.content_item_id = ci.id"
            ).fetchall()
        finally:
            conn.close()
        grouped: dict[str, list[str]] = {}
        for normalized_title, external_id in rows:
            grouped.setdefault(normalized_title, []).append(external_id)
        return {title: sorted(ids) for title, ids in grouped.items()}

    def _upgraded(
        self, tmp_path: Path, name: str, rows: tuple[tuple[Any, ...], ...]
    ) -> Path:
        db_path = tmp_path / name
        _open(db_path)
        self._seed_books(db_path, rows)
        _rewind_to(db_path, 9)

        _open(db_path)

        return db_path

    @staticmethod
    def _sync_books(db: SQLiteDB, rows: tuple[tuple[Any, ...], ...]) -> list[int]:
        saved = []
        for source, external_id, title, _, author in rows:
            bare, series = split_series_from_title(title)
            saved.append(
                db.save_content_item(
                    ContentItem(
                        id=external_id,
                        title=bare,
                        content_type=ContentType.BOOK,
                        status=ConsumptionStatus.UNREAD,
                        source=source,
                        author=author,
                        metadata=series,
                    )
                )
            )
        return saved

    def _saved_fresh(
        self, tmp_path: Path, name: str, rows: tuple[tuple[Any, ...], ...]
    ) -> Path:
        self._sync_books(SQLiteDB(tmp_path / name), rows)
        return tmp_path / name

    @staticmethod
    def _book(db_path: Path, external_id: str) -> tuple[Any, ...]:
        conn = sqlite3.connect(db_path)
        try:
            title, key, sort_title, author, metadata = conn.execute(
                "SELECT ci.title, ci.normalized_title, ci.sort_title, bd.author,"
                " bd.metadata FROM content_items ci"
                " JOIN content_item_external_ids x ON x.content_item_id = ci.id"
                " JOIN book_details bd ON bd.content_item_id = ci.id"
                " WHERE x.external_id = ?",
                (external_id,),
            ).fetchone()
        finally:
            conn.close()
        return title, key, sort_title, author, json.loads(metadata or "{}")

    @staticmethod
    def _state_series(db_path: Path, external_id: str, blob: dict[str, Any]) -> None:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "UPDATE book_details SET metadata = ? WHERE content_item_id ="
                " (SELECT content_item_id FROM content_item_external_ids"
                "  WHERE external_id = ?)",
                (json.dumps(blob), external_id),
            )
            conn.commit()
        finally:
            conn.close()

    def test_a_crammed_title_becomes_the_title_and_the_series_it_states(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "v16-crammed.db"
        _open(db_path)
        self._seed_books(db_path, self._FERAL_GODS)
        _rewind_to(db_path, 16)

        _open(db_path)

        assert self._book(db_path, "57905101") == (
            "The Gate of the Feral Gods",
            "gate of the feral gods",
            get_sort_title("The Gate of the Feral Gods"),
            "Matt Dinniman",
            {"series": "Dungeon Crawler Carl", "series_index": 4.0},
        )

    @staticmethod
    def _search_text(db_path: Path, external_id: str) -> str:
        conn = sqlite3.connect(db_path)
        try:
            (text,) = conn.execute(
                "SELECT ci.search_text FROM content_items ci"
                " JOIN content_item_external_ids x ON x.content_item_id = ci.id"
                " WHERE x.external_id = ?",
                (external_id,),
            ).fetchone()
        finally:
            conn.close()
        return str(text)

    def test_an_upgraded_row_is_searchable_by_the_series_it_states(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "v16-searchable.db"
        _open(db_path)
        self._seed_books(db_path, self._FERAL_GODS)
        _open(db_path)
        self._state_series(
            db_path, "calibre:51a0e808", {"series": "Dungeon Crawler Carl"}
        )
        _rewind_to(db_path, 16)

        _open(db_path)

        assert search_text_matches(
            self._search_text(db_path, "calibre:51a0e808"),
            normalize_for_search("Dungeon Crawler Carl"),
        )

    @pytest.mark.parametrize(
        ("stated", "expected"),
        [
            (
                {"series": "DCC", "series_index": 4.5},
                {"series": "DCC", "series_index": 4.5},
            ),
            ({"series": "DCC"}, {"series": "DCC", "series_index": 4.0}),
        ],
        ids=["stated-in-full", "no-position-stated"],
    )
    def test_a_stated_series_wins_key_by_key_over_the_one_the_title_states(
        self, tmp_path: Path, stated: dict[str, Any], expected: dict[str, Any]
    ) -> None:
        db_path = tmp_path / "v16-stated.db"
        _open(db_path)
        self._seed_books(db_path, self._FERAL_GODS)
        self._state_series(db_path, "57905101", stated)
        _rewind_to(db_path, 16)

        _open(db_path)

        title, _key, _sort_title, _author, metadata = self._book(db_path, "57905101")
        assert title == "The Gate of the Feral Gods"
        assert metadata == expected

    def test_an_upgraded_row_drops_the_author_a_shelf_wrote_for_no_author(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "v16-unknown-author.db"
        _open(db_path)
        self._seed_books(db_path, self._AUTHORLESS)
        _rewind_to(db_path, 16)

        _open(db_path)

        _title, _key, _sort, author, _metadata = self._book(db_path, "calibre:0d1a4c19")
        assert author is None

    def test_the_re_keyed_pair_is_still_two_rows_after_both_sources_sync(
        self, tmp_path: Path
    ) -> None:
        db_path = self._upgraded(tmp_path, "v9-then-synced.db", self._FERAL_GODS)
        db = SQLiteDB(db_path)

        landed = self._sync_books(db, self._FERAL_GODS)

        assert len(set(landed)) == 2
        assert db.count_items() == 2

    def test_a_version_nine_library_keys_its_books_as_a_fresh_one_does(
        self, tmp_path: Path
    ) -> None:
        upgraded = self._upgraded(tmp_path, "v9-books.db", self._FERAL_GODS)
        fresh = self._saved_fresh(tmp_path, "fresh-books.db", self._FERAL_GODS)

        assert (
            set(self._groups(upgraded))
            == {"gate of the feral gods"}
            == set(self._groups(fresh))
        )

    _FERAL_GODS_AND_A_REISSUE = (
        (
            "calibre_web",
            "calibre:51a0e808",
            "The Gate of the Feral Gods",
            "gate of the feral gods",
            None,
        ),
        (
            "goodreads_rss",
            "57905101",
            "The Gate of the Feral Gods (Dungeon Crawler Carl, #4)",
            "gate of the feral gods dungeon crawler carl 4",
            "Matt Dinniman",
        ),
        (
            "goodreads_rss",
            "57905102",
            "The Gate of the Feral Gods (Dungeon Crawler Carl, #4)",
            "gate of the feral gods dungeon crawler carl 4",
            "Matt Dinniman",
        ),
    )

    def test_the_upgrade_leaves_the_merge_the_operator_made_in_force(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "v9-already-merged.db"
        _open(db_path)
        self._seed_books(db_path, self._FERAL_GODS_AND_A_REISSUE)
        _merge_by_hand(db_path, survivor="57905101", absorbed="57905102")
        _rewind_to(db_path, 9)

        _open(db_path)

        assert _merge_targets(db_path) == {
            "57905101": "57905101",
            "57905102": "57905101",
            "calibre:51a0e808": "calibre:51a0e808",
        }


class TestWhatTheRenormalizationRewrites:
    @staticmethod
    def _seed(db_path: Path, rows: list[tuple[Any, ...]]) -> None:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO users (id, username) VALUES (2, 'second')"
            )
            for user_id, external_id, title, content_type in rows:
                cursor = conn.execute(
                    """INSERT INTO content_items
                       (user_id, title, content_type, status, source)
                       VALUES (?, ?, ?, 'completed', 'legacy')""",
                    (user_id, title, content_type),
                )
                _record_external_id(conn, cursor.lastrowid, external_id)
            conn.execute("PRAGMA user_version = 0")
            conn.commit()
        finally:
            conn.close()

    def test_a_non_ascii_title_is_keyed_only_once_python_has_lowered_it(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "non-ascii.db"
        _open(db_path)
        self._seed(
            db_path,
            [(1, "disc", "Æon Flux", "movie"), (1, "stream", "Æon Flux™", "movie")],
        )

        _open(db_path)

        assert _content_rows(db_path) == [
            ("disc", "æon flux", None, None),
            ("stream", "æon flux", None, None),
        ]
