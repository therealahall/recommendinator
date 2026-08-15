"""Tests for the settings metadata registry.

The registry in ``src.settings.metadata`` is the single source of truth for
every in-scope global-config leaf: its label, type, widget, validation, and
hardcoded default. These tests guard the contract other tasks (API, CLI,
frontend, config assembly) rely on. ``config/example.yaml`` is deliberately
bootstrap-only and no longer duplicates these defaults, so the parity guard is
per LEAF rather than per section: no registry leaf may appear in example.yaml.
It cannot be per section, because ``web`` legitimately carries the bootstrap
bind settings (``host``/``port``/``debug``) — read before any database is open
and therefore deliberately NOT registry leaves.
"""

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
    """example.yaml is bootstrap-only; the registry owns the in-scope defaults."""

    def test_no_registry_leaf_appears_in_example(self) -> None:
        """No registry-managed leaf appears in example.yaml.

        Registry leaves have const defaults and are edited via the Settings page
        / ``settings`` CLI, so example.yaml must not duplicate them or the file
        drifts from the registry. Asserted per leaf rather than per section
        because ``web`` legitimately carries the bootstrap bind settings, which
        are deliberately not registry leaves.
        """
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
    """``default_of`` must not hand out the registry's own mutable objects."""

    def test_every_accessor_returns_a_fresh_container(self) -> None:
        """No accessor may hand out an object shared with the registry.

        Identity, not mutation: an append-then-check version poisons the
        registry for the rest of the session when it fails, turning one clear
        failure into a cascade of unrelated ones.

        All four paths are covered because fixing only ``default_of`` left the
        others leaking — ``web.allowed_origins`` goes straight into
        CORSMiddleware, so an in-place mutation would widen the CORS policy
        process-wide.
        """
        key = "web.allowed_origins"

        assert default_of(key) is not default_of(key)
        assert flat_defaults()[key] is not flat_defaults()[key]
        assert default_config()["web"]["allowed_origins"] is not (
            default_config()["web"]["allowed_origins"]
        )
        # And none of them is the stored object itself.
        entry = get_entry(key)
        assert entry is not None
        assert default_of(key) is not entry.default


class TestEntryShape:
    """Structural invariants on individual entries."""

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


class TestOutOfScope:
    """Out-of-scope config must never appear in the registry."""

    @pytest.mark.parametrize(
        "key",
        [
            "storage.database_path",
            "storage.cache_dir",
            "inputs.steam.api_key",
            "inputs.goodreads.path",
            # Bootstrap web settings: the uvicorn launcher reads these before
            # any database is open, so a registry entry would promise a restart
            # applies them when it never could.
            "web.host",
            "web.port",
            "web.debug",
        ],
    )
    def test_out_of_scope_key_has_no_entry(self, key: str) -> None:
        """Storage paths, per-source inputs, and web bind settings aren't registry leaves."""
        assert get_entry(key) is None
