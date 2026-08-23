"""Tests for CLI commands."""

from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from click.testing import CliRunner, Result

from src.cli.main import cli
from src.ingestion.sync import SyncResult
from src.models.content import (
    MAX_CREATOR_LENGTH,
    MAX_REVIEW_LENGTH,
    MAX_TITLE_LENGTH,
    ConsumptionStatus,
    ContentItem,
    ContentType,
)
from src.models.user_preferences import UserPreferenceConfig
from src.recommendations.engine import RecommendationEngine
from src.recommendations.record import Recommendation
from src.storage.manager import StorageManager
from tests.cli.conftest import _invoke_with_mocks
from tests.factories import (
    back_mock_preference_store,
    back_mock_settings_store,
    make_item,
    spec_sub_stores,
)


@pytest.fixture
def mock_config():
    """Create a mock configuration."""
    return {
        "storage": {"database_path": "data/test.db"},
        "inputs": {
            "goodreads_rss": {
                "plugin": "goodreads_rss",
                "user_id": "12345",
                "enabled": True,
            }
        },
        "recommendations": {
            "min_rating_for_preference": 4,
        },
    }


@pytest.fixture
def mock_components(mock_config):
    """Create mock components."""
    with (
        patch("src.cli.main.load_config", return_value=mock_config),
        patch("src.cli.main.create_storage_manager") as mock_storage,
        patch("src.cli.main.create_recommendation_engine") as mock_engine,
        patch("src.cli.main.migrate_config_credentials"),
    ):
        # Setup mocks
        mock_storage_manager = Mock(spec=StorageManager)
        spec_sub_stores(mock_storage_manager)
        mock_storage_manager.credentials.get_for_source.return_value = {}
        mock_storage_manager.sources.list.return_value = []
        # Let the real migrate_config_settings boot hook run against an empty
        # settings store (no stub) — the DB overlay is a no-op and nothing
        # leaks across tests.
        back_mock_settings_store(mock_storage_manager)
        mock_storage.return_value = mock_storage_manager

        mock_engine_instance = Mock(spec=RecommendationEngine)
        mock_engine.return_value = mock_engine_instance

        yield {
            "storage": mock_storage_manager,
            "engine": mock_engine_instance,
        }


