"""Application state management."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import watchfiles

from src.cli.config import load_config
from src.storage.credential_migration import migrate_config_credentials
from src.storage.global_secrets import migrate_config_secrets
from src.storage.settings_migration import migrate_config_settings
from src.storage.source_migration import (
    migrate_source_config_plugins,
    migrate_source_labels,
)

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
                # Off the loop. ``reload_config`` waits on the config lock,
                # which a settings save holds across a whole registry sweep,
                # and then does a file read, five migrations and a run of
                # Fernet decrypts. Called straight from this task, a config
                # file touched mid-save parks the server: no request accepted,
                # no SSE chunk sent, for as long as the save runs.
                success = await asyncio.to_thread(reload_config)
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

# The one serialiser for every read-copy-store of the running config. Four
# paths write it: ``PUT /api/settings``, ``DELETE /api/settings/{key}``,
# ``POST /api/config/reload`` and the config watcher, all of them in threadpool
# workers, so no single thread keeps them apart. Unserialised, a reload
# rebinding ``app_state.config`` while a save is mid-request leaves the save
# publishing into the dict nobody reads any more: the database keeps the new
# value, the server runs the old one, and nothing reports an error.
_config_lock = threading.Lock()


@contextmanager
def locked_running_config() -> Iterator[dict[str, Any] | None]:
    """Yield the running config with the config lock held for the whole block.

    The binding is resolved inside the lock, which is the point of the helper:
    a caller that resolved it first — a ``Depends`` guard, say — would be
    holding a dict :func:`reload_config` can replace before the caller stores
    into it, and a lock that does not cover the read cannot stop that.
    """
    with _config_lock:
        yield app_state.config


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

    The assembled config is bound in one statement, and the dict it replaces is
    never touched. Readers resolve the running config through ``get_config``
    from a threadpool worker, so anything short of a single rebind would hand
    one of them a config part-way through being rewritten.

    The whole assembly runs under the config lock. Taking it only for the
    rebind would still let a settings save resolve the outgoing dict and
    publish into it after this replaced it.

    Returns:
        True if config was reloaded successfully, False otherwise.
    """
    config_path = app_state.config_path
    if not config_path:
        logger.warning("Cannot reload config: no config path stored")
        return False

    try:
        with _config_lock:
            config = load_config(Path(config_path))
            # Re-assemble the effective config on hot-reload. Mutates config in
            # place: in-scope sections are rebuilt from const/YAML/DB layers (DB
            # wins), and sensitive fields are popped after credential migration.
            if app_state.storage is not None:
                migrate_config_settings(config, app_state.storage)
                migrate_config_credentials(config, app_state.storage)
                # Relabel stored goodreads source values and plugin names after
                # the plugin rename
                migrate_source_labels(app_state.storage)
                migrate_source_config_plugins(app_state.storage)
                migrate_config_secrets(config, app_state.storage)
            app_state.config = config
        logger.info("Reloaded config from %s", config_path)
        return True
    except Exception:
        logger.exception("Failed to reload config from %s", config_path)
        return False
