import logging

import pytest

from src.enrichment.provider_base import TRAILING_PRECEDENCE, EnrichmentProvider
from src.enrichment.registry import get_enrichment_registry
from src.settings.metadata import (
    PROVIDER_ORDER_KEY,
    SettingMetadata,
    all_entries,
    default_config,
    default_of,
    flat_defaults,
    get_entry,
)
from tests.fakes.enrichment_providers import UNRENDERABLE_FIELD_NAME


def _installed_providers() -> dict[str, EnrichmentProvider]:
    providers = get_enrichment_registry().get_all_providers()
    # A failed provider import is swallowed by the registry, and an empty one
    # would pass every sweep below without reading a provider.
    assert providers
    return providers


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

    def test_no_key_is_declared_twice(self) -> None:
        """A provider declaring its own ``enabled`` field would otherwise get a
        second row, and ``get_entry`` would answer with whichever came first."""
        keys = [entry.key for entry in all_entries()]

        assert sorted(keys) == sorted(set(keys))

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
        for name, provider in _installed_providers().items():
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

    def test_the_default_order_ranks_every_installed_provider_exactly_once(
        self,
    ) -> None:
        assert sorted(default_of(PROVIDER_ORDER_KEY)) == sorted(_installed_providers())

    def test_no_provider_calls_a_third_party_until_the_operator_enables_it(
        self,
    ) -> None:
        enabled_out_of_the_box = [
            name
            for name in _installed_providers()
            if default_of(f"enrichment.providers.{name}.enabled") is not False
        ]

        assert enabled_out_of_the_box == []


class TestAProviderNoListNames:
    def test_its_own_fields_are_settings_and_its_secret_is_masked(
        self, registry_with_a_private_provider: str
    ) -> None:
        prefix = f"enrichment.providers.{registry_with_a_private_provider}."
        toggle = get_entry(f"{prefix}enabled")
        option = get_entry(f"{prefix}include_wishlist")
        secret = get_entry(f"{prefix}api_key")

        assert toggle is not None and toggle.default is False
        assert option is not None and option.type == "bool"
        assert secret is not None and secret.sensitive

    def test_the_default_order_ranks_it_behind_every_provider_stating_a_precedence(
        self, registry_with_a_private_provider: str
    ) -> None:
        order = default_of(PROVIDER_ORDER_KEY)
        stated = [
            name
            for name, provider in _installed_providers().items()
            if provider.precedence < TRAILING_PRECEDENCE
        ]

        assert max(order.index(name) for name in stated) < order.index(
            registry_with_a_private_provider
        )


class TestAFieldNoControlCanHold:
    def test_the_log_names_the_provider_and_field_left_off_the_page(
        self,
        registry_with_an_unrenderable_field: str,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        key = (
            f"enrichment.providers.{registry_with_an_unrenderable_field}."
            f"{UNRENDERABLE_FIELD_NAME}"
        )

        with caplog.at_level(logging.WARNING):
            entry = get_entry(key)

        assert entry is None
        assert registry_with_an_unrenderable_field in caplog.text
        assert UNRENDERABLE_FIELD_NAME in caplog.text

    def test_the_rebuild_behind_every_lookup_logs_it_once(
        self,
        registry_with_an_unrenderable_field: str,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A settings page rebuilds the provider entries once per leaf read, so
        an undeduplicated warning lands dozens of times per load."""
        key = f"enrichment.providers.{registry_with_an_unrenderable_field}.enabled"

        with caplog.at_level(logging.WARNING):
            for _ in range(3):
                get_entry(key)

        warnings = [
            record
            for record in caplog.records
            if UNRENDERABLE_FIELD_NAME in record.getMessage()
        ]
        assert len(warnings) == 1


class TestAProviderThatFlagsNoCredential:
    def test_its_api_key_is_a_secret_rather_than_a_plaintext_settings_row(
        self, registry_with_an_unflagged_api_key: str
    ) -> None:
        entry = get_entry(
            f"enrichment.providers.{registry_with_an_unflagged_api_key}.api_key"
        )

        assert entry is not None
        assert entry.sensitive


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
