from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from src.settings.metadata import (
    SettingMetadata,
    Validation,
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
from src.utils.urls import is_bare_origin

_SECRET_KEY = "enrichment.providers.tmdb.api_key"
_INT_KEY = "recommendations.default_count"
_ORIGINS_KEY = "web.allowed_origins"


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "settings.db")


@pytest.fixture()
def config() -> dict[str, Any]:
    return {
        "recommendations": {"default_count": 5, "max_count": 20},
        "logging": {"level": "INFO", "file": "data/logs/recommendations.log"},
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
        assert setting["has_stored_value"] is False
        assert "has_secret" not in setting

    def test_a_row_holding_the_default_is_not_an_override(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        storage.settings.set(_INT_KEY, default_of(_INT_KEY))

        setting = _find(build_settings_view(config, storage), _INT_KEY)

        assert setting["db_overridden"] is False

    def test_a_row_holding_the_default_is_still_a_row_that_can_be_deleted(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        storage.settings.set(_INT_KEY, default_of(_INT_KEY))

        setting = _find(build_settings_view(config, storage), _INT_KEY)

        assert setting["has_stored_value"] is True

    def test_a_list_row_matching_the_tuple_default_is_not_an_override(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        storage.settings.set(_ORIGINS_KEY, default_of(_ORIGINS_KEY))

        setting = _find(build_settings_view(config, storage), _ORIGINS_KEY)

        assert setting["db_overridden"] is False

    def test_a_row_differing_from_the_default_is_an_override(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        storage.settings.set(_INT_KEY, default_of(_INT_KEY) + 1)

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
        assert "has_stored_value" not in setting

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
            coerce_and_validate(entry, 0)

    def test_float_rejects_bool_which_would_otherwise_coerce_to_one(self) -> None:
        for entry_key in (
            "recommendations.scorer_weights.genre_match",
            "recommendations.scorer_weights.tag_overlap",
        ):
            with pytest.raises(SettingsValidationError) as exc_info:
                coerce_and_validate(_entry(entry_key), True)
            assert exc_info.value.reason == "expected a number"

    @pytest.mark.parametrize("bad", [5, 5.0, True, None, ["a"], {"a": 1}])
    def test_string_rejects_non_strings(self, bad: Any) -> None:
        with pytest.raises(SettingsValidationError) as exc_info:
            coerce_and_validate(_entry("logging.file"), bad)
        assert exc_info.value.reason == "expected a string"

    def test_numeric_bounds_are_inclusive_at_min_and_max(self) -> None:
        rating = _entry("recommendations.min_rating_for_preference")
        assert coerce_and_validate(rating, 1) == 1
        assert coerce_and_validate(rating, 5) == 5

        weight = _entry("recommendations.scorer_weights.genre_match")
        assert coerce_and_validate(weight, 0.0) == 0.0

    def test_tmdb_language_pattern_accepts_locales_rejects_junk(self) -> None:
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
        assert coerce_and_validate(_entry(_ORIGINS_KEY), origins) == origins

    def test_allowed_origins_rejects_null(self) -> None:
        for spelling in ("null", "NULL", "Null", "  null  "):
            with pytest.raises(SettingsValidationError) as exc_info:
                coerce_and_validate(_entry(_ORIGINS_KEY), [spelling])
            assert exc_info.value.key == _ORIGINS_KEY
            assert '"null"' in exc_info.value.reason

    def test_allowed_origins_rejects_empty_entry(self) -> None:
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
        assert coerce_and_validate(_entry(_ORIGINS_KEY), [raw]) == [
            "http://localhost:18473"
        ]

    def test_logging_file_pattern_rejects_absolute_traversal_and_non_logs_paths(
        self,
    ) -> None:
        entry = _entry("logging.file")

        for good in ("data/logs/app.log", "data/logs/sub/app.log"):
            assert coerce_and_validate(entry, good) == good
        for bad in (
            "/etc/cron.d/evil",
            "/var/log/app.log",
            "secrets/app.txt",
            "logs/recommendations.log",
            "data/logs/../../../tmp/pwned.log",
            "data/logs/../secrets.log",
        ):
            with pytest.raises(SettingsValidationError):
                coerce_and_validate(entry, bad)


class TestApplySettings:
    def test_persists_and_live_applies_non_restart(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        apply_settings(config, storage, {_INT_KEY: 9})

        assert storage.settings.get(_INT_KEY) == 9
        assert config["recommendations"]["default_count"] == 9

    def test_restart_required_persists_without_live_apply(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        apply_settings(config, storage, {"logging.level": "DEBUG"})

        assert storage.settings.get("logging.level") == "DEBUG"
        assert config["logging"]["level"] == "INFO"

    def test_sensitive_key_rejected(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        with pytest.raises(SettingsValidationError):
            apply_settings(config, storage, {_SECRET_KEY: "leak"})

        assert storage.settings.list() == {}

    def test_all_or_nothing_no_partial_write(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        with pytest.raises(SettingsValidationError):
            apply_settings(
                config,
                storage,
                {_INT_KEY: 9, "recommendations.max_count": 0},
            )

        assert storage.settings.list() == {}
        assert config["recommendations"]["default_count"] == 5


class TestResetSetting:
    def test_reset_deletes_row_and_restores_default(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        apply_settings(config, storage, {_INT_KEY: 9})

        reset_setting(config, storage, _INT_KEY)

        assert storage.settings.get(_INT_KEY) is None
        assert config["recommendations"]["default_count"] == default_of(_INT_KEY)

    def test_reset_restart_required_drops_row_without_live_apply(
        self, storage: StorageManager, config: dict[str, Any]
    ) -> None:
        config["logging"]["level"] = "WARNING"
        storage.settings.set("logging.level", "DEBUG")

        reset_setting(config, storage, "logging.level")

        assert storage.settings.get("logging.level") is None
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
        assert is_bare_origin(value) is False

        with pytest.raises(SettingsValidationError) as exc_info:
            coerce_and_validate(_entry(_ORIGINS_KEY), [value])

        assert "is not an origin a browser can send" in exc_info.value.reason

    def test_the_stored_origin_is_the_one_a_browser_will_be_matched_against(
        self,
    ) -> None:
        stored = coerce_and_validate(_entry(_ORIGINS_KEY), ["http://local\thost:18473"])

        assert httpx.URL(stored[0]).host == "localhost"


class TestSecretGating:
    def test_set_secret_stores_encrypted_not_in_settings(
        self, storage: StorageManager
    ) -> None:
        set_secret(storage, _SECRET_KEY, "tmdb-key")

        assert storage.secrets.has(_SECRET_KEY) is True
        assert storage.settings.list() == {}

    def test_clear_secret_removes_it(self, storage: StorageManager) -> None:
        set_secret(storage, _SECRET_KEY, "tmdb-key")

        assert clear_secret(storage, _SECRET_KEY) is True
        assert storage.secrets.has(_SECRET_KEY) is False

    def test_set_secret_rejects_non_sensitive(self, storage: StorageManager) -> None:
        with pytest.raises(SettingsValidationError):
            set_secret(storage, _INT_KEY, "nope")
