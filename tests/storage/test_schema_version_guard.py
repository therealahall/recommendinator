"""Boundary tests for the version guard on the one-time schema upgrade.

``create_schema`` reads ``PRAGMA user_version`` once per open, hands that
number to every version-guarded step, and stamps the current version back
after the last of them, inside the same transaction. ``tests/test_schema.py``
and ``tests/storage/test_detail_shape_migration.py`` cover the happy path of
that scheme; each class below names one edge of it.
"""

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

# The upgrade the two rows below are waiting for: both carry the SQL
# ``lower(title)`` backfill, and dropping the article, the punctuation and the
# Roman numeral puts these two spellings on the one key the save door matches.
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

# The ``content_items`` shape that predates the ``normalized_title`` column, so
# the ALTER, the SQL backfill and the Python re-normalization all have work to
# do on the first open. Written out rather than derived, because the point is
# to be a build this one no longer is.
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
    """Run ``create_schema`` over its own connection, as the app does."""
    conn = sqlite3.connect(db_path)
    try:
        create_schema(conn)
    finally:
        conn.close()


def _user_version(db_path: Path) -> int:
    """Read the persisted version without going through ``create_schema``."""
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def _rewind_to(db_path: Path, version: int) -> None:
    """Stamp *version*, standing the database in for a build that wrote it.

    PRAGMA statements cannot be parameterised; the value is an int the test
    supplies, coerced here so nothing but a number can reach the statement.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(f"PRAGMA user_version = {int(version)}")
        conn.commit()
    finally:
        conn.close()


def _merge_by_hand(db_path: Path, survivor: str, absorbed: str) -> None:
    """Record a merge the operator already made."""
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
    """Write ``_THE_DUPLICATE_PAIR`` into an existing schema."""
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
    """Every live content row, oldest first, read without opening the schema.

    Reading through ``SQLiteDB`` would run the upgrade being observed, so
    every assertion about "what the database holds now" comes through here.
    """
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
    """Each row's external id, mapped to that of the row it now lives as."""
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
    """Every settings leaf, read without opening the schema."""
    conn = sqlite3.connect(db_path)
    try:
        return dict(conn.execute("SELECT key, value_json FROM settings").fetchall())
    finally:
        conn.close()


def _repair_runs_of_one_open(db_path: Path) -> int:
    """Open the database, returning how many times the guarded block ran.

    The block still does its work: "did the scan happen" and "what did it
    leave behind" are both being asked, and every pass inside it skips a row
    already in the current shape, so only the count separates a skipped scan
    from one that found nothing.
    """
    with patch.object(
        schema,
        "_repair_legacy_content_rows",
        wraps=schema._repair_legacy_content_rows,
    ) as repair:
        SQLiteDB(db_path)
    return int(repair.call_count)


class TestTheVersionsAnUpgradingDatabaseCanBeAt:
    """Every stored version below the current one upgrades to it, once.

    The marker is shared: it guarded the two settings steps before it guarded
    the content repair, and it stopped at 2. So an operator arriving at this
    build is at 0 (a database predating the marker), 1 or 2 — and each of
    those has a different set of steps still owing.
    """

    @pytest.mark.parametrize("stored_version", [0, 1, 2])
    def test_every_pre_upgrade_version_repairs_once_and_then_stops(
        self, tmp_path: Path, stored_version: int
    ) -> None:
        """One open does the work; the next reads nothing.

        The repair is new at version 3, so it is owed from all three of them —
        unlike the settings steps, which are spent at different points.
        """
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
        """The version the previous build stamped is the sharp one.

        At 2 the whole-table clear and the orphan prune have both already run,
        so both must stay spent while the content repair runs for the first
        time. ``web.debug`` is the discriminator: it is one of the five keys
        the prune deletes, so a guard widened to "below the current version"
        would delete it again here, and a test asserting only on a live
        registry leaf would not notice.
        """
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
            (one.survivor.db_id, one.absorbed.db_id)
            for one in db.list_duplicate_suggestions().suggestions
        ] == [(kept, refused)]
        assert db.decline_duplicate_suggestion(kept, refused) is not None
        assert db.list_duplicate_suggestions().suggestions == []


class TestWhatAnOpenThatRaisedLeavesBehind:
    """The stamp is the last guarded write, and it shares their transaction.

    The repair's passes rewrite rows before the pass that fails, and the stamp
    is issued after all of them, so a half-applied upgrade and a database
    claiming to be current are the same rollback. Nothing may commit in
    between, or the retry would skip work the first attempt discarded.
    """

    @staticmethod
    def _fail_the_last_pass(db_path: Path) -> None:
        """Open the database with the detail repair failing, as a bad disk would."""
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
        """A database at version 0 holding the pair the upgrade will re-key."""
        db_path = tmp_path / "legacy.db"
        _open(db_path)
        _seed_the_duplicate_pair(db_path)
        _rewind_to(db_path, 0)
        return db_path

    def test_a_failed_open_advances_neither_the_rows_nor_the_version(
        self, tmp_path: Path
    ) -> None:
        """Both halves of the rollback, asserted together on purpose.

        The re-normalization had already rewritten both rows by the time the
        repair raised. If the rows came back but the version did not, the next
        open would repair them again — harmless. If the version came back
        stamped but the rows did not, the pair would keep its stale keys for
        the life of the database with nothing left to notice.
        """
        db_path = self._legacy_database(tmp_path)

        self._fail_the_last_pass(db_path)

        assert _content_rows(db_path) == _THE_PAIR_UNTOUCHED
        assert _user_version(db_path) == 0

    def test_the_retry_runs_the_whole_upgrade_rather_than_the_remainder(
        self, tmp_path: Path
    ) -> None:
        """A transient failure costs the attempt, not the upgrade."""
        db_path = self._legacy_database(tmp_path)
        self._fail_the_last_pass(db_path)

        assert _repair_runs_of_one_open(db_path) == 1
        assert _content_rows(db_path) == _THE_PAIR_RENORMALIZED
        assert _user_version(db_path) == _SCHEMA_VERSION


