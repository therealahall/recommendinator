"""Boundary tests for the version guard on the one-time schema upgrade.

``create_schema`` reads ``PRAGMA user_version`` once per open, hands that
number to every version-guarded step, and stamps the current version back
after the last of them, inside the same transaction. ``tests/test_schema.py``
and ``tests/storage/test_detail_shape_migration.py`` cover the happy path of
that scheme. These cover its edges, and there are four:

* **The versions a real database can be sitting at.** The marker used to
  belong to the settings migration alone and stopped at 2, so an operator
  upgrading to this build is at 0, 1 or 2 — and at 2 the settings steps must
  stay spent while the content repair still runs.
* **What a failed open leaves behind.** The stamp is written after every
  guarded step and before the single commit, so an open that raises advances
  nothing and the retry re-runs the lot.
* **What counts as a fresh database.** "No tables at all" reads as already
  current. A half-created database, a zero-byte file and a file that is not a
  database at all are the three neighbours of that rule.
* **What the passes group and what they decline.** ``_deduplicate_inline``
  merges by ``(user_id, content_type, normalized_title)`` and skips the empty
  title, and ``_renormalize_titles`` is what makes a non-ASCII title match at
  all, because SQLite's ``lower()`` only folds ASCII.
"""

import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage import schema
from src.storage.schema import _SCHEMA_VERSION, create_schema
from src.storage.sqlite_db import SQLiteDB

# The upgrade the two rows below are waiting for. Both carry the SQL
# ``lower(title)`` backfill as their normalized title, which is what the full
# Python normalization corrects: dropping the article, the punctuation and the
# Roman numeral collapses these two spellings of one game onto one value, and
# only then are they a pair the merge reconciles.
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

#: What ``_THE_DUPLICATE_PAIR`` looks like once the upgrade has run: one row,
#: the older of the two, carrying its own rating and the review only the
#: duplicate held.
_THE_MERGED_SURVIVOR = [("steam-witcher", "witcher 3 wild hunt", 5, "Great writing")]

#: What it looks like while the upgrade has not run: both rows, untouched.
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


def _seed_the_duplicate_pair(db_path: Path) -> None:
    """Write ``_THE_DUPLICATE_PAIR`` into an existing schema."""
    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(
            """INSERT INTO content_items
               (user_id, external_id, title, normalized_title, content_type,
                status, rating, review, source)
               VALUES (1, ?, ?, ?, 'video_game', 'completed', ?, ?, 'legacy')""",
            _THE_DUPLICATE_PAIR,
        )
        conn.commit()
    finally:
        conn.close()


def _content_rows(db_path: Path) -> list[tuple[Any, ...]]:
    """Every content row, oldest first, read without opening the schema.

    Reading through ``SQLiteDB`` would run the upgrade being observed, so
    every assertion about "what the database holds now" comes through here.
    """
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT external_id, normalized_title, rating, review"
            " FROM content_items ORDER BY id"
        ).fetchall()
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


def _statements_of_one_open(db_path: Path) -> list[str]:
    """Every SQL statement one ``create_schema`` issues, in order."""
    seen: list[str] = []
    conn = sqlite3.connect(db_path)
    conn.set_trace_callback(seen.append)
    try:
        create_schema(conn)
    finally:
        conn.set_trace_callback(None)
        conn.close()
    return seen


def _wrote_the_version(statements: list[str]) -> bool:
    """Whether the statements include a *write* of ``user_version``.

    ``PRAGMA user_version`` reads it and ``PRAGMA user_version = N`` writes
    it, so the assignment is what separates the guarded stamp from the read
    that decides whether to issue it.
    """
    return any(
        statement.strip().startswith("PRAGMA user_version =")
        for statement in statements
    )


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
        assert _content_rows(db_path) == _THE_MERGED_SURVIVOR
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
        assert _content_rows(db_path) == _THE_MERGED_SURVIVOR

    def test_a_version_zero_database_still_has_its_settings_table_cleared(
        self, tmp_path: Path
    ) -> None:
        """The other end of the same parametrisation, in data.

        The pair above says the content repair runs from every version. This
        says the settings steps are still keyed to their own versions rather
        than riding along with it: at 0 the whole-table clear is owed, and it
        takes the leaf the version-2 database keeps.
        """
        db_path = tmp_path / "at-version-zero.db"
        _open(db_path)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "INSERT INTO settings (key, value_json) VALUES"
                " ('recommendations.default_count', '9')"
            )
            conn.commit()
        finally:
            conn.close()
        _rewind_to(db_path, 0)

        _open(db_path)

        assert _settings_rows(db_path) == {}


