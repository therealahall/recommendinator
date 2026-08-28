from pathlib import Path

import pytest
import yaml

from src.settings.metadata import (
    SettingMetadata,
    all_entries,
    default_config,
    default_of,
    flat_defaults,
    get_entry,
    is_sensitive,
)
from src.storage.settings_migration import SENSITIVE_LEAF_KEYS
from src.utils.dotted_path import get_leaf

_EXAMPLE_CONFIG = Path("config/example.yaml")


class TestExampleConfigIsBootstrapOnly:
    def test_no_registry_leaf_appears_in_example(self) -> None:
        config = yaml.safe_load(_EXAMPLE_CONFIG.read_text())
        sentinel = object()
        present = [
            key
            for key in flat_defaults()
            if get_leaf(config, tuple(key.split(".")), sentinel) is not sentinel
        ]
        assert (
            present == []
        ), f"registry leaves must not appear in example.yaml: {present}"


class TestDefaultOfIsolation:
    def test_every_accessor_returns_a_fresh_container(self) -> None:
        key = "web.allowed_origins"

        assert default_of(key) is not default_of(key)
        assert flat_defaults()[key] is not flat_defaults()[key]
        assert default_config()["web"]["allowed_origins"] is not (
            default_config()["web"]["allowed_origins"]
        )
        entry = get_entry(key)
        assert entry is not None
        assert default_of(key) is not entry.default


class TestEntryShape:
    @pytest.mark.parametrize("entry", all_entries(), ids=lambda e: e.key)
    def test_enum_entries_have_choices_containing_default(
        self, entry: SettingMetadata
    ) -> None:
        if entry.type == "enum":
            assert entry.choices
            assert entry.default in entry.choices
            assert entry.widget == "select"
        else:
            assert entry.choices is None

    @pytest.mark.parametrize("entry", all_entries(), ids=lambda e: e.key)
    def test_numeric_validation_bounds_are_sane(self, entry: SettingMetadata) -> None:
        if entry.validation is None:
            return
        if entry.validation.min is not None:
            assert entry.validation.min <= entry.default
        if entry.validation.max is not None:
            assert entry.default <= entry.validation.max
        if entry.validation.min is not None and entry.validation.max is not None:
            assert entry.validation.min <= entry.validation.max


class TestSensitivity:
    def test_sensitive_registry_leaves_are_flagged(self) -> None:
        sensitive_keys = {
            entry.key
            for entry in all_entries()
            if entry.key.rsplit(".", 1)[-1] in SENSITIVE_LEAF_KEYS
        }
        assert "enrichment.providers.tmdb.api_key" in sensitive_keys
        assert "enrichment.providers.rawg.api_key" in sensitive_keys
        for key in sensitive_keys:
            entry = get_entry(key)
            assert entry is not None
            assert entry.sensitive is True
            assert is_sensitive(key) is True


class TestOutOfScope:
    @pytest.mark.parametrize(
        "key",
        [
            "storage.database_path",
            "storage.cache_dir",
            "inputs.steam.api_key",
            "inputs.goodreads.path",
            "web.host",
            "web.port",
            "web.debug",
        ],
    )
    def test_out_of_scope_key_has_no_entry(self, key: str) -> None:
        assert get_entry(key) is None
