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

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.settings.metadata import (
    SettingMetadata,
    all_entries,
    default_config,
    default_of,
    entries_by_section,
    flat_defaults,
    get_entry,
    is_sensitive,
)
from src.storage.settings_migration import IN_SCOPE_SECTIONS, SENSITIVE_LEAF_KEYS
from src.utils.dotted_path import get_leaf

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

    def test_example_carries_only_bootstrap_web_leaves(self) -> None:
        """example.yaml's ``web`` section holds the boot settings, nothing else.

        None can be a registry leaf: the launcher reads all three before a
        database exists to open the socket.
        """
        config = yaml.safe_load(_EXAMPLE_CONFIG.read_text())

        assert set(config["web"]) == {"host", "port", "debug"}

    def test_only_bootstrap_sections_remain(self) -> None:
        """example.yaml carries the two bootstrap sections and nothing else.

        Anything settable from the app belongs in the database, so the file is
        exactly ``web`` (bind settings) plus ``storage`` (database paths).
        ``inputs`` is absent: sources live in the ``source_configs`` table and
        are created from the Data tab or the ``source`` CLI.
        """
        config = yaml.safe_load(_EXAMPLE_CONFIG.read_text())

        assert set(config) == {"web", "storage"}


# The worked example each pattern leaf's help must contain, named explicitly.
# Scanning the prose for ANY token matching the pattern was vacuous: the tmdb
# pattern `[a-z]{2}(-[A-Z]{2})?` is satisfied by the English word "an" already in
# that help string, so deleting the real "(en, en-US, pt-BR)" examples still
# passed. A short pattern can never be pinned by scanning prose.
_PATTERN_EXAMPLES = {
    "logging.file": "logs/recommendations.log",
    "enrichment.providers.tmdb.language": "en-US",
}


class TestPatternLeaves:
    """A pattern rejection routes the user to the help, so the help must deliver."""

    def test_pattern_leaves_carry_a_conforming_example_in_their_help(self) -> None:
        """Every pattern leaf's help must contain a value matching its pattern.

        The rejection message says "see this setting's help for examples" and no
        longer includes the regex (a role="alert" region reads metacharacters
        aloud as a plausible-but-wrong literal). That makes this a load-bearing
        conformance property: trimming the worked example out of either help
        string turns the error into a dead pointer with no way to recover, and
        nothing else in the suite would notice.
        """
        pattern_entries = [
            entry
            for entry in all_entries()
            if entry.validation is not None and entry.validation.pattern is not None
        ]

        assert {entry.key for entry in pattern_entries} == set(_PATTERN_EXAMPLES)

        for entry in pattern_entries:
            assert entry.validation is not None
            pattern = entry.validation.pattern
            assert pattern is not None
            example = _PATTERN_EXAMPLES[entry.key]
            # The named example must satisfy the pattern...
            assert re.fullmatch(pattern, example), (
                f"{entry.key}: the documented example {example!r} does not match "
                f"its own pattern {pattern}"
            )
            # ...and must actually appear in the help the error points at.
            assert example in entry.help, (
                f"{entry.key} help no longer contains {example!r}, so the "
                "'see this setting's help for examples' error points at nothing"
            )


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


class TestAdvancedSections:
    """The advanced flag is coupled to frontend copy that must exist."""

    def test_advanced_entries_only_live_in_sections_with_caution_copy(self) -> None:
        """Only sections the frontend has caution copy for may hold advanced leaves.

        SettingsSection.vue renders a per-section caution note above the Advanced
        disclosure, keyed by section name (``CAUTION_BY_SECTION``) with a generic
        fallback. Adding an advanced leaf to a third section would silently
        render that fallback instead of copy describing the actual risk — this
        fails first, so whoever adds it writes the copy.
        """
        advanced_sections = {entry.section for entry in all_entries() if entry.advanced}

        assert advanced_sections == {"web", "logging"}, (
            "an advanced leaf in a new section needs caution copy in "
            "resources/js/components/organisms/SettingsSection.vue "
            "(CAUTION_BY_SECTION), then this set updated to match"
        )


class TestEntryShape:
    """Structural invariants on individual entries."""

    @pytest.mark.parametrize("entry", all_entries(), ids=lambda e: e.key)
    def test_default_type_matches_declared_type(self, entry: SettingMetadata) -> None:
        """The default value's Python type matches the declared ``type``.

        Asserted on what callers receive, not on the stored form: container
        defaults are stored immutably (a ``tuple``) so the registry cannot hand
        out an object a caller could mutate, and ``default_of`` converts on the
        way out.
        """
        value = default_of(entry.key)
        expected = _TYPE_CHECKS[entry.type]
        assert isinstance(value, expected)
        # bool is a subclass of int — an int/float field must not hold a bool.
        if entry.type in {"int", "float"}:
            assert not isinstance(value, bool)

    @pytest.mark.parametrize(
        "entry",
        [entry for entry in all_entries() if entry.type == "list"],
        ids=lambda e: e.key,
    )
    def test_container_defaults_are_stored_immutably(
        self, entry: SettingMetadata
    ) -> None:
        """A ``list`` leaf stores a tuple, so no accessor can leak a live list.

        Storing mutably and copying at each accessor is the version that goes
        wrong: one new accessor, or one caller reading ``entry.default``
        directly, hands out the shared object — and ``web.allowed_origins``
        flows straight into CORSMiddleware.

        Filtered in the parametrize rather than branching in the body: with an
        ``if`` inside, ~50 of the ~51 cases reported green having asserted
        nothing, and the id list claimed a coverage that did not exist.
        """
        assert isinstance(entry.default, tuple)

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
        entry = get_entry("recommendations.default_count")
        assert entry is not None
        assert entry.sensitive is False
        assert is_sensitive("recommendations.default_count") is False

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

    def test_no_entry_outside_in_scope_sections(self) -> None:
        """No entry belongs to a section outside IN_SCOPE_SECTIONS."""
        for entry in all_entries():
            assert entry.section in IN_SCOPE_SECTIONS


class TestDefaults:
    """Defaults expose both a flat and a nested view that round-trip."""

    def test_flat_defaults_match_entries(self) -> None:
        """flat_defaults reflects every entry's key and public default."""
        expected = {entry.key: default_of(entry.key) for entry in all_entries()}
        assert flat_defaults() == expected

    def test_default_config_round_trips_to_flat(self) -> None:
        """default_config flattens back to the flat defaults exactly."""
        assert _flatten_all(default_config()) == flat_defaults()

    def test_default_config_nests_by_section(self) -> None:
        """default_config produces the expected nested shape."""
        nested = default_config()
        assert nested["web"]["allowed_origins"] == ["http://localhost:18473"]
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