class TestWhatAnOpenThatRaisedLeavesBehind:
    """The stamp is the last guarded write, and it shares their transaction.

    The repair's passes rewrite rows before the pass that fails, and the stamp
    is issued after all of them, so a half-applied upgrade and a database
    claiming to be current are the same rollback. Nothing may commit in
    between, or the retry would skip work the first attempt discarded.
    """

    @staticmethod
    def _fail_the_last_pass(db_path: Path) -> None:
        """Open the database with the merge failing, as a bad disk would."""
        with (
            patch.object(
                schema, "_deduplicate_inline", side_effect=OSError("disk failure")
            ),
            pytest.raises(OSError),
        ):
            SQLiteDB(db_path)

    def _legacy_database(self, tmp_path: Path) -> Path:
        """A database at version 0 holding the pair awaiting the merge."""
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
        merge raised. If the rows came back but the version did not, the next
        open would repair them again — harmless. If the version came back
        stamped but the rows did not, the pair would stay unmerged for the
        life of the database with nothing left to notice.
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
        assert _content_rows(db_path) == _THE_MERGED_SURVIVOR
        assert _user_version(db_path) == _SCHEMA_VERSION

    def test_no_guarded_step_runs_with_the_new_version_already_stamped(
        self, tmp_path: Path
    ) -> None:
        """The mechanism behind the rollback above, read from inside it.

        ``_migrate_settings_table`` is the last guarded step, so the version
        it sees on the cursor is the version any of them would see. Reading 0
        there is what makes "an open that raises advances nothing" true of a
        failure anywhere in the sequence, rather than only of the one the
        tests above provoke.
        """
        db_path = self._legacy_database(tmp_path)
        seen: list[int] = []
        run_the_step = schema._migrate_settings_table

        def _record_then_run(cursor: sqlite3.Cursor, stored_version: int) -> None:
            cursor.execute("PRAGMA user_version")
            seen.append(int(cursor.fetchone()[0]))
            run_the_step(cursor, stored_version)

        with patch.object(schema, "_migrate_settings_table", _record_then_run):
            SQLiteDB(db_path)

        assert seen == [0]
        assert _user_version(db_path) == _SCHEMA_VERSION


class TestTheStampIsSkippedWhenItWouldNotChange:
    """The steady-state open issues no stamp, and the branch is reached.

    ``tests/test_schema.py`` pins the consequence — the file is not written —
    with ``PRAGMA data_version``. This pins the cause, so that a stamp issued
    unconditionally fails as a stamp rather than only as a side effect, and so
    the skip cannot be mistaken for a branch nothing reaches.
    """

    def test_the_upgrading_open_writes_the_version(self, tmp_path: Path) -> None:
        """The half that must keep happening."""
        db_path = tmp_path / "upgrading.db"
        _open(db_path)
        _rewind_to(db_path, 0)

        assert _wrote_the_version(_statements_of_one_open(db_path)) is True

    def test_the_steady_state_open_does_not_write_the_version(
        self, tmp_path: Path
    ) -> None:
        """The half the guard adds."""
        db_path = tmp_path / "steady.db"
        _open(db_path)

        assert _wrote_the_version(_statements_of_one_open(db_path)) is False

    def test_a_fresh_database_writes_the_version_it_never_read(
        self, tmp_path: Path
    ) -> None:
        """The one case where the two readings of the version disagree.

        ``_stored_schema_version`` answers "already current" for a database
        with no tables, but the file itself still says 0, so the stamp must
        be issued anyway. Skipping it on the strength of the value handed to
        the guarded steps would leave every fresh install at 0 and hand the
        whole upgrade to its second open.
        """
        db_path = tmp_path / "fresh.db"

        statements = _statements_of_one_open(db_path)

        assert _wrote_the_version(statements) is True
        assert _user_version(db_path) == _SCHEMA_VERSION