class TestSchemaVersionRewindRegression:
    """Rewinding a rolled-back build's stamp re-ran ``DELETE FROM settings``.

    The stamp compares with ``<`` for that reason.
    """

    def test_a_version_above_this_build_is_left_where_it_is(
        self, tmp_path: Path
    ) -> None:
        """The marker may only move forward, and this open must not move it."""
        db_path = tmp_path / "from-a-later-build.db"
        _open(db_path)
        _rewind_to(db_path, _SCHEMA_VERSION + 1)

        _open(db_path)

        assert _user_version(db_path) == _SCHEMA_VERSION + 1


class TestWhatCountsAsADatabaseWithNoTables:
    """The neighbours of the "no tables reads as current" shortcut.

    Reading a database as current skips every repair, so the shortcut is only
    safe while nothing that holds rows can take it. Its three neighbours are a
    file with some tables but not all, a file with no bytes at all, and a file
    that is not a database.
    """

    def test_a_zero_byte_file_is_a_fresh_install(self, tmp_path: Path) -> None:
        """SQLite reads an empty file as an empty database, and so does this.

        The interesting half is the second assertion: taking the shortcut must
        still leave the database stamped, or the install pays for the upgrade
        on its next start.
        """
        db_path = tmp_path / "empty.db"
        db_path.touch()

        assert _repair_runs_of_one_open(db_path) == 0
        assert _user_version(db_path) == _SCHEMA_VERSION

    def test_a_half_created_database_is_not_read_as_current(
        self, tmp_path: Path
    ) -> None:
        """One table is enough to disqualify the shortcut.

        An open that raised between the first ``CREATE TABLE`` — which commits
        on its own, before the first DML opens the transaction — and the stamp
        leaves exactly this: tables, and a version of 0. It has to finish
        creating the schema *and* run the repairs, because the version is the
        only thing saying whether they have run.
        """
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
        """The oldest shape there is, and the one the shortcut must not claim.

        The column does not exist yet, so the ALTER adds it empty and the SQL
        backfill and the Python re-normalization both run under the same
        guard. A database read as current here would keep a NULL
        ``normalized_title`` for good, and cross-source dedup matches on that
        column — a NULL matches nothing, so every later import of these two
        rows would add a third.
        """
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
        """Refusing is the whole requirement, and refusing without writing.

        A file pointed at by mistake must come back as an error rather than as
        an empty library, and the bytes must survive it: a shortcut that read
        an unparseable file as "no tables" would stamp a version into
        somebody's data.
        """
        db_path = tmp_path / "not-a-database.db"
        db_path.write_bytes(b"recommendinator was never here\n" * 8)
        before = db_path.read_bytes()

        with pytest.raises(sqlite3.DatabaseError):
            SQLiteDB(db_path)

        assert db_path.read_bytes() == before


class TestUpgradingALibraryWrittenUnderTheOldTitleRules:
    """An older build's keys are stale, and its upgrade merged on them."""

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
        """Each live item's normalized title and the ids its group holds."""
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
        """Save each row through the door every sync comes through."""
        return [
            db.save_content_item(
                ContentItem(
                    id=external_id,
                    title=title,
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.UNREAD,
                    source=source,
                    author=author,
                )
            )
            for source, external_id, title, _, author in rows
        ]

    def _saved_fresh(
        self, tmp_path: Path, name: str, rows: tuple[tuple[Any, ...], ...]
    ) -> Path:
        self._sync_books(SQLiteDB(tmp_path / name), rows)
        return tmp_path / name

    def test_the_re_keyed_pair_is_still_two_rows_after_both_sources_sync(
        self, tmp_path: Path
    ) -> None:
        """Each row already answers to its own source's id, so the save door
        updates it in place and absorbs nothing: an upgraded library keeps the
        pair for a review surface rather than collapsing it on the next sync."""
        db_path = self._upgraded(tmp_path, "v9-then-synced.db", self._FERAL_GODS)
        db = SQLiteDB(db_path)

        landed = self._sync_books(db, self._FERAL_GODS)

        assert len(set(landed)) == 2
        assert db.count_items() == 2

    def test_a_version_nine_library_keys_its_books_as_a_fresh_one_does(
        self, tmp_path: Path
    ) -> None:
        """What the upgrade owes the save door is the key today's normalizer
        computes. Grouping the two rows is the save door's to do, on the sync
        that next names either of them."""
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
        """Re-keying every title moves no row out from behind a merge."""
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
    """The stored key is what every later title match reads."""

    @staticmethod
    def _seed(db_path: Path, rows: list[tuple[Any, ...]]) -> None:
        """Write raw pre-upgrade rows and rewind, then leave them to the open.

        ``normalized_title`` is left NULL so the SQL backfill and the Python
        re-normalization both run over them, as they would on the database
        these rows stand for.
        """
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
        """SQLite's ``lower()`` folds ASCII and nothing else, so the backfill
        leaves ``Æ`` standing and two spellings of one film keep two keys.
        ``_renormalize_titles`` lowers the character and strips the trademark
        sign, and only then do the rows name each other."""
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
