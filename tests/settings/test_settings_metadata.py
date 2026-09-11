import pytest

from src.enrichment.provider_base import EnrichmentProvider
from src.enrichment.registry import EnrichmentRegistry
from src.settings.metadata import (
    SettingMetadata,
    all_entries,
    default_config,
    default_of,
    flat_defaults,
    get_entry,
)

_BUILTIN_PROVIDER_PACKAGE = "src.enrichment.providers."


def _builtin_providers() -> dict[str, EnrichmentProvider]:
    registry = EnrichmentRegistry()
    registry.discover_providers()
    return {
        name: provider
        for name, provider in registry.get_all_providers().items()
        if type(provider).__module__.startswith(_BUILTIN_PROVIDER_PACKAGE)
    }


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


class TestEveryDiscoveredProviderIsConfigurable:
    def test_a_provider_has_an_enable_toggle_and_a_masked_entry_for_each_secret(
        self,
    ) -> None:
        toggles: list[str] = []
        secrets: list[str] = []
        for name, provider in _builtin_providers().items():
            toggles.append(f"enrichment.providers.{name}.enabled")
            secrets += [
                f"enrichment.providers.{name}.{field.name}"
                for field in provider.get_config_schema()
                if field.sensitive
            ]

        assert [key for key in (*toggles, *secrets) if get_entry(key) is None] == []
        # An unflagged secret leaf is one the settings table would hold in
        # plaintext instead of the encrypted credentials store.
        entries = [get_entry(key) for key in secrets]
        assert [e.key for e in entries if e is not None and not e.sensitive] == []


class TestOutOfScope:
    @pytest.mark.parametrize(
        "key",
        [
            "storage.database_path",
            "web.host",
            "web.port",
            "web.debug",
        ],
    )
    def test_out_of_scope_key_has_no_entry(self, key: str) -> None:
        assert get_entry(key) is None
