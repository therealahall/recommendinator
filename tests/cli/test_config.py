"""Tests for CLI configuration, especially scorer registration.

Regression: ContinuationScorer, SeriesAffinityScorer, and ContentLengthScorer
were missing from _SCORER_CONFIG_MAP, so they never ran in production even
though they were listed in SCORER_NAME_MAP and DEFAULT_SCORERS.
"""

import logging
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.cli.config import (
    _SCORER_CONFIG_MAP,
    BOOTSTRAP_WEB_DEBUG,
    BOOTSTRAP_WEB_HOST,
    BOOTSTRAP_WEB_PORT,
    MIN_API_TOKEN_LENGTH,
    NO_API_TOKEN_MESSAGE,
    NON_ASCII_API_TOKEN_MESSAGE,
    SHORT_API_TOKEN_MESSAGE,
    MissingApiTokenError,
    build_scorers_from_config,
    create_llm_components,
    load_config,
    resolve_bootstrap_web,
    take_api_token,
    warn_if_config_is_shared,
)
from src.recommendations.scorers import SCORER_NAME_MAP, Scorer
from src.settings.metadata import default_config, flat_defaults
from src.storage.manager import StorageManager
from src.storage.settings_migration import migrate_config_settings
from src.utils.dotted_path import get_leaf

