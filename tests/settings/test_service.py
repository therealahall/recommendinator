"""Unit tests for the framework-agnostic settings service layer.

These exercise :mod:`src.settings.service` directly against a real temp-DB
:class:`StorageManager` (no FastAPI): the grouped view shape, effective value
vs. ``db_overridden``, secret masking, per-type coercion/validation, all-or-
nothing writes with live-apply, reset-to-default, and secret gating.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from src.enrichment.providers.tmdb.tmdb import TMDBProvider
from src.settings.metadata import (
    SettingMetadata,
    Validation,
    all_entries,
    default_of,
    get_entry,
)
from src.settings.service import (
    SettingsValidationError,
    apply_settings,
    build_settings_view,
    clear_secret,
    coerce_and_validate,
    reset_setting,
    set_secret,
    setting_view,
)
from src.storage.manager import StorageManager
from src.utils.urls import is_bare_origin, is_local_url

# A representative sensitive leaf and a non-sensitive numeric leaf reused across
# tests. Kept as module constants so a registry rename fails loudly in one place.
_SECRET_KEY = "enrichment.providers.tmdb.api_key"
_INT_KEY = "recommendations.default_count"
_ORIGINS_KEY = "web.allowed_origins"
_OLLAMA_URL_KEY = "ollama.base_url"


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "settings.db")


@pytest.fixture()
def config() -> dict[str, Any]:
    """A partial running config; missing leaves fall back to registry defaults.

    ``logging`` is populated so the restart-required test can prove a stored
    value is left ALONE, rather than only proving an absent section is not
    created — a much weaker claim that a live-apply regression would pass.
    """
    return {
        "recommendations": {"default_count": 5, "max_count": 20},
        "logging": {"level": "INFO", "file": "logs/recommendations.log"},
    }


def _entry(key: str) -> SettingMetadata:
    entry = get_entry(key)
    assert entry is not None
    return entry


def _find(view: dict[str, Any], key: str) -> dict[str, Any]:
    for section in view["sections"]:
        for setting in section["settings"]:
            if setting["key"] == key:
                return setting
    raise AssertionError(f"{key} not in view")


class TestBuildSettingsView:
    def test_grouped_by_section_in_registry_order(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        view = build_settings_view(config, storage)

        section_names = [section["section"] for section in view["sections"]]
        # First declared section is "features"; each section carries settings.
        assert section_names[0] == "features"
        assert all(section["settings"] for section in view["sections"])

    def test_non_sensitive_setting_carries_metadata_and_value(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        setting = _find(build_settings_view(config, storage), _INT_KEY)

        assert setting["type"] == "int"
        assert setting["widget"] == "number"
        assert setting["label"] == "Default count"
        assert setting["validation"] == {
            "min": 1,
            "max": None,
            "max_length": None,
            "pattern": None,
        }
        assert setting["value"] == 5
        assert setting["db_overridden"] is False
        assert "has_secret" not in setting

    def test_effective_value_falls_back_to_default(
        self, storage: StorageManager
    ) -> None:
        # Empty config → the leaf is read from the registry default (5).
        setting = _find(build_settings_view({}, storage), _INT_KEY)

        assert setting["value"] == 5

    def test_db_overridden_true_after_explicit_set(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        storage.set_setting(_INT_KEY, 9)

        setting = _find(build_settings_view(config, storage), _INT_KEY)

        assert setting["db_overridden"] is True

    def test_sensitive_setting_masks_value(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        setting = _find(build_settings_view(config, storage), _SECRET_KEY)

        assert setting["sensitive"] is True
        assert setting["has_secret"] is False
        assert "value" not in setting
        assert "db_overridden" not in setting

    def test_sensitive_has_secret_true_after_set(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        storage.set_global_secret(_SECRET_KEY, "tmdb-key")

        setting = setting_view(_entry(_SECRET_KEY), config, storage)

        assert setting["has_secret"] is True
        assert "value" not in setting


class TestCoerceAndValidate:
    def test_bool_accepts_bool_rejects_other(self) -> None:
        entry = _entry("features.ai_enabled")

        assert coerce_and_validate(entry, True) is True
        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, "true")
        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, 1)

    def test_int_accepts_int_and_integral_float(self) -> None:
        entry = _entry(_INT_KEY)

        assert coerce_and_validate(entry, 3) == 3
        assert coerce_and_validate(entry, 3.0) == 3

    def test_int_rejects_bool_string_and_below_min(self) -> None:
        entry = _entry(_INT_KEY)

        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, True)
        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, "3")
        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, 0)  # violates min=1

    def test_float_accepts_int_and_enforces_bounds(self) -> None:
        entry = _entry("conversation.llm.temperature")  # min 0.0, max 2.0

        assert coerce_and_validate(entry, 1) == 1.0
        assert coerce_and_validate(entry, 1.5) == 1.5
        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, 2.5)
        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, -0.1)

    @pytest.mark.parametrize("bad", ["hot", None, ["1.0"], {"value": 1.0}])
    def test_float_rejects_non_numbers(self, bad: Any) -> None:
        """The float branch's type guard had no coverage at all.

        Every sibling type has a rejection test; float was the gap. The JSON
        body is caller-controlled, so these shapes reach the validator from
        ``PUT /api/settings``.
        """
        entry = _entry("conversation.llm.temperature")

        with pytest.raises(SettingsValidationError) as exc_info:
            coerce_and_validate(entry, bad)
        assert exc_info.value.reason == "expected a number"

    def test_float_rejects_bool_which_would_otherwise_coerce_to_one(self) -> None:
        """The load-bearing half: bool subclasses int, so float(True) is 1.0.

        Delete the ``isinstance(value, bool)`` guard and
        ``{"conversation.llm.temperature": true}`` is silently accepted as 1.0
        — as are all ten scorer weights — with the rest of the suite green.
        """
        for entry_key in (
            "conversation.llm.temperature",
            "recommendations.scorer_weights.genre_match",
        ):
            with pytest.raises(SettingsValidationError) as exc_info:
                coerce_and_validate(_entry(entry_key), True)
            assert exc_info.value.reason == "expected a number"

    @pytest.mark.parametrize("bad", [5, 5.0, True, None, ["a"], {"a": 1}])
    def test_string_rejects_non_strings(self, bad: Any) -> None:
        """The string branch's type guard had no coverage either.

        ``{"updates": {"ollama.model": 5}}`` reaches this from the network.
        """
        with pytest.raises(SettingsValidationError) as exc_info:
            coerce_and_validate(_entry("ollama.model"), bad)
        assert exc_info.value.reason == "expected a string"

    def test_numeric_bounds_are_inclusive_at_min_and_max(self) -> None:
        """The exact min and max values are accepted (bounds are inclusive).

        Locks the inclusivity of ``coerce_and_validate``'s ``<``/``>`` checks so
        a future slip to ``<=``/``>=`` (which would reject the boundary) fails
        here. Covers an int leaf
        (recommendations.min_rating_for_preference: 1-5) and a float leaf
        (conversation.llm.temperature: 0.0-2.0).
        """
        rating = _entry("recommendations.min_rating_for_preference")  # int, 1-5
        assert coerce_and_validate(rating, 1) == 1
        assert coerce_and_validate(rating, 5) == 5

        temperature = _entry("conversation.llm.temperature")  # float, 0.0-2.0
        assert coerce_and_validate(temperature, 0.0) == 0.0
        assert coerce_and_validate(temperature, 2.0) == 2.0

    def test_tmdb_language_pattern_accepts_locales_rejects_junk(self) -> None:
        """The TMDB language pattern accepts ISO 639-1 with an optional region.

        The value is passed straight through to the TMDB query, and the pattern
        is the only guard on a network-settable string. It must accept a bare
        code (``en``) as well as a region-qualified one — TMDB honours both, and
        rejecting ``en`` in the UI while config.yaml accepted it would be an
        arbitrary asymmetry. ``re.fullmatch`` anchoring is what stops a trailing
        payload; this pins that too.
        """
        entry = _entry("enrichment.providers.tmdb.language")

        for good in ("en", "en-US", "pt-BR", "de-DE"):
            assert coerce_and_validate(entry, good) == good

        for bad in ("EN-us", "english", "en_US", "en-US extra", "en-US&api_key=x", ""):
            with pytest.raises(SettingsValidationError):
                coerce_and_validate(entry, bad)

    def test_context_window_size_upper_bound_is_enforced(self) -> None:
        """The num_ctx cap is a security bound, so pin the constant.

        This value becomes Ollama's ``num_ctx``, which sizes the KV cache — an
        unbounded value set over the network could OOM the model server.
        """
        entry = _entry("conversation.llm.context_window_size")

        assert coerce_and_validate(entry, 131072) == 131072
        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, 131073)
        # A negative num_ctx is exactly what the lower bound exists to stop.
        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, -1)

    def test_tmdb_registry_defaults_match_the_provider_schema(self) -> None:
        """The registry and the provider must not drift on the same defaults.

        ``language`` and ``include_keywords`` are declared in the registry AND in
        TMDBProvider.get_config_schema(); changing one silently leaves the other
        serving the old value to any path that skips the assembled config.
        The provider's schema and its ``enrich`` fallback now share one module
        constant, so pinning the schema covers both copies on that side.
        """
        schema = {
            field.name: field.default for field in TMDBProvider().get_config_schema()
        }

        assert default_of("enrichment.providers.tmdb.language") == schema["language"]
        assert (
            default_of("enrichment.providers.tmdb.include_keywords")
            == schema["include_keywords"]
        )

    def test_enum_accepts_choice_rejects_other(self) -> None:
        entry = _entry("logging.level")
        valid = entry.choices[0] if entry.choices else ""

        assert coerce_and_validate(entry, valid) == valid
        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, "not-a-level")

    def test_string_max_length(self) -> None:
        entry = SettingMetadata(
            key="test.synthetic_string",
            section="test",
            label="Synthetic string",
            help="",
            type="string",
            default="",
            widget="text",
            sensitive=False,
            restart_required=False,
            advanced=False,
            validation=Validation(max_length=3),
        )

        assert coerce_and_validate(entry, "abc") == "abc"
        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, "abcd")

    def test_string_pattern(self) -> None:
        entry = SettingMetadata(
            key="test.synthetic_string",
            section="test",
            label="Synthetic string",
            help="",
            type="string",
            default="",
            widget="text",
            sensitive=False,
            restart_required=False,
            advanced=False,
            validation=Validation(pattern=r"[a-z]+"),
        )

        assert coerce_and_validate(entry, "abc") == "abc"
        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, "ABC")

    def test_list_accepts_list_rejects_scalar(self) -> None:
        entry = _entry(_ORIGINS_KEY)

        assert coerce_and_validate(entry, ["http://localhost:18473"]) == [
            "http://localhost:18473"
        ]
        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, "http://localhost:18473")

    def test_list_accepts_strings_rejects_non_string_items(self) -> None:
        """A list must contain only strings — a mixed/non-string item is rejected.

        Covers the network-settable list leaf ``web.allowed_origins`` so an
        injected number/dict can't slip into a config the CORS layer later
        treats as a string.
        """
        entry = _entry(_ORIGINS_KEY)

        assert coerce_and_validate(entry, ["http://localhost:18473"]) == [
            "http://localhost:18473"
        ]

        with pytest.raises(SettingsValidationError) as exc_info:
            coerce_and_validate(entry, ["http://localhost:18473", 3])
        assert exc_info.value.key == entry.key
        assert "list of strings" in exc_info.value.reason

    @pytest.mark.parametrize(
        "origins",
        [
            ["*"],
            ["http://localhost:18473"],
            ["https://app.example.com"],
            ["https://app.example.com:8443"],
            ["http://[::1]:3000"],
            ["http://localhost:18473", "https://app.example.com"],
        ],
    )
    def test_allowed_origins_accepts_wildcard_and_well_formed_origins(
        self, origins: list[str]
    ) -> None:
        """A bare ``scheme://host[:port]`` — and the ``"*"`` escape hatch — pass.

        ``"*"`` is the documented allow-all value and ``create_app`` turns
        ``allow_credentials`` off whenever it appears, so tightening this leaf
        must not break it.
        """
        assert coerce_and_validate(_entry(_ORIGINS_KEY), origins) == origins

    def test_allowed_origins_rejects_null(self) -> None:
        """``"null"`` must never reach the CORS allowlist.

        Starlette compares with ``origin in self.allow_origins``, and a
        sandboxed iframe or a ``data:``/``file:`` document sends
        ``Origin: null``. ``"null"`` is not ``"*"``, so ``allow_credentials``
        stays on — and this app ships no authentication, so allowing it would
        give any page the user visits full read/write of the library.
        """
        for spelling in ("null", "NULL", "Null", "  null  "):
            with pytest.raises(SettingsValidationError) as exc_info:
                coerce_and_validate(_entry(_ORIGINS_KEY), [spelling])
            assert exc_info.value.key == _ORIGINS_KEY
            assert '"null"' in exc_info.value.reason

    def test_allowed_origins_rejects_empty_entry(self) -> None:
        """An empty entry is rejected rather than persisted as a dead allowance."""
        with pytest.raises(SettingsValidationError) as exc_info:
            coerce_and_validate(_entry(_ORIGINS_KEY), ["http://localhost:18473", "  "])

        assert exc_info.value.key == _ORIGINS_KEY
        assert exc_info.value.reason == "must not contain an empty origin"

    @pytest.mark.parametrize(
        "origin",
        [
            "http://x.example/path",
            "http://x.example?q=1",
            "http://x.example#frag",
            "*.example.com",
            "https://",
            "ftp://x.example",
            "localhost:18473",
            "http://user:pw@x.example",
            "http://x.example:notaport",
            "http://x.example:99999",
        ],
    )
    def test_allowed_origins_rejects_entries_a_browser_never_sends(
        self, origin: str
    ) -> None:
        """Origins that can never match are refused instead of silently inert.

        Starlette's check is exact-match against the ``Origin`` header, so a
        trailing path, a wildcard subdomain, embedded credentials or an
        unparseable port sits in the allowlist doing nothing while the operator
        believes the origin is allowed.
        """
        with pytest.raises(SettingsValidationError) as exc_info:
            coerce_and_validate(_entry(_ORIGINS_KEY), [origin])

        assert exc_info.value.key == _ORIGINS_KEY
        assert _ORIGINS_KEY in str(exc_info.value)
        assert "scheme://host[:port]" in exc_info.value.reason

    @pytest.mark.parametrize(
        "raw",
        [
            pytest.param(" http://localhost:18473 ", id="surrounding-space"),
            pytest.param("http://localhost:18473/", id="trailing-slash"),
            pytest.param("http://local\thost:18473", id="embedded-tab"),
        ],
    )
    def test_allowed_origins_persists_the_normalized_value(self, raw: str) -> None:
        """Validating one spelling and persisting another stores a dead entry.

        ``str.strip`` caught the first of these only. A trailing slash is
        dropped rather than refused: no ``Origin`` header carries one.
        """
        assert coerce_and_validate(_entry(_ORIGINS_KEY), [raw]) == [
            "http://localhost:18473"
        ]

    @pytest.mark.parametrize(
        "base_url",
        [
            "http://ollama:11434",
            "http://localhost:11434",
            "http://127.0.0.1:11434",
            "https://[::1]:11434",
            "http://192.168.1.5:11434",
            "http://10.0.0.4:11434",
            # Tailscale hands out 100.64.0.0/10, and Docker Desktop resolves
            # host.docker.internal to the host gateway.
            "http://100.101.102.103:11434",
            "http://host.docker.internal:11434",
            "http://nas.local:11434",
        ],
    )
    def test_ollama_base_url_accepts_hosts_that_keep_prompts_on_the_network(
        self, base_url: str
    ) -> None:
        """Every shape a real local Ollama is reached at still round-trips."""
        assert coerce_and_validate(_entry(_OLLAMA_URL_KEY), base_url) == base_url

    @pytest.mark.parametrize(
        "base_url",
        ["http://attacker.example:11434", "https://api.example.com", "http://8.8.8.8"],
    )
    def test_ollama_base_url_rejects_a_host_off_the_machine(
        self, base_url: str
    ) -> None:
        """One write would send every prompt, and the library in it, elsewhere.

        A remote Ollama is still reachable through ``config.yaml``, which takes
        a file on the box rather than a request.
        """
        with pytest.raises(SettingsValidationError) as exc_info:
            coerce_and_validate(_entry(_OLLAMA_URL_KEY), base_url)

        assert exc_info.value.key == _OLLAMA_URL_KEY
        assert "config.yaml" in exc_info.value.reason

    @pytest.mark.parametrize(
        "base_url",
        [
            "",
            "javascript:alert(1)",
            "ftp://localhost",
            "http://user:pw@localhost:11434",
            "http://localhost:11434/api",
            "http://localhost:11434?q=1",
            "http://localhost:11434#frag",
            "http://localhost:notaport",
            "http://localhost:99999",
        ],
    )
    def test_ollama_base_url_rejects_anything_but_a_bare_origin(
        self, base_url: str
    ) -> None:
        """Credentials and a path are refused here, not handed to ``ollama.Client``."""
        with pytest.raises(SettingsValidationError) as exc_info:
            coerce_and_validate(_entry(_OLLAMA_URL_KEY), base_url)

        assert exc_info.value.key == _OLLAMA_URL_KEY
        assert "http(s)://host[:port]" in exc_info.value.reason

    def test_ollama_base_url_persists_the_normalised_value(self) -> None:
        """Whitespace and a trailing slash are removed, not merely tolerated."""
        assert (
            coerce_and_validate(_entry(_OLLAMA_URL_KEY), "  http://ollama:11434/  ")
            == "http://ollama:11434"
        )

    def test_ollama_base_url_rejects_a_value_that_is_not_a_string(self) -> None:
        """A JSON number reaches this before anything tries to split it."""
        with pytest.raises(SettingsValidationError) as exc_info:
            coerce_and_validate(_entry(_OLLAMA_URL_KEY), 11434)

        assert exc_info.value.reason == "expected a string"

    def test_ollama_base_url_rejects_a_host_longer_than_the_field(self) -> None:
        """``max_length`` is checked before the locality rule sees it.

        Asserted on the reason, because a single-label host that long is
        local enough for the rule after it to accept.
        """
        with pytest.raises(SettingsValidationError) as exc_info:
            coerce_and_validate(
                _entry(_OLLAMA_URL_KEY), "http://" + "a" * 300 + ":11434"
            )

        assert "at most" in exc_info.value.reason

    def test_validating_a_base_url_resolves_no_name(self) -> None:
        """The rule is text alone, and has to be: it runs under the config lock.

        ``PUT /api/settings`` holds ``src.web.state._config_lock`` across the
        whole save, so a DNS lookup in here would park every other request
        behind a resolver timeout.
        """
        with (
            patch.object(
                socket, "getaddrinfo", side_effect=AssertionError("resolved a name")
            ),
            patch.object(
                socket, "gethostbyname", side_effect=AssertionError("resolved a name")
            ),
        ):
            assert coerce_and_validate(_entry(_OLLAMA_URL_KEY), "http://ollama:11434")
            for remote in ("http://gpu.example.com:11434", "http://8.8.8.8:11434"):
                with pytest.raises(SettingsValidationError):
                    coerce_and_validate(_entry(_OLLAMA_URL_KEY), remote)

    def test_logging_file_pattern_rejects_absolute_traversal_and_non_logs_paths(
        self,
    ) -> None:
        """logging.file only accepts a contained ``logs/*.log`` path.

        Traversal is rejected HERE now, by a negative lookahead. It previously
        validated and was caught later by ``src.web.app._safe_log_path``, which
        left the value persisted and displayed as the effective log file while
        the app wrote somewhere else. ``_safe_log_path`` remains the containment
        backstop (see TestSafeLogPath in tests/test_web_api.py) — this is the
        boundary check that stops the divergence.
        """
        entry = _entry("logging.file")

        assert coerce_and_validate(entry, "logs/app.log") == "logs/app.log"
        assert coerce_and_validate(entry, "logs/sub/app.log") == "logs/sub/app.log"
        for bad in (
            "/etc/cron.d/evil",
            "/var/log/app.log",
            "secrets/app.txt",
            "logs/../../../tmp/pwned.log",
            "logs/../secrets.log",
        ):
            with pytest.raises(SettingsValidationError):
                coerce_and_validate(entry, bad)

    @pytest.mark.parametrize(
        "key",
        [
            entry.key
            for entry in all_entries()
            if entry.validation is not None and entry.validation.pattern is not None
        ],
    )
    def test_pattern_failure_points_at_the_help_without_leaking_the_regex(
        self, key: str
    ) -> None:
        """A pattern rejection must route the user to the help, not the regex.

        The message is surfaced verbatim as ``fieldErrors[key]`` inside a
        ``role="alert"`` live region. A bare "does not match the required
        pattern" leaves the user stuck; interpolating the raw regex is worse for
        screen reader users, who hear the metacharacters read out as a
        plausible-but-wrong literal value.

        Parametrized off the registry rather than naming the leaves, so a new
        pattern leaf is covered the moment it is added — the failure mode this
        guards is precisely someone adding a leaf and hand-rolling a message
        that interpolates its own regex.
        """
        entry = _entry(key)
        assert entry.validation is not None
        assert entry.validation.pattern is not None

        with pytest.raises(SettingsValidationError) as exc_info:
            # No pattern in the registry accepts a bare space.
            coerce_and_validate(entry, " ")

        assert exc_info.value.key == key
        # The whole string: this is product copy that lands in a role="alert"
        # region, so "contains the word help" would pass for something useless.
        assert exc_info.value.reason == (
            "does not match the required format — see this setting's help for examples"
        )
        assert entry.validation.pattern not in exc_info.value.reason

    def test_error_carries_key_and_reason(self) -> None:
        entry = _entry(_INT_KEY)

        with pytest.raises(SettingsValidationError) as exc_info:
            coerce_and_validate(entry, 0)

        assert exc_info.value.key == _INT_KEY
        assert entry.validation is not None
        assert exc_info.value.reason == f"must be >= {entry.validation.min}"


class TestApplySettings:
    def test_persists_and_live_applies_non_restart(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        apply_settings(config, storage, {_INT_KEY: 9})

        assert storage.get_setting(_INT_KEY) == 9
        assert config["recommendations"]["default_count"] == 9

    def test_restart_required_persists_without_live_apply(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        apply_settings(config, storage, {"logging.level": "DEBUG"})

        assert storage.get_setting("logging.level") == "DEBUG"
        # logging.level is restart_required — the value is persisted for the
        # next boot but the RUNNING value is left as it was.
        assert config["logging"]["level"] == "INFO"

    def test_unknown_key_rejected(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        with pytest.raises(SettingsValidationError):
            apply_settings(config, storage, {"web.nonsense": 1})

    def test_sensitive_key_rejected(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        with pytest.raises(SettingsValidationError):
            apply_settings(config, storage, {_SECRET_KEY: "leak"})

        assert storage.list_settings() == {}

    def test_all_or_nothing_no_partial_write(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        # First key valid, second invalid (below min) → nothing is written.
        with pytest.raises(SettingsValidationError):
            apply_settings(
                config,
                storage,
                {_INT_KEY: 9, "recommendations.max_count": 0},
            )

        assert storage.list_settings() == {}
        assert config["recommendations"]["default_count"] == 5


class TestResetSetting:
    def test_reset_deletes_row_and_restores_default(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        apply_settings(config, storage, {_INT_KEY: 9})

        reset_setting(config, storage, _INT_KEY)

        assert storage.get_setting(_INT_KEY) is None
        # Non-restart leaf is live-applied back to the const default.
        assert config["recommendations"]["default_count"] == 5

    def test_reset_restart_required_drops_row_without_live_apply(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        """Resetting a restart-required leaf must not rewrite the running value.

        The running value is deliberately set apart from the const default here:
        with both at "INFO" the assertion would pass whether or not the
        restart_required branch exists, proving nothing.
        """
        config["logging"]["level"] = "WARNING"
        storage.set_setting("logging.level", "DEBUG")

        reset_setting(config, storage, "logging.level")

        assert storage.get_setting("logging.level") is None
        # Not live-applied: the running value is left as it was, NOT reset to
        # the const default of INFO.
        assert config["logging"]["level"] == "WARNING"

    def test_reset_unknown_key_raises(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        with pytest.raises(SettingsValidationError):
            reset_setting(config, storage, "web.nonsense")

    def test_reset_sensitive_key_raises(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        with pytest.raises(SettingsValidationError):
            reset_setting(config, storage, _SECRET_KEY)


class TestOllamaBaseUrlHostsThatOnlyLookLocal:
    """Hosts whose validated spelling looks local and whose address is not.

    ``is_local_url`` reads the hostname ``urlsplit`` returns; the transport
    hands the same string to ``idna`` and the resolver, which split and parse
    it differently.
    """

    @pytest.mark.parametrize("separator", ["。", "．", "｡"])
    def test_a_unicode_label_separator_is_not_one_label(self, separator: str) -> None:
        """IDNA splits on three more dots than ``str`` does.

        With no ASCII dot in it, ``"." not in host`` reads the whole name as a
        single label served by the local resolver, so it is accepted and
        stored — and the transport then sends every prompt to example.com.
        """
        base_url = f"http://ollama{separator}example{separator}com:11434"
        assert httpx.URL(base_url).raw_host == b"ollama.example.com"

        with pytest.raises(SettingsValidationError) as exc_info:
            coerce_and_validate(_entry(_OLLAMA_URL_KEY), base_url)

        assert "config.yaml" in exc_info.value.reason

    @pytest.mark.parametrize(
        "host", ["134744072", "0x8080808", "01002004010", "8.526344"]
    )
    def test_an_ipv4_in_a_non_dotted_quad_form_is_still_that_address(
        self, host: str
    ) -> None:
        """Every one of these is 8.8.8.8 to ``inet_aton``, which is what resolves.

        ``ipaddress`` parses dotted-quad only, so the integer, hex, octal and
        short forms fall through to the single-label branch.
        """
        assert socket.inet_aton(host) == socket.inet_aton("8.8.8.8")

        with pytest.raises(SettingsValidationError) as exc_info:
            coerce_and_validate(_entry(_OLLAMA_URL_KEY), f"http://{host}:11434")

        assert "config.yaml" in exc_info.value.reason

    @pytest.mark.parametrize(
        "host",
        [
            pytest.param("::ffff:8.8.8.8", id="ipv4-mapped"),
            pytest.param("2002:808:808::", id="6to4"),
            # The Teredo client field is stored inverted, so f7f7:f7f7 is 8.8.8.8.
            pytest.param("2001:0:4136:e378:8000:63bf:f7f7:f7f7", id="teredo"),
        ],
    )
    def test_a_v6_host_carrying_a_public_ipv4_is_judged_by_that_ipv4(
        self, host: str
    ) -> None:
        """Each of these is 8.8.8.8 wearing a v6 hat.

        ``IPv6Address.is_private`` counts all of 6to4 and Teredo private, and
        answered the mapped form only from CPython 3.11.10 (CVE-2024-4032) —
        a patch level ``requires-python = ">=3.11"`` does not guarantee.
        """
        with pytest.raises(SettingsValidationError) as exc_info:
            coerce_and_validate(_entry(_OLLAMA_URL_KEY), f"http://[{host}]")

        # Locality, not grammar: the same exception covers both, and only this
        # reason names the file a remote Ollama has to be configured in.
        assert "config.yaml" in exc_info.value.reason

    @pytest.mark.parametrize(
        "value", ["http://[foo]", "http://[1.2.3.4]", "http://ollama]"]
    )
    def test_a_netloc_urlsplit_cannot_parse_is_refused_not_raised(
        self, value: str
    ) -> None:
        """``urlsplit`` raises ``ValueError`` on these, outside the old guard.

        ``update_settings`` catches ``SettingsValidationError`` alone, so it
        escaped as a 500 carrying a traceback.
        """
        with pytest.raises(SettingsValidationError) as exc_info:
            coerce_and_validate(_entry(_OLLAMA_URL_KEY), value)

        # Grammar, not locality: an unparseable netloc has no host to judge.
        assert "http(s)://host[:port]" in exc_info.value.reason

    def test_a_bare_origin_check_answers_false_for_an_unparseable_netloc(self) -> None:
        """The sibling entry point: ``web.allowed_origins`` validates each item
        through ``is_bare_origin`` without an ``is_local_url`` call after it.
        """
        assert is_bare_origin("http://[foo]") is False
        assert is_local_url("http://[foo]") is False

    def test_the_stored_url_is_the_one_the_transport_will_dial(self) -> None:
        """What was validated is what gets used, or the check judged another URL.

        ``urlsplit`` drops tab, CR and LF before the host is read, so a value
        carrying one is checked as a different string from the one persisted.
        """
        stored = coerce_and_validate(
            _entry(_OLLAMA_URL_KEY), "http://local\thost:11434"
        )

        assert httpx.URL(stored).host == "localhost"


class TestSecretGating:
    def test_set_secret_stores_encrypted_not_in_settings(
        self, storage: StorageManager
    ) -> None:
        set_secret(storage, _SECRET_KEY, "tmdb-key")

        assert storage.has_global_secret(_SECRET_KEY) is True
        # The secret never lands in the plaintext settings table.
        assert storage.list_settings() == {}

    def test_clear_secret_removes_it(self, storage: StorageManager) -> None:
        set_secret(storage, _SECRET_KEY, "tmdb-key")

        assert clear_secret(storage, _SECRET_KEY) is True
        assert storage.has_global_secret(_SECRET_KEY) is False

    def test_set_secret_rejects_non_sensitive(self, storage: StorageManager) -> None:
        with pytest.raises(SettingsValidationError):
            set_secret(storage, _INT_KEY, "nope")

    def test_clear_secret_rejects_unknown(self, storage: StorageManager) -> None:
        with pytest.raises(SettingsValidationError):
            clear_secret(storage, "web.nonsense")
