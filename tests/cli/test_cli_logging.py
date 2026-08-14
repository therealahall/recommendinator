"""What the CLI's boot does to the root logger, and which stream it writes on."""

from __future__ import annotations

import ast
import csv
import io
import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest
from click.testing import CliRunner

import src as src_package
from src.cli.commands._recommend import RECOMMEND_FAILED
from src.cli.main import cli
from src.models.content import ContentType
from src.settings.metadata import default_of
from src.storage.manager import StorageManager
from src.utils import logging as log_config
from src.utils.export import export_items_csv

# Bound at import, before the root conftest's per-test patch: this is the real
# callable, and handing it back to ``patch`` is how a test opts out of the
# blanket no-op that keeps every other test off the production log.
from src.utils.logging import configure_logging
from tests.cli.conftest import _invoke_with_mocks

#: The boot warning ``_config_that_warns_at_boot`` provokes, once handlers exist.
_BOOT_WARNING = "Ignoring unusable logging.level"

_SRC_ROOT = Path(src_package.__file__).parent

#: Everything that can put a log record on a stream of its own choosing.
_HANDLER_BUILDERS = {"StreamHandler", "basicConfig"}


def _module_id(path: Path) -> str:
    return str(path.relative_to(_SRC_ROOT))


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _names_stdout(node: ast.expr) -> bool:
    # ``sys.__stdout__`` is the spelling reached for once ``sys.stdout`` has
    # been redirected — the case this sweep exists to catch.
    return isinstance(node, ast.Attribute) and node.attr in {"stdout", "__stdout__"}


def _called_name(node: ast.Call) -> str:
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _stdout_handler_calls(tree: ast.AST) -> list[str]:
    """Every handler construction in *tree* pinned to ``sys.stdout``."""
    return [
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (name := _called_name(node)) in _HANDLER_BUILDERS
        and any(
            _names_stdout(argument)
            for argument in (*node.args, *(kw.value for kw in node.keywords))
        )
    ]


def _log_to(log_file: str) -> dict[str, Any]:
    """A config saying only where the log goes."""
    return {"logging": {"file": log_file}}


def _config_that_warns_at_boot(log_file: str) -> dict[str, Any]:
    """A config whose unusable ``level`` makes boot log one WARNING record.

    The tests below assert where a record lands, so each needs the boot to
    produce one at all.
    """
    return {"logging": {"file": log_file, "level": "verbose"}}


def _csv_source_config(csv_path: Path) -> dict[str, Any]:
    """One offline source, so a real ``update`` has something to sync."""
    return {
        "inputs": {
            "my_csv": {
                "plugin": "csv_import",
                "enabled": True,
                "path": str(csv_path),
                "content_type": "book",
            }
        },
        "logging": {"file": "logs/cli.log"},
    }


def _rows(document: str) -> list[list[str]]:
    """Parse a CSV document into its rows."""
    return list(csv.reader(io.StringIO(document)))


def _invoke_with_real_logging(
    runner: CliRunner,
    storage: StorageManager,
    config: dict[str, Any],
    args: list[str],
    input_text: str | None = None,
    engine: Any = None,
) -> Any:
    """Run *args* with the log wiring unstubbed."""
    with (
        patch("src.utils.logging.configure_logging", configure_logging),
        patch("src.cli.main.load_config", return_value=config),
        patch("src.cli.main.create_storage_manager", return_value=storage),
        patch(
            "src.cli.main.create_recommendation_engine",
            return_value=engine if engine is not None else MagicMock(),
        ),
    ):
        return runner.invoke(cli, args, input=input_text)


def _failing_engine(error: Exception) -> MagicMock:
    """An engine whose ``recommend`` call raises, to exercise the log funnel."""
    engine = MagicMock()
    engine.generate_recommendations.side_effect = error
    return engine