# These scorers are instantiated directly by RecommendationEngine, not via
# _SCORER_CONFIG_MAP. They require special construction (embeddings client,
# rule objects) that the config map cannot provide generically.
_ENGINE_MANAGED_SCORERS = {"semantic_similarity", "custom_preference"}


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

    def test_bootstrap_web_settings_come_from_yaml_not_the_registry(
        self, example_config: dict[str, Any]
    ) -> None:
        """web.host/port/debug resolve from example.yaml and are not registry leaves.

        They bind the socket before any database is open, so a DB-backed value
        could never be honoured — the Settings page must not offer them.
        """
        assert example_config["web"]["host"] == BOOTSTRAP_WEB_HOST
        assert example_config["web"]["port"] == BOOTSTRAP_WEB_PORT
        assert example_config["web"]["debug"] == BOOTSTRAP_WEB_DEBUG

        for key in ("web.host", "web.port", "web.debug"):
            assert key not in flat_defaults()

    def test_bootstrap_sections_pass_through(
        self, example_config: dict[str, Any]
    ) -> None:
        """The bootstrap storage paths survive the merge; no sources are declared.

        Sources live in the ``source_configs`` table and are created from the
        Data tab or the ``source`` CLI, so example.yaml declares none.
        """
        assert example_config["storage"]["database_path"] == "data/recommendations.db"
        assert "inputs" not in example_config

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
        storage.set_setting("recommendations.default_count", 9)

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

    def test_absent_web_section_uses_bootstrap_defaults(self) -> None:
        resolved = resolve_bootstrap_web({})

        assert resolved == (BOOTSTRAP_WEB_HOST, BOOTSTRAP_WEB_PORT, BOOTSTRAP_WEB_DEBUG)

    @pytest.mark.parametrize("section", [None, "broken", []])
    def test_non_dict_web_section_uses_bootstrap_defaults(self, section: Any) -> None:
        """A malformed section must not crash, and must not widen the bind.

        This resolver runs BEFORE migrate_config_settings heals a non-dict
        section, so it has to do its own type guard.
        """
        resolved = resolve_bootstrap_web({"web": section})

        assert resolved.host == BOOTSTRAP_WEB_HOST
        assert resolved.debug is False

    @pytest.mark.parametrize("port", [0, 1, 18473, 65535])
    def test_usable_port_is_preserved(self, port: int) -> None:
        """Both range boundaries are accepted, not just the middle.

        Without 65535 here, a slip to ``< 65535`` would silently fall the
        highest legal port back to the default.
        """
        assert resolve_bootstrap_web({"web": {"port": port}}).port == port

    def test_port_zero_is_preserved(self) -> None:
        """0 is a real value (ask the OS for a free port), not a blank.

        Regression: a truthiness fallback silently rewrote it to 18473.
        """
        assert resolve_bootstrap_web({"web": {"port": 0}}).port == 0

    def test_unusable_value_is_reported_not_swallowed(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A present-but-unusable value must name itself in the log.

        Regression: hardening the guards turned loud misconfiguration into
        invisible wrong behaviour. `port: 8O80` (a capital O typo) parses as a
        string, fails the int check, and silently bound 18473 — the operator's
        browser could not reach the port they set and nothing said why.
        """
        with caplog.at_level(logging.WARNING, logger="src.cli.config"):
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
        with caplog.at_level(logging.WARNING, logger="src.cli.config"):
            resolved = resolve_bootstrap_web(bad, warn=False)

        assert caplog.messages == []
        assert resolved == (BOOTSTRAP_WEB_HOST, BOOTSTRAP_WEB_PORT, False)
        assert resolved == resolve_bootstrap_web(bad)

    def test_absent_web_section_logs_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The common case — no ``web:`` section — must stay silent.

        Warning on every default install would train operators to ignore it.
        """
        with caplog.at_level(logging.WARNING, logger="src.cli.config"):
            resolve_bootstrap_web({})

        assert caplog.messages == []

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


_GOOD_TOKEN = "a" * MIN_API_TOKEN_LENGTH


class TestTakeApiToken:
    """The one credential read straight from ``config.yaml``.

    Every unusable shape raises rather than degrading, because the alternative
    to a token is not a weaker server, it is an open one.
    """

    def test_a_usable_token_is_returned_and_removed(self) -> None:
        """Popped, so nothing downstream is handed the plaintext credential."""
        config = {"web": {"host": "127.0.0.1", "api_token": _GOOD_TOKEN}}

        assert take_api_token(config) == _GOOD_TOKEN
        assert config["web"] == {"host": "127.0.0.1"}

    def test_surrounding_whitespace_is_trimmed(self) -> None:
        """A YAML edit that leaves a trailing newline is still the same token."""
        assert take_api_token({"web": {"api_token": f"  {_GOOD_TOKEN}\n"}}) == (
            _GOOD_TOKEN
        )

    @pytest.mark.parametrize(
        "web",
        [
            pytest.param({}, id="no-key"),
            pytest.param({"api_token": None}, id="blank-value"),
            pytest.param({"api_token": ""}, id="empty-string"),
            pytest.param({"api_token": "   "}, id="whitespace-only"),
            pytest.param({"api_token": 12345678901234567890123456789012}, id="number"),
        ],
    )
    def test_an_absent_or_unusable_token_raises(self, web: dict[str, Any]) -> None:
        """Including the shapes YAML produces for a key someone meant to fill in."""
        with pytest.raises(MissingApiTokenError) as raised:
            take_api_token({"web": web})

        assert str(raised.value) == NO_API_TOKEN_MESSAGE

    @pytest.mark.parametrize(
        "section",
        [pytest.param(None, id="bare-header"), pytest.param("x", id="scalar")],
    )
    def test_a_non_dict_web_section_raises_rather_than_crashing(
        self, section: Any
    ) -> None:
        """``web:`` with no children parses to None, which has no ``pop``."""
        with pytest.raises(MissingApiTokenError):
            take_api_token({"web": section})

    def test_a_short_token_raises_and_is_still_removed(self) -> None:
        """A guessable token is refused, and does not linger in the config."""
        config = {"web": {"api_token": "short"}}

        with pytest.raises(MissingApiTokenError) as raised:
            take_api_token(config)

        assert str(raised.value) == SHORT_API_TOKEN_MESSAGE
        assert config["web"] == {}

    def test_a_non_ascii_token_raises_rather_than_booting_unusable(self) -> None:
        """Starlette decodes headers as latin-1 and the guard re-encodes UTF-8.

        A token carrying one of these booted cleanly and then answered 401 to
        every request, with nothing anywhere naming the reason.
        """
        config = {"web": {"api_token": "π" * MIN_API_TOKEN_LENGTH}}

        with pytest.raises(MissingApiTokenError) as raised:
            take_api_token(config)

        assert str(raised.value) == NON_ASCII_API_TOKEN_MESSAGE

    def test_the_length_bound_is_exact_at_both_ends(self) -> None:
        """Off-by-one either way refuses what the docs promise, or accepts one
        character less than every message names as the minimum.
        """
        assert take_api_token({"web": {"api_token": _GOOD_TOKEN}}) == _GOOD_TOKEN
        with pytest.raises(MissingApiTokenError):
            take_api_token({"web": {"api_token": _GOOD_TOKEN[:-1]}})

    def test_neither_message_quotes_the_value_it_refused(self) -> None:
        """A message echoing the token would copy it into the operator's logs."""
        secret = "s" * (MIN_API_TOKEN_LENGTH - 1)

        with pytest.raises(MissingApiTokenError) as raised:
            take_api_token({"web": {"api_token": secret}})

        assert secret not in str(raised.value)


class TestWarnIfConfigIsShared:
    """``config.yaml`` holds the API token, and only the Docker entrypoint
    chmods it — a clone-and-run install leaves it at whatever umask produced.
    """

    @pytest.fixture()
    def config_file(self, tmp_path: Path) -> Path:
        path = tmp_path / "config.yaml"
        path.write_text("web:\n")
        return path

    @pytest.mark.parametrize("mode", [0o640, 0o604, 0o644, 0o660])
    def test_a_mode_others_can_reach_is_named_with_its_remedy(
        self, config_file: Path, mode: int, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Group and world, read and write: any of the four is a leak."""
        config_file.chmod(mode)

        with caplog.at_level(logging.WARNING, logger="src.cli.config"):
            warn_if_config_is_shared(config_file)

        assert oct(mode) in caplog.text
        assert f"chmod 600 {config_file}" in caplog.text

    def test_an_owner_only_mode_says_nothing(
        self, config_file: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Otherwise it fires on every correctly installed boot and is ignored."""
        config_file.chmod(0o600)

        with caplog.at_level(logging.WARNING, logger="src.cli.config"):
            warn_if_config_is_shared(config_file)

        assert caplog.text == ""

    def test_a_path_that_is_not_there_is_not_a_boot_failure(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A warning that raised would be worse than the mode it reports on."""
        with caplog.at_level(logging.WARNING, logger="src.cli.config"):
            warn_if_config_is_shared(tmp_path / "absent.yaml")

        assert caplog.text == ""


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

    def test_config_map_classes_match_scorer_name_map(self) -> None:
        """Classes in _SCORER_CONFIG_MAP must match SCORER_NAME_MAP."""
        for key, cls in _SCORER_CONFIG_MAP.items():
            assert key in SCORER_NAME_MAP, f"{key!r} not in SCORER_NAME_MAP"
            assert cls is SCORER_NAME_MAP[key], (
                f"Class mismatch for {key!r}: "
                f"config has {cls.__name__}, name map has {SCORER_NAME_MAP[key].__name__}"
            )


class TestBuildScorersFromConfig:
    """Verify build_scorers_from_config produces the right scorers."""

    def test_produces_all_config_map_scorers(
        self, example_config: dict[str, Any]
    ) -> None:
        """build_scorers_from_config returns one scorer per _SCORER_CONFIG_MAP entry."""
        scorers = build_scorers_from_config(example_config)

        scorer_types = {type(s) for s in scorers}
        expected_types = set(_SCORER_CONFIG_MAP.values())
        assert scorer_types == expected_types, (
            f"build_scorers_from_config is missing scorer types.\n"
            f"  Missing: {expected_types - scorer_types}\n"
            f"  Extra:   {scorer_types - expected_types}"
        )

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

    def test_uses_class_defaults_without_overrides(self) -> None:
        """Without config overrides, each scorer uses its class default weight."""
        config: dict[str, Any] = {"recommendations": {}}
        scorers = build_scorers_from_config(config)

        for scorer in scorers:
            # Each scorer should have its class default (created with no args)
            default_instance = type(scorer)()
            assert scorer.weight == default_instance.weight, (
                f"{type(scorer).__name__} weight {scorer.weight} != "
                f"default {default_instance.weight}"
            )


class TestCreateLlmComponents:
    """Tests for create_llm_components including graceful degradation."""

    @pytest.fixture()
    def ai_enabled_config(self) -> dict[str, Any]:
        """Config with AI features enabled."""
        return {
            "features": {"ai_enabled": True},
            "ollama": {
                "base_url": "http://localhost:11434",
                "model": "mistral:7b",
                "embedding_model": "nomic-embed-text",
                "conversation_model": "",
            },
        }

    def test_returns_none_tuple_when_ai_disabled(self) -> None:
        """Returns (None, None, None) when ai_enabled is False."""
        config: dict[str, Any] = {"features": {"ai_enabled": False}}
        client, embedding_gen, rec_gen = create_llm_components(config)
        assert client is None
        assert embedding_gen is None
        assert rec_gen is None

    def test_returns_none_tuple_when_ollama_not_installed(
        self, ai_enabled_config: dict[str, Any]
    ) -> None:
        """Returns (None, None, None) when ollama package is absent.

        Regression: the non-AI Docker image has no ollama package. If a user's
        config has ai_enabled: true, create_llm_components must degrade
        gracefully instead of crashing with ImportError.
        """
        with patch("src.llm.client.Client", None):
            client, embedding_gen, rec_gen = create_llm_components(ai_enabled_config)

        assert client is None
        assert embedding_gen is None
        assert rec_gen is None

    def test_logs_warning_when_ollama_not_installed(
        self,
        ai_enabled_config: dict[str, Any],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A warning with install instructions is logged when ollama is absent."""
        with patch("src.llm.client.Client", None):
            with caplog.at_level(logging.WARNING, logger="src.cli.config"):
                create_llm_components(ai_enabled_config)

        assert any(
            "ollama is not installed" in message
            and "uv sync --locked --extra ai" in message
            for message in caplog.messages
        )
