"""Tests for the settings metadata registry.

The registry in ``src.settings.metadata`` is the single source of truth for
every in-scope global-config leaf: its label, type, widget, validation, and
hardcoded default. These tests guard the contract other tasks (API, CLI,
frontend, config assembly) rely on. ``config/example.yaml`` is deliberately
bootstrap-only and no longer duplicates these defaults, so the parity guard now
asserts the in-scope sections are ABSENT from example.yaml (the registry owns
them).
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

from src.settings.metadata import (
    SettingMetadata,
    all_entries,
    default_config,
    entries_by_section,
    flat_defaults,
    get_entry,
    is_sensitive,
)
from src.storage.settings_migration import IN_SCOPE_SECTIONS, SENSITIVE_LEAF_KEYS

_EXAMPLE_CONFIG = Path("config/example.yaml")

# Types whose default must be an instance of the given Python type(s). ``bool``
# is excluded from ``int`` because ``bool`` is a subclass of ``int`` in Python.
_TYPE_CHECKS: dict[str, Any] = {
    "bool": bool,
    "int": int,
    "float": float,
    "string": str,
    "list": list,
    "enum": str,
}


def _flatten(value: Any, prefix: str) -> dict[str, Any]:
    """Flatten a nested config value to dotted leaf paths (all leaves included)."""
    if not isinstance(value, dict):
        return {prefix: value}
    leaves: dict[str, Any] = {}
    for key, child in value.items():
        leaves.update(_flatten(child, f"{prefix}.{key}"))
    return leaves


class TestExampleConfigIsBootstrapOnly:
    """example.yaml is bootstrap-only; the registry owns the in-scope defaults."""

    def test_in_scope_sections_absent_from_example(self) -> None:
        """No in-scope global section appears in example.yaml.

        These sections have registry const defaults and are edited via the
        Settings page / ``settings`` CLI, so example.yaml no longer duplicates
        them. Keeping them out prevents the file from drifting from the
        registry.
        """
        config = yaml.safe_load(_EXAMPLE_CONFIG.read_text())
        present = [section for section in IN_SCOPE_SECTIONS if section in config]
        assert (
            present == []
        ), f"in-scope sections must not appear in example.yaml: {present}"

    def test_bootstrap_sections_remain(self) -> None:
        """The bootstrap ``storage`` paths and ``inputs`` sources stay in YAML."""
        config = yaml.safe_load(_EXAMPLE_CONFIG.read_text())
        assert "storage" in config
        assert "inputs" in config


class TestEntryShape:
    """Structural invariants on individual entries."""

    @pytest.mark.parametrize("entry", all_entries(), ids=lambda e: e.key)
    def test_default_type_matches_declared_type(self, entry: SettingMetadata) -> None:
        """The default value's Python type matches the declared ``type``."""
        expected = _TYPE_CHECKS[entry.type]
        assert isinstance(entry.default, expected)
        # bool is a subclass of int — an int/float field must not hold a bool.
        if entry.type in {"int", "float"}:
            assert not isinstance(entry.default, bool)

    @pytest.mark.parametrize("entry", all_entries(), ids=lambda e: e.key)
    def test_section_derived_from_key(self, entry: SettingMetadata) -> None:
        """Each entry's section is the key prefix and is in scope."""
        assert entry.section == entry.key.split(".", 1)[0]
        assert entry.section in IN_SCOPE_SECTIONS

    @pytest.mark.parametrize("entry", all_entries(), ids=lambda e: e.key)
    def test_labels_and_help_present(self, entry: SettingMetadata) -> None:
        """Every entry carries a non-empty label and help string."""
        assert entry.label.strip()
        assert entry.help.strip()

    @pytest.mark.parametrize("entry", all_entries(), ids=lambda e: e.key)
    def test_enum_entries_have_choices_containing_default(
        self, entry: SettingMetadata
    ) -> None:
        """Enum entries have non-empty choices that include their default."""
        if entry.type == "enum":
            assert entry.choices
            assert entry.default in entry.choices
            assert entry.widget == "select"
        else:
            assert entry.choices is None

    @pytest.mark.parametrize("entry", all_entries(), ids=lambda e: e.key)
    def test_numeric_validation_bounds_are_sane(self, entry: SettingMetadata) -> None:
        """Numeric validation bounds satisfy min <= default <= max."""
        if entry.validation is None:
            return
        if entry.validation.min is not None:
            assert entry.validation.min <= entry.default
        if entry.validation.max is not None:
            assert entry.default <= entry.validation.max
        if entry.validation.min is not None and entry.validation.max is not None:
            assert entry.validation.min <= entry.validation.max


