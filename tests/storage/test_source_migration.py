"""Tests for the source label, plugin name and item attribution migrations."""

import logging
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.ingestion.registry import get_registry
from src.ingestion.sources.goodreads_csv.goodreads_csv import GoodreadsCsvPlugin
from src.storage.manager import StorageManager
from src.storage.source_migration import (
    migrate_source_attribution,
    migrate_source_config_plugins,
    migrate_source_labels,
)


def _yaml_inputs(**sources: str) -> dict[str, Any]:
    """A config whose ``inputs`` map each given source id to its plugin."""
    return {
        "inputs": {
            source_id: {"plugin": plugin, "enabled": True}
            for source_id, plugin in sources.items()
        }
    }


def _insert_user(storage: StorageManager, user_id: int) -> None:
    """Insert a users row so content_items FK constraints are satisfied."""
    with storage.connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, username) VALUES (?, ?)",
            (user_id, f"user{user_id}"),
        )
        conn.commit()


def _insert_item(storage: StorageManager, source: str, user_id: int = 1) -> None:
    """Insert a minimal content item with the given source label."""
    with storage.connection() as conn:
        conn.execute(
            "INSERT INTO content_items (user_id, title, content_type, status, source) "
            "VALUES (?, 'Some Title', 'book', 'completed', ?)",
            (user_id, source),
        )
        conn.commit()


def _insert_source_config(
    storage: StorageManager,
    source_id: str,
    plugin: str,
    user_id: int = 1,
    enabled: bool = True,
) -> None:
    """Insert a migrated source_configs row with the given plugin name."""
    with storage.connection() as conn:
        conn.execute(
            "INSERT INTO source_configs "
            "(user_id, source_id, plugin, config_json, enabled) "
            "VALUES (?, ?, ?, '{}', ?)",
            (user_id, source_id, plugin, int(enabled)),
        )
        conn.commit()


def _sources(storage: StorageManager) -> list[str]:
    """Return every stored ``source`` value ordered by row id."""
    with storage.connection() as conn:
        cursor = conn.execute("SELECT source FROM content_items ORDER BY id")
        return [row[0] for row in cursor.fetchall()]


def _completed_migrations(storage: StorageManager) -> list[str]:
    """Return every name recorded in ``completed_migrations``."""
    with storage.connection() as conn:
        cursor = conn.execute("SELECT name FROM completed_migrations ORDER BY name")
        return [row[0] for row in cursor.fetchall()]


def _plugins(storage: StorageManager) -> list[tuple[int, str, str]]:
    """Return every source_configs ``(user_id, source_id, plugin)`` by source_id."""
    with storage.connection() as conn:
        cursor = conn.execute(
            "SELECT user_id, source_id, plugin FROM source_configs "
            "ORDER BY user_id, source_id"
        )
        return [(row[0], row[1], row[2]) for row in cursor.fetchall()]


