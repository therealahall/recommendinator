"""Application state management."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.cli.config import load_config

if TYPE_CHECKING:
    from src.conversation.engine import ConversationEngine
    from src.llm.client import OllamaClient
    from src.llm.embeddings import EmbeddingGenerator
    from src.recommendations.engine import RecommendationEngine
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)

# Global app state
app_state: dict[str, Any] = {}


def get_engine() -> RecommendationEngine | None:
    """Get recommendation engine from app state."""
    return app_state.get("engine")


def get_storage() -> StorageManager | None:
    """Get storage manager from app state."""
    return app_state.get("storage")


def get_embedding_gen() -> EmbeddingGenerator | None:
    """Get embedding generator from app state."""
    return app_state.get("embedding_gen")


def get_config() -> dict[str, Any] | None:
    """Get configuration from app state."""
    return app_state.get("config")


def get_conversation_engine() -> ConversationEngine | None:
    """Get conversation engine from app state."""
    return app_state.get("conversation_engine")


def get_ollama_client() -> OllamaClient | None:
    """Get Ollama client from app state."""
    return app_state.get("ollama_client")


def get_config_path() -> str | None:
    """Get configuration file path from app state."""
    return app_state.get("config_path")


def reload_config() -> bool:
    """Reload configuration from disk.

    Re-reads the config file and updates app_state.
    Useful for picking up config changes without restarting.

    Returns:
        True if config was reloaded successfully, False otherwise.
    """
    config_path = app_state.get("config_path")
    if not config_path:
        logger.warning("Cannot reload config: no config path stored")
        return False

    try:
        config = load_config(Path(config_path))
        app_state["config"] = config
        logger.info(f"Reloaded config from {config_path}")
        return True
    except Exception as error:
        logger.error(f"Failed to reload config: {error}")
        return False
