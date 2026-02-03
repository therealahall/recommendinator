"""Metadata enrichment system for filling gaps in content metadata.

This module provides a plugin-based system for enriching content items
with metadata from external APIs (TMDB, OpenLibrary, RAWG, etc.).

Key components:
- EnrichmentProvider: ABC for enrichment plugins
- EnrichmentRegistry: Singleton registry for provider discovery
- EnrichmentManager: Background worker for processing enrichment jobs
- RateLimiter: Token bucket rate limiter for API calls
"""

from src.enrichment.provider_base import (
    ConfigField,
    EnrichmentProvider,
    EnrichmentResult,
    ProviderError,
    ProviderInfo,
)
from src.enrichment.rate_limiter import RateLimiter
from src.enrichment.registry import EnrichmentRegistry, get_enrichment_registry

__all__ = [
    "ConfigField",
    "EnrichmentProvider",
    "EnrichmentRegistry",
    "EnrichmentResult",
    "ProviderError",
    "ProviderInfo",
    "RateLimiter",
    "get_enrichment_registry",
]
