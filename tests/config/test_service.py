import inspect
import logging
import os
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

import src.settings
from src.config.service import (
    _SCORER_CONFIG_MAP,
    BOOTSTRAP_WEB_HOST,
    BOOTSTRAP_WEB_PORT,
    auto_enrich_enabled,
    build_scorers_from_config,
    load_config,
    resolve_bootstrap_web,
)
from src.recommendations.scorers import SCORER_NAME_MAP, Scorer
from src.settings.metadata import default_config, default_of, flat_defaults
from src.storage.manager import StorageManager
from src.storage.settings_migration import migrate_config_settings
from src.utils.dotted_path import get_leaf
from src.web.app import create_app
from src.web.state import app_state

_ENGINE_MANAGED_SCORERS = {"custom_preference"}

_SRC = Path(__file__).resolve().parents[2] / "src"


@pytest.fixture()
def example_config() -> dict[str, Any]:
    return load_config(Path("config/example.yaml"))


class TestLoadConfigDefaults:
    def test_trimmed_example_resolves_every_registry_leaf(
        self, example_config: dict[str, Any]
    ) -> None:
        sentinel = object()
        for key, expected in flat_defaults().items():
            resolved = get_leaf(example_config, tuple(key.split(".")), sentinel)
            assert resolved == expected, f"{key} did not resolve to its default"

    def test_yaml_overrides_const_default(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text("recommendations:\n  default_count: 11\n")

        config = load_config(config_path)

        assert config["recommendations"]["default_count"] == 11
        assert (
            config["recommendations"]["max_count"]
            == default_config()["recommendations"]["max_count"]
        )

    def test_db_overlay_wins_over_loaded_defaults(self, tmp_path: Path) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        storage.settings.set("recommendations.default_count", 9)

        config = load_config(Path("config/example.yaml"))
        assert (
            config["recommendations"]["default_count"]
            == default_config()["recommendations"]["default_count"]
        )

        migrate_config_settings(config, storage)

        assert config["recommendations"]["default_count"] == 9


class TestResolveBootstrapWeb:
    @pytest.mark.parametrize("port", [0, 65535])
    def test_usable_port_is_preserved(self, port: int) -> None:
        assert resolve_bootstrap_web({"web": {"port": port}}).port == port

    def test_unusable_value_is_reported_not_swallowed(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="src.config.service"):
            resolved = resolve_bootstrap_web(
                {"web": {"host": 8080, "port": "8O80", "debug": "true"}}
            )

        assert resolved == (BOOTSTRAP_WEB_HOST, BOOTSTRAP_WEB_PORT, False)
        assert any("web.host" in m and "8080" in m for m in caplog.messages)
        assert any("web.port" in m and "8O80" in m for m in caplog.messages)
        assert any("web.debug" in m and "true" in m for m in caplog.messages)

    def test_warn_false_suppresses_the_log_but_not_the_fallback(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        bad = {"web": {"host": 8080, "port": "8O80", "debug": "true"}}
        with caplog.at_level(logging.WARNING, logger="src.config.service"):
            resolved = resolve_bootstrap_web(bad, warn=False)

        assert caplog.messages == []
        assert resolved == (BOOTSTRAP_WEB_HOST, BOOTSTRAP_WEB_PORT, False)
        assert resolved == resolve_bootstrap_web(bad)

    @pytest.mark.parametrize(
        "bad_port", ["", None, "18473", True, False, -1, 70000, 65536]
    )
    def test_unusable_port_falls_back(self, bad_port: Any) -> None:
        assert resolve_bootstrap_web({"web": {"port": bad_port}}).port == (
            BOOTSTRAP_WEB_PORT
        )

    @pytest.mark.parametrize("bad_host", [None, "", 8080, ["0.0.0.0"], {}])
    def test_unusable_host_falls_back_to_loopback(self, bad_host: Any) -> None:
        assert resolve_bootstrap_web({"web": {"host": bad_host}}).host == (
            BOOTSTRAP_WEB_HOST
        )

    @pytest.mark.parametrize("truthy_but_not_true", ["false", "no", "0", 1, "true"])
    def test_only_a_real_boolean_true_enables_debug(
        self, truthy_but_not_true: Any
    ) -> None:
        resolved = resolve_bootstrap_web({"web": {"debug": truthy_but_not_true}})

        assert resolved.debug is False

    def test_boolean_true_enables_debug(self) -> None:
        assert resolve_bootstrap_web({"web": {"debug": True}}).debug is True


class TestScorerConfigMap:
    def test_config_map_contains_all_standard_scorers(self) -> None:
        expected = set(SCORER_NAME_MAP.keys()) - _ENGINE_MANAGED_SCORERS
        actual = set(_SCORER_CONFIG_MAP.keys())
        assert actual == expected, (
            f"_SCORER_CONFIG_MAP is out of sync with SCORER_NAME_MAP.\n"
            f"  Missing: {expected - actual}\n"
            f"  Extra:   {actual - expected}"
        )


class TestBuildScorersFromConfig:
    def test_respects_weight_overrides(self) -> None:
        config: dict[str, Any] = {
            "recommendations": {
                "scorer_weights": {
                    "genre_match": 5.0,
                    "continuation": 0.5,
                },
            },
        }
        scorers = build_scorers_from_config(config)
        cls_to_name = {cls: name for name, cls in _SCORER_CONFIG_MAP.items()}
        by_name: dict[str, Scorer] = {
            cls_to_name[type(s)]: s for s in scorers if type(s) in cls_to_name
        }

        assert by_name["genre_match"].weight == 5.0
        assert by_name["continuation"].weight == 0.5


class TestTheAutoEnrichGate:
    def test_only_the_shared_gate_reads_the_setting(self) -> None:
        gate = Path(str(inspect.getsourcefile(auto_enrich_enabled))).resolve()
        registry = Path(src.settings.__file__).resolve().parent
        readers = {
            path
            for path in _SRC.rglob("*.py")
            if not path.name.startswith("test_")
            and "auto_enrich_on_sync" in path.read_text(encoding="utf-8")
        }

        assert gate in readers, "the scan read no gate, so this proves nothing"
        assert (
            sorted(
                path.relative_to(_SRC).as_posix()
                for path in readers
                if path != gate and not path.is_relative_to(registry)
            )
            == []
        )


class TestARetiredAiConfigBlockIsIgnored:
    def test_a_config_still_naming_llm_and_ollama_loads_clean(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "llm:\n  provider: ollama\n"
            "ollama:\n  base_url: http://ollama:11434\n  model: mistral:7b\n"
            "features:\n  ai_enabled: true\n"
            "recommendations:\n  default_count: 6\n",
            encoding="utf-8",
        )

        with caplog.at_level(logging.WARNING):
            config = load_config(config_file)

        assert config["recommendations"]["default_count"] == 6
        assert caplog.messages == []


class TestAChildlessRecommendationsHeaderStillBoots:
    @pytest.fixture()
    def restored_app_state(self) -> Iterator[None]:
        saved = {f.name: getattr(app_state, f.name) for f in fields(app_state)}
        yield
        for name, value in saved.items():
            setattr(app_state, name, value)

    @pytest.mark.parametrize(
        "section", ["", "recommendations:\n"], ids=["absent", "childless-header"]
    )
    def test_the_web_boot_reaches_the_baseline_engine(
        self, section: str, tmp_path: Path, restored_app_state: None
    ) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            f"storage:\n  database_path: {tmp_path / 'boot.db'}\n{section}",
            encoding="utf-8",
        )

        create_app(config_path)

        engine = app_state.engine
        assert engine is not None
        assert engine.preference_analyzer.min_rating == default_of(
            "recommendations.min_rating_for_preference"
        )
        weights = {type(scorer): scorer.weight for scorer in engine.pipeline.scorers}
        assert weights == {
            scorer_class: default_of(f"recommendations.scorer_weights.{name}")
            for name, scorer_class in _SCORER_CONFIG_MAP.items()
        }


class TestEveryChildlessHeaderIsDroppedAtTheDoor:
    def test_no_section_survives_as_none(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "storage:\nenrichment:\ninputs:\nrecommendations:\n  scorer_weights:\n",
            encoding="utf-8",
        )

        config = load_config(config_path)

        assert "storage" not in config
        assert "inputs" not in config
        assert config["recommendations"]["scorer_weights"]
        assert auto_enrich_enabled(config) is False
        assert len(build_scorers_from_config(config)) == len(_SCORER_CONFIG_MAP)


class TestConfigYamlIsReadAsUtf8:
    def test_a_non_ascii_value_survives_a_non_utf8_locale(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        database_path = "data/Shōgun/recommendations.db"
        config_path.write_text(
            f"storage:\n  database_path: {database_path}\n", encoding="utf-8"
        )
        locale_report = tmp_path / "locale"
        loaded = tmp_path / "loaded"

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import locale, sys\n"
                "from pathlib import Path\n"
                "Path(sys.argv[2]).write_text("
                "locale.getpreferredencoding(False), encoding='ascii')\n"
                "from src.config.service import load_config\n"
                "config = load_config(Path(sys.argv[1]))\n"
                "Path(sys.argv[3]).write_bytes("
                "config['storage']['database_path'].encode('utf-8'))\n",
                str(config_path),
                str(locale_report),
                str(loaded),
            ],
            cwd=_SRC.parent,
            env={
                **os.environ,
                "LC_ALL": "C",
                "LANG": "C",
                "PYTHONUTF8": "0",
                "PYTHONCOERCECLOCALE": "0",
            },
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr
        child_encoding = locale_report.read_text(encoding="ascii")
        assert (
            "utf" not in child_encoding.lower()
        ), f"the child read files as {child_encoding}, so this proves nothing"
        assert loaded.read_bytes().decode("utf-8") == database_path