class TestTheConsoleNeverWritesToTheDataChannelRegression:
    """A record on stdout lands ahead of the JSON document or the CSV header row.

    Bug risk: the CLI's console handler is one argument from the data channel.
    Fix: the stream is required, and the CLI passes stderr.
    """

    def test_a_json_command_keeps_stdout_clean_while_the_boot_warns(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        """A boot warning would prefix a log line onto the JSON document."""
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        monkeypatch.chdir(tmp_path)

        result = _invoke_with_real_logging(
            CliRunner(mix_stderr=False),
            storage,
            _config_that_warns_at_boot("logs/cli.log"),
            ["status", "--format", "json"],
        )

        assert result.exit_code == 0, result.stderr
        assert json.loads(result.stdout)["status"] == "ready"
        # Anchors the assertion above: without a record on the wire it holds
        # over an invocation that logged nothing at all.
        assert _BOOT_WARNING in result.stderr
        assert _BOOT_WARNING in (tmp_path / "logs" / "cli.log").read_text(
            encoding="utf-8"
        )

    def test_the_csv_export_on_stdout_is_not_prefixed_by_a_log_line(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        """``library export`` with no ``--output`` is redirected to a file.

        A console record on stdout lands ahead of the header row, so every
        column name shifts and the file no longer parses as the CSV it names.
        """
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        monkeypatch.chdir(tmp_path)

        result = _invoke_with_real_logging(
            CliRunner(mix_stderr=False),
            storage,
            _config_that_warns_at_boot("logs/cli.log"),
            ["library", "export", "--type", "book", "--format", "csv"],
        )

        expected = export_items_csv(
            storage.get_content_items(
                user_id=1, content_type=ContentType.BOOK, include_ignored=True
            ),
            ContentType.BOOK,
        )

        assert result.exit_code == 0, result.stderr
        assert next(csv.reader(io.StringIO(result.stdout)))[0] == "title"
        # Parsed rather than compared as text: CliRunner hands back the document
        # with the writer's CRLF row endings normalised.
        assert _rows(result.stdout) == _rows(expected)
        # Anchors the two above: they hold over an invocation that logged nothing.
        assert _BOOT_WARNING in result.stderr

    def test_a_mutation_rendering_json_keeps_stdout_clean(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        """``emit_view`` is the one renderer every settings and source write uses.

        ``status`` only proves the read surface; a write emits its document after
        the boot has had its say on the same invocation.
        """
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        monkeypatch.chdir(tmp_path)

        result = _invoke_with_real_logging(
            CliRunner(mix_stderr=False),
            storage,
            _config_that_warns_at_boot("logs/cli.log"),
            [
                "settings",
                "set",
                "recommendations.default_count",
                "7",
                "--format",
                "json",
            ],
        )

        assert result.exit_code == 0, result.stderr
        assert json.loads(result.stdout)["sections"]
        assert _BOOT_WARNING in result.stderr


def test_the_log_level_comes_from_the_settings_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restore_root_logging: None
) -> None:
    """``logging.level`` is DB-backed, so the overlay has to run first."""
    storage = StorageManager(sqlite_path=tmp_path / "test.db")
    storage.set_setting("logging.level", "DEBUG")
    monkeypatch.chdir(tmp_path)

    result = _invoke_with_real_logging(
        CliRunner(), storage, _config_that_warns_at_boot("logs/cli.log"), ["status"]
    )

    assert result.exit_code == 0, result.output
    # Only the stored row says DEBUG: configuring before the overlay leaves the
    # root logger on the registry default.
    assert default_of("logging.level") == "INFO"
    assert logging.getLogger().level == logging.DEBUG


def test_the_log_file_comes_from_the_settings_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restore_root_logging: None
) -> None:
    """``logging.file`` is DB-backed too, and the stored row outranks the YAML.

    Configuring before the overlay would open the YAML path and look right in
    every test that does not set the row.
    """
    storage = StorageManager(sqlite_path=tmp_path / "test.db")
    storage.set_setting("logging.file", "logs/from-db.log")
    monkeypatch.chdir(tmp_path)

    result = _invoke_with_real_logging(
        CliRunner(),
        storage,
        _config_that_warns_at_boot("logs/from-yaml.log"),
        ["status"],
    )

    assert result.exit_code == 0, result.output
    assert _BOOT_WARNING in (tmp_path / "logs" / "from-db.log").read_text(
        encoding="utf-8"
    )
    assert not (tmp_path / "logs" / "from-yaml.log").exists()


def test_a_stored_level_the_registry_would_reject_degrades_at_boot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restore_root_logging: None
) -> None:
    """The overlay applies stored rows without re-validating them.

    ``set_setting`` writes past the Settings API exactly as a row persisted
    under an older pattern would read back, so the boot must fall back rather
    than take the value to ``setLevel``.
    """
    storage = StorageManager(sqlite_path=tmp_path / "test.db")
    storage.set_setting("logging.level", "verbose")
    monkeypatch.chdir(tmp_path)

    result = _invoke_with_real_logging(
        CliRunner(), storage, _log_to("logs/cli.log"), ["status"]
    )

    assert result.exit_code == 0, result.output
    assert logging.getLogger().level == logging.INFO


