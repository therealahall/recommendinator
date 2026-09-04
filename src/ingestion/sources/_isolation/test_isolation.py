"""Tests proving the repository-wide isolation fixtures reach plugin-local tests."""

import logging
import os
import sys
from datetime import date
from pathlib import Path

from src.storage.manager import StorageManager
from src.utils import logging as log_config
from src.utils.dates import local_date_from_iso_timestamp


class TestPluginLocalIsolationRegression:
    def test_credentials_written_from_a_plugin_test_land_in_tmp_path(
        self, tmp_path: Path
    ) -> None:
        isolated_key_path = Path(os.environ["RECOMMENDINATOR_KEY_PATH"])
        assert isolated_key_path.parent == tmp_path

        database_dir = tmp_path / "data"
        storage = StorageManager(sqlite_path=database_dir / "library.db")
        storage.credentials.save(1, "steam", "api_key", "plugin-local-secret")

        assert storage.credentials.get(1, "steam", "api_key") == "plugin-local-secret"
        assert isolated_key_path.exists()
        assert not (database_dir / ".credential_key").exists()

    def test_configure_logging_cannot_attach_the_production_log_handler(self) -> None:
        root_logger = logging.getLogger()
        handlers_before = list(root_logger.handlers)

        log_config.configure_logging(
            {"logging": {"file": "data/logs/recommendations.log"}},
            console_stream=sys.stdout,
            console_tracebacks=True,
            console_floor=logging.NOTSET,
        )

        assert not [
            handler
            for handler in root_logger.handlers
            if isinstance(handler, logging.FileHandler)
            and handler.baseFilename.endswith("recommendations.log")
        ]
        assert root_logger.handlers == handlers_before

    def test_the_process_timezone_is_pinned_without_requesting_the_fixture(
        self,
    ) -> None:
        assert os.environ["TZ"] == "UTC"

        assert local_date_from_iso_timestamp("2024-03-10T02:30:00Z") == date(
            2024, 3, 10
        )
