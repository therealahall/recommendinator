from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from src.enrichment.provider_base import (
    ConfigField,
    EnrichmentProvider,
    EnrichmentResult,
)
from src.enrichment.registry import EnrichmentRegistry
from src.models.content import ContentItem, ContentType
from src.settings import metadata

PRIVATE_PROVIDER_NAME = "personal_shelf"
UNRENDERABLE_PROVIDER_NAME = "personal_ledger"
UNRENDERABLE_FIELD_NAME = "shelf_map"
UNFLAGGED_PROVIDER_NAME = "personal_vault"


class PrivateProvider(EnrichmentProvider):
    """Shaped like a provider dropped into ``private/plugins/``: nothing in the
    repository names it, and it states no precedence.
    """

    @property
    def name(self) -> str:
        return PRIVATE_PROVIDER_NAME

    @property
    def display_name(self) -> str:
        return "Personal Shelf"

    @property
    def description(self) -> str:
        return "Genres and covers from a shelf of my own"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.BOOK]

    @property
    def requires_api_key(self) -> bool:
        return True

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="api_key",
                field_type=str,
                required=True,
                description="Token for the shelf",
                sensitive=True,
            ),
            ConfigField(
                name="include_wishlist",
                field_type=bool,
                required=False,
                default=False,
                description="Enrich from wishlisted editions too",
            ),
        ]

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        return []

    def enrich(
        self, item: ContentItem, config: dict[str, Any]
    ) -> EnrichmentResult | None:
        return EnrichmentResult(genres=["Fantasy"], match_quality="high")


class UnrenderableFieldProvider(PrivateProvider):
    """Declares a field shape no settings control can hold, as a private provider
    author reaching past the schema's types does.
    """

    @property
    def name(self) -> str:
        return UNRENDERABLE_PROVIDER_NAME

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name=UNRENDERABLE_FIELD_NAME,
                field_type=dict,
                required=False,
                description="A shelf per content type",
            )
        ]


class UnflaggedApiKeyProvider(PrivateProvider):
    """The author's mistake: a provider saying it needs an api key while its
    schema flags no field sensitive.
    """

    @property
    def name(self) -> str:
        return UNFLAGGED_PROVIDER_NAME

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(
                name="api_key",
                field_type=str,
                required=True,
                description="Token for the vault",
            )
        ]


def _installed_beside_the_built_ins(provider: EnrichmentProvider) -> Iterator[str]:
    registry = EnrichmentRegistry.get_instance()
    registry.discover_providers()
    registry.register(provider)
    # Process-global like the registry singleton: a pair already warned about
    # would otherwise go unlogged in whichever test installs the fake second.
    metadata._warned_keys.clear()
    yield provider.name
    EnrichmentRegistry.reset_instance()


@pytest.fixture()
def registry_with_a_private_provider() -> Iterator[str]:
    """Installed beside the built-ins, as a private plugin is."""
    yield from _installed_beside_the_built_ins(PrivateProvider())


@pytest.fixture()
def registry_with_an_unrenderable_field() -> Iterator[str]:
    yield from _installed_beside_the_built_ins(UnrenderableFieldProvider())


@pytest.fixture()
def registry_with_an_unflagged_api_key() -> Iterator[str]:
    yield from _installed_beside_the_built_ins(UnflaggedApiKeyProvider())
