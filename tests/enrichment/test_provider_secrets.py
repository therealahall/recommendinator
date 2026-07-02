"""Tests that enrichment providers read secrets from encrypted credentials.

After boot migration a provider's ``api_key`` lives in the encrypted
``credentials`` table, not plaintext config. These tests use a real
``StorageManager`` (isolated temp DB) to prove the enrichment layer sources the
key from credentials and degrades gracefully when it is absent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import src.enrichment.manager as manager_module
from src.enrichment.manager import EnrichmentManager
from src.enrichment.provider_base import (
    ConfigField,
    EnrichmentProvider,
    EnrichmentResult,
)
from src.enrichment.registry import EnrichmentRegistry
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.global_secrets import migrate_config_secrets
from src.storage.manager import StorageManager

_SECRET_KEY = "enrichment.providers.keyed.api_key"


class KeyedProvider(EnrichmentProvider):
    """Provider with a sensitive ``api_key`` field that records the config it gets."""

    def __init__(self, name: str = "keyed") -> None:
        self._name = name
        self.received_configs: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return "Keyed Provider"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.MOVIE]

    @property
    def requires_api_key(self) -> bool:
        return True

    @property
    def rate_limit_requests_per_second(self) -> float:
        return 100.0

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="api_key",
                field_type=str,
                required=True,
                description="Test API key",
                sensitive=True,
            ),
        ]

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        return []

    def enrich(
        self, item: ContentItem, config: dict[str, Any]
    ) -> EnrichmentResult | None:
        self.received_configs.append(dict(config))
        return EnrichmentResult(
            external_id=f"keyed:{item.id}",
            genres=["Action"],
            match_quality="high",
            provider=self.name,
        )


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    """Create a StorageManager backed by an isolated temp DB."""
    return StorageManager(sqlite_path=tmp_path / "test.db")


@pytest.fixture()
def registry() -> EnrichmentRegistry:
    """A registry pre-seeded with only the KeyedProvider (no disk discovery)."""
    reg = EnrichmentRegistry()
    reg._discovered = True
    reg.register(KeyedProvider())
    return reg


def _config() -> dict[str, Any]:
    """Config with the provider enabled but no plaintext api_key."""
    return {"enrichment": {"providers": {"keyed": {"enabled": True}}}}


class TestProviderConfigInjection:
    """The provider config seam overlays the secret from credentials."""

    def test_api_key_read_from_credentials(
        self, storage: StorageManager, registry: EnrichmentRegistry
    ) -> None:
        """A stored secret is injected into the provider config from credentials."""
        storage.set_global_secret(_SECRET_KEY, "cred_key")
        manager = EnrichmentManager(storage, _config(), registry)

        provider_config = manager._get_provider_config("keyed")

        assert provider_config["api_key"] == "cred_key"

    def test_absent_key_leaves_field_unset(
        self, storage: StorageManager, registry: EnrichmentRegistry
    ) -> None:
        """With no stored secret the api_key stays absent (graceful degradation)."""
        manager = EnrichmentManager(storage, _config(), registry)

        provider_config = manager._get_provider_config("keyed")

        assert "api_key" not in provider_config

    def test_boot_migration_then_enrichment_reads_credential(
        self, storage: StorageManager
    ) -> None:
        """End to end: YAML key is swept away, provider still gets it from credentials.

        Uses the real registry leaf ``enrichment.providers.tmdb.api_key`` so the
        metadata-driven boot sweep actually relocates it.
        """
        registry = EnrichmentRegistry()
        registry._discovered = True
        registry.register(KeyedProvider(name="tmdb"))

        config = {
            "enrichment": {
                "providers": {"tmdb": {"enabled": True, "api_key": "yaml_key"}}
            }
        }
        migrate_config_secrets(config, storage)

        # The plaintext key is gone from the config the manager runs on.
        assert "api_key" not in config["enrichment"]["providers"]["tmdb"]

        manager = EnrichmentManager(storage, config, registry)
        assert manager._get_provider_config("tmdb")["api_key"] == "yaml_key"


class TestProviderReceivesSecretDuringJob:
    """A full enrichment run hands the credential-sourced key to enrich()."""

    def test_enrich_receives_credential_api_key(
        self, storage: StorageManager, registry: EnrichmentRegistry
    ) -> None:
        """The running job passes the credential api_key into provider.enrich()."""
        storage.set_global_secret(_SECRET_KEY, "job_key")
        storage.save_content_item(
            ContentItem(
                id="movie1",
                user_id=1,
                title="The Matrix",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
            )
        )

        provider = registry.get_provider("keyed")
        assert isinstance(provider, KeyedProvider)

        manager = EnrichmentManager(storage, _config(), registry)
        manager.start_enrichment(user_id=1)
        assert manager._wait_for_completion()

        assert provider.received_configs
        assert all(cfg["api_key"] == "job_key" for cfg in provider.received_configs)


class TestSecretResolutionCaching:
    """A provider's secret is resolved once per run, not once per item.

    Regression: ``_get_provider_config`` used to call ``read_secret`` (a SQLite
    query + Fernet decrypt) for every sensitive field on every content item.
    The per-manager cache resolves each secret once and reuses it for the run.
    """

    def test_secret_read_once_across_items(
        self,
        storage: StorageManager,
        registry: EnrichmentRegistry,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Three items enrich, but the api_key is decrypted from storage once."""
        storage.set_global_secret(_SECRET_KEY, "cred_key")
        for i in range(3):
            storage.save_content_item(
                ContentItem(
                    id=f"movie{i}",
                    user_id=1,
                    title=f"Film {i}",
                    content_type=ContentType.MOVIE,
                    status=ConsumptionStatus.UNREAD,
                )
            )

        reads: list[str] = []
        real_read = manager_module.read_secret

        def counting_read(store: StorageManager, key: str) -> str | None:
            reads.append(key)
            return real_read(store, key)

        monkeypatch.setattr(manager_module, "read_secret", counting_read)

        manager = EnrichmentManager(storage, _config(), registry)
        manager.start_enrichment(user_id=1)
        assert manager._wait_for_completion()

        provider = registry.get_provider("keyed")
        assert isinstance(provider, KeyedProvider)
        assert len(provider.received_configs) == 3
        assert all(cfg["api_key"] == "cred_key" for cfg in provider.received_configs)
        # Read exactly once despite three processed items.
        assert reads == [_SECRET_KEY]
