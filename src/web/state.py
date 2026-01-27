"""Application state management."""

from typing import Any

# Global app state
app_state: dict[str, Any] = {}


def get_engine() -> Any:
    """Get recommendation engine from app state."""
    return app_state.get("engine")


def get_storage() -> Any:
    """Get storage manager from app state."""
    return app_state.get("storage")


def get_embedding_gen() -> Any:
    """Get embedding generator from app state."""
    return app_state.get("embedding_gen")


def get_config() -> Any:
    """Get configuration from app state."""
    return app_state.get("config")