class TestSensitivity:
    """Sensitive leaves must be flagged so they are never persisted plaintext."""

    def test_sensitive_registry_leaves_are_flagged(self) -> None:
        """Every registry leaf named like a secret is flagged sensitive."""
        sensitive_keys = {
            entry.key
            for entry in all_entries()
            if entry.key.rsplit(".", 1)[-1] in SENSITIVE_LEAF_KEYS
        }
        # The provider api_key leaves are the in-scope secrets.
        assert "enrichment.providers.tmdb.api_key" in sensitive_keys
        assert "enrichment.providers.rawg.api_key" in sensitive_keys
        for key in sensitive_keys:
            entry = get_entry(key)
            assert entry is not None
            assert entry.sensitive is True
            assert is_sensitive(key) is True

    def test_non_secret_leaf_not_sensitive(self) -> None:
        """A plainly non-secret leaf is not flagged sensitive."""
        entry = get_entry("web.port")
        assert entry is not None
        assert entry.sensitive is False
        assert is_sensitive("web.port") is False

    def test_is_sensitive_falls_back_for_unknown_key(self) -> None:
        """is_sensitive matches the leaf name even for out-of-scope keys."""
        assert is_sensitive("inputs.steam.api_key") is True
        assert is_sensitive("storage.database_path") is False


class TestOutOfScope:
    """Out-of-scope config must never appear in the registry."""

    @pytest.mark.parametrize(
        "key",
        [
            "storage.database_path",
            "storage.vector_db_path",
            "storage.cache_dir",
            "inputs.steam.api_key",
            "inputs.goodreads.path",
        ],
    )
    def test_out_of_scope_key_has_no_entry(self, key: str) -> None:
        """Storage paths and per-source inputs are not in the registry."""
        assert get_entry(key) is None

    def test_no_entry_outside_in_scope_sections(self) -> None:
        """No entry belongs to a section outside IN_SCOPE_SECTIONS."""
        for entry in all_entries():
            assert entry.section in IN_SCOPE_SECTIONS


class TestDefaults:
    """Defaults expose both a flat and a nested view that round-trip."""

    def test_flat_defaults_match_entries(self) -> None:
        """flat_defaults reflects every entry's key and default."""
        expected = {entry.key: entry.default for entry in all_entries()}
        assert flat_defaults() == expected

    def test_default_config_round_trips_to_flat(self) -> None:
        """default_config flattens back to the flat defaults exactly."""
        assert _flatten_all(default_config()) == flat_defaults()

    def test_default_config_nests_by_section(self) -> None:
        """default_config produces the expected nested shape."""
        nested = default_config()
        assert nested["web"]["port"] == 18473
        assert nested["recommendations"]["scorer_weights"]["genre_match"] == 2.0
        assert nested["conversation"]["llm"]["temperature"] == 0.7
        assert set(nested).issubset(set(IN_SCOPE_SECTIONS))


class TestGrouping:
    """entries_by_section groups entries for the API/CLI/frontend to consume."""

    def test_grouped_sections_are_ordered_and_in_scope(self) -> None:
        """Groups follow IN_SCOPE_SECTIONS order and cover only in-scope keys."""
        grouped = entries_by_section()
        assert list(grouped) == [
            section
            for section in IN_SCOPE_SECTIONS
            if any(e.section == section for e in all_entries())
        ]

    def test_every_entry_appears_in_its_group(self) -> None:
        """Each entry is grouped under its own section, none dropped."""
        grouped = entries_by_section()
        flattened = [entry for entries in grouped.values() for entry in entries]
        assert len(flattened) == len(all_entries())
        for section, entries in grouped.items():
            for entry in entries:
                assert entry.section == section


def _flatten_all(nested: dict[str, Any]) -> dict[str, Any]:
    """Flatten a nested defaults dict to dotted-key -> value for comparison."""
    leaves: dict[str, Any] = {}
    for key, value in nested.items():
        leaves.update(_flatten(value, key))
    return leaves