class TestMigrateSourceLabels:
    """Tests for migrate_source_labels."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_relabels_goodreads_source(self, storage: StorageManager) -> None:
        """An item with source='goodreads' is relabeled to 'goodreads_csv'."""
        _insert_item(storage, "goodreads")

        migrate_source_labels(storage)

        assert _sources(storage) == ["goodreads_csv"]

    def test_logs_count_on_migration(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The migration logs at INFO with the number of rows it updated."""
        _insert_item(storage, "goodreads")
        _insert_item(storage, "goodreads")

        with caplog.at_level(logging.INFO):
            migrate_source_labels(storage)

        assert "Relabeled 2 content item(s)" in caplog.text

    def test_idempotent_second_run_is_noop(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Running twice yields the same result and the second run reports nothing."""
        _insert_item(storage, "goodreads")

        migrate_source_labels(storage)

        caplog.clear()
        with caplog.at_level(logging.INFO):
            migrate_source_labels(storage)

        assert _sources(storage) == ["goodreads_csv"]
        # Second run matches no rows, so nothing is logged.
        assert "Relabeled" not in caplog.text

    def test_other_sources_untouched(self, storage: StorageManager) -> None:
        """Items with other source labels are left exactly as-is."""
        _insert_item(storage, "steam")
        _insert_item(storage, "mybooks")
        _insert_item(storage, "goodreads")
        # An arbitrary user-chosen config-block key must not be rewritten.
        _insert_item(storage, "goodreads_rss")

        migrate_source_labels(storage)

        assert _sources(storage) == [
            "steam",
            "mybooks",
            "goodreads_csv",
            "goodreads_rss",
        ]

    def test_scopes_to_requested_user(self, storage: StorageManager) -> None:
        """Only the target user's goodreads rows are relabeled.

        The migration is user-scoped (default user 1). A goodreads row owned by
        another user must be left untouched when migrating user 1, and relabeled
        only when that user is explicitly migrated.
        """
        _insert_user(storage, 2)
        _insert_item(storage, "goodreads", user_id=1)
        _insert_item(storage, "goodreads", user_id=2)

        migrate_source_labels(storage)  # defaults to user_id=1

        assert _sources(storage) == ["goodreads_csv", "goodreads"]

        migrate_source_labels(storage, user_id=2)

        assert _sources(storage) == ["goodreads_csv", "goodreads_csv"]

    def test_empty_db_is_noop(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An empty database with no matching rows completes as a silent no-op."""
        with caplog.at_level(logging.INFO):
            migrate_source_labels(storage)

        assert _sources(storage) == []
        assert "Relabeled" not in caplog.text


class TestMigrateSourceConfigPlugins:
    """Tests for migrate_source_config_plugins."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_relabels_goodreads_plugin(self, storage: StorageManager) -> None:
        """A source_configs row with plugin='goodreads' becomes 'goodreads_csv'."""
        _insert_source_config(storage, "my_books", "goodreads")

        migrate_source_config_plugins(storage)

        assert _plugins(storage) == [(1, "my_books", "goodreads_csv")]

    def test_logs_count_on_migration(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The migration logs at INFO with the number of rows it updated."""
        _insert_source_config(storage, "books_a", "goodreads")
        _insert_source_config(storage, "books_b", "goodreads")

        with caplog.at_level(logging.INFO):
            migrate_source_config_plugins(storage)

        assert "Relabeled 2 source config(s)" in caplog.text

    def test_idempotent_second_run_is_noop(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Running twice yields the same result and the second run logs nothing."""
        _insert_source_config(storage, "my_books", "goodreads")

        migrate_source_config_plugins(storage)

        caplog.clear()
        with caplog.at_level(logging.INFO):
            migrate_source_config_plugins(storage)

        assert _plugins(storage) == [(1, "my_books", "goodreads_csv")]
        assert "Relabeled" not in caplog.text

    def test_other_plugins_untouched(self, storage: StorageManager) -> None:
        """Rows with other plugin names are left exactly as-is."""
        _insert_source_config(storage, "games", "steam")
        _insert_source_config(storage, "books", "goodreads")
        _insert_source_config(storage, "shelves", "goodreads_rss")

        migrate_source_config_plugins(storage)

        assert _plugins(storage) == [
            (1, "books", "goodreads_csv"),
            (1, "games", "steam"),
            (1, "shelves", "goodreads_rss"),
        ]

    def test_scopes_to_requested_user(self, storage: StorageManager) -> None:
        """Only the target user's goodreads rows are relabeled.

        A goodreads plugin row owned by another user must be left untouched when
        migrating user 1, and relabeled only when that user is migrated.
        """
        _insert_user(storage, 2)
        _insert_source_config(storage, "books", "goodreads", user_id=1)
        _insert_source_config(storage, "books", "goodreads", user_id=2)

        migrate_source_config_plugins(storage)  # defaults to user_id=1

        assert _plugins(storage) == [
            (1, "books", "goodreads_csv"),
            (2, "books", "goodreads"),
        ]

        migrate_source_config_plugins(storage, user_id=2)

        assert _plugins(storage) == [
            (1, "books", "goodreads_csv"),
            (2, "books", "goodreads_csv"),
        ]

    def test_empty_db_is_noop(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An empty database with no matching rows completes as a silent no-op."""
        with caplog.at_level(logging.INFO):
            migrate_source_config_plugins(storage)

        assert _plugins(storage) == []
        assert "Relabeled" not in caplog.text


class TestMigratedPluginResolvesThroughRegistry:
    """End-to-end: a migrated source_config must resolve through the registry.

    The rename is a hard cutover — the old ``goodreads`` plugin name no longer
    exists, so a stored ``plugin='goodreads'`` row would resolve to ``None`` and
    silently stop syncing. These tests prove the migration rewrites the stored
    plugin name to the value the registry actually serves, closing the loop.
    """

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_old_goodreads_plugin_name_does_not_resolve(self) -> None:
        """The historical ``goodreads`` plugin name no longer resolves.

        Proves the hard cutover: only ``goodreads_csv`` exists in the registry,
        so an un-migrated ``plugin='goodreads'`` row would vanish.
        """
        registry = get_registry()

        assert registry.get_plugin("goodreads") is None
        assert isinstance(registry.get_plugin("goodreads_csv"), GoodreadsCsvPlugin)

    def test_migrated_plugin_value_resolves_to_csv_plugin(
        self, storage: StorageManager
    ) -> None:
        """After migration the stored plugin value resolves to GoodreadsCsvPlugin.

        Reads the plugin name back out of the DB exactly as the sync path would
        and looks it up in the registry, proving the round-trip: a previously
        DB-configured Goodreads source keeps working after the rename instead of
        silently vanishing.
        """
        _insert_source_config(storage, "my_books", "goodreads")

        migrate_source_config_plugins(storage)

        stored_plugin = _plugins(storage)[0][2]
        assert stored_plugin == "goodreads_csv"
        resolved = get_registry().get_plugin(stored_plugin)
        assert isinstance(resolved, GoodreadsCsvPlugin)

    def test_combined_source_and_config_relabel_consistently(
        self, storage: StorageManager
    ) -> None:
        """A user with BOTH a goodreads source_config AND goodreads items.

        Startup runs both migrations. Afterwards the source_config plugin must
        resolve through the registry AND every content item must be re-attributed
        to the same ``goodreads_csv`` label, so the source and its items stay
        consistent rather than drifting apart.
        """
        _insert_source_config(storage, "goodreads", "goodreads")
        _insert_item(storage, "goodreads")
        _insert_item(storage, "goodreads")

        # Mirrors the startup order in cli/main.py, app.py and state.py.
        migrate_source_labels(storage)
        migrate_source_config_plugins(storage)

        assert _sources(storage) == ["goodreads_csv", "goodreads_csv"]
        assert _plugins(storage) == [(1, "goodreads", "goodreads_csv")]
        resolved = get_registry().get_plugin(_plugins(storage)[0][2])
        assert isinstance(resolved, GoodreadsCsvPlugin)


class TestMigrateSourceAttribution:
    """Tests for migrate_source_attribution."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_items_move_onto_the_only_source_using_that_plugin(
        self, storage: StorageManager
    ) -> None:
        """One sonarr source owns every item stored under the plugin name."""
        _insert_item(storage, "sonarr")
        _insert_item(storage, "sonarr")

        migrate_source_attribution(_yaml_inputs(my_sonarr="sonarr"), storage)

        assert _sources(storage) == ["my_sonarr", "my_sonarr"]

    def test_a_shared_plugin_leaves_every_item_where_it_is(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Two gog sources: nothing records which one each item came from.

        Splitting them across ``gog_work`` and ``gog_home`` would mis-attribute
        real data, so the rows keep the plugin name and the operator is told
        which sources collided.
        """
        _insert_item(storage, "gog")

        with caplog.at_level(logging.WARNING):
            migrate_source_attribution(
                _yaml_inputs(gog_work="gog", gog_home="gog"), storage
            )

        assert _sources(storage) == ["gog"]
        assert "'gog_home', 'gog_work'" in caplog.text

    def test_a_shared_plugin_with_nothing_stranded_says_nothing(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Two gog sources are only worth a warning if items are stuck.

        Otherwise every boot and every hot-reload logs a refusal about a
        library that has nothing wrong with it.
        """
        _insert_item(storage, "gog_work")

        with caplog.at_level(logging.WARNING):
            migrate_source_attribution(
                _yaml_inputs(gog_work="gog", gog_home="gog"), storage
            )

        assert caplog.text == ""

    def test_a_db_source_makes_a_yaml_source_ambiguous_too(
        self, storage: StorageManager
    ) -> None:
        """A source that lives only in ``source_configs`` still counts.

        Reading YAML alone would see one gog source and hand it the other's
        items — the exact mis-attribution the ambiguity rule exists to stop.
        """
        _insert_source_config(storage, "gog_home", "gog")
        _insert_item(storage, "gog")

        migrate_source_attribution(_yaml_inputs(gog_work="gog"), storage)

        assert _sources(storage) == ["gog"]

    def test_a_source_named_after_its_plugin_moves_nothing(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The default naming: source id equals plugin name, so it is a no-op."""
        _insert_item(storage, "steam")

        with caplog.at_level(logging.WARNING):
            migrate_source_attribution(_yaml_inputs(steam="steam"), storage)

        assert _sources(storage) == ["steam"]
        assert caplog.text == ""

    def test_a_source_named_after_another_sources_plugin_is_refused(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Rows saying ``gog`` could be either source's, so neither gets them.

        Renaming unblocks the pass, so the operator hears about it at WARNING —
        along with what it costs, since ``my_gog`` then inherits the renamed
        source's own rows too.
        """
        _insert_item(storage, "gog")

        with caplog.at_level(logging.INFO):
            migrate_source_attribution(_yaml_inputs(gog="steam", my_gog="gog"), storage)

        assert _sources(storage) == ["gog"]
        assert caplog.record_tuples == [
            (
                "src.storage.source_migration",
                logging.WARNING,
                "Leaving 1 content item(s) under plugin 'gog': a source is named "
                "after it but runs 'steam', so its own rows are spelled the same "
                "way. Renaming it hands every one of these rows to 'my_gog', the "
                "renamed source's included, until its next sync relabels those "
                "still upstream.",
            )
        ]

    def test_a_source_running_the_plugin_it_is_named_after_is_only_noted(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``gog`` and ``gog_family`` both run gog: no rename separates them.

        The ``gog`` source's own rows are spelled ``gog`` by design, so there is
        no remedy to hand the operator. Warning them would demand an action that
        does not exist.
        """
        _insert_item(storage, "gog")

        with caplog.at_level(logging.INFO):
            migrate_source_attribution(
                _yaml_inputs(gog="gog", gog_family="gog"), storage
            )

        assert _sources(storage) == ["gog"]
        assert caplog.record_tuples == [
            (
                "src.storage.source_migration",
                logging.INFO,
                "Leaving 1 content item(s) under plugin 'gog': the source named "
                "after it runs it, so its own rows are spelled the same way as "
                "its siblings'. No rename separates them.",
            )
        ]

    def test_sharing_siblings_outrank_the_rename_advice(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Both obstacles at once: renaming ``gog`` would not unblock anything.

        ``gog`` runs steam while ``a`` and ``b`` both run gog. Following the
        rename advice leaves the siblings sharing the plugin, so the operator
        hears about the sharing instead.
        """
        _insert_item(storage, "gog")

        with caplog.at_level(logging.INFO):
            migrate_source_attribution(
                _yaml_inputs(gog="steam", a="gog", b="gog"), storage
            )

        assert _sources(storage) == ["gog"]
        assert "2 sources share it ('a', 'b')" in caplog.text
        assert "Rename" not in caplog.text

    def test_items_of_an_unconfigured_plugin_are_left_alone(
        self, storage: StorageManager
    ) -> None:
        """A deleted source's items have no owner to move to."""
        _insert_item(storage, "trakt")

        migrate_source_attribution(_yaml_inputs(my_sonarr="sonarr"), storage)

        assert _sources(storage) == ["trakt"]

    def test_idempotent_second_run_is_noop(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Running twice moves the rows once and reports nothing the second time."""
        _insert_item(storage, "sonarr")
        config = _yaml_inputs(my_sonarr="sonarr")

        migrate_source_attribution(config, storage)

        caplog.clear()
        with caplog.at_level(logging.INFO):
            migrate_source_attribution(config, storage)

        assert _sources(storage) == ["my_sonarr"]
        assert "Re-attributed" not in caplog.text

    def test_scopes_to_requested_user(self, storage: StorageManager) -> None:
        """Another user's identically labelled items are not swept up."""
        _insert_user(storage, 2)
        _insert_item(storage, "sonarr", user_id=1)
        _insert_item(storage, "sonarr", user_id=2)

        migrate_source_attribution(_yaml_inputs(my_sonarr="sonarr"), storage)

        assert _sources(storage) == ["my_sonarr", "sonarr"]

    def test_empty_config_is_a_noop(self, storage: StorageManager) -> None:
        """No inputs section at all: nothing to attribute anything to."""
        _insert_item(storage, "sonarr")

        migrate_source_attribution({}, storage)

        assert _sources(storage) == ["sonarr"]

    def test_a_switched_off_sibling_still_makes_the_plugin_ambiguous(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A disabled source still owns the items it already synced.

        Counting only enabled ones would see a single gog source and hand it
        the other's rows.
        """
        _insert_item(storage, "gog")
        config = _yaml_inputs(gog_work="gog", gog_home="gog")
        config["inputs"]["gog_home"]["enabled"] = False

        with caplog.at_level(logging.WARNING):
            migrate_source_attribution(config, storage)

        assert _sources(storage) == ["gog"]
        assert "'gog_home', 'gog_work'" in caplog.text

    def test_a_switched_off_db_sibling_still_makes_the_plugin_ambiguous(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A disabled source living only in ``source_configs`` still owns items.

        The guarantee holds only while ``list_source_configs`` returns disabled
        rows: adding ``WHERE enabled = 1`` there would hand ``gog_work`` those
        items in silence.
        """
        _insert_source_config(storage, "gog_home", "gog", enabled=False)
        _insert_item(storage, "gog")

        with caplog.at_level(logging.WARNING):
            migrate_source_attribution(_yaml_inputs(gog_work="gog"), storage)

        assert _sources(storage) == ["gog"]
        assert "'gog_home', 'gog_work'" in caplog.text

    def test_a_relabelled_row_is_not_carried_on_to_a_second_source(
        self, storage: StorageManager
    ) -> None:
        """One row must not walk through two owners in a single pass.

        ``sonarr`` runs the radarr plugin and ``my_sonarr`` runs the sonarr
        plugin, so radarr rows land on ``sonarr`` — which the alphabetical
        pass reaches next.
        """
        _insert_item(storage, "radarr")

        migrate_source_attribution(
            _yaml_inputs(sonarr="radarr", my_sonarr="sonarr"), storage
        )

        assert _sources(storage) == ["sonarr"]

    def test_a_db_row_replaces_the_yaml_plugin_for_the_same_source(
        self, storage: StorageManager
    ) -> None:
        """``resolve_inputs`` prefers the DB row, so ownership must too.

        ``mine`` was a gog source in YAML and its row now says steam: the
        steam rows are its, and the gog rows belong to nobody.
        """
        _insert_source_config(storage, "mine", "steam")
        _insert_item(storage, "gog")
        _insert_item(storage, "steam")

        migrate_source_attribution(_yaml_inputs(mine="gog"), storage)

        assert _sources(storage) == ["gog", "mine"]

    def test_an_inputs_section_that_is_not_a_mapping_is_ignored(
        self, storage: StorageManager
    ) -> None:
        """A bare ``inputs:`` key parses as ``None`` and owns nothing."""
        _insert_item(storage, "sonarr")

        migrate_source_attribution({"inputs": None}, storage)

        assert _sources(storage) == ["sonarr"]

    def test_a_source_entry_that_is_not_a_mapping_is_ignored(
        self, storage: StorageManager
    ) -> None:
        """``my_sonarr: sonarr`` is a typo, not a source block."""
        _insert_item(storage, "sonarr")

        migrate_source_attribution({"inputs": {"my_sonarr": "sonarr"}}, storage)

        assert _sources(storage) == ["sonarr"]

    def test_a_source_entry_naming_no_plugin_is_ignored(
        self, storage: StorageManager
    ) -> None:
        """``resolve_inputs`` skips a block with no ``plugin``, so this does."""
        _insert_item(storage, "sonarr")

        migrate_source_attribution({"inputs": {"my_sonarr": {}}}, storage)

        assert _sources(storage) == ["sonarr"]

    def test_the_move_reports_the_count_and_both_names(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The operator's only record of which rows changed hands."""
        _insert_item(storage, "sonarr")
        _insert_item(storage, "sonarr")

        with caplog.at_level(logging.INFO):
            migrate_source_attribution(_yaml_inputs(my_sonarr="sonarr"), storage)

        assert (
            "Re-attributed 2 content item(s) from plugin name 'sonarr' "
            "to source 'my_sonarr'" in caplog.text
        )

    def test_a_refusal_is_not_repeated_on_the_next_run(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A refused plugin is refused once, not on every boot.

        The message is spent, not the pass: removing one of the two gog sources
        resolves this, so the completion record is withheld for the run that
        would act on it.
        """
        _insert_item(storage, "gog")
        config = _yaml_inputs(gog_work="gog", gog_home="gog")

        migrate_source_attribution(config, storage)

        caplog.clear()
        with caplog.at_level(logging.INFO):
            migrate_source_attribution(config, storage)

        assert _sources(storage) == ["gog"]
        assert caplog.text == ""
        assert _completed_migrations(storage) == [
            "source_attribution.user_1.refused.gog.shared"
        ]

    def test_the_completion_marker_is_per_user(self, storage: StorageManager) -> None:
        """User 1's run must not skip user 2's, whose rows are untouched by it."""
        _insert_user(storage, 2)
        _insert_item(storage, "sonarr", user_id=1)
        _insert_item(storage, "sonarr", user_id=2)
        config = _yaml_inputs(my_sonarr="sonarr")

        migrate_source_attribution(config, storage)
        migrate_source_attribution(config, storage, user_id=2)

        assert _sources(storage) == ["my_sonarr", "my_sonarr"]

    def test_a_pass_with_nothing_left_to_do_records_itself(
        self, storage: StorageManager
    ) -> None:
        """The record is what buys the silence, so it has to be written.

        Every assertion below that no record exists means nothing without it.
        """
        _insert_item(storage, "sonarr")

        migrate_source_attribution(_yaml_inputs(my_sonarr="sonarr"), storage)

        assert _completed_migrations(storage) == ["source_attribution.user_1"]

    def test_a_boot_with_no_sources_configured_records_nothing(
        self, storage: StorageManager
    ) -> None:
        """One CLI run against a sourceless config must not spend the pass.

        ``create_storage_manager`` defaults to the real ``data/`` database
        whichever file was loaded, so an ``example.yaml`` invocation reaches a
        populated library having decided nothing about it.
        """
        _insert_item(storage, "sonarr")

        migrate_source_attribution({}, storage)

        assert _completed_migrations(storage) == []

        migrate_source_attribution(_yaml_inputs(my_sonarr="sonarr"), storage)

        assert _sources(storage) == ["my_sonarr"]

    def test_a_pass_that_dies_partway_records_nothing(
        self, storage: StorageManager
    ) -> None:
        """A failed migration must retry rather than be abandoned.

        The record is written last and nothing catches, so a move that raises
        leaves it unwritten.
        """
        _insert_item(storage, "sonarr")
        config = _yaml_inputs(my_sonarr="sonarr")

        with patch(
            "src.storage.source_migration._relabel_items",
            side_effect=RuntimeError("database is locked"),
        ):
            with pytest.raises(RuntimeError):
                migrate_source_attribution(config, storage)

        assert _completed_migrations(storage) == []

        migrate_source_attribution(config, storage)

        assert _sources(storage) == ["my_sonarr"]


class TestSourceIdAttributionRegression:
    """Reported: six plugins dropped ``_source_id``, storing items under the
    plugin name. Once the framework kept the key, a ``my_sonarr`` user had rows
    saying ``sonarr`` and new rows saying ``my_sonarr`` — two populations behind
    every filter, count and delete.
    """

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_the_two_populations_become_one(self, storage: StorageManager) -> None:
        """Pre-change rows and post-change rows end up under one label."""
        _insert_item(storage, "sonarr")
        _insert_item(storage, "my_sonarr")

        migrate_source_attribution(_yaml_inputs(my_sonarr="sonarr"), storage)

        assert _sources(storage) == ["my_sonarr", "my_sonarr"]


class TestPerpetualRefusalRegression:
    """Reported: ``gog`` and ``gog_family`` on one plugin warned about the
    ``gog`` source's own live rows on every boot, demanding a rename the app
    cannot do. Such a refusal is now reported once.
    """

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_a_fresh_install_says_nothing_about_the_items_it_syncs(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Boot, sync, boot again: the second boot has nothing to say."""
        config = _yaml_inputs(gog="gog", gog_family="gog")

        with caplog.at_level(logging.INFO):
            migrate_source_attribution(config, storage)
            _insert_item(storage, "gog")
            _insert_item(storage, "gog")
            migrate_source_attribution(config, storage)

        assert _sources(storage) == ["gog", "gog"]
        assert caplog.text == ""

    def test_a_terminal_refusal_is_silent_while_another_holds_the_pass_open(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The only arrangement in which the terminal branch reruns at all.

        ``steam`` is renameable, so the pass never records itself. Without its
        own dedup the ``gog`` note rides along on every boot — the perpetual
        nag, one branch over.
        """
        _insert_item(storage, "gog")
        _insert_item(storage, "steam")
        config = _yaml_inputs(
            gog="gog", gog_family="gog", steam="trakt", my_steam="steam"
        )

        migrate_source_attribution(config, storage)

        caplog.clear()
        with caplog.at_level(logging.INFO):
            migrate_source_attribution(config, storage)

        assert caplog.text == ""
        assert _completed_migrations(storage) == [
            "source_attribution.user_1.refused.gog.namesake",
            "source_attribution.user_1.refused.steam.renameable",
        ]


class TestUnfollowableAdviceRegression:
    """Reported: the pass named a config change and recorded itself on the same
    boot, so the change it asked for could never take effect. Both remedies it
    names are covered here.
    """

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_renaming_the_namesake_source_moves_the_rows(
        self, storage: StorageManager
    ) -> None:
        """``gog`` runs steam, so renaming it hands the rows to ``my_gog``."""
        _insert_item(storage, "gog")

        migrate_source_attribution(_yaml_inputs(gog="steam", my_gog="gog"), storage)

        assert _sources(storage) == ["gog"]

        # Exactly what the warning asked for: the namesake source renamed.
        migrate_source_attribution(
            _yaml_inputs(my_steam="steam", my_gog="gog"), storage
        )

        assert _sources(storage) == ["my_gog"]

    def test_removing_one_of_two_sharing_sources_moves_the_rows(
        self, storage: StorageManager
    ) -> None:
        """One gog source left means the rows have an unambiguous owner."""
        _insert_item(storage, "gog")

        migrate_source_attribution(
            _yaml_inputs(gog_work="gog", gog_home="gog"), storage
        )

        assert _sources(storage) == ["gog"]

        migrate_source_attribution(_yaml_inputs(gog_work="gog"), storage)

        assert _sources(storage) == ["gog_work"]

    def test_the_pass_stays_live_though_the_advice_is_said_once(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The record that goes unwritten is the one that would retire it."""
        _insert_item(storage, "gog")
        config = _yaml_inputs(gog="steam", my_gog="gog")

        migrate_source_attribution(config, storage)

        caplog.clear()
        with caplog.at_level(logging.INFO):
            migrate_source_attribution(config, storage)

        assert caplog.text == ""
        assert _completed_migrations(storage) == [
            "source_attribution.user_1.refused.gog.renameable"
        ]


class TestSwappedRefusalKindRegression:
    """Reported: the refusal record was keyed by the plugin alone, so an edit
    that traded one obstacle for another met the first refusal's silence and
    the new remedy went unsaid — with the pass live forever. Both trades here.
    """

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_dropping_a_sibling_leaves_a_rename_the_operator_is_told_about(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Removing ``b`` does what the sharing warning asked and is not enough.

        ``gog`` still runs steam, so the rows are still ambiguous — under the
        remedy the operator has never heard.
        """
        _insert_item(storage, "gog")

        migrate_source_attribution(_yaml_inputs(gog="steam", a="gog", b="gog"), storage)

        caplog.clear()
        with caplog.at_level(logging.INFO):
            migrate_source_attribution(_yaml_inputs(gog="steam", a="gog"), storage)

        assert _sources(storage) == ["gog"]
        assert "Renaming it hands every one of these rows to 'a'" in caplog.text

    def test_renaming_the_namesake_leaves_siblings_the_operator_is_told_about(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The trade in the other direction: terminal becomes resolvable.

        ``steam`` keeps the pass live so this boot happens at all, and renaming
        ``gog`` leaves two siblings sharing the plugin.
        """
        _insert_item(storage, "gog")
        _insert_item(storage, "steam")

        migrate_source_attribution(
            _yaml_inputs(gog="gog", gog_family="gog", steam="trakt", my_steam="steam"),
            storage,
        )

        caplog.clear()
        with caplog.at_level(logging.INFO):
            migrate_source_attribution(
                _yaml_inputs(
                    gog_main="gog", gog_family="gog", steam="trakt", my_steam="steam"
                ),
                storage,
            )

        assert _sources(storage) == ["gog", "steam"]
        assert "2 sources share it ('gog_family', 'gog_main')" in caplog.text