def test_recommend_command_basic(mock_components):
    """Test basic recommend command."""
    # Setup mock recommendations
    mock_item = ContentItem(
        id="1",
        title="Test Book",
        author="Test Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )

    mock_recommendations = [
        Recommendation(
            item=mock_item,
            score=0.85,
            reasoning="Recommended highly similar to items you've enjoyed",
        )
    ]

    # Mock storage to return consumed items
    mock_components["storage"].get_completed_items.return_value = [
        ContentItem(
            id="2",
            title="Read Book",
            author="Author",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
    ]
    mock_components["storage"].get_unconsumed_items.return_value = [mock_item]

    mock_components["engine"].generate_recommendations.return_value = (
        mock_recommendations
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["recommend", "--type", "book", "--count", "1"])

    assert result.exit_code == 0
    assert "Test Book" in result.output
    assert "Test Author" in result.output


def test_recommend_command_surfaces_variety_penalty(mock_components):
    """The variety penalty appears in JSON output and the table reasoning."""
    mock_item = ContentItem(
        id="1",
        title="Penalised Book",
        author="Author",
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )
    mock_recommendations = [
        Recommendation(
            item=mock_item, score=0.2, reasoning="Recommended", variety_penalty=0.64
        )
    ]
    mock_components["storage"].get_completed_items.return_value = [
        ContentItem(
            id="2",
            title="Read",
            author="Author",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            rating=5,
        )
    ]
    mock_components["storage"].get_unconsumed_items.return_value = [mock_item]
    mock_components["engine"].generate_recommendations.return_value = (
        mock_recommendations
    )

    runner = CliRunner()
    json_result = runner.invoke(
        cli, ["recommend", "--type", "book", "--count", "1", "--format", "json"]
    )
    assert json_result.exit_code == 0
    assert '"variety_penalty": 0.64' in json_result.output

    table_result = runner.invoke(cli, ["recommend", "--type", "book", "--count", "1"])
    assert table_result.exit_code == 0
    assert "variety penalty -64%" in table_result.output


class TestCompleteCommandCreator:
    """Regression: ``complete --author`` was dropped for everything but books.

    Bug reported: ``complete --type movie --title Arrival --author "Denis
    Villeneuve"`` reported the movie completed and stored no director.
    Root cause: the command passed ``author`` through for books alone,
    because no other content type had anywhere to keep a creator.
    Fix: every type stores its creator in the column its type declares, so
    the command hands the value over whatever the type. The web door's half
    of this is in ``tests/test_web_api.py``.
    """

    def test_complete_stores_a_movie_director_regression(self, tmp_path: Path) -> None:
        """A director given to the command is the stored movie's author."""
        storage = StorageManager(sqlite_path=tmp_path / "creator.db")

        result = _invoke_with_mocks(
            CliRunner(),
            [
                "complete",
                "--type",
                "movie",
                "--title",
                "Arrival",
                "--author",
                "Denis Villeneuve",
            ],
            storage,
        )

        assert result.exit_code == 0, result.output
        stored = storage.get_content_items(content_type=ContentType.MOVIE)
        assert [item.author for item in stored] == ["Denis Villeneuve"]


class TestCompleteCommandDate:
    """Regression tests for the date `complete` records.

    Bug reported: two of them, both about a date the user did not choose. An
    item finished at 21:00 in America/Los_Angeles was dated tomorrow, because
    the command stamped ``datetime.now(UTC).date()``; and completing an item
    imported with ``date_completed = 2020-01-01`` rewrote that date to today,
    because the command stamped a date at all and the sync door's
    later-date-wins rule takes today over any past date. Both feed the variety
    ladder's ordering, and the second is silent loss of a date the user owns.
    Root cause: the command decided the completion date itself, in UTC, and
    handed it to a door whose rule is "later wins".
    Fix: the command sends no date. The storage door fills an empty one with
    today in the host's zone and keeps a date the item already carries.
    """

    def test_complete_dates_by_the_host_calendar_day_regression(
        self, tmp_path: Path, host_timezone
    ) -> None:
        """An evening completion is dated the day the user is living."""
        host_timezone("America/Los_Angeles")
        storage = StorageManager(sqlite_path=tmp_path / "complete.db")

        with patch(
            "src.utils.dates.utc_now",
            return_value=datetime(2026, 3, 15, 4, 0, tzinfo=UTC),
        ):
            result = _invoke_with_mocks(
                CliRunner(),
                ["complete", "--type", "book", "--title", "Piranesi"],
                storage,
            )

        assert result.exit_code == 0, result.output
        stored = storage.get_content_items(content_type=ContentType.BOOK)
        assert len(stored) == 1
        assert stored[0].date_completed == date(2026, 3, 14)

    def test_complete_preserves_an_imported_completion_date_regression(
        self, tmp_path: Path
    ) -> None:
        """Completing an item that already has a date does not re-date it."""
        storage = StorageManager(sqlite_path=tmp_path / "complete.db")
        db_id = storage.save_content_item(
            ContentItem(
                id="book-1",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                date_completed=date(2020, 1, 1),
            )
        )

        result = _invoke_with_mocks(
            CliRunner(),
            ["complete", "--type", "book", "--title", "Dune", "--rating", "4"],
            storage,
        )

        assert result.exit_code == 0, result.output
        stored = storage.get_content_item(db_id)
        assert stored is not None
        assert stored.date_completed == date(2020, 1, 1)
        assert stored.rating == 4


class TestCompleteCommandUserOwnedFields:
    """Regression tests for `complete` discarding an explicit rating/review.

    Bug reported: `complete --type book --title Dune --rating 2` against a
    library where Dune is already rated 5 prints "Marked 'Dune' as completed"
    and exits 0, but `library show` still reports rating 5. The same holds for
    `--review`. The user believes they corrected their taste signal;
    preference analysis keeps scoring on the stale value.
    Root cause: the command persisted through
    ``StorageManager.save_content_item`` — the ingestion/sync door, whose
    fill-only rule never overwrites a user-owned field that already has a
    value. `complete` is an explicit user action, so under the user-owned
    fields rule its rating and review must win.
    Fix: an explicit completion goes through ``complete_content_item``, the
    storage door that applies the explicit-action rules, so the value the user
    typed is the value stored.
    """

    def _seeded_storage(self, tmp_path: Path) -> tuple[StorageManager, int]:
        """A real temp-DB storage holding one rated, reviewed book."""
        storage = StorageManager(sqlite_path=tmp_path / "complete.db")
        db_id = storage.save_content_item(
            ContentItem(
                id="book-1",
                title="Dune",
                author="Frank Herbert",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                review="Loved it",
            )
        )
        return storage, db_id

    def test_complete_overwrites_existing_rating_regression(self, tmp_path):
        """An explicit `complete --rating` replaces the stored rating."""
        storage, db_id = self._seeded_storage(tmp_path)

        result = _invoke_with_mocks(
            CliRunner(),
            ["complete", "--type", "book", "--title", "Dune", "--rating", "2"],
            storage,
        )

        assert result.exit_code == 0, result.output
        stored = storage.get_content_item(db_id)
        assert stored is not None
        assert stored.rating == 2

    def test_complete_overwrites_existing_review_regression(self, tmp_path):
        """An explicit `complete --review` replaces the stored review."""
        storage, db_id = self._seeded_storage(tmp_path)

        result = _invoke_with_mocks(
            CliRunner(),
            [
                "complete",
                "--type",
                "book",
                "--title",
                "Dune",
                "--review",
                "On reflection, overrated",
            ],
            storage,
        )

        assert result.exit_code == 0, result.output
        stored = storage.get_content_item(db_id)
        assert stored is not None
        assert stored.review == "On reflection, overrated"


def test_complete_refuses_a_review_over_the_bound_regression(tmp_path: Path) -> None:
    """`complete` refuses a review over MAX_REVIEW_LENGTH and stores one at it.

    Bug reported: `library edit` and both write endpoints refuse a longer
    review, but `complete` stored one the web edit dialog then could not save.
    """
    storage = StorageManager(sqlite_path=tmp_path / "complete.db")

    def complete_with(review: str) -> Result:
        return _invoke_with_mocks(
            CliRunner(),
            ["complete", "--type", "book", "--title", "Piranesi", "--review", review],
            storage,
        )

    refused = complete_with("x" * (MAX_REVIEW_LENGTH + 1))

    assert refused.exit_code != 0
    assert f"at most {MAX_REVIEW_LENGTH} characters" in refused.output
    assert storage.get_content_items(content_type=ContentType.BOOK) == []

    accepted = complete_with("x" * MAX_REVIEW_LENGTH)

    assert accepted.exit_code == 0, accepted.output
    stored = storage.get_content_items(content_type=ContentType.BOOK)
    assert [len(item.review or "") for item in stored] == [MAX_REVIEW_LENGTH]


def test_complete_refuses_a_title_or_author_the_web_door_would_refuse(
    tmp_path: Path,
) -> None:
    """`complete` bounds --title and --author where CompletionRequest does."""
    storage = StorageManager(sqlite_path=tmp_path / "complete-bounds.db")

    def complete_with(title: str, author: str) -> Result:
        return _invoke_with_mocks(
            CliRunner(),
            ["complete", "--type", "book", "--title", title, "--author", author],
            storage,
        )

    long_title = complete_with("x" * (MAX_TITLE_LENGTH + 1), "Susanna Clarke")

    assert long_title.exit_code != 0
    assert f"at most {MAX_TITLE_LENGTH} characters" in long_title.output

    long_author = complete_with("Piranesi", "x" * (MAX_CREATOR_LENGTH + 1))

    assert long_author.exit_code != 0
    assert f"at most {MAX_CREATOR_LENGTH} characters" in long_author.output
    assert storage.get_content_items(content_type=ContentType.BOOK) == []

    accepted = complete_with("x" * MAX_TITLE_LENGTH, "x" * MAX_CREATOR_LENGTH)

    assert accepted.exit_code == 0, accepted.output
    assert len(storage.get_content_items(content_type=ContentType.BOOK)) == 1


@pytest.mark.parametrize("title", ["   ", "\udcff\udcfe"])
def test_complete_refuses_a_blank_title_the_web_door_would_refuse(
    tmp_path: Path, title: str
) -> None:
    """A blank title stored a library row nothing could name or find.

    Undecodable bytes are the same case: the group strips them to ``""``
    upstream of every guard, so both reach one rule.
    """
    storage = StorageManager(sqlite_path=tmp_path / "complete-blank-title.db")

    result = _invoke_with_mocks(
        CliRunner(), ["complete", "--type", "book", "--title", title], storage
    )

    assert result.exit_code != 0
    assert "--title" in result.output
    assert storage.get_content_items(content_type=ContentType.BOOK) == []


def test_complete_command_invalid_rating(mock_components):
    """Test complete command with invalid rating."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "complete",
            "--type",
            "book",
            "--title",
            "Test Book",
            "--rating",
            "6",
        ],
    )

    assert result.exit_code != 0
    assert "Rating must be between 1 and 5" in result.output


def test_update_command_steam_success(mock_components):
    """Test update command with Steam source."""
    # Update mock config to include Steam
    mock_config = {
        "storage": {"database_path": "data/test.db"},
        "inputs": {
            "steam": {
                "plugin": "steam",
                "api_key": "test_api_key",
                "steam_id": "76561198000000000",
                "enabled": True,
            }
        },
        "recommendations": {
            "min_rating_for_preference": 4,
        },
    }

    mock_steam_item = ContentItem(
        id="12345",
        title="Test Game",
        author=None,
        content_type=ContentType.VIDEO_GAME,
        status=ConsumptionStatus.COMPLETED,
        rating=4,
    )

    with (
        patch("src.cli.main.load_config", return_value=mock_config),
        patch(
            "src.ingestion.sources.steam.SteamPlugin.fetch",
            return_value=iter([mock_steam_item]),
        ),
        patch(
            "src.ingestion.sources.steam.SteamPlugin.validate_config",
            return_value=[],
        ),
    ):
        mock_components["storage"].save_content_item.return_value = 1

        runner = CliRunner()
        result = runner.invoke(cli, ["update", "--source", "steam"])

        assert result.exit_code == 0
        assert "Updated" in result.output or "updated" in result.output
        assert "Steam" in result.output


def test_update_command_steam_api_error(mock_components):
    """Test update command with Steam API error."""
    from src.ingestion.plugin_base import SourceError

    mock_config = {
        "storage": {"database_path": "data/test.db"},
        "inputs": {
            "steam": {
                "plugin": "steam",
                "api_key": "test_api_key",
                "steam_id": "76561198000000000",
                "enabled": True,
            }
        },
        "recommendations": {
            "min_rating_for_preference": 4,
        },
    }

    with (
        patch("src.cli.main.load_config", return_value=mock_config),
        patch(
            "src.ingestion.sources.steam.SteamPlugin.fetch",
            side_effect=SourceError("steam", "API error"),
        ),
        patch(
            "src.ingestion.sources.steam.SteamPlugin.validate_config",
            return_value=[],
        ),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["update", "--source", "steam"])

        assert result.exit_code == 0
        # A SourceError is our own wording, and it names what to fix, so the
        # per-source warning carries it (see src/ingestion/sync.py).
        assert "Warning: API error" in result.output
        assert "No items were updated" in result.output


class TestUpdateWorkersFlag:
    """Tests for the parallel-sync --workers flag (issue #45).

    The CLI must (1) accept --workers N to override the worker pool size,
    (2) fall back to config['sync']['max_workers'] when the flag is
    omitted, (3) default to 4 when neither is configured, and (4) forward
    the resolved value to execute_multi_source_sync so the underlying
    ThreadPoolExecutor sizes correctly.
    """

    @pytest.fixture(autouse=True)
    def _isolated_db(self, tmp_path: Path) -> None:
        """Give each test its own SQLite DB.

        These tests drive the real StorageManager + real settings-migration
        boot hook (load_config is patched but create_storage_manager is not),
        so a shared on-disk DB would leak seeded ``sync.max_workers`` leaves
        across tests. Pointing each test at a temp DB keeps the real hook
        running while isolating its state.
        """
        self._db_path = tmp_path / "test.db"

    def _config_with_sources(
        self,
        sync_block: dict | None = None,
    ) -> dict:
        config: dict = {
            "storage": {"database_path": str(self._db_path)},
            "inputs": {
                "steam": {
                    "plugin": "steam",
                    "api_key": "test_api_key",
                    "steam_id": "76561198000000000",
                    "enabled": True,
                },
                "goodreads_rss": {
                    "plugin": "goodreads_rss",
                    "user_id": "12345",
                    "enabled": True,
                },
            },
            "recommendations": {
                "min_rating_for_preference": 4,
            },
        }
        if sync_block is not None:
            config["sync"] = sync_block
        return config

    def test_workers_flag_overrides_config(self) -> None:
        """--workers overrides config['sync']['max_workers']."""
        config = self._config_with_sources(sync_block={"max_workers": 2})

        captured: dict = {}

        def fake_execute(
            **kwargs: object,
        ) -> list:
            captured.update(kwargs)
            sources_arg = kwargs.get("sources") or []
            return [
                SyncResult(source_name=plugin.display_name)
                for plugin, _config in sources_arg  # type: ignore[misc]
            ]

        with (
            patch("src.cli.main.load_config", return_value=config),
            patch(
                "src.cli.commands._update.execute_multi_source_sync",
                side_effect=fake_execute,
            ),
            patch(
                "src.ingestion.sources.steam.SteamPlugin.validate_config",
                return_value=[],
            ),
            patch(
                "src.ingestion.sources.goodreads_rss.GoodreadsRssPlugin.validate_config",
                return_value=[],
            ),
        ):
            runner = CliRunner()
            result = runner.invoke(cli, ["update", "--workers", "8"])

        assert result.exit_code == 0, result.output
        assert captured["max_workers"] == 8

    def test_workers_falls_back_to_config(self) -> None:
        """Without --workers, config['sync']['max_workers'] is used."""
        config = self._config_with_sources(sync_block={"max_workers": 6})

        captured: dict = {}

        def fake_execute(**kwargs: object) -> list:
            captured.update(kwargs)
            sources_arg = kwargs.get("sources") or []
            return [
                SyncResult(source_name=plugin.display_name)
                for plugin, _config in sources_arg  # type: ignore[misc]
            ]

        with (
            patch("src.cli.main.load_config", return_value=config),
            patch(
                "src.cli.commands._update.execute_multi_source_sync",
                side_effect=fake_execute,
            ),
            patch(
                "src.ingestion.sources.steam.SteamPlugin.validate_config",
                return_value=[],
            ),
            patch(
                "src.ingestion.sources.goodreads_rss.GoodreadsRssPlugin.validate_config",
                return_value=[],
            ),
        ):
            runner = CliRunner()
            result = runner.invoke(cli, ["update"])

        assert result.exit_code == 0, result.output
        assert captured["max_workers"] == 6

    def test_workers_defaults_to_four_when_unset(self) -> None:
        """No --workers and no config => default 4."""
        config = self._config_with_sources()  # no sync block

        captured: dict = {}

        def fake_execute(**kwargs: object) -> list:
            captured.update(kwargs)
            sources_arg = kwargs.get("sources") or []
            return [
                SyncResult(source_name=plugin.display_name)
                for plugin, _config in sources_arg  # type: ignore[misc]
            ]

        with (
            patch("src.cli.main.load_config", return_value=config),
            patch(
                "src.cli.commands._update.execute_multi_source_sync",
                side_effect=fake_execute,
            ),
            patch(
                "src.ingestion.sources.steam.SteamPlugin.validate_config",
                return_value=[],
            ),
            patch(
                "src.ingestion.sources.goodreads_rss.GoodreadsRssPlugin.validate_config",
                return_value=[],
            ),
        ):
            runner = CliRunner()
            result = runner.invoke(cli, ["update"])

        assert result.exit_code == 0, result.output
        assert captured["max_workers"] == 4


def test_update_records_the_run_it_just_finished(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    config = {
        "storage": {"database_path": str(db_path)},
        "inputs": {
            "steam": {
                "plugin": "steam",
                "api_key": "test_api_key",
                "steam_id": "76561198000000000",
                "enabled": True,
            }
        },
    }
    games = [make_item("Game", ContentType.VIDEO_GAME, item_id="g1")]

    with (
        patch("src.cli.main.load_config", return_value=config),
        patch(
            "src.ingestion.sources.steam.SteamPlugin.fetch", return_value=iter(games)
        ),
        patch(
            "src.ingestion.sources.steam.SteamPlugin.validate_config", return_value=[]
        ),
    ):
        result = CliRunner().invoke(cli, ["update", "--source", "steam"])

    assert result.exit_code == 0, result.output
    run = StorageManager(sqlite_path=db_path).sync_runs.latest_per_source(1)["steam"]
    assert run["status"] == "completed"


def test_preferences_get(mock_components):
    """Test preferences get command."""
    mock_components["storage"].get_user_preference_config = Mock(
        return_value=UserPreferenceConfig(scorer_weights={"genre_match": 3.0})
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["preferences", "get", "--format", "json"])

    assert result.exit_code == 0
    assert "genre_match" in result.output
    assert "3.0" in result.output


def test_preferences_set_variety(mock_components):
    """Test setting the numeric variety penalty via set-variety."""
    config = UserPreferenceConfig()
    back_mock_preference_store(mock_components["storage"], config)

    runner = CliRunner()
    result = runner.invoke(cli, ["preferences", "set-variety", "5.0"])

    assert result.exit_code == 0
    assert "Set variety_penalty to 5.0" in result.output
    assert config.variety_penalty == 5.0


def test_preferences_set_variety_rejects_out_of_range(mock_components):
    """A value above the 5.0 maximum is rejected with a non-zero exit and no save."""
    merge = back_mock_preference_store(mock_components["storage"])

    runner = CliRunner()
    result = runner.invoke(cli, ["preferences", "set-variety", "6.0"])

    assert result.exit_code != 0
    # The rejection must name both ends of the accepted range.
    assert "0.0" in result.output
    assert "5.0" in result.output
    merge.assert_not_called()


def test_preferences_set_toggle_off(mock_components):
    """Test disabling a toggle via set-toggle."""
    config = UserPreferenceConfig(series_in_order=True)
    back_mock_preference_store(mock_components["storage"], config)

    runner = CliRunner()
    result = runner.invoke(cli, ["preferences", "set-toggle", "series_in_order", "off"])

    assert result.exit_code == 0
    assert "Set series_in_order off" in result.output
    assert config.series_in_order is False


def test_custom_rules_add(mock_components):
    """Test adding a custom rule."""
    mock_config = UserPreferenceConfig()
    back_mock_preference_store(mock_components["storage"], mock_config)

    runner = CliRunner()
    result = runner.invoke(cli, ["preferences", "custom-rules", "add", "avoid horror"])

    assert result.exit_code == 0
    assert "Added rule" in result.output
    assert "avoid horror" in result.output
    assert mock_config.custom_rules == ["avoid horror"]


def test_custom_rules_add_refuses_an_over_long_rule(mock_components):
    """A rule the Preferences page could not save back is refused here too."""
    merge = back_mock_preference_store(mock_components["storage"])

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "preferences",
            "custom-rules",
            "add",
            "r" * (UserPreferenceConfig.MAX_CUSTOM_RULE_LENGTH + 1),
        ],
    )

    assert result.exit_code != 0
    assert "at most" in result.output
    merge.assert_not_called()


def test_custom_rules_add_refuses_one_rule_past_the_bound(mock_components):
    """The list itself is bounded, since the CLI appends and the web merges."""
    stored = UserPreferenceConfig(
        custom_rules=["avoid horror"] * UserPreferenceConfig.MAX_CUSTOM_RULES
    )
    back_mock_preference_store(mock_components["storage"], stored)

    runner = CliRunner()
    result = runner.invoke(cli, ["preferences", "custom-rules", "add", "prefer sci-fi"])

    assert result.exit_code != 0
    assert "Remove one first" in result.output
    assert "prefer sci-fi" not in stored.custom_rules


def test_custom_rules_list_with_rules(mock_components):
    """Test listing custom rules when some exist."""
    mock_config = UserPreferenceConfig(custom_rules=["avoid horror", "prefer sci-fi"])
    mock_components["storage"].get_user_preference_config = Mock(
        return_value=mock_config
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["preferences", "custom-rules", "list"])

    assert result.exit_code == 0
    assert "0: avoid horror" in result.output
    assert "1: prefer sci-fi" in result.output


def test_custom_rules_remove(mock_components):
    """Test removing a custom rule."""
    mock_config = UserPreferenceConfig(custom_rules=["avoid horror"])
    back_mock_preference_store(mock_components["storage"], mock_config)

    runner = CliRunner()
    result = runner.invoke(cli, ["preferences", "custom-rules", "remove", "0"])

    assert result.exit_code == 0
    assert "Removed rule: 'avoid horror'" in result.output
    assert mock_config.custom_rules == []


def test_custom_rules_remove_invalid_index(mock_components):
    """Test removing a rule with invalid index."""
    merge = back_mock_preference_store(mock_components["storage"])

    runner = CliRunner()
    result = runner.invoke(cli, ["preferences", "custom-rules", "remove", "99"])

    assert result.exit_code != 0
    assert "Invalid index" in result.output
    merge.assert_called_once()


def test_custom_rules_clear(mock_components):
    """Test clearing all custom rules."""
    mock_config = UserPreferenceConfig(custom_rules=["avoid horror", "prefer sci-fi"])
    back_mock_preference_store(mock_components["storage"], mock_config)

    runner = CliRunner()
    result = runner.invoke(cli, ["preferences", "custom-rules", "clear", "--yes"])

    assert result.exit_code == 0
    assert "Cleared 2" in result.output
    assert mock_config.custom_rules == []


def test_custom_rules_interpret_pattern(mock_components):
    """Test interpreting a rule using pattern matcher."""
    runner = CliRunner()
    result = runner.invoke(
        cli, ["preferences", "custom-rules", "interpret", "avoid horror"]
    )

    assert result.exit_code == 0
    assert "Rule: 'avoid horror'" in result.output
    assert "horror" in result.output


class TestPreferencesResetConfirms:
    """``reset`` wipes every preference a user has, so it asks first."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        storage = StorageManager(sqlite_path=tmp_path / "reset.db")
        storage.save_user_preference_config(
            1,
            UserPreferenceConfig(
                custom_rules=["avoid horror", "prefer sci-fi", "no sequels"],
                scorer_weights={"genre_match": 3.0},
                variety_penalty=4.0,
            ),
        )
        return storage

    @pytest.mark.parametrize("answer", ["n\n", "\n"])
    def test_neither_no_nor_a_bare_enter_resets(
        self, storage: StorageManager, answer: str
    ) -> None:
        before = storage.get_user_preference_config(1)

        result = _invoke_with_mocks(
            CliRunner(), ["preferences", "reset"], storage, input_text=answer
        )

        assert result.exit_code == 0
        # The prompt sizes the loss rather than just asking.
        assert "3" in result.output
        assert storage.get_user_preference_config(1) == before

    def test_yes_resets_without_prompting(self, storage: StorageManager) -> None:
        """No stdin to answer with: a prompt here would abort instead."""
        result = _invoke_with_mocks(
            CliRunner(), ["preferences", "reset", "--yes"], storage
        )

        assert result.exit_code == 0
        assert storage.get_user_preference_config(1) == UserPreferenceConfig()


def test_set_length_preference(mock_components):
    """Test setting a length preference."""
    mock_config = UserPreferenceConfig()
    back_mock_preference_store(mock_components["storage"], mock_config)

    runner = CliRunner()
    result = runner.invoke(cli, ["preferences", "set-length", "book", "short"])

    assert result.exit_code == 0
    assert "book" in result.output
    assert "short" in result.output
    assert mock_config.content_length_preferences == {"book": "short"}


class TestPreferenceWritesTheStoreRefusesRegression:
    """Reported: ``--user 999`` printed success and persisted nothing, and
    ``set-weight genre_match inf`` stored a value every later read of the
    Preferences page then answered 500 for. Both doors now close in
    ``StorageManager``, the one site each interface's writes pass through.
    """

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """A real store: the defect is which rows the UPDATE matched."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_an_unknown_user_is_reported_rather_than_silently_dropped(
        self, storage: StorageManager
    ) -> None:
        result = _invoke_with_mocks(
            CliRunner(),
            ["preferences", "set-weight", "genre_match", "3.0", "--user", "999"],
            storage,
        )

        assert result.exit_code != 0
        assert "No user with id 999" in result.output
        assert storage.get_user_preference_config(999).scorer_weights == {}

    @pytest.mark.parametrize("literal", ["inf", "nan"])
    def test_a_non_finite_weight_is_refused(
        self, storage: StorageManager, literal: str
    ) -> None:
        """Click's ``float`` takes these, and the API's guard is HTTP-side."""
        result = _invoke_with_mocks(
            CliRunner(),
            ["preferences", "set-weight", "genre_match", literal],
            storage,
        )

        assert result.exit_code != 0
        assert "must be a finite number" in result.output
        assert storage.get_user_preference_config(1).scorer_weights == {}

    def test_the_same_write_lands_for_a_user_that_exists(
        self, storage: StorageManager
    ) -> None:
        """Without this the refusals above would hold on a store that never
        writes at all.
        """
        result = _invoke_with_mocks(
            CliRunner(),
            ["preferences", "set-weight", "genre_match", "3.0"],
            storage,
        )

        assert result.exit_code == 0
        assert storage.get_user_preference_config(1).scorer_weights == {
            "genre_match": 3.0
        }

    @pytest.mark.parametrize(
        "command",
        [
            pytest.param(
                ["preferences", "set-toggle", "series_in_order", "off"], id="set-toggle"
            ),
            pytest.param(["preferences", "set-variety", "2.0"], id="set-variety"),
            pytest.param(
                ["preferences", "set-length", "book", "short"], id="set-length"
            ),
            pytest.param(
                ["preferences", "custom-rules", "add", "prefer sci-fi"],
                id="custom-rules-add",
            ),
        ],
    )
    def test_every_other_edit_names_the_unknown_user_too(
        self, storage: StorageManager, command: list[str]
    ) -> None:
        """One door in four more places: they all write via ``_edit_preferences``."""
        result = _invoke_with_mocks(CliRunner(), [*command, "--user", "999"], storage)

        assert result.exit_code != 0
        assert "No user with id 999" in result.output
        assert storage.get_user_preference_config(999) == UserPreferenceConfig()

    def test_removing_a_rule_refuses_before_it_reaches_the_write(
        self, storage: StorageManager
    ) -> None:
        """The index check answers first, so this one never names the user."""
        result = _invoke_with_mocks(
            CliRunner(),
            ["preferences", "custom-rules", "remove", "0", "--user", "999"],
            storage,
        )

        assert result.exit_code != 0
        assert "Invalid index 0" in result.output

    def test_reset_names_the_unknown_user_too(self, storage: StorageManager) -> None:
        """The one write that does not read first, so it took another path."""
        result = _invoke_with_mocks(
            CliRunner(), ["preferences", "reset", "--user", "999", "--yes"], storage
        )

        assert result.exit_code != 0
        assert "No user with id 999" in result.output

    def test_custom_rules_clear_names_the_unknown_user_too(
        self, storage: StorageManager
    ) -> None:
        """It returned early on the empty rule list defaults answer for an id
        no ``users`` row carries, so it never reached the write that refuses.
        """
        result = _invoke_with_mocks(
            CliRunner(),
            ["preferences", "custom-rules", "clear", "--user", "999", "--yes"],
            storage,
        )

        assert result.exit_code != 0
        assert "No user with id 999" in result.output

    def test_clearing_nothing_for_a_real_user_still_says_so(
        self, storage: StorageManager
    ) -> None:
        """The refusal above must not have cost the empty-list message."""
        result = _invoke_with_mocks(
            CliRunner(), ["preferences", "custom-rules", "clear", "--yes"], storage
        )

        assert result.exit_code == 0
        assert "No custom rules to clear for user 1" in result.output


class TestConfigLoadingRegression:
    """Regression tests for configuration loading bugs."""

    def test_load_config_prefers_config_yaml_over_example_regression(self, tmp_path):
        """Regression test: load_config should prefer config.yaml over example.yaml.

        Bug reported: User had Steam enabled in config/config.yaml but web app
        was loading config/example.yaml (where Steam is disabled).

        Root cause: get_app() in web/app.py was explicitly defaulting to
        example.yaml instead of letting load_config() handle the default
        logic (which correctly tries config.yaml first).

        Fix: Removed the explicit example.yaml default from get_app().
        """

        from src.config.service import load_config

        # Create a config directory with both files
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        # config.yaml has steam enabled
        config_yaml = config_dir / "config.yaml"
        config_yaml.write_text(
            """
inputs:
  steam:
    enabled: true
    api_key: "test"
"""
        )

        # example.yaml has steam disabled
        example_yaml = config_dir / "example.yaml"
        example_yaml.write_text(
            """
inputs:
  steam:
    enabled: false
"""
        )

        # When load_config is called with None, it should use config.yaml
        # We need to temporarily change the working directory
        import os

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            config = load_config(None)
            steam_enabled = config.get("inputs", {}).get("steam", {}).get("enabled")
            assert steam_enabled is True, (
                "load_config should prefer config.yaml (steam enabled) "
                "over example.yaml (steam disabled)"
            )
        finally:
            os.chdir(original_cwd)
