"""Tests for application configuration, especially scorer registration.

Regression: ContinuationScorer, SeriesAffinityScorer, and ContentLengthScorer
were missing from _SCORER_CONFIG_MAP, so they never ran in production even
though they were listed in SCORER_NAME_MAP and DEFAULT_SCORERS.
"""

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
    create_recommendation_engine,
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

# Built per call by RecommendationEngine from the user's rules, not via
# _SCORER_CONFIG_MAP, which cannot construct it generically.
_ENGINE_MANAGED_SCORERS = {"custom_preference"}

# parents[2] resolves /tests/config/test_service.py -> repo root.
_SRC = Path(__file__).resolve().parents[2] / "src"


@pytest.fixture()
def example_config() -> dict[str, Any]:
    """Load the example config for tests."""
    return load_config(Path("config/example.yaml"))


class TestLoadConfigDefaults:
    """load_config layers the registry const defaults UNDER the parsed YAML.

    The trimmed, bootstrap-only ``example.yaml`` omits every in-scope global
    section; loading it must still yield a complete, usable config for each
    section from the const defaults (const default < YAML), before any DB
    overlay runs. The DB overlay (``migrate_config_settings``) still wins on
    top, so end to end the precedence stays const default < YAML < DB.
    """

    def test_trimmed_example_resolves_every_registry_leaf(
        self, example_config: dict[str, Any]
    ) -> None:
        """Every registry leaf resolves to its const default from example.yaml.

        example.yaml carries no registry-managed leaf, so this proves the const
        layer alone (no DB overlay) produces a complete effective config.
        Asserted per leaf rather than per section because ``web`` also holds the
        bootstrap bind settings, which are deliberately not registry leaves.
        """
        sentinel = object()
        for key, expected in flat_defaults().items():
            resolved = get_leaf(example_config, tuple(key.split(".")), sentinel)
            assert resolved == expected, f"{key} did not resolve to its default"

    def test_yaml_overrides_const_default(self, tmp_path: Path) -> None:
        """A YAML leaf overrides the registry const default; siblings resolve."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("recommendations:\n  default_count: 11\n")

        config = load_config(config_path)

        assert config["recommendations"]["default_count"] == 11
        # A sibling the YAML omits still resolves from the const default.
        assert (
            config["recommendations"]["max_count"]
            == default_config()["recommendations"]["max_count"]
        )

    def test_db_overlay_wins_over_loaded_defaults(self, tmp_path: Path) -> None:
        """End to end: DB overlay wins over the const-defaulted, loaded config."""
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        storage.settings.set("recommendations.default_count", 9)

        config = load_config(Path("config/example.yaml"))
        # Const layer resolved the leaf to the registry default (no DB yet).
        assert (
            config["recommendations"]["default_count"]
            == default_config()["recommendations"]["default_count"]
        )

        migrate_config_settings(config, storage)

        # DB overlay wins on top: const default < YAML < DB.
        assert config["recommendations"]["default_count"] == 9


class TestResolveBootstrapWeb:
    """The single resolver both web entry points share.

    ``src/web/main.py`` binds the socket and ``src/web/app.py`` gates /docs on
    these values. They MUST agree — if the launcher reads debug as false while
    create_app reads it as true, the OpenAPI console opens on a bind the
    launcher believes is closed. Having one implementation makes that structural;
    these tests are what stop it being quietly re-specialised.
    """

    @pytest.mark.parametrize("port", [0, 65535])
    def test_usable_port_is_preserved(self, port: int) -> None:
        """Both range boundaries are accepted, not just the middle.

        Without 65535 here, a slip to ``< 65535`` would silently fall the
        highest legal port back to the default.
        """
        assert resolve_bootstrap_web({"web": {"port": port}}).port == port

    def test_unusable_value_is_reported_not_swallowed(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A present-but-unusable value must name itself in the log.

        Regression: hardening the guards turned loud misconfiguration into
        invisible wrong behaviour. `port: 8O80` (a capital O typo) parses as a
        string, fails the int check, and silently bound 18473 — the operator's
        browser could not reach the port they set and nothing said why.
        """
        with caplog.at_level(logging.WARNING, logger="src.config.service"):
            resolved = resolve_bootstrap_web(
                {"web": {"host": 8080, "port": "8O80", "debug": "true"}}
            )

        assert resolved == (BOOTSTRAP_WEB_HOST, BOOTSTRAP_WEB_PORT, False)
        assert any("web.host" in m and "8080" in m for m in caplog.messages)
        assert any("web.port" in m and "8O80" in m for m in caplog.messages)
        # debug too: a quoted `debug: "true"` is a truthy string that fails
        # closed, leaving an operator with no /docs, no reload, and no
        # explanation. Without this the whole debug warning block could be
        # deleted with the suite still green.
        assert any("web.debug" in m and "true" in m for m in caplog.messages)

    def test_warn_false_suppresses_the_log_but_not_the_fallback(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``warn=False`` must silence the diagnostics only, never the guards.

        Regression: both readers resolve the same config per launch, so every
        bind diagnostic printed twice and read like two separate faults.
        create_app now passes ``warn=False``; if that had suppressed the
        fallbacks too, a malformed ``web.debug`` would open /docs on a bind the
        launcher believes is closed.
        """
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
        """A port that is not a usable integer falls back to the default.

        Regression: bool subclasses int, so `isinstance(port, int)` accepted
        PyYAML's resolution of `port: no` to False. That reached uvicorn as 0 and
        bound a random ephemeral port rather than falling back — with the startup
        banner reporting "http://localhost:False". Out-of-range values are
        rejected here too, rather than failing at the socket layer.
        """
        assert resolve_bootstrap_web({"web": {"port": bad_port}}).port == (
            BOOTSTRAP_WEB_PORT
        )

    @pytest.mark.parametrize("bad_host", [None, "", 8080, ["0.0.0.0"], {}])
    def test_unusable_host_falls_back_to_loopback(self, bad_host: Any) -> None:
        """Anything that is not a non-empty string falls back to loopback.

        ``web_config`` is an untyped dict, so mypy cannot catch a non-string
        here. This is the leaf where the stakes are highest — the fallback must
        be loopback for every malformed shape, never a wildcard.
        """
        assert resolve_bootstrap_web({"web": {"host": bad_host}}).host == (
            BOOTSTRAP_WEB_HOST
        )

    @pytest.mark.parametrize("truthy_but_not_true", ["false", "no", "0", 1, "true"])
    def test_only_a_real_boolean_true_enables_debug(
        self, truthy_but_not_true: Any
    ) -> None:
        """Debug must fail closed on anything that is not literally ``True``.

        Regression: a ``bool()`` cast meant a quoted ``debug: "false"`` — a
        plausible YAML edit — was truthy and published /docs and /redoc on an
        unauthenticated instance.
        """
        resolved = resolve_bootstrap_web({"web": {"debug": truthy_but_not_true}})

        assert resolved.debug is False

    def test_boolean_true_enables_debug(self) -> None:
        assert resolve_bootstrap_web({"web": {"debug": True}}).debug is True


class TestScorerConfigMap:
    """Verify _SCORER_CONFIG_MAP stays in sync with SCORER_NAME_MAP."""

    def test_config_map_contains_all_standard_scorers(self) -> None:
        """Every scorer in SCORER_NAME_MAP (except engine-managed ones) must
        appear in _SCORER_CONFIG_MAP so it actually runs in production.

        Bug: ContinuationScorer, SeriesAffinityScorer, ContentLengthScorer
        were absent from _SCORER_CONFIG_MAP, causing them to silently not run.
        """
        expected = set(SCORER_NAME_MAP.keys()) - _ENGINE_MANAGED_SCORERS
        actual = set(_SCORER_CONFIG_MAP.keys())
        assert actual == expected, (
            f"_SCORER_CONFIG_MAP is out of sync with SCORER_NAME_MAP.\n"
            f"  Missing: {expected - actual}\n"
            f"  Extra:   {actual - expected}"
        )


class TestBuildScorersFromConfig:
    """Verify build_scorers_from_config produces the right scorers."""

    def test_respects_weight_overrides(self) -> None:
        """Config weight overrides are applied to the returned scorers."""
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
    """Web sync, web import, ``update`` and ``import`` all queue new items."""

    def test_only_the_shared_gate_reads_the_setting(self) -> None:
        """A fourth hand-rolled copy is how the CLI drifted from the web: a
        condition added to the gate would reach two callers and miss two.
        """
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
    """A user's ``config.yaml`` may still carry the deleted AI sections.

    The AI removal ships no migration, so ``load_config`` has to merge those
    blocks harmlessly: nothing reads them, and nothing may refuse to boot over
    them.
    """

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
    """Regression: boot died with AttributeError on a bare ``recommendations:``.

    The header parses to None, which a ``.get`` default cannot replace, and the
    engine's own guard for a non-dict section never ran because nothing got as
    far as building an engine.
    """

    @pytest.fixture()
    def restored_app_state(self) -> Iterator[None]:
        """``create_app`` writes a module-level singleton other tests read."""
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
        """Through ``create_app``: a directly built engine never saw this."""
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

    def test_the_engine_factory_guards_the_none_section_itself(
        self, tmp_path: Path
    ) -> None:
        """Called with the raw YAML, before any overlay heals the section."""
        engine = create_recommendation_engine(
            StorageManager(sqlite_path=tmp_path / "engine.db"),
            {"recommendations": None},
        )

        assert engine.preference_analyzer.min_rating == default_of(
            "recommendations.min_rating_for_preference"
        )
        assert len(engine.pipeline.scorers) == len(_SCORER_CONFIG_MAP)


class TestEveryChildlessHeaderIsDroppedAtTheDoor:
    """``storage:`` and ``enrichment:`` are read with the same ``.get`` default
    and are absent from the settings registry, so no overlay heals them.
    """

    def test_no_section_survives_as_none(self, tmp_path: Path) -> None:
        """One that did would reach a reader as None and fail its ``.get``."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "storage:\nenrichment:\ninputs:\nrecommendations:\n  scorer_weights:\n",
            encoding="utf-8",
        )

        config = load_config(config_path)

        def none_valued(section: dict[str, Any], prefix: str = "") -> list[str]:
            found = []
            for key, value in section.items():
                if value is None:
                    found.append(f"{prefix}{key}")
                elif isinstance(value, dict):
                    found += none_valued(value, f"{prefix}{key}.")
            return found

        assert none_valued(config) == []
        assert auto_enrich_enabled(config) is False
        assert len(build_scorers_from_config(config)) == len(_SCORER_CONFIG_MAP)


class TestConfigYamlIsReadAsUtf8:
    """Regression: config.yaml was decoded in the locale encoding.

    Under a non-UTF-8 locale a non-ASCII path or display name in it raised
    UnicodeDecodeError, killing the boot of every entry point.
    """

    def test_a_non_ascii_value_survives_a_non_utf8_locale(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        database_path = "data/Shōgun/recommendations.db"
        config_path.write_text(
            f"storage:\n  database_path: {database_path}\n", encoding="utf-8"
        )
        locale_report = tmp_path / "locale"
        loaded = tmp_path / "loaded"

        # A subprocess, because CPython resolves the locale encoding from the C
        # library as ``open`` runs, not from anything patchable in process. The
        # child reports back through files: its own stdout is ASCII under C.
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
                # Both disable a fallback to UTF-8 that a C locale otherwise
                # triggers, which would decode the file correctly regardless.
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
