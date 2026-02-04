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


def get_conversation_engine() -> Any:
    """Get conversation engine from app state."""
    return app_state.get("conversation_engine")


def get_ollama_client() -> Any:
    """Get Ollama client from app state."""
    return app_state.get("ollama_client")