class TestSchemaVersionRewindRegression:
    """Opening a newer database with this build rewinds its schema version.

    **Symptom.** A database stamped at a version above this build's — one an
    operator wrote with a later release and then opened with an earlier one,
    which is what rolling a container tag back does — comes away stamped at
    this build's version instead. The later build then sees a version below
    its own and re-runs a one-time upgrade step over a database that has
    already had it. Those steps exist precisely because they must not run
    twice: the version-1 step is ``DELETE FROM settings``.

    **Root cause.** The stamp is guarded by ``!=`` rather than ``<``::

        cursor.execute("PRAGMA user_version")
        if cursor.fetchone()[0] != _SCHEMA_VERSION:
            cursor.execute(f"PRAGMA user_version = {int(_SCHEMA_VERSION)}")

    The guard was written to answer "would this write change anything", which
    is the right question for the steady-state open it was added for and the
    wrong one for a version above this build's: it changes something, and what
    it changes is a marker that may only ever move forward. The guard it
    replaced could not do this — ``_migrate_settings_table`` returned before
    reaching its stamp whenever the stored version was at or above the
    current one, so the value only ever advanced.

    **Fix.** Compare with ``<``, so the stamp advances the version and never
    lowers it. The steady-state open stays silent, because a version equal to
    this build's is not below it either.
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

    def test_an_open_with_nothing_to_upgrade_writes_no_version_at_all(
        self, tmp_path: Path
    ) -> None:
        """The same defect seen as a write, which is what the guard is for.

        The stamp is skipped "because the pragma rewrites the database header
        and an open with nothing to upgrade should leave the file alone". A
        database from a later build has nothing here to upgrade either, so the
        write is unwanted on its own terms, before what it writes is even
        considered.
        """
        db_path = tmp_path / "from-a-later-build.db"
        _open(db_path)
        _rewind_to(db_path, _SCHEMA_VERSION + 1)

        assert _wrote_the_version(_statements_of_one_open(db_path)) is False


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

        assert _content_rows(db_path) == _THE_MERGED_SURVIVOR
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


class TestRunningTheUpgradeTwiceOverOneLibrary:
    """A second run over an upgraded library converges rather than compounds.

    Two processes opening one database at the same time both read the stored
    version before either commits, so both can decide the upgrade is owed.
    SQLite serialises the writers, which turns that race into this: the whole
    upgrade, run a second time over rows the first run already corrected. The
    same shape as the retry after a failed open, and the reason neither is
    dangerous.
    """

    def test_a_second_upgrade_neither_re_merges_nor_unwinds_the_first(
        self, tmp_path: Path
    ) -> None:
        """The merged survivor is not merged into anything, and keeps it all.

        The dedup pass reads groups of more than one row, so the danger is not
        that it repeats work — it is that the row the first run left behind
        looks like a member of a group again, or that the re-normalization
        moves it back off the value the merge grouped it on.
        """
        db_path = tmp_path / "raced.db"
        _open(db_path)
        _seed_the_duplicate_pair(db_path)
        _rewind_to(db_path, 0)
        _open(db_path)
        after_the_first_run = _content_rows(db_path)

        _rewind_to(db_path, 0)
        _open(db_path)

        assert after_the_first_run == _THE_MERGED_SURVIVOR
        assert _content_rows(db_path) == after_the_first_run


class TestWhatTheDeduplicationPassRefusesToGroup:
    """The merge is keyed on three columns, and it deletes rows.

    It runs once per database now, so a group it gets wrong is not a churn
    that shows up on the next start — it is a row gone. These are the
    neighbours of the key: the same title under another user, under another
    content type, and the empty title that every untitled row shares.
    """

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
            conn.executemany(
                """INSERT INTO content_items
                   (user_id, external_id, title, content_type, status, source)
                   VALUES (?, ?, ?, ?, 'completed', 'legacy')""",
                rows,
            )
            conn.execute("PRAGMA user_version = 0")
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _external_ids(db_path: Path) -> list[Any]:
        """The external ids still present, oldest first."""
        return [row[0] for row in _content_rows(db_path)]

    def test_two_untitled_rows_are_not_merged_into_one(self, tmp_path: Path) -> None:
        """The empty title is not an identity, and the query says so.

        Every untitled row normalizes to ``""``, so grouping on it would merge
        the whole set into whichever one happens to be oldest. The pass
        excludes the empty string for exactly that reason, and this is the
        only edge where the exclusion is the difference between one row and
        two.
        """
        db_path = tmp_path / "untitled.db"
        _open(db_path)
        self._seed(
            db_path,
            [(1, "first", "", "book"), (1, "second", "", "book")],
        )

        _open(db_path)

        assert self._external_ids(db_path) == ["first", "second"]

    def test_one_title_under_two_users_stays_two_rows(self, tmp_path: Path) -> None:
        """A shared library would otherwise leak one user's row into another's."""
        db_path = tmp_path / "two-users.db"
        _open(db_path)
        self._seed(
            db_path,
            [
                (1, "mine", "The Dispossessed", "book"),
                (2, "theirs", "Dispossessed", "book"),
            ],
        )

        _open(db_path)

        assert self._external_ids(db_path) == ["mine", "theirs"]

    def test_one_title_under_two_content_types_stays_two_rows(
        self, tmp_path: Path
    ) -> None:
        """The book and the film of the same name are two things."""
        db_path = tmp_path / "two-types.db"
        _open(db_path)
        self._seed(
            db_path,
            [(1, "novel", "The Shining", "book"), (1, "film", "Shining", "movie")],
        )

        _open(db_path)

        assert self._external_ids(db_path) == ["novel", "film"]

    def test_a_group_of_three_collapses_onto_the_oldest_row(
        self, tmp_path: Path
    ) -> None:
        """The merge loops, so three spellings of one game leave one row.

        Two is the case every other test drives; three is where a loop that
        merged only the first duplicate would still look right on a count of
        "fewer rows than before".
        """
        db_path = tmp_path / "three.db"
        _open(db_path)
        self._seed(
            db_path,
            [
                (1, "steam", "The Witcher III: Wild Hunt", "video_game"),
                (1, "gog", "Witcher 3 - Wild Hunt", "video_game"),
                (1, "blog", "witcher iii wild hunt", "video_game"),
            ],
        )

        _open(db_path)

        assert self._external_ids(db_path) == ["steam"]

    def test_a_non_ascii_title_is_merged_only_once_python_has_lowered_it(
        self, tmp_path: Path
    ) -> None:
        """Why the SQL backfill is not the end of the job.

        SQLite's ``lower()`` folds ASCII and nothing else, so the backfill
        leaves ``Æ`` standing and two spellings of one film keep two distinct
        normalized titles. ``_renormalize_titles`` is what lowers the
        character and strips the trademark sign, and only then are the rows a
        group. The survivor's stored value is asserted, because a pass that
        merged them on the ASCII fold would leave the capital behind.
        """
        db_path = tmp_path / "non-ascii.db"
        _open(db_path)
        self._seed(
            db_path,
            [(1, "disc", "Æon Flux", "movie"), (1, "stream", "Æon Flux™", "movie")],
        )

        _open(db_path)

        assert _content_rows(db_path) == [("disc", "æon flux", None, None)]


class TestTheRowsTheSeasonPassDeclinesToSettle:
    """A row the season pass keeps rather than rewrites is read once.

    ``tests/storage/test_detail_shape_migration.py`` pins this class of row
    for the platform pass, whose prefilter is ``platforms LIKE '%{%'``. The
    season pass has the same shape of prefilter and the same decline, so it
    has the same class of row: one whose blob mentions ``total_seasons``
    without carrying the key. Keeping it is the behaviour that must not
    change; the endless re-reading is what the version guard ends.
    """

    @staticmethod
    def _strand_a_note(db_path: Path, blob: str) -> int:
        """Save a show, then overwrite its blob with *blob* and rewind."""
        db = SQLiteDB(db_path)
        db_id = db.save_content_item(
            ContentItem(
                id="trakt:1",
                title="The Wire",
                content_type=ContentType.TV_SHOW,
                status=ConsumptionStatus.COMPLETED,
            )
        )
        with db.connection() as conn:
            conn.execute(
                "UPDATE tv_show_details SET seasons = 5, metadata = ?"
                " WHERE content_item_id = ?",
                (blob, db_id),
            )
            conn.execute("PRAGMA user_version = 0")
            conn.commit()
        return db_id

    @staticmethod
    def _stored_blob(db_path: Path, db_id: int) -> Any:
        """The blob as stored, read without opening the schema."""
        conn = sqlite3.connect(db_path)
        try:
            return conn.execute(
                "SELECT metadata FROM tv_show_details WHERE content_item_id = ?",
                (db_id,),
            ).fetchone()[0]
        finally:
            conn.close()

    def test_a_blob_that_only_mentions_the_key_is_kept_and_read_once(
        self, tmp_path: Path
    ) -> None:
        """The prefilter matches, the pass declines, and that is the end of it.

        The note is a value rather than a key, so the pass finds nothing to
        move and leaves the row exactly as it was — which leaves it matching
        ``metadata LIKE '%total_seasons%'`` for good. Before the guard that
        meant re-reading and re-parsing it on every start.
        """
        db_path = tmp_path / "noted.db"
        blob = '{"note": "total_seasons is a guess"}'
        db_id = self._strand_a_note(db_path, blob)

        reads = (_repair_runs_of_one_open(db_path), _repair_runs_of_one_open(db_path))

        assert reads == (1, 0)
        assert self._stored_blob(db_path, db_id) == blob
