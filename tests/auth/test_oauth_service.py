import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from src.auth.service import connect_with_code, oauth_plugins
from src.ingestion.plugin_base import OAuthError
from src.storage.manager import StorageManager
from tests.fakes.source_plugins import FAKE_CODE

SERVICE_LOGGER = "src.auth.service"


@pytest.mark.usefixtures("registry_with_oauth_fakes")
def test_a_token_that_cannot_be_saved_logs_its_class_not_a_traceback(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    storage = StorageManager(sqlite_path=tmp_path / "test.db")
    storage.sources.upsert(1, "paste_work", "fake_paste", {}, enabled=True)

    with (
        caplog.at_level(logging.ERROR, logger=SERVICE_LOGGER),
        patch.object(storage.credentials, "save", side_effect=OSError("disk full")),
        pytest.raises(OAuthError),
    ):
        connect_with_code(
            oauth_plugins()["fake_paste"], "paste_work", FAKE_CODE, storage, 1
        )

    records = [record for record in caplog.records if record.name == SERVICE_LOGGER]
    assert [record.getMessage() for record in records] == [
        "Failed to save Fake Paste token to database: OSError"
    ]
    assert not any(record.exc_info for record in records)
