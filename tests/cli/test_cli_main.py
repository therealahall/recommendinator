import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
from click.testing import CliRunner

from src.cli.main import cli
from src.ingestion.sync import SyncResult
from src.recommendations.engine import RecommendationEngine
from src.sources.service import resolve_inputs
from src.storage.manager import StorageManager
from tests.cli.conftest import _invoke_with_mocks
from tests.factories import make_storage_mock


def test_a_source_named_goodreads_keeps_its_items_across_boots(
    tmp_path: Path,
) -> None:
    storage = StorageManager(sqlite_path=tmp_path / "test.db")
    storage.sources.upsert(
        1, "goodreads", "goodreads_csv", {"path": "inputs/library.csv"}, enabled=True
    )
    with storage.connection() as conn:
        conn.execute(
            "INSERT INTO content_items (user_id, title, content_type, status, source) "
            "VALUES (1, 'Some Title', 'book', 'completed', 'goodreads')"
        )
        conn.commit()

    config: dict[str, Any] = {"inputs": {}}
    with (
        patch("src.cli.main.load_config", return_value=config),
        patch("src.cli.main.create_storage_manager", return_value=storage),
        patch(
            "src.cli.main.create_recommendation_engine",
            return_value=MagicMock(spec=RecommendationEngine),
        ),
    ):
        for _ in range(2):
            result = CliRunner().invoke(cli, ["status"])
            assert result.exit_code == 0, result.output

    with storage.connection() as conn:
        rows = conn.execute("SELECT source FROM content_items").fetchall()
    assert [row[0] for row in rows] == ["goodreads"]


@pytest.mark.usefixtures("registry_with_source_fakes")
class TestUpdateDbOnlySourceRegression:
    def _db_only_config(self) -> dict[str, Any]:
        return {"inputs": {}, "recommendations": {"min_rating_for_preference": 4}}

    def _seed_db_source(self, storage: StorageManager, enabled: bool = True) -> None:
        storage.sources.upsert(
            1,
            "calibre-web",
            "fake_api",
            {"user_id": "reader"},
            enabled=enabled,
        )
        storage.credentials.save(1, "calibre-web", "api_key", "top-secret")

    def _run_update(
        self,
        storage: StorageManager,
        config: dict[str, Any],
        args: list[str],
        sync_side_effect: Any,
    ) -> object:
        with (
            patch("src.cli.main.load_config", return_value=config),
            patch("src.cli.main.create_storage_manager", return_value=storage),
            patch(
                "src.cli.main.create_recommendation_engine",
                return_value=MagicMock(spec=RecommendationEngine),
            ),
            patch(
                "src.cli.commands._update.execute_multi_source_sync",
                side_effect=sync_side_effect,
            ),
        ):
            return CliRunner().invoke(cli, args)

    def test_enabled_db_only_source_syncs_end_to_end_regression(
        self, tmp_path: Path
    ) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        self._seed_db_source(storage, enabled=True)

        captured: dict[str, Any] = {}

        def _fake_sync(**kwargs: Any) -> list[SyncResult]:
            captured["sources"] = kwargs.get("sources") or []
            return [
                SyncResult(source_name=plugin.display_name)
                for plugin, _config in captured["sources"]
            ]

        result = self._run_update(
            storage,
            self._db_only_config(),
            ["update", "--source", "calibre-web"],
            _fake_sync,
        )

        assert result.exit_code == 0, result.output
        assert "Unknown" not in result.output
        sources = captured["sources"]
        assert len(sources) == 1
        _plugin, resolved_config = sources[0]
        assert resolved_config["_source_id"] == "calibre-web"
        assert resolved_config["api_key"] == "top-secret"
        assert resolved_config["user_id"] == "reader"

    def test_disabled_db_only_source_aborts_regression(self, tmp_path: Path) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        self._seed_db_source(storage, enabled=False)

        def _never(**_: Any) -> list[SyncResult]:
            raise AssertionError("sync must not run for a disabled source")

        result = self._run_update(
            storage,
            self._db_only_config(),
            ["update", "--source", "calibre-web"],
            _never,
        )

        assert result.exit_code != 0
        assert "Unknown or disabled source 'calibre-web'" in result.output

    def test_unknown_source_aborts_regression(self, tmp_path: Path) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "test.db")

        def _never(**_: Any) -> list[SyncResult]:
            raise AssertionError("sync must not run for an unknown source")

        result = self._run_update(
            storage,
            self._db_only_config(),
            ["update", "--source", "no_such_source"],
            _never,
        )

        assert result.exit_code != 0
        assert "Unknown or disabled source 'no_such_source'" in result.output

    def test_source_list_surfaces_db_only_source_regression(
        self, tmp_path: Path
    ) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        self._seed_db_source(storage, enabled=True)

        def _never(**_: Any) -> list[SyncResult]:
            raise AssertionError("list must not run a sync")

        result = self._run_update(
            storage,
            self._db_only_config(),
            ["update", "--source", "list"],
            _never,
        )

        assert result.exit_code == 0, result.output
        assert "calibre-web" in result.output
        assert "enabled" in result.output
        assert "cadence=daily" in result.output


