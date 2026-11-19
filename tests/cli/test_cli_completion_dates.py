import json
from datetime import date
from pathlib import Path

import pytest
from click.testing import CliRunner

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.manager import StorageManager
from tests.cli.conftest import _invoke_with_mocks


@pytest.mark.parametrize("operation", ["complete", "edit"])
def test_cli_historical_date_overrides_existing_date_without_an_interactive_prompt(
    cli_runner: CliRunner, tmp_path: Path, operation: str
) -> None:
    storage = StorageManager(tmp_path / "dates.db")
    db_id = storage.save_content_item(
        ContentItem(
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            date_completed=date(2024, 1, 1),
        )
    )
    args = (
        ["complete", "--type", "book", "--title", "Dune"]
        if operation == "complete"
        else ["library", "edit", "--id", str(db_id)]
    )

    result = _invoke_with_mocks(
        cli_runner,
        [*args, "--date-completed", "1999-06-15", "--format", "json"],
        storage,
    )

    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    assert document["id" if operation == "complete" else "db_id"] == db_id
    stored = storage.get_content_item(db_id)
    assert stored is not None
    assert stored.date_completed == date(1999, 6, 15)
    if operation == "edit":
        assert document["date_completed"] == "1999-06-15"

    result = _invoke_with_mocks(cli_runner, [*args, "--rating", "4"], storage)
    assert result.exit_code == 0, result.output
    stored = storage.get_content_item(db_id)
    assert stored is not None
    assert stored.date_completed == date(1999, 6, 15)


@pytest.mark.parametrize("operation", ["complete", "edit"])
@pytest.mark.parametrize(
    "supplied",
    [
        "yesterday",
        "2024-02-30",
        "20240101",
        "2024-W01-1",
        "2024-01-01T12:00:00Z",
        "",
        "9999-12-31",
    ],
)
def test_cli_invalid_date_is_readable_and_cannot_partially_complete_or_rate_an_item(
    cli_runner: CliRunner, tmp_path: Path, operation: str, supplied: str
) -> None:
    storage = StorageManager(tmp_path / "dates.db")
    db_id = storage.save_content_item(
        ContentItem(
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
            rating=2,
        )
    )
    args = (
        ["complete", "--type", "book", "--title", "Dune"]
        if operation == "complete"
        else ["library", "edit", "--id", str(db_id), "--status", "completed"]
    )

    result = _invoke_with_mocks(
        cli_runner, [*args, "--date-completed", supplied, "--rating", "5"], storage
    )

    assert result.exit_code != 0
    assert "completion date" in result.output.lower()
    assert ("future" if supplied == "9999-12-31" else "YYYY-MM-DD") in result.output
    stored = storage.get_content_item(db_id)
    assert stored is not None
    assert (stored.status, stored.rating, stored.date_completed) == (
        ConsumptionStatus.UNREAD,
        2,
        None,
    )