def test_a_stored_log_path_escaping_logs_is_contained_at_cli_boot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restore_root_logging: None
) -> None:
    """The containment backstop had only ever been driven from the web boot.

    A row written before the registry pattern grew its ``..`` lookahead still
    overlays without re-validation, and the CLI now opens the handler too.
    """
    storage = StorageManager(sqlite_path=tmp_path / "test.db")
    storage.set_setting("logging.file", "logs/../../evil.log")
    monkeypatch.chdir(tmp_path)

    result = _invoke_with_real_logging(
        CliRunner(), storage, _config_that_warns_at_boot("logs/cli.log"), ["status"]
    )

    assert result.exit_code == 0, result.output
    assert not (tmp_path.parent / "evil.log").exists()
    assert _BOOT_WARNING in (
        tmp_path / "logs" / Path(default_of("logging.file")).name
    ).read_text(encoding="utf-8")


def test_a_healthy_update_puts_no_log_record_on_the_console(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restore_root_logging: None
) -> None:
    """``update`` is the command the console chatter would have buried.

    Plugin discovery and both sync banners record at INFO, the last restating
    the command's own total, so the console floors above them. Its own
    progress line is what is left.
    """
    storage = StorageManager(sqlite_path=tmp_path / "test.db")
    source = tmp_path / "books.csv"
    source.write_text("title,status\nDune,completed\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = _invoke_with_real_logging(
        CliRunner(mix_stderr=False), storage, _csv_source_config(source), ["update"]
    )

    assert result.exit_code == 0, result.stderr
    assert "Total: 1 items updated." in result.stdout
    assert result.stderr == "Updating data from my_csv (workers=4)...\n"
    # Anchors the silence: those records were emitted, and the file is where
    # "check the logs" sends the operator to read them.
    written = (tmp_path / "logs" / "cli.log").read_text(encoding="utf-8")
    assert "[SYNC] === Starting sync for source: csv_import ===" in written
    assert "[SYNC] === Completed. Total items processed: 1 ===" in written


