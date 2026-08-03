"""Tests proving the repository-wide isolation fixtures reach plugin-local tests."""

import logging
import os
from datetime import date
from pathlib import Path

from src.storage.manager import StorageManager
from src.utils.dates import local_date_from_iso_timestamp
from src.web import app


class TestPluginLocalIsolationRegression:
    """Plugin-local tests under ``src/`` ran without the isolation fixtures.

    Reported: ``testpaths`` collects ``tests`` and ``src``, but the autouse
    fixtures that redirect the credential key and neutralise production logging
    lived in ``tests/conftest.py``. A conftest only applies to its own subtree,
    so the ``test_<plugin>.py`` files next to each plugin ran with neither.

    Root cause: the fixtures were scoped to one of the three trees tests are
    collected from (``tests/``, ``src/``, and ``private/`` on demand).

    Fix: the fixtures moved to the repository-root ``conftest.py``, which is
    above all three. These tests live next to the plugins deliberately — they
    fail if the fixtures ever narrow back to a subtree that excludes them, and
    there is one per autouse fixture that conftest defines, the timezone pin
    included, so no leg of the isolation can quietly stop reaching here.
    """

    def test_credentials_written_from_a_plugin_test_land_in_tmp_path(
        self, tmp_path: Path
    ) -> None:
        """A real StorageManager encrypts against a key inside this test's tmp_path.

        The database lives in a subdirectory so the isolated key path
        (``tmp_path/.credential_key``) and the co-located default the manager
        would otherwise use (``tmp_path/data/.credential_key``) are distinct:
        only the redirect puts the key where this asserts it is.
        """
        isolated_key_path = Path(os.environ["RECOMMENDINATOR_KEY_PATH"])
        assert isolated_key_path.parent == tmp_path

        database_dir = tmp_path / "data"
        storage = StorageManager(sqlite_path=database_dir / "library.db")
        storage.save_credential(1, "steam", "api_key", "plugin-local-secret")

        assert storage.get_credential(1, "steam", "api_key") == "plugin-local-secret"
        assert isolated_key_path.exists()
        assert not (database_dir / ".credential_key").exists()

    def test_configure_logging_cannot_attach_the_production_log_handler(self) -> None:
        """Configuring logging from a plugin test opens no production log file.

        ``configure_logging`` is called through the module attribute, not a
        ``from`` import, because the fixture patches it on the module — a name
        bound at import time would call the real function and reintroduce the
        handler this asserts is absent.
        """
        root_logger = logging.getLogger()
        handlers_before = list(root_logger.handlers)

        app.configure_logging({"logging": {"file": "logs/recommendations.log"}})

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
        """A plugin-local test runs on UTC by default, not on the host's zone.

        The Trakt plugin's date tests request ``host_timezone`` explicitly, so
        they prove it is *requestable* from under ``src/`` and nothing more. A
        change that left it requestable but no longer autouse here would leave
        every other plugin-local date assertion reading the host's zone, which
        passes on a UTC machine and fails on the contributor's laptop.

        Deliberately requests no fixture: the default is the whole subject.
        """
        assert os.environ["TZ"] == "UTC"

        # 02:30Z is the previous calendar day everywhere west of UTC, so an
        # unpinned host narrows it to the 9th rather than the 10th.
        assert local_date_from_iso_timestamp("2024-03-10T02:30:00Z") == date(
            2024, 3, 10
        )
