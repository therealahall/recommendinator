"""Application state management."""

from typing import Dict, Any, Optional

# Global app state
app_state: Dict[str, Any] = {}


def get_engine():
    """Get recommendation engine from app state."""
    return app_state.get("engine")


def get_storage():
    """Get storage manager from app state."""
    return app_state.get("storage")


def get_embedding_gen():
    """Get embedding generator from app state."""
    return app_state.get("embedding_gen")


def get_config():
    """Get configuration from app state."""
    return app_state.get("config")