@pytest.mark.usefixtures("registry_with_source_fakes")
class TestUpdateResolvesEachSourceOnceRegression:
    _SOURCE_IDS = ("games", "movies")

    def _seeded_storage(self, tmp_path: Path) -> StorageManager:
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        for source_id in self._SOURCE_IDS:
            storage.sources.upsert(
                1, source_id, "fake_api", {"user_id": "reader"}, enabled=True
            )
            storage.credentials.save(1, source_id, "api_key", "top-secret")
        return storage

    @pytest.mark.parametrize(
        ("args", "synced_ids"),
        [
            (["update"], list(_SOURCE_IDS)),
            (["update", "--source", "games"], ["games"]),
        ],
    )
    def test_a_sync_resolves_and_decrypts_once_per_source(
        self, tmp_path: Path, args: list[str], synced_ids: list[str]
    ) -> None:
        storage = self._seeded_storage(tmp_path)
        synced: list[str] = []
        resolve_spy = MagicMock(wraps=resolve_inputs)

        def _fake_sync(**kwargs: Any) -> list[SyncResult]:
            synced.extend(config["_source_id"] for _plugin, config in kwargs["sources"])
            return [
                SyncResult(source_name=plugin.display_name)
                for plugin, _config in kwargs["sources"]
            ]

        with (
            patch("src.cli.main.load_config", return_value={"inputs": {}}),
            patch("src.cli.main.create_storage_manager", return_value=storage),
            patch(
                "src.cli.main.create_recommendation_engine",
                return_value=MagicMock(spec=RecommendationEngine),
            ),
            patch(
                "src.cli.commands._update.execute_multi_source_sync",
                side_effect=_fake_sync,
            ),
            patch("src.cli.commands._update.resolve_inputs", resolve_spy),
            patch("src.sources.service.resolve_inputs", resolve_spy),
            patch.object(
                storage.credentials,
                "get_for_source",
                wraps=storage.credentials.get_for_source,
            ) as credential_spy,
        ):
            result = CliRunner().invoke(cli, args)

        assert result.exit_code == 0, result.output
        assert synced == synced_ids
        assert resolve_spy.call_count == 1
        decrypted_for = [call.args[1] for call in credential_spy.call_args_list]
        assert sorted(decrypted_for) == sorted(self._SOURCE_IDS)


