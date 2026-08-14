"""What a CLI command says when something under it fails.

Ten sites echoed the caught exception into the terminal. The wording is now
one sentence per operation, read off both interfaces here so neither can be
reworded on its own.
"""

from __future__ import annotations

import ast
import json
import logging
import sqlite3
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
from click.testing import CliRunner

import src.cli.commands as cli_commands
import src.web.api
import src.web.chat_api
import src.web.sync_manager
from src.auth.trakt import TraktAuthError
from src.cli.commands._auth import (
    EPIC_AUTH_FAILED,
    GOG_AUTH_FAILED,
    TRAKT_AUTH_FAILED,
)
from src.cli.commands._chat import CHAT_FAILED
from src.cli.commands._complete import COMPLETE_FAILED
from src.cli.commands._recommend import RECOMMEND_FAILED
from src.cli.commands._update import SYNC_FAILED
from src.cli.main import SurrogateFreeGroup, cli
from src.models.content import (
    MAX_REVIEW_LENGTH,
    ConsumptionStatus,
    ContentItem,
    ContentType,
)
from src.models.user_preferences import UserPreferenceConfig
from src.recommendations.engine import RecommendationEngine
from src.sources.service import SOURCE_ID_RULE, SOURCE_MISCONFIGURED_DETAIL
from src.storage.manager import StorageManager
from tests.fakes.source_plugins import FakeFilePlugin

from .conftest import _invoke_with_mocks

#: One fault text for every case below, so the assertions read the same.
_FAULT = "no such table: content_items"

#: The CLI message beside the web handler that answers with the same sentence.
_SHARED_REFUSALS = [
    pytest.param(COMPLETE_FAILED, src.web.api, "mark_complete", id="complete"),
    pytest.param(RECOMMEND_FAILED, src.web.api, "get_recommendations", id="recommend"),
    pytest.param(
        TRAKT_AUTH_FAILED, src.web.api, "start_trakt_device_flow", id="trakt-start"
    ),
    pytest.param(
        TRAKT_AUTH_FAILED, src.web.api, "poll_trakt_device_approval", id="trakt-poll"
    ),
    pytest.param(SYNC_FAILED, src.web.sync_manager, "_run_sync", id="update"),
    pytest.param(GOG_AUTH_FAILED, src.web.api, "exchange_gog_token", id="gog-connect"),
    pytest.param(
        EPIC_AUTH_FAILED, src.web.api, "exchange_epic_token", id="epic-connect"
    ),
    pytest.param(CHAT_FAILED, src.web.chat_api, "generate_sse", id="chat"),
]


