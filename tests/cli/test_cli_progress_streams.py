"""``docs/CLI.md`` promises progress lines on stderr, CLI-wide.

It was true of ``recommend`` and ``profile regenerate`` alone. The sweep below
decides which commands print progress, so a hand list cannot go stale.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

import src.cli as cli_package
from src.auth.trakt import DevicePollResult, DevicePollStatus
from src.enrichment.manager import EnrichmentJobStatus, EnrichmentManager
from src.ingestion.sync import SyncResult
from src.storage.manager import StorageManager

from .conftest import _invoke_with_mocks

_CLI_ROOT = Path(cli_package.__file__).parent

_ECHO_SINKS = {"click.echo", "click.secho"}

#: Named, so a sweep that discovered nothing fails here rather than
#: reporting every command clean.
_MODULES_THAT_PRINT_PROGRESS = {
    "commands/_auth.py",
    "commands/_enrichment.py",
    "commands/_profile.py",
    "commands/_recommend.py",
    "commands/_update.py",
}

#: An ellipsis that is not a command reporting progress, with the reason.
_NOT_PROGRESS = {
    (
        "commands/_enrichment.py",
        "f'    ... and {len(status.errors) - 5} more'",
    ): "the tail of the error list in the final tally, which is the result",
}

_TREES = {
    path.relative_to(_CLI_ROOT).as_posix(): ast.parse(path.read_text(encoding="utf-8"))
    for path in sorted(_CLI_ROOT.rglob("*.py"))
}


def _echo_calls(tree: ast.AST) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and ast.unparse(node.func) in _ECHO_SINKS
    ]


def _literal_parts(node: ast.expr) -> list[str]:
    """The string this expression spells out, skipping any call inside it.

    ``update`` builds its header with ``+ '...'``, so a bare ``Constant`` is
    too narrow. Descending into calls is too wide: ``click.echo(tabulate(...))``
    would inherit the row truncation marker.
    """
    if isinstance(node, ast.Constant):
        return [node.value] if isinstance(node.value, str) else []
    if isinstance(node, ast.Call):
        return []
    return [
        part for child in ast.iter_child_nodes(node) for part in _literal_parts(child)  # type: ignore[arg-type]
    ]


def _ellipsis_echoes(tree: ast.AST) -> list[ast.Call]:
    """An ellipsis is how a command spells "still working".

    Not only a trailing one: ``auth connect`` puts the Ctrl-C hint after its.
    """
    return [
        call
        for call in _echo_calls(tree)
        if call.args and any("..." in part for part in _literal_parts(call.args[0]))
    ]


def _progress_lines(module: str, tree: ast.AST) -> set[str]:
    return {
        f"{ast.unparse(call.args[0])} (line {call.lineno})"
        for call in _ellipsis_echoes(tree)
        if (module, ast.unparse(call.args[0])) not in _NOT_PROGRESS
    }


def _progress_lines_on_stdout(module: str, tree: ast.AST) -> set[str]:
    return {
        f"{ast.unparse(call.args[0])} (line {call.lineno})"
        for call in _ellipsis_echoes(tree)
        if (module, ast.unparse(call.args[0])) not in _NOT_PROGRESS
        and not any(
            word.arg == "err" and ast.unparse(word.value) == "True"
            for word in call.keywords
        )
    }


def _piping_runner() -> CliRunner:
    """Streams kept apart, the way a shell pipe sees them."""
    return CliRunner(mix_stderr=False)


def _finished_status() -> MagicMock:
    status = MagicMock(spec=EnrichmentJobStatus)
    status.running = False
    status.cancelled = False
    status.items_processed = 10
    status.items_enriched = 8
    status.items_not_found = 2
    status.items_failed = 0
    status.elapsed_seconds = 5.0
    status.progress_percent = 100.0
    status.errors = []
    return status


@pytest.mark.usefixtures("registry_with_source_fakes")
class TestUpdateProgressIsOffTheDataChannel:
    """Both of its progress lines: the header and the per-source counter."""

    _CONFIG = {
        "inputs": {"books": {"plugin": "fake_file", "enabled": True, "path": "b.csv"}}
    }

    def _run(self, errors: list[str] | None = None) -> Any:
        def sync(progress_callback: Any, **_: Any) -> list[SyncResult]:
            progress_callback(10, 100, "Dune", "books")
            return [
                SyncResult(
                    source_name="books",
                    items_synced=3,
                    items_added=3,
                    total_items=3,
                    errors=errors or [],
                )
            ]

        with patch(
            "src.cli.commands._update.execute_multi_source_sync", side_effect=sync
        ):
            return _invoke_with_mocks(
                _piping_runner(),
                ["update"],
                MagicMock(spec=StorageManager),
                config=self._CONFIG,
            )

    def test_the_counts_are_the_whole_of_stdout(self) -> None:
        result = self._run()

        assert result.exit_code == 0
        assert "3 of 3 items saved (3 added, 0 updated, 0 unchanged)" in result.stdout
        assert "Updating data from" not in result.stdout
        assert "Processed 10/100" not in result.stdout

    def test_a_plain_run_still_shows_what_it_is_doing(self) -> None:
        """Anchors the test above, which an empty stderr also satisfies."""
        result = self._run()

        assert "Updating data from books (workers=4)..." in result.stderr
        assert "Processed 10/100..." in result.stderr

    def test_a_sources_own_failure_is_read_out_beside_its_counts(self) -> None:
        """An enrichment-marking failure has no other channel to the operator
        — the CLI reads no log file back — and the counts line alone would
        report the run as a clean success.
        """
        result = self._run(["Saved 'Dune' but could not queue it for enrichment"])

        assert result.exit_code == 0
        assert (
            "    Warning: Saved 'Dune' but could not queue it for enrichment"
            in result.stderr
        )
        assert "Warning" not in result.stdout


class TestEnrichmentProgressIsOffTheDataChannel:
    def _run(self) -> Any:
        manager = MagicMock(spec=EnrichmentManager)
        manager.start_enrichment.return_value = True
        manager.get_status.return_value = _finished_status()
        with patch(
            "src.cli.commands._enrichment.EnrichmentManager", return_value=manager
        ):
            return _invoke_with_mocks(
                _piping_runner(),
                ["enrichment", "start"],
                MagicMock(spec=StorageManager),
                config={"enrichment": {"enabled": True}},
            )

    def test_the_final_tally_is_the_whole_of_stdout(self) -> None:
        result = self._run()

        assert result.exit_code == 0
        assert "Items processed: 10" in result.stdout
        assert "Started enrichment for" not in result.stdout

    def test_the_start_line_still_reaches_the_operator(self) -> None:
        assert "Started enrichment for all types..." in self._run().stderr


class TestInterpretProgressIsOffTheDataChannel:
    def _run(self) -> Any:
        return _invoke_with_mocks(
            _piping_runner(),
            ["preferences", "custom-rules", "interpret", "avoid horror"],
            MagicMock(spec=StorageManager),
        )

    def test_the_reading_is_the_whole_of_stdout(self) -> None:
        result = self._run()

        assert result.exit_code == 0
        assert result.stdout.startswith("\nRule: 'avoid horror'")


class TestTraktConnectProgressIsOffTheDataChannel:
    """The device flow polls for up to ten minutes with a line saying so."""

    _FLOW = {
        "device_code": "dev123",
        "user_code": "ABCD1234",
        "verification_url": "https://trakt.tv/activate",
        "expires_in": 600,
        "interval": 5,
    }

    def _run(self) -> Any:
        with (
            patch(
                "src.cli.commands._auth.resolve_trakt_client_credentials",
                return_value=("cid", "secret"),
            ),
            patch(
                "src.cli.commands._auth.start_device_auth_flow",
                return_value=self._FLOW,
            ),
            patch(
                "src.cli.commands._auth.poll_device_token",
                return_value=DevicePollResult(DevicePollStatus.SUCCESS, "refresh"),
            ),
            patch("src.cli.commands._auth.save_trakt_token"),
            patch("src.cli.commands._auth.time.sleep"),
        ):
            return _invoke_with_mocks(
                _piping_runner(),
                ["auth", "connect", "--source", "trakt"],
                MagicMock(spec=StorageManager),
            )

    def test_the_outcome_is_the_whole_of_stdout(self) -> None:
        result = self._run()

        assert result.exit_code == 0, result.stderr
        assert "trakt connected successfully." in result.stdout
        assert "Waiting for approval" not in result.stdout

    def test_the_wait_still_reaches_the_operator(self) -> None:
        """Anchors the test above, which an empty stderr also satisfies."""
        assert "Waiting for approval..." in self._run().stderr


@pytest.mark.parametrize("module", sorted(_TREES))
def test_no_cli_module_prints_progress_on_the_data_channel(module: str) -> None:
    assert _progress_lines_on_stdout(module, _TREES[module]) == set()


class TestTheSweptProgressPopulationIsNotEmpty:
    """``set()`` is also what a sweep that found no modules at all returns."""

    def test_every_module_that_prints_progress_is_swept(self) -> None:
        assert {
            module for module, tree in _TREES.items() if _progress_lines(module, tree)
        } == _MODULES_THAT_PRINT_PROGRESS

    def test_the_sweep_reads_more_than_one_line_per_module(self) -> None:
        """``update`` has two, so a predicate matching one is not enough."""
        module = "commands/_update.py"

        assert len(_progress_lines(module, _TREES[module])) == 2

    def test_every_waiver_is_still_a_live_site(self) -> None:
        """A stale one widens the blind spot in silence."""
        assert {
            (module, ast.unparse(call.args[0]))
            for module, tree in _TREES.items()
            for call in _ellipsis_echoes(tree)
        } >= set(_NOT_PROGRESS)


class TestTheProgressSweepFailsOnANewStdoutLine:
    """The sweep above passes; these prove it is not passing vacuously."""

    _ELSEWHERE = "commands/_thief.py"

    @pytest.mark.parametrize(
        ("source", "reported"),
        [
            ("click.echo('Working...')", "'Working...'"),
            ("click.echo(f'Updating {name}...')", "f'Updating {name}...'"),
            ("click.echo('Updating ' + name + '...')", "'Updating ' + name + '...'"),
            ("click.secho('Working...', fg='green')", "'Working...'"),
            ("click.echo('Working...', err=False)", "'Working...'"),
            ("click.echo('Waiting... (Ctrl-C)')", "'Waiting... (Ctrl-C)'"),
        ],
    )
    def test_a_progress_line_left_on_stdout_is_reported(
        self, source: str, reported: str
    ) -> None:
        tree = ast.parse(source)

        assert _progress_lines_on_stdout(self._ELSEWHERE, tree) == {
            f"{reported} (line 1)"
        }

    @pytest.mark.parametrize(
        "source",
        [
            "click.echo('Working...', err=True)",
            "click.echo('Enrichment stopped.')",
            "click.echo(tabulate(rows[:60] + ('...' if long else '')))",
        ],
        ids=["on-stderr", "an-outcome", "a-truncation-marker"],
    )
    def test_what_is_not_a_progress_line_is_not_reported(self, source: str) -> None:
        assert _progress_lines_on_stdout(self._ELSEWHERE, ast.parse(source)) == set()

    def test_a_waived_site_is_reported_under_another_module(self) -> None:
        """The waiver is keyed on the module, so a copy elsewhere still fails."""
        module, text = next(iter(_NOT_PROGRESS))
        tree = ast.parse(f"click.echo({text})")

        assert _progress_lines_on_stdout(self._ELSEWHERE, tree) == {f"{text} (line 1)"}
        assert _progress_lines_on_stdout(module, tree) == set()

    @pytest.mark.parametrize("module", sorted(_MODULES_THAT_PRINT_PROGRESS))
    def test_a_stdout_progress_line_added_to_a_swept_module_is_reported(
        self, module: str
    ) -> None:
        """Links the controls to ``_TREES``: it holds the text they stand in for."""
        source = (_CLI_ROOT / module).read_text(encoding="utf-8")
        tree = ast.parse(f"{source}\nclick.echo('Reticulating splines...')\n")

        assert "'Reticulating splines...'" in " ".join(
            _progress_lines_on_stdout(module, tree)
        )
