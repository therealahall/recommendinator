from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest
from click.testing import CliRunner, Result

from src.storage.manager import StorageManager
from tests.cli.conftest import _invoke_with_mocks
from tests.factories import authenticated_client, booted_web_app


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "theme.db")


def _run(storage: StorageManager, args: list[str]) -> Result:
    return cast(Result, _invoke_with_mocks(CliRunner(), args, storage))


def _shown(storage: StorageManager) -> object:
    return json.loads(_run(storage, ["theme", "show", "--format", "json"]).output)


class TestTheme:
    def test_a_set_theme_reads_back_in_the_shape_the_api_answers(
        self, storage: StorageManager
    ) -> None:
        assert _run(storage, ["theme", "set", "snowstorm"]).exit_code == 0

        shown = _run(storage, ["theme", "show", "--format", "json"])

        assert json.loads(shown.output) == {"theme": "snowstorm"}

    def test_a_user_who_has_picked_nothing_reads_empty(
        self, storage: StorageManager
    ) -> None:
        shown = _run(storage, ["theme", "show", "--format", "json"])

        assert json.loads(shown.output) == {"theme": ""}

    def test_every_id_it_lists_is_one_it_accepts(self, storage: StorageManager) -> None:
        listed = json.loads(_run(storage, ["theme", "list", "--format", "json"]).output)

        assert listed
        for theme in listed:
            assert _run(storage, ["theme", "set", theme["id"]]).exit_code == 0

    def test_a_theme_this_install_does_not_have_is_refused(
        self, storage: StorageManager
    ) -> None:
        result = _run(storage, ["theme", "set", "../evil"])

        assert result.exit_code != 0
        assert storage.ui_settings.get_theme(1) == ""

    def test_re_picking_the_stored_theme_is_not_reported_as_a_failure(
        self, storage: StorageManager
    ) -> None:
        _run(storage, ["theme", "set", "snowstorm"])

        assert _run(storage, ["theme", "set", "snowstorm"]).exit_code == 0

    def test_an_unknown_user_is_reported_rather_than_silently_dropped(
        self, storage: StorageManager
    ) -> None:
        result = _run(storage, ["theme", "set", "snowstorm", "--user", "999"])

        assert result.exit_code != 0
        assert "No user with id 999" in result.output
        assert storage.ui_settings.get_theme(999) == ""


class TestTheThemeGroupCostsTheCliNoWebServer:
    def test_reading_the_installed_themes_loads_no_asgi_stack(self) -> None:
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys\n"
                "import src.cli.commands._theme\n"
                "print(any(name.startswith(('starlette', 'anyio')) "
                "for name in sys.modules))\n",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        assert child.stdout.strip() == "False"


class TestBothDoorsOnOneStore:
    def test_a_pick_made_at_either_door_reads_back_the_same_at_both(
        self, storage: StorageManager
    ) -> None:
        with booted_web_app(storage, {}) as app:
            client = authenticated_client(app)

            written = client.put("/api/users/1/theme", json={"theme": "snowstorm"})
            assert written.json() == _shown(storage)

            _run(storage, ["theme", "set", "nord"])
            assert client.get("/api/users/1/theme").json() == _shown(storage)
