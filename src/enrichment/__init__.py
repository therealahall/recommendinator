"""Metadata enrichment: providers fill gaps from external APIs.

- ``provider_base.EnrichmentProvider``: ABC for enrichment providers
- ``registry.EnrichmentRegistry``: the discovered providers, by name
- ``manager.EnrichmentManager``: background worker for enrichment jobs
- ``rate_limiter.RateLimiter``: token bucket rate limiter for API calls
"""