class TestCheckLogsForDetailsNamesAFileHoldingThemRegression:
    """Reported by QA: a failing command named a log nobody was writing.

    Bug: no CLI command configured logging, so every ``exc_info`` record fell
    to a root logger with no handler.
    Fix: the boot wires the shared file handler.
    """

    def test_the_traceback_the_command_points_at_is_on_disk(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        """Its handler logs the caught exception with ``exc_info`` and then
        prints "Check logs for details", so the traceback is what the file must
        hold."""
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        monkeypatch.chdir(tmp_path)

        result = _invoke_with_real_logging(
            CliRunner(mix_stderr=False),
            storage,
            _log_to("logs/cli.log"),
            ["recommend", "--type", "book"],
            engine=_failing_engine(RuntimeError("the library is unreadable")),
        )

        assert result.exit_code == 1
        assert "Check logs for details" in result.stderr
        # The data channel stays empty on the failure path too.
        assert result.stdout == ""
        written = (tmp_path / "logs" / "cli.log").read_text(encoding="utf-8")
        assert RECOMMEND_FAILED in written
        assert "RuntimeError: the library is unreadable" in written


class TestAnUnusableLogDestinationDegradesRatherThanAbortingRegression:
    """Reported by QA: an unwritable ``logs/`` killed every CLI command.

    Bug: the configure call sat inside the callback's ``except Exception``, so
    a refusal read as "Error initializing components".
    Fix: it degrades to the console, reporting once.
    """

    def test_the_degrade_is_reported_once_on_stderr(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        """Degrading in silence leaves an operator hunting for a log nobody writes.

        One line, on stderr, naming the destination — the data channel stays
        clean, which is what makes ``--format json`` survive the degraded run.
        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / "logs").write_text("not a directory", encoding="utf-8")
        storage = StorageManager(sqlite_path=tmp_path / "test.db")

        result = _invoke_with_real_logging(
            CliRunner(mix_stderr=False),
            storage,
            _log_to("logs/cli.log"),
            ["status", "--format", "json"],
        )

        assert result.exit_code == 0, result.stderr
        assert json.loads(result.stdout)["status"] == "ready"
        reported = result.stderr.splitlines()
        assert len(reported) == 1
        assert reported[0].startswith("Warning: no log file for this run: ")
        assert str((tmp_path / "logs").resolve()) in reported[0]


class TestTheConsoleWithholdsTracebacksRegression:
    """Reported by QA: a console handler dumped a traceback above every "Check
    logs for details" line.

    Bug: ``Formatter.format`` appends it whatever the format string says.
    Fix: the CLI's console renders the message alone, on both paths.
    """

    def test_a_caught_fault_keeps_its_traceback_off_the_terminal(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        monkeypatch.chdir(tmp_path)

        result = _invoke_with_real_logging(
            CliRunner(mix_stderr=False),
            storage,
            _log_to("logs/cli.log"),
            ["recommend", "--type", "book"],
            engine=_failing_engine(RuntimeError("the library is unreadable")),
        )

        assert "Check logs for details" in result.stderr
        assert "Traceback (most recent call last):" not in result.stderr
        assert "the library is unreadable" not in result.stderr
        # Anchors both: the record carried a traceback for the console to drop.
        assert "Traceback (most recent call last):" in (
            tmp_path / "logs" / "cli.log"
        ).read_text(encoding="utf-8")

    def test_a_run_whose_log_is_off_still_keeps_the_traceback_off_the_console(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        """The degrade path builds a console handler of its own, and this is the
        one run with no log file to read a withheld traceback out of."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "logs").write_text("not a directory", encoding="utf-8")
        storage = StorageManager(sqlite_path=tmp_path / "test.db")

        result = _invoke_with_real_logging(
            CliRunner(mix_stderr=False),
            storage,
            _log_to("logs/cli.log"),
            ["recommend", "--type", "book"],
            engine=_failing_engine(RuntimeError("the library is unreadable")),
        )

        assert "Check logs for details" in result.stderr
        # Anchors the two below: a console handler dropping the record satisfies
        # them, and there is no log file left to read the record out of.
        assert f"ERROR | src.cli._shared | {RECOMMEND_FAILED}" in result.stderr
        assert "Traceback (most recent call last):" not in result.stderr
        assert "the library is unreadable" not in result.stderr

    def test_a_run_whose_log_is_off_still_formats_its_diagnostics(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        restore_root_logging: None,
    ) -> None:
        """Anchors the test above, which a handler dropping every record
        satisfies. The level and logger name are what say the CLI's own handler
        rendered this."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "logs").write_text("not a directory", encoding="utf-8")
        storage = StorageManager(sqlite_path=tmp_path / "test.db")

        result = _invoke_with_real_logging(
            CliRunner(mix_stderr=False),
            storage,
            _config_that_warns_at_boot("logs/cli.log"),
            ["status", "--format", "json"],
        )

        assert result.exit_code == 0, result.stderr
        # The data channel survives the degraded run's console records too.
        assert json.loads(result.stdout)["status"] == "ready"
        reported = [
            line for line in result.stderr.splitlines() if _BOOT_WARNING in line
        ]
        assert len(reported) == 1
        assert reported[0].startswith("WARNING | src.utils.logging | ")


class TestNothingUnderSrcHardwiresAHandlerOntoStdout:
    """One ``--format json`` surface at a time proves nothing about the next.

    The stream argument is what keeps all ~21 of them clean, so the invariant
    is swept instead.
    """

    @pytest.mark.parametrize("path", sorted(_SRC_ROOT.rglob("*.py")), ids=_module_id)
    def test_no_module_builds_a_log_handler_on_stdout(self, path: Path) -> None:
        assert _stdout_handler_calls(_parse(path)) == []

    def test_the_sweep_reaches_the_modules_it_exists_for(self) -> None:
        """An empty or narrowed population would skip, not fail: no
        ``empty_parameter_set_mark`` is configured."""
        swept = {_module_id(path) for path in _SRC_ROOT.rglob("*.py")}

        assert {"web/app.py", "cli/main.py", "utils/logging.py"} <= swept

    def test_the_sweep_reports_a_planted_one(self) -> None:
        """A predicate matching nothing would clear every module above."""
        planted = (
            "logging.StreamHandler(sys.stdout)\n"
            "logging.basicConfig(stream=sys.stdout)\n"
            "logging.StreamHandler(sys.__stdout__)\n"
            "logging.basicConfig(stream=sys.__stdout__)\n"
        )

        assert _stdout_handler_calls(ast.parse(planted)) == [
            "StreamHandler",
            "basicConfig",
            "StreamHandler",
            "basicConfig",
        ]

    def test_passing_stdout_to_the_shared_configurer_is_not_reported(self) -> None:
        """``src/web/app.py`` does exactly this, and it is right there:
        ``docker logs`` shows stdout."""
        allowed = "log_config.configure_logging(config, console_stream=sys.stdout)\n"

        assert _stdout_handler_calls(ast.parse(allowed)) == []


def test_a_cli_invocation_under_the_suite_opens_no_production_log_handler(
    cli_runner: CliRunner,
) -> None:
    """A ``from`` import in ``main.py`` would resolve past the conftest's patch
    and write the developer's own ``logs/recommendations.log``."""
    configurer = log_config.configure_logging
    assert isinstance(configurer, Mock), "the root conftest no longer patches it"
    root_logger = logging.getLogger()
    handlers_before = list(root_logger.handlers)

    result = _invoke_with_mocks(cli_runner, ["status"], MagicMock(spec=StorageManager))

    assert result.exit_code == 0, result.output
    configurer.assert_called_once()
    assert root_logger.handlers == handlers_before
