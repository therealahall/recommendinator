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
from src.storage.manager import StorageManager

_SECRET_KEY = "enrichment.providers.keyed.api_key"


class KeyedProvider(EnrichmentProvider):
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
        return EnrichmentResult(genres=["Action"], match_quality="high")


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "test.db")


@pytest.fixture()
def registry() -> EnrichmentRegistry:
    reg = EnrichmentRegistry()
    reg._discovered = True
    reg.register(KeyedProvider())
    return reg


def _config() -> dict[str, Any]:
    return {"enrichment": {"enabled": True, "providers": {"keyed": {"enabled": True}}}}


class TestProviderConfigInjection:
    def test_a_config_api_key_never_reaches_the_provider(
        self, storage: StorageManager, registry: EnrichmentRegistry
    ) -> None:
        """config.yaml holds no secret: a value left under a sensitive field is
        overwritten by the secret store, not merged in behind it."""
        storage.secrets.set(_SECRET_KEY, "stored_key")
        config = {
            "enrichment": {
                "providers": {"keyed": {"enabled": True, "api_key": "file_key"}}
            }
        }

        manager = EnrichmentManager(storage, config, registry)

        assert manager._get_provider_config("keyed")["api_key"] == "stored_key"


class TestSecretResolutionCaching:
    def test_secret_read_once_across_items(
        self,
        storage: StorageManager,
        registry: EnrichmentRegistry,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        storage.secrets.set(_SECRET_KEY, "cred_key")
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
        assert reads == [_SECRET_KEY]
