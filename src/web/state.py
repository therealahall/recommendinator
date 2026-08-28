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

from src.config.service import load_config
from src.storage.credential_migration import migrate_config_credentials
from src.storage.global_secrets import migrate_config_secrets
from src.storage.settings_migration import migrate_config_settings

if TYPE_CHECKING:
    from src.recommendations.engine import RecommendationEngine
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)


class ConfigWatcher:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None

    async def start(self, config_path: Path) -> None:
        if self.running:
            return
        self._task = asyncio.create_task(self._watch(config_path))

    async def stop(self) -> None:
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
        return self._task is not None and not self._task.done()

    async def _watch(self, config_path: Path) -> None:
        logger.info("Config watcher started for %s", config_path)
        try:
            async for _changes in watchfiles.awatch(config_path):
                logger.info("Config file change detected, reloading...")
                # ``reload_config`` waits on the config lock, which a settings save
                # holds across a whole registry sweep, and then does a file read, five
                # migrations and a run of Fernet decrypts.
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
    config: dict[str, Any] | None = None
    config_path: str | None = None
    storage: StorageManager | None = None
    engine: RecommendationEngine | None = None
    config_watcher: ConfigWatcher = field(default_factory=ConfigWatcher)


app_state = AppState()

# The one serialiser for every read-copy-store of the running config. Four
# paths write it: ``PUT /api/settings``, ``DELETE /api/settings/{key}``,
# ``POST /api/config/reload`` and the config watcher, all of them in threadpool
# workers, so no single thread keeps them apart.
_config_lock = threading.Lock()


@contextmanager
def locked_running_config() -> Iterator[dict[str, Any] | None]:
    with _config_lock:
        yield app_state.config


def get_engine() -> RecommendationEngine | None:
    return app_state.engine


def get_storage() -> StorageManager | None:
    return app_state.storage


def get_config() -> dict[str, Any] | None:
    return app_state.config


def reload_config() -> bool:
    """The whole assembly runs under the config lock. Taking it only for the
    rebind would still let a settings save resolve the outgoing dict and
    publish into it after this replaced it.
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
                migrate_config_secrets(config, app_state.storage)
            app_state.config = config
        logger.info("Reloaded config from %s", config_path)
        return True
    except Exception:
        logger.exception("Failed to reload config from %s", config_path)
        return False
