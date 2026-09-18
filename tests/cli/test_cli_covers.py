import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from src.covers.fetch import CoverUnavailable
from tests.factories import make_item, make_storage_mock

from .conftest import _invoke_with_mocks


def test_show_prints_where_the_cover_is_cached(cli_runner: CliRunner) -> None:
    storage = make_storage_mock()
    storage.get_content_item.return_value = make_item(
        db_id=3, cover_url="https://1.2.3.4/c.jpg"
    )

    with patch(
        "src.cli.commands._covers.fill_cover", return_value=Path("/data/covers/3-abc")
    ) as mock_fill:
        result = _invoke_with_mocks(
            cli_runner, ["covers", "show", "3", "--user", "2"], storage
        )

    assert result.exit_code == 0
    assert result.output.strip() == "/data/covers/3-abc"
    assert mock_fill.call_args.kwargs["user_id"] == 2


def test_show_says_an_item_has_no_cover_rather_than_naming_a_url(
    cli_runner: CliRunner,
) -> None:
    storage = make_storage_mock()
    storage.get_content_item.return_value = make_item(title="Unadorned", db_id=3)
    unavailable = CoverUnavailable("this item has no cover art", permanent=False)

    with patch("src.cli.commands._covers.fill_cover", return_value=unavailable):
        result = _invoke_with_mocks(
            cli_runner, ["covers", "show", "3", "--format", "json"], storage
        )

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "db_id": 3,
        "title": "Unadorned",
        "path": None,
        "reason": "this item has no cover art",
    }
