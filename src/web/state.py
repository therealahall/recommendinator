"""Application state management."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import watchfiles

from src.cli.config import load_config
from src.storage.credential_migration import migrate_config_credentials

if TYPE_CHECKING:
    from src.conversation.engine import ConversationEngine
    from src.conversation.memory import MemoryManager
    from src.llm.client import OllamaClient
    from src.llm.embeddings import EmbeddingGenerator
    from src.recommendations.engine import RecommendationEngine
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)


class ConfigWatcher:
    """Watches the config file for changes and triggers hot-reload."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None

    async def start(self, config_path: Path) -> None:
        """Start watching the config file for changes.

        Args:
            config_path: Path to the config file to watch.
        """
        if self.running:
            return
        self._task = asyncio.create_task(self._watch(config_path))

    async def stop(self) -> None:
        """Stop watching for config changes."""
        if self._task is None:
            return
        if not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    @property
    def running(self) -> bool:
        """Whether the watcher is currently running."""
        return self._task is not None and not self._task.done()

    async def _watch(self, config_path: Path) -> None:
        """Watch loop that detects config file changes."""
        logger.info("Config watcher started for %s", config_path)
        try:
            async for _changes in watchfiles.awatch(config_path):
                logger.info("Config file change detected, reloading...")
                success = reload_config()
                if success:
                    logger.info("Config hot-reloaded successfully")
                else:
                    logger.warning("Config hot-reload failed")
        except asyncio.CancelledError:
            logger.info("Config watcher stopped")
            raise
        except Exception:
            logger.exception(
                "Config watcher crashed for %s — hot-reload disabled",
                config_path,
            )


@dataclass
class AppState:
    """Typed application state container."""

    config: dict[str, Any] | None = None
    config_path: str | None = None
    storage: StorageManager | None = None
    engine: RecommendationEngine | None = None
    embedding_gen: EmbeddingGenerator | None = None
    ollama_client: OllamaClient | None = None
    conversation_engine: ConversationEngine | None = None
    memory_manager: MemoryManager | None = None
    config_watcher: ConfigWatcher = field(default_factory=ConfigWatcher)


# Global app state
app_state = AppState()


def get_engine() -> RecommendationEngine | None:
    """Get recommendation engine from app state."""
    return app_state.engine


def get_storage() -> StorageManager | None:
    """Get storage manager from app state."""
    return app_state.storage


def get_embedding_gen() -> EmbeddingGenerator | None:
    """Get embedding generator from app state."""
    return app_state.embedding_gen


def get_config() -> dict[str, Any] | None:
    """Get configuration from app state."""
    return app_state.config


def get_conversation_engine() -> ConversationEngine | None:
    """Get conversation engine from app state."""
    return app_state.conversation_engine


def get_ollama_client() -> OllamaClient | None:
    """Get Ollama client from app state."""
    return app_state.ollama_client


def get_memory_manager() -> MemoryManager | None:
    """Get memory manager from app state."""
    return app_state.memory_manager


def reload_config() -> bool:
    """Reload configuration from disk.

    Re-reads the config file and updates app_state.
    Useful for picking up config changes without restarting.

    Returns:
        True if config was reloaded successfully, False otherwise.
    """
    config_path = app_state.config_path
    if not config_path:
        logger.warning("Cannot reload config: no config path stored")
        return False

    try:
        config = load_config(Path(config_path))
        # Migrate any new sensitive credentials to encrypted DB storage.
        # Mutates config in place: sensitive fields are popped after migration.
        if app_state.storage is not None:
            migrate_config_credentials(config, app_state.storage)
        app_state.config = config
        logger.info("Reloaded config from %s", config_path)
        return True
    except Exception:
        logger.exception("Failed to reload config from %s", config_path)
        return False
