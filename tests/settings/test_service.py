"""Unit tests for the framework-agnostic settings service layer.

These exercise :mod:`src.settings.service` directly against a real temp-DB
:class:`StorageManager` (no FastAPI): the grouped view shape, effective value
vs. ``db_overridden``, secret masking, per-type coercion/validation, all-or-
nothing writes with live-apply, reset-to-default, and secret gating.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from src.settings.metadata import (
    SettingMetadata,
    Validation,
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
from src.utils.urls import is_bare_origin

# A representative sensitive leaf and a non-sensitive numeric leaf reused across
# tests. Kept as module constants so a registry rename fails loudly in one place.
_SECRET_KEY = "enrichment.providers.tmdb.api_key"
_INT_KEY = "recommendations.default_count"
_ORIGINS_KEY = "web.allowed_origins"


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
        storage.secrets.set(_SECRET_KEY, "tmdb-key")

        setting = setting_view(_entry(_SECRET_KEY), config, storage)

        assert setting["has_secret"] is True
        assert "value" not in setting


class TestCoerceAndValidate:
    def test_bool_accepts_bool_rejects_other(self) -> None:
        entry = _entry("enrichment.enabled")

        assert coerce_and_validate(entry, True) is True
        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, "true")
        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, 1)

    def test_int_rejects_bool_string_and_below_min(self) -> None:
        entry = _entry(_INT_KEY)

        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, True)
        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, "3")
        with pytest.raises(SettingsValidationError):
            coerce_and_validate(entry, 0)  # violates min=1

    def test_float_rejects_bool_which_would_otherwise_coerce_to_one(self) -> None:
        """The load-bearing half: bool subclasses int, so float(True) is 1.0.

        Delete the ``isinstance(value, bool)`` guard and every scorer weight
        silently accepts ``true`` as 1.0, with the rest of the suite green.
        """
        for entry_key in (
            "recommendations.scorer_weights.genre_match",
            "recommendations.scorer_weights.tag_overlap",
        ):
            with pytest.raises(SettingsValidationError) as exc_info:
                coerce_and_validate(_entry(entry_key), True)
            assert exc_info.value.reason == "expected a number"

    @pytest.mark.parametrize("bad", [5, 5.0, True, None, ["a"], {"a": 1}])
    def test_string_rejects_non_strings(self, bad: Any) -> None:
        """The string branch's type guard had no coverage either.

        ``{"updates": {"logging.file": 5}}`` reaches this from the network.
        """
        with pytest.raises(SettingsValidationError) as exc_info:
            coerce_and_validate(_entry("logging.file"), bad)
        assert exc_info.value.reason == "expected a string"

    def test_numeric_bounds_are_inclusive_at_min_and_max(self) -> None:
        """The exact min and max values are accepted (bounds are inclusive).

        Locks the inclusivity of ``coerce_and_validate``'s ``<``/``>`` checks so
        a future slip to ``<=``/``>=`` (which would reject the boundary) fails
        here. Covers an int leaf bounded at both ends and a float leaf bounded
        below.
        """
        rating = _entry("recommendations.min_rating_for_preference")  # int, 1-5
        assert coerce_and_validate(rating, 1) == 1
        assert coerce_and_validate(rating, 5) == 5

        weight = _entry("recommendations.scorer_weights.genre_match")  # float, >= 0
        assert coerce_and_validate(weight, 0.0) == 0.0

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
            ["https://app.example.com:8443"],
            ["http://[::1]:3000"],
            ["http://localhost:18473", "https://app.example.com"],
        ],
    )
    def test_allowed_origins_accepts_wildcard_and_well_formed_origins(
        self, origins: list[str]
    ) -> None:
        """A bare ``scheme://host[:port]`` — and the ``"*"`` escape hatch — pass.

        ``"*"`` is the documented allow-all value, so tightening this leaf must
        not break it.
        """
        assert coerce_and_validate(_entry(_ORIGINS_KEY), origins) == origins

    def test_allowed_origins_rejects_null(self) -> None:
        """``"null"`` must never reach the CORS allowlist.

        Starlette compares with ``origin in self.allow_origins``, and a
        sandboxed iframe or a ``data:``/``file:`` document sends
        ``Origin: null``. It names no site, so it is an entry nobody intended.
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

    def test_logging_file_pattern_rejects_absolute_traversal_and_non_logs_paths(
        self,
    ) -> None:
        """logging.file only accepts a contained ``logs/*.log`` path.

        Traversal is rejected HERE now, by a negative lookahead. It previously
        validated and was caught later by ``src.utils.logging._safe_log_path``, which
        left the value persisted and displayed as the effective log file while
        the app wrote somewhere else. ``_safe_log_path`` remains the containment
        backstop (see TestSafeLogPath in tests/utils/test_logging.py) — this is the
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

    def test_reset_sensitive_key_raises(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        with pytest.raises(SettingsValidationError):
            reset_setting(config, storage, _SECRET_KEY)


class TestOriginGrammar:
    @pytest.mark.parametrize(
        "value", ["http://[foo]", "http://[1.2.3.4]", "http://tmdb]"]
    )
    def test_a_netloc_urlsplit_cannot_parse_is_refused_not_raised(
        self, value: str
    ) -> None:
        """``urlsplit`` raises ``ValueError`` on these, outside the old guard.

        ``update_settings`` catches ``SettingsValidationError`` alone, so it
        escaped as a 500 carrying a traceback.
        """
        assert is_bare_origin(value) is False

        with pytest.raises(SettingsValidationError) as exc_info:
            coerce_and_validate(_entry(_ORIGINS_KEY), [value])

        assert "is not an origin a browser can send" in exc_info.value.reason

    def test_the_stored_origin_is_the_one_a_browser_will_be_matched_against(
        self,
    ) -> None:
        """What was validated is what gets compared, or the check judged another
        string: ``urlsplit`` drops tab, CR and LF before the host is read.
        """
        stored = coerce_and_validate(_entry(_ORIGINS_KEY), ["http://local\thost:18473"])

        assert httpx.URL(stored[0]).host == "localhost"


class TestSecretGating:
    def test_set_secret_stores_encrypted_not_in_settings(
        self, storage: StorageManager
    ) -> None:
        set_secret(storage, _SECRET_KEY, "tmdb-key")

        assert storage.secrets.has(_SECRET_KEY) is True
        # The secret never lands in the plaintext settings table.
        assert storage.list_settings() == {}

    def test_clear_secret_removes_it(self, storage: StorageManager) -> None:
        set_secret(storage, _SECRET_KEY, "tmdb-key")

        assert clear_secret(storage, _SECRET_KEY) is True
        assert storage.secrets.has(_SECRET_KEY) is False

    def test_set_secret_rejects_non_sensitive(self, storage: StorageManager) -> None:
        with pytest.raises(SettingsValidationError):
            set_secret(storage, _INT_KEY, "nope")