class TestUndecodableRomNameDoesNotAbortUpdateRegression:
    @staticmethod
    def _run(storage: StorageManager, root: Path) -> Any:
        config: dict[str, Any] = {
            "inputs": {
                "roms": {"plugin": "roms", "enabled": True, "paths": [str(root)]}
            },
        }
        with (
            patch("src.cli.main.load_config", return_value=config),
            patch("src.cli.main.create_storage_manager", return_value=storage),
            patch(
                "src.cli.main.create_recommendation_engine",
                return_value=MagicMock(spec=RecommendationEngine),
            ),
        ):
            return CliRunner().invoke(cli, ["update", "--source", "roms"])

    def test_the_odd_rom_and_its_neighbours_all_land(self, tmp_path: Path) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        root = tmp_path / "roms"
        root.mkdir()
        (root / os.fsdecode(b"Metr\xffoid (USA).zip")).write_bytes(b"rom")
        (root / "Chrono Trigger.zip").write_bytes(b"rom")
        (root / "Super Mario World.zip").write_bytes(b"rom")
        assert b"Metr\xffoid (USA).zip" in os.listdir(os.fsencode(root))

        result = self._run(storage, root)

        assert result.exit_code == 0, result.output
        assert "Total: 3 of 3 items saved (3 added, 0 updated, 0 unchanged)." in (
            result.output
        )
        with storage.connection() as conn:
            rows = conn.execute(
                "SELECT ci.title, x.external_id FROM content_items ci"
                " JOIN content_item_external_ids x ON x.content_item_id = ci.id"
            ).fetchall()
        assert {row["title"] for row in rows} == {
            "Metr\\udcffoid",
            "Chrono Trigger",
            "Super Mario World",
        }
        assert len({row["external_id"] for row in rows}) == 3


def test_cli_boot_overlays_db_settings_without_seeding(tmp_path: Path) -> None:
    runner = CliRunner()
    config: dict[str, Any] = {"recommendations": {"default_count": 11}}
    storage = StorageManager(sqlite_path=tmp_path / "test.db")
    storage.settings.set("recommendations.default_count", 9)

    with (
        patch("src.cli.main.load_config", return_value=config),
        patch("src.cli.main.create_storage_manager", return_value=storage),
        patch("src.cli.main.create_recommendation_engine"),
    ):
        result = runner.invoke(cli, ["status"])

    assert result.exit_code == 0
    assert config["recommendations"]["default_count"] == 9
    assert storage.settings.list() == {"recommendations.default_count": 9}


_CONFIG_EXIT = "Error: "
_COMPONENT_EXIT = "Error initializing components: "


class TestCliBootstrapFailures:
    @staticmethod
    def _boot_failing_at(patched: str, error: Exception) -> Any:
        with (
            patch("src.cli.main.load_config", return_value={}),
            patch("src.cli.main.create_storage_manager", return_value=MagicMock()),
            patch("src.cli.main.migrate_config_settings"),
            patch("src.cli.main.migrate_config_credentials"),
            patch("src.cli.main.migrate_config_secrets"),
            patch("src.cli.main.create_recommendation_engine"),
            patch(f"src.cli.main.{patched}", side_effect=error),
        ):
            return CliRunner().invoke(cli, ["status"])

    def test_a_missing_config_file_exits_one_naming_the_fault(self) -> None:
        result = self._boot_failing_at(
            "load_config", FileNotFoundError("config.yaml not found")
        )

        assert result.exit_code == 1
        assert result.stderr.startswith(
            f"{_CONFIG_EXIT}FileNotFoundError: config.yaml not found"
        )
        assert result.stdout == ""

    def test_a_url_borne_token_does_not_reach_the_terminal(self) -> None:
        token = "sk-live-9f3c2a"

        result = self._boot_failing_at(
            "create_recommendation_engine",
            requests.ConnectionError(
                f"HTTPConnectionPool: /api/tags?api_key={token} refused"
            ),
        )

        assert result.exit_code == 1
        assert result.stderr == f"{_COMPONENT_EXIT}ConnectionError\n"
        assert token not in result.output


class TestAMalformedInputsBlockDoesNotAbortTheBoot:
    def test_a_read_only_command_still_runs(self, cli_runner: CliRunner) -> None:
        storage = make_storage_mock()
        config: dict[str, Any] = {"inputs": [{"plugin": "gog", "enabled": True}]}

        result = _invoke_with_mocks(cli_runner, ["status"], storage, config=config)

        assert result.exit_code == 0, result.output
        assert "Components:" in result.output