def _literals_in(module: ModuleType, function: str) -> set[str]:
    """Every string constant *function* carries, read off the module source."""
    tree = ast.parse(Path(str(module.__file__)).read_text(encoding="utf-8"))
    scopes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name == function
    ]
    assert len(scopes) == 1, f"{module.__name__} declares {len(scopes)} {function}"
    return {
        node.value
        for node in ast.walk(scopes[0])
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def _source_config() -> dict[str, Any]:
    """A single enabled ``fake_file`` source, fresh: boot overlays onto it."""
    return {
        "inputs": {"books": {"plugin": "fake_file", "enabled": True, "path": "b.csv"}}
    }


def _assert_generic(result: Any, message: str) -> None:
    assert result.exit_code != 0
    assert f"{message}. Check logs for details." in result.output
    assert _FAULT not in result.output


def _assert_verbose(result: Any, message: str) -> None:
    assert result.exit_code != 0
    assert f"{message}: OperationalError: {_FAULT}" in result.output


class TestTheCliRefusesInTheWebsWords:
    """Read off both handlers, so rewording either fails here."""

    @pytest.mark.parametrize(("message", "module", "function"), _SHARED_REFUSALS)
    def test_the_web_handler_carries_the_same_sentence(
        self, message: str, module: ModuleType, function: str
    ) -> None:
        assert message in _literals_in(module, function)

    def test_a_sentence_the_handler_does_not_carry_is_not_found(self) -> None:
        """A scan returning every string would pass the parametrization above."""
        assert COMPLETE_FAILED not in _literals_in(src.web.api, "get_recommendations")


class TestCompleteHidesTheWriteThatFailed:
    def test_the_refusal_is_generic_and_the_log_holds_the_reason(
        self, cli_runner: CliRunner, caplog: pytest.LogCaptureFixture
    ) -> None:
        storage = MagicMock(spec=StorageManager)
        storage.complete_content_item.side_effect = sqlite3.OperationalError(_FAULT)

        with caplog.at_level(logging.ERROR, logger="src.cli._shared"):
            result = _invoke_with_mocks(
                cli_runner, ["complete", "--type", "book", "--title", "Dune"], storage
            )

        _assert_generic(result, COMPLETE_FAILED)
        assert _FAULT in caplog.text

    def test_verbose_adds_the_reason_and_still_no_traceback(
        self, cli_runner: CliRunner
    ) -> None:
        storage = MagicMock(spec=StorageManager)
        storage.complete_content_item.side_effect = sqlite3.OperationalError(_FAULT)

        result = _invoke_with_mocks(
            cli_runner,
            ["--verbose", "complete", "--type", "book", "--title", "Dune"],
            storage,
        )

        _assert_verbose(result, COMPLETE_FAILED)
        assert "Traceback" not in result.output


class TestRecommendHidesTheEnginesWords:
    """The engine walks the library, so its faults quote item titles."""

    def _run(self, cli_runner: CliRunner, args: list[str]) -> Any:
        engine = MagicMock(spec=RecommendationEngine)
        engine.generate_recommendations.side_effect = sqlite3.OperationalError(_FAULT)
        return _invoke_with_mocks(
            cli_runner, args, MagicMock(spec=StorageManager), engine=engine
        )

    def test_the_refusal_is_generic(self, cli_runner: CliRunner) -> None:
        _assert_generic(
            self._run(cli_runner, ["recommend", "--type", "book"]), RECOMMEND_FAILED
        )

    def test_verbose_adds_the_reason(self, cli_runner: CliRunner) -> None:
        _assert_verbose(
            self._run(cli_runner, ["--verbose", "recommend", "--type", "book"]),
            RECOMMEND_FAILED,
        )


@pytest.mark.usefixtures("registry_with_source_fakes")
class TestUpdateHidesTheSyncFault:
    def _run(self, cli_runner: CliRunner, args: list[str]) -> Any:
        with patch(
            "src.cli.commands._update.execute_multi_source_sync",
            side_effect=sqlite3.OperationalError(_FAULT),
        ):
            return _invoke_with_mocks(
                cli_runner,
                args,
                MagicMock(spec=StorageManager),
                config=_source_config(),
            )

    def test_the_refusal_is_generic(self, cli_runner: CliRunner) -> None:
        _assert_generic(self._run(cli_runner, ["update"]), SYNC_FAILED)

    def test_verbose_adds_the_reason(self, cli_runner: CliRunner) -> None:
        _assert_verbose(self._run(cli_runner, ["--verbose", "update"]), SYNC_FAILED)

    def test_verbose_still_withholds_a_token_the_fault_quotes(
        self, cli_runner: CliRunner
    ) -> None:
        """``--verbose`` waives the log, not the scrub a source URL needs.

        A ``requests`` fault names the request it failed on, and a sync runs
        against endpoints carrying a key in the query string.
        """
        token = "sk-live-9f3c2a"
        with patch(
            "src.cli.commands._update.execute_multi_source_sync",
            side_effect=requests.ConnectionError(
                f"HTTPConnectionPool: /v1/library?api_key={token} refused"
            ),
        ):
            result = _invoke_with_mocks(
                cli_runner,
                ["--verbose", "update"],
                MagicMock(spec=StorageManager),
                config=_source_config(),
            )

        assert result.exit_code != 0
        assert f"{SYNC_FAILED}: ConnectionError" in result.output
        assert token not in result.output


class TestTraktDeviceFlowHidesTheRequestItFailedOn:
    def _run(self, cli_runner: CliRunner, args: list[str]) -> Any:
        with patch(
            "src.cli.commands._auth.resolve_trakt_client_credentials",
            side_effect=TraktAuthError(_FAULT),
        ):
            return _invoke_with_mocks(cli_runner, args, MagicMock(spec=StorageManager))

    def test_the_refusal_is_generic(self, cli_runner: CliRunner) -> None:
        result = self._run(cli_runner, ["auth", "connect", "--source", "trakt"])

        assert result.exit_code != 0
        assert f"{TRAKT_AUTH_FAILED}. Check logs for details." in result.output
        assert _FAULT not in result.output

    def test_verbose_adds_the_reason(self, cli_runner: CliRunner) -> None:
        result = self._run(
            cli_runner, ["--verbose", "auth", "connect", "--source", "trakt"]
        )

        assert result.exit_code != 0
        assert f"{TRAKT_AUTH_FAILED}: TraktAuthError: {_FAULT}" in result.output


@pytest.mark.usefixtures("registry_with_source_fakes")
class TestUpdateNamesTheSettingRatherThanThePath:
    """Regression: ``update`` printed the operator's filesystem layout.

    Root cause: a plugin's validation error names the file it looked for.
    Fix: both doors answer through ``misconfigured_detail``.
    """

    def _run_with_validation_error(
        self, cli_runner: CliRunner, args: list[str], reason: str
    ) -> Any:
        with patch.object(FakeFilePlugin, "validate_config", return_value=[reason]):
            return _invoke_with_mocks(
                cli_runner,
                args,
                MagicMock(spec=StorageManager),
                config=_source_config(),
            )

    @pytest.mark.parametrize("args", [["update"], ["update", "--source", "books"]])
    def test_a_quoted_field_is_named_and_the_path_is_not_regression(
        self, cli_runner: CliRunner, tmp_path: Path, args: list[str]
    ) -> None:
        secret_path = str(tmp_path / "books.csv")

        result = self._run_with_validation_error(
            cli_runner, args, f"'path' is not readable: {secret_path}"
        )

        assert "check its 'path' setting" in result.output
        assert secret_path not in result.output

    def test_prose_naming_no_field_gets_the_unqualified_refusal(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        secret_path = str(tmp_path / "books.csv")

        result = self._run_with_validation_error(
            cli_runner, ["update"], f"File not found: {secret_path}"
        )

        assert SOURCE_MISCONFIGURED_DETAIL in result.output
        assert secret_path not in result.output

    def test_the_reason_still_reaches_the_log(
        self, cli_runner: CliRunner, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Hidden from the terminal is not hidden from the operator."""
        with caplog.at_level(logging.WARNING, logger="src.cli.commands._update"):
            self._run_with_validation_error(
                cli_runner, ["update"], "'path' is not readable"
            )

        assert "'path' is not readable" in caplog.text


class TestVerboseIsAnsweredByEveryCommandThatRefusesRegression:
    """Reported: ``--verbose`` is inert on ``chat`` and on OAuth connect.

    Root cause: the funnel took the handlers that bind the fault; four bind
    nothing and hand-roll the log and the refusal. Fix: funnel those too.
    """

    _AI_CONFIG = {"features": {"ai_enabled": True}, "ollama": {}}
    _CODE = "test-auth-code-abc123xyz\n"

    def test_chat_send_puts_the_engines_reason_on_the_terminal(
        self, cli_runner: CliRunner
    ) -> None:
        with patch("src.cli.commands._chat.ConversationEngine") as engine_class:
            engine_class.return_value.process_message_sync.side_effect = RuntimeError(
                _FAULT
            )
            result = _invoke_with_mocks(
                cli_runner,
                ["--verbose", "chat", "send", "--message", "Hi"],
                MagicMock(spec=StorageManager),
                config=self._AI_CONFIG,
                llm_client=MagicMock(),
            )

        assert result.exit_code != 0
        assert _FAULT in result.output

    def test_chat_start_prints_the_reason_and_keeps_the_session_open(
        self, cli_runner: CliRunner
    ) -> None:
        """The REPL reports and carries on, so the funnel must not abort here."""
        with patch("src.cli.commands._chat.ConversationEngine") as engine_class:
            engine_class.return_value.process_message_sync.side_effect = [
                RuntimeError(_FAULT),
                "Try Dune",
            ]
            result = _invoke_with_mocks(
                cli_runner,
                ["--verbose", "chat", "start"],
                MagicMock(spec=StorageManager),
                config=self._AI_CONFIG,
                llm_client=MagicMock(),
                input_text="one\ntwo\n",
            )

        assert result.exit_code == 0
        assert _FAULT in result.output
        assert "Try Dune" in result.output

    def test_auth_connect_puts_the_exchanges_reason_on_the_terminal(
        self, cli_runner: CliRunner
    ) -> None:
        with (
            patch("src.cli.commands._auth.is_gog_enabled", return_value=True),
            patch(
                "src.cli.commands._auth.get_gog_auth_url",
                return_value="https://auth.gog.com",
            ),
            patch(
                "src.cli.commands._auth.exchange_gog_code",
                side_effect=RuntimeError(_FAULT),
            ),
            patch("webbrowser.open"),
        ):
            result = _invoke_with_mocks(
                cli_runner,
                ["--verbose", "auth", "connect", "--source", "gog"],
                MagicMock(spec=StorageManager),
                input_text=self._CODE,
            )

        assert result.exit_code != 0
        assert _FAULT in result.output


class TestVerboseRendersAFaultTheTerminalCannotEncode:
    """A driver quotes the bytes it choked on, and ``click.echo`` encodes
    strictly, so an unescaped one would abort the refusal itself."""

    @pytest.mark.parametrize(
        ("raw", "rendered"),
        [
            ("no such table: \udcff", "no such table: \\udcff"),
            (
                "no such table: \x07\r\n injected",
                "no such table: \\u0007\\r\\n injected",
            ),
        ],
        ids=["lone-surrogate", "control-characters"],
    )
    def test_the_reason_is_escaped_rather_than_raising(
        self, cli_runner: CliRunner, raw: str, rendered: str
    ) -> None:
        storage = MagicMock(spec=StorageManager)
        storage.complete_content_item.side_effect = sqlite3.OperationalError(raw)

        result = _invoke_with_mocks(
            cli_runner,
            ["--verbose", "complete", "--type", "book", "--title", "Dune"],
            storage,
        )

        assert result.exit_code != 0
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert f"{COMPLETE_FAILED}: OperationalError: {rendered}" in result.output


class TestACustomRuleIsShownSanitizedRegression:
    """A rule with a lone surrogate crashed the command.

    Root cause: argv arrives via ``surrogateescape``; ``click.echo`` encodes
    strictly. Fix: echo the sanitized form, the rule being the operator's own.
    """

    #: A byte no UTF-8 decoder accepts, as ``surrogateescape`` hands it over,
    #: plus a control character the terminal would obey.
    _RAW = "avoid horror\udcff\x07"
    _CLEANED = "avoid horror"

    def _storage_holding(self, rules: list[str]) -> MagicMock:
        storage = MagicMock(spec=StorageManager)
        preferences = UserPreferenceConfig(custom_rules=list(rules))

        def merge(_user_id: int, apply: Any) -> UserPreferenceConfig:
            apply(preferences)
            return preferences

        storage.get_user_preference_config.return_value = preferences
        storage.merge_user_preference_config.side_effect = merge
        return storage

    def test_add_echoes_the_cleaned_rule_regression(
        self, cli_runner: CliRunner
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["preferences", "custom-rules", "add", self._RAW],
            self._storage_holding([]),
        )

        assert result.exit_code == 0, result.output
        assert f"Added rule: '{self._CLEANED}'" in result.output

    def test_list_echoes_the_cleaned_rule_regression(
        self, cli_runner: CliRunner
    ) -> None:
        """A rule already stored raw is still readable afterwards."""
        result = _invoke_with_mocks(
            cli_runner,
            ["preferences", "custom-rules", "list"],
            self._storage_holding([self._RAW]),
        )

        assert result.exit_code == 0, result.output
        assert f"0: {self._CLEANED}" in result.output

    def test_remove_echoes_the_cleaned_rule_regression(
        self, cli_runner: CliRunner
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            ["preferences", "custom-rules", "remove", "0"],
            self._storage_holding([self._RAW]),
        )

        assert result.exit_code == 0, result.output
        assert f"Removed rule: '{self._CLEANED}'" in result.output

    @pytest.mark.parametrize("output_format", ["table", "json"])
    def test_preferences_get_still_renders_the_rule_regression(
        self, cli_runner: CliRunner, output_format: str
    ) -> None:
        """The same rule read back by the command that shows every setting.

        ``custom-rules add`` stores the text as typed, so whatever can be
        added has to survive being displayed.
        """
        result = _invoke_with_mocks(
            cli_runner,
            ["preferences", "get", "--format", output_format],
            self._storage_holding([self._RAW]),
        )

        assert result.exit_code == 0, result.output
        assert self._CLEANED in result.output
        assert "\udcff" not in result.output
        assert "\x07" not in result.output

    def test_interpret_echoes_the_text_it_parsed_regression(
        self, cli_runner: CliRunner
    ) -> None:
        """``original_rule`` is what the interpreter actually ran against."""
        result = _invoke_with_mocks(
            cli_runner,
            ["preferences", "custom-rules", "interpret", self._RAW],
            self._storage_holding([]),
        )

        assert result.exit_code == 0, result.output
        assert f"Rule: '{self._CLEANED}'" in result.output


class TestArgvTextIsStoredWithoutItsSurrogatesRegression:
    """The custom rule's siblings, on real storage rather than a mock.

    Root cause: argv arrives via ``surrogateescape`` and SQLite encodes as
    strictly as ``click.echo``, so the ``UnicodeEncodeError`` came out of the
    write. Fix: strip the surrogate where the value enters.
    """

    _RAW = "loved it\udcff"
    _CLEANED = "loved it"

    def test_memory_add_stores_the_stripped_text_regression(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "memory.db")

        result = _invoke_with_mocks(
            cli_runner, ["memory", "add", "--text", self._RAW], storage
        )

        assert result.exit_code == 0, result.output
        assert storage.get_core_memories(1)[0]["memory_text"] == self._CLEANED

    def test_memory_edit_stores_the_stripped_text_regression(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """Same write, second door: ``--text`` here replaces a stored memory."""
        storage = StorageManager(sqlite_path=tmp_path / "memory.db")
        memory_id = storage.save_core_memory(
            user_id=1, memory_text="old", memory_type="user_stated", source="manual"
        )

        result = _invoke_with_mocks(
            cli_runner,
            ["memory", "edit", "--id", str(memory_id), "--text", self._RAW],
            storage,
        )

        assert result.exit_code == 0, result.output
        assert storage.get_core_memories(1)[0]["memory_text"] == self._CLEANED

    @pytest.mark.parametrize(
        ("option", "field", "stored"),
        [
            ("--review", "review", _CLEANED),
            ("--description", "description", _CLEANED),
            ("--genre", "genres", [_CLEANED]),
            ("--tag", "tags", [_CLEANED]),
        ],
    )
    def test_library_edit_stores_the_stripped_value_regression(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
        option: str,
        field: str,
        stored: object,
    ) -> None:
        """Every free-text option on the command, repeatable ones included."""
        storage, db_id = self._seeded_library(tmp_path)

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", str(db_id), option, self._RAW],
            storage,
        )
        assert result.exit_code == 0, result.output

        shown = _invoke_with_mocks(
            cli_runner,
            ["library", "show", "--id", str(db_id), "--format", "json"],
            storage,
        )
        assert json.loads(shown.output)[field] == stored

    def test_library_edit_refuses_a_review_that_strips_to_nothing(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """The strip runs before Click parses, so the guard sees what storage
        would get. Ordered the other way, a review of nothing but an
        undecodable byte passed the guard and landed as ``''``.
        """
        storage, db_id = self._seeded_library(tmp_path)
        storage.update_item_from_ui(
            db_id=db_id, status="completed", review="worth it", user_id=1
        )

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", str(db_id), "--review", "\udcff"],
            storage,
        )

        shown = _invoke_with_mocks(
            cli_runner,
            ["library", "show", "--id", str(db_id), "--format", "json"],
            storage,
        )
        assert json.loads(shown.output)["review"] == "worth it"
        assert result.exit_code != 0

    def test_source_create_refuses_a_surrogate_in_its_id(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """The id rule admits ``[a-z0-9_-]`` only, so the echo never sees one."""
        storage = StorageManager(sqlite_path=tmp_path / "source.db")

        result = _invoke_with_mocks(
            cli_runner, ["source", "create", self._RAW, "fake_file"], storage
        )

        assert result.exit_code != 0
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert SOURCE_ID_RULE in result.output

    def test_source_create_refuses_a_surrogate_in_its_plugin_name(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """The command's other argument, which no id rule filters.

        ``Unknown plugin: <name>`` quotes it back, so the same undecodable
        byte reaches ``click.echo`` from the second position instead.
        """
        storage = StorageManager(sqlite_path=tmp_path / "source.db")

        result = _invoke_with_mocks(
            cli_runner, ["source", "create", "books", self._RAW], storage
        )

        assert result.exit_code != 0
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert "Unknown plugin" in result.output

    @pytest.mark.parametrize(
        "command", ["show", "schema", "migrate", "enable", "disable"]
    )
    def test_an_unknown_source_id_is_named_back_without_its_surrogate(
        self, cli_runner: CliRunner, tmp_path: Path, command: str
    ) -> None:
        """``Unknown source: <id>`` echoes the argument as argv handed it over."""
        storage = StorageManager(sqlite_path=tmp_path / "source.db")

        result = _invoke_with_mocks(cli_runner, ["source", command, self._RAW], storage)

        assert result.exit_code != 0
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert "Unknown source" in result.output

    def test_complete_stores_the_stripped_title_and_review_regression(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """A command nobody pointed the per-option strip at.

        ``--title`` reaches the success line as well as the write, so this one
        crashed at both sinks.
        """
        storage = StorageManager(sqlite_path=tmp_path / "complete.db")

        result = _invoke_with_mocks(
            cli_runner,
            ["complete", "--type", "book", "--title", self._RAW, "--review", self._RAW],
            storage,
        )

        assert result.exit_code == 0, result.output
        assert f"Marked '{self._CLEANED}' as completed" in result.output
        stored = storage.get_content_items(content_type=ContentType.BOOK)
        assert [(item.title, item.review) for item in stored] == [
            (self._CLEANED, self._CLEANED)
        ]

    @staticmethod
    def _seeded_library(tmp_path: Path) -> tuple[StorageManager, int]:
        storage = StorageManager(sqlite_path=tmp_path / "library.db")
        db_id = storage.save_content_item(
            ContentItem(
                id="book-1",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
            ),
            user_id=1,
        )
        return storage, db_id


class TestTheSurrogateStripIsOneGate:
    """Where the guarantee lives, so the class above needs no hand list.

    Every argv token is stripped by the root group before Click binds it to a
    parameter, which is upstream of both sinks a command has.
    """

    def test_the_root_group_strips_every_token_before_parsing(self) -> None:
        with cli.make_context("recommendinator", ["source", "show", "a\udcffb"]) as ctx:
            assert [*ctx.protected_args, *ctx.args] == ["source", "show", "ab"]

    def test_every_command_hangs_off_the_group_that_strips(self) -> None:
        """One reachable another way would parse an argv nobody stripped."""
        exported = {getattr(cli_commands, name) for name in cli_commands.__all__}

        assert exported
        assert isinstance(cli, SurrogateFreeGroup)
        assert exported == set(cli.commands.values())

    def test_the_module_entry_point_runs_that_group(self) -> None:
        """``python3.11 -m src.cli`` is the only door into the tree."""
        entry = ast.parse(
            (Path(str(cli_commands.__file__)).parent.parent / "__main__.py").read_text(
                encoding="utf-8"
            )
        )

        assert {
            ast.unparse(node.func)
            for node in ast.walk(entry)
            if isinstance(node, ast.Call)
        } == {"cli"}

    def test_the_cli_strips_in_exactly_one_place(self) -> None:
        """A second call site is the per-option habit growing back, and it is
        what put the strip downstream of ``library edit``'s blank check.
        """
        root = Path(str(cli_commands.__file__)).parent.parent
        called_in = [
            path.relative_to(root).as_posix()
            for path in sorted(root.rglob("*.py"))
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Call)
            and ast.unparse(node.func) == "strip_lone_surrogates"
        ]

        assert called_in == ["main.py"]

    def test_text_the_locale_can_decode_survives_the_strip(self) -> None:
        """An emoji is one codepoint outside the BMP, not the surrogate pair
        its UTF-16 encoding would be, so the stripped range must miss it.
        """
        kept = "Sublime — 日本語 🎉 café"

        with cli.make_context("recommendinator", ["memory", "add", kept]) as ctx:
            assert [*ctx.protected_args, *ctx.args] == ["memory", "add", kept]


class TestAGuardSeesTheValueStorageWillGetRegression:
    """Reported: ``library edit --review`` of undecodable bytes wiped the
    stored review. Root cause: the strip ran after the blank check, so ``''``
    reached the write. Fix: strip before Click binds, upstream of every guard.
    """

    _ALL_UNDECODABLE = "\udcff\udcfe"

    @staticmethod
    def _library(tmp_path: Path) -> tuple[StorageManager, int]:
        storage = StorageManager(sqlite_path=tmp_path / "guards.db")
        db_id = storage.save_content_item(
            ContentItem(
                id="book-1",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
            ),
            user_id=1,
        )
        storage.update_item_from_ui(
            db_id=db_id, status="completed", review="worth it", user_id=1
        )
        return storage, db_id

    def test_library_edit_says_why_it_refused_the_review(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """Refused is not enough on its own: silence reads as a crash."""
        storage, db_id = self._library(tmp_path)

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", str(db_id), "--review", self._ALL_UNDECODABLE],
            storage,
        )

        assert result.exit_code != 0
        assert "--review cannot be empty" in result.output
        assert "--clear-review" in result.output
        assert storage.get_content_item(db_id, user_id=1).review == "worth it"

    def test_complete_refuses_the_same_review_regression(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """The other command guarding ``--review``, and the other write."""
        storage = StorageManager(sqlite_path=tmp_path / "complete.db")

        result = _invoke_with_mocks(
            cli_runner,
            ["complete", "--type", "book", "--title", "Dune"]
            + ["--review", self._ALL_UNDECODABLE],
            storage,
        )

        assert result.exit_code != 0
        assert "--review cannot be empty" in result.output
        assert storage.get_content_items(content_type=ContentType.BOOK) == []

    def test_the_length_cap_measures_the_review_that_gets_stored(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """The guard's other direction: counting the surrogates would refuse a
        review that fits, which is the same bug with the sign flipped.
        """
        storage, db_id = self._library(tmp_path)
        review = "a" * MAX_REVIEW_LENGTH + "\udcff" * 20

        result = _invoke_with_mocks(
            cli_runner,
            ["library", "edit", "--id", str(db_id), "--review", review],
            storage,
        )

        assert result.exit_code == 0, result.output
        assert storage.get_content_item(db_id, user_id=1).review == (
            "a" * MAX_REVIEW_LENGTH
        )

    def test_a_config_path_of_undecodable_bytes_is_refused_as_missing(
        self, cli_runner: CliRunner, tmp_path: Path
    ) -> None:
        """``docs/CLI.md`` names this as the trade the one strip costs."""
        config = tmp_path / "example\udcff.yaml"
        config.write_text("{}", encoding="utf-8", errors="surrogateescape")

        result = cli_runner.invoke(cli, ["--config", str(config), "status"])

        assert result.exit_code != 0
        assert "does not exist" in result.output
