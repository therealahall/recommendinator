"""The command surface, transcribed from ``30dfe35:src/cli/commands.py``.

``main.py`` imports group names off the package, so a subcommand left behind in
the split is one nobody can run, and nothing else in the suite reaches below
the group level.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, get_args
from unittest.mock import MagicMock

import click
import pytest
from click.testing import CliRunner

import src.sources.service as sources_service
from src.cli._shared import ValueCoercionError, coerce_value
from src.cli.commands._settings import _VALUE_TYPE_ERRORS
from src.cli.commands._source import _FIELD_TYPE_ERRORS
from src.cli.main import cli
from src.ingestion.plugin_base import ConfigField, SourcePlugin
from src.ingestion.registry import PluginRegistry
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.settings.metadata import SettingType
from src.sources.service import field_type_name
from src.storage.manager import StorageManager
from tests.cli.conftest import _invoke_with_mocks

_CONTENT_TYPES = ("book", "movie", "tv_show", "video_game")
_TABLE_JSON = ("table", "json")

#: ``None`` marks a leaf, a dict a group — so ``custom-rules`` nests as Click
#: nests it.
_COMMAND_TREE: dict[str, Any] = {
    "account": {"show": None, "set-password": None, "set-name": None},
    "auth": {"status": None, "connect": None, "disconnect": None},
    "complete": None,
    "enrichment": {"start": None, "status": None, "reset": None},
    "library": {
        "list": None,
        "show": None,
        "edit": None,
        "ignore": None,
        "unignore": None,
        "export": None,
    },
    "preferences": {
        "get": None,
        "set-weight": None,
        "set-toggle": None,
        "set-variety": None,
        "reset": None,
        "set-length": None,
        "custom-rules": {
            "list": None,
            "add": None,
            "remove": None,
            "clear": None,
            "interpret": None,
        },
    },
    "profile": {"show": None, "regenerate": None},
    "recommend": None,
    "settings": {
        "list": None,
        "get": None,
        "set": None,
        "apply": None,
        "reset": None,
        "set-secret": None,
        "clear-secret": None,
    },
    "source": {
        "list": None,
        "show": None,
        "schema": None,
        "migrate": None,
        "enable": None,
        "disable": None,
        "set": None,
        "apply": None,
        "set-secret": None,
        "clear-secret": None,
        "plugins": None,
        "create": None,
        "remove": None,
    },
    "status": None,
    "update": None,
}

#: An option with a secondary flag is joined with ``/``; a positional argument
#: is its own name.
_PARAM_SURFACE: dict[tuple[str, ...], tuple[str, ...]] = {
    ("status",): ("--format",),
    ("recommend",): ("--count", "--format", "--type", "--user"),
    ("update",): ("--source", "--workers"),
    ("complete",): ("--author", "--rating", "--review", "--title", "--type"),
    ("preferences", "get"): ("--format", "--user"),
    ("preferences", "set-weight"): ("--user", "scorer_name", "weight"),
    ("preferences", "set-toggle"): ("--user", "toggle_name", "value"),
    ("preferences", "set-variety"): ("--user", "penalty"),
    ("preferences", "reset"): ("--user",),
    ("preferences", "set-length"): ("--user", "content_type", "length_preference"),
    ("preferences", "custom-rules", "list"): ("--user",),
    ("preferences", "custom-rules", "add"): ("--user", "rule_text"),
    ("preferences", "custom-rules", "remove"): ("--user", "index"),
    ("preferences", "custom-rules", "clear"): ("--user", "--yes"),
    ("preferences", "custom-rules", "interpret"): ("rule_text",),
    ("enrichment", "start"): ("--retry-not-found", "--type", "--user"),
    ("enrichment", "status"): ("--format", "--user"),
    ("enrichment", "reset"): ("--provider", "--type", "--user", "--yes"),
    ("library", "list"): (
        "--enrichment",
        "--format",
        "--limit",
        "--needs-rating",
        "--offset",
        "--search",
        "--show-ignored",
        "--sort",
        "--status",
        "--type",
        "--user",
    ),
    ("library", "show"): ("--format", "--id", "--user"),
    ("library", "edit"): (
        "--clear-rating",
        "--clear-review",
        "--description",
        "--genre",
        "--id",
        "--rating",
        "--review",
        "--seasons-watched",
        "--status",
        "--tag",
        "--user",
    ),
    ("library", "ignore"): ("--id", "--user"),
    ("library", "unignore"): ("--id", "--user"),
    ("library", "export"): ("--format", "--output", "--type", "--user"),
    ("account", "show"): ("--format", "--user"),
    ("account", "set-password"): ("--format", "--user"),
    ("account", "set-name"): (
        "--display-name",
        "--format",
        "--user",
        "--username",
    ),
    ("auth", "status"): ("--user",),
    ("auth", "connect"): ("--no-browser", "--source", "--source-id", "--user"),
    ("auth", "disconnect"): ("--source", "--source-id", "--user", "--yes"),
    ("profile", "show"): ("--format", "--user"),
    ("profile", "regenerate"): ("--user",),
    ("source", "list"): ("--format",),
    ("source", "show"): ("--format", "source_id"),
    ("source", "schema"): ("--format", "source_id"),
    ("source", "migrate"): ("--format", "source_id"),
    ("source", "enable"): ("--format", "source_id"),
    ("source", "disable"): ("--format", "source_id"),
    ("source", "set"): ("--format", "field_name", "source_id", "value"),
    ("source", "apply"): ("--format", "--from-json", "source_id"),
    ("source", "set-secret"): ("field_name", "source_id"),
    ("source", "clear-secret"): ("field_name", "source_id"),
    ("source", "plugins"): ("--format",),
    ("source", "create"): (
        "--disabled/--enabled",
        "--format",
        "--from-json",
        "plugin_name",
        "source_id",
    ),
    ("source", "remove"): ("--yes", "source_id"),
    ("settings", "list"): ("--advanced", "--format", "--section"),
    ("settings", "get"): ("--format", "key"),
    ("settings", "set"): ("--format", "key", "value"),
    ("settings", "apply"): ("--format", "--from-json"),
    ("settings", "reset"): ("--format", "key"),
    ("settings", "set-secret"): ("key",),
    ("settings", "clear-secret"): ("key",),
}

#: The ``click.Choice`` lists that the two sweeps below do not reach.
_NAMED_CHOICES: dict[tuple[str, ...], dict[str, tuple[str, ...]]] = {
    ("preferences", "set-toggle"): {
        "toggle_name": ("series_in_order",),
        "value": ("on", "off"),
    },
    ("preferences", "set-length"): {
        "length_preference": ("any", "short", "medium", "long")
    },
    ("enrichment", "reset"): {"provider": ("tmdb", "openlibrary", "rawg", "all")},
    ("library", "list"): {
        "status_str": ("unread", "currently_consuming", "completed"),
        "sort_by": ("title", "updated_at", "rating", "created_at"),
        "enrichment_str": ("enriched", "not_enriched"),
    },
    ("library", "edit"): {"status_str": ("unread", "currently_consuming", "completed")},
    ("auth", "connect"): {"source": ("gog", "epic", "trakt")},
    ("auth", "disconnect"): {"source": ("gog", "epic", "trakt")},
}

_EXPORT_FORMATS = ("csv", "json")

#: Derived from the option spellings above rather than listed again: a
#: ``--format`` renamed on one side alone leaves the two sets disagreeing
#: instead of leaving a sweep keyed on the dest name checking nothing.
_FORMAT_COMMANDS = {
    path for path, params in _PARAM_SURFACE.items() if "--format" in params
}

#: Every command taking a content type, under either dest name the groups spell
#: it with — ``set-length`` takes it as an argument, the rest as ``--type``.
_CONTENT_TYPE_DESTS = ("content_type", "content_type_str")
_CONTENT_TYPE_COMMANDS = {
    ("complete",),
    ("enrichment", "reset"),
    ("enrichment", "start"),
    ("library", "export"),
    ("library", "list"),
    ("preferences", "set-length"),
    ("recommend",),
}

#: Every ``--format`` option is case-insensitive, so nothing is exempt.
_CASE_SENSITIVE_FORMATS: set[tuple[str, ...]] = set()


def _subtree(command: click.Command) -> dict[str, Any] | None:
    subcommands = getattr(command, "commands", None)
    if subcommands is None:
        return None
    return {name: _subtree(sub) for name, sub in subcommands.items()}


def _command_at(path: tuple[str, ...]) -> click.Command:
    command: click.Command = cli
    for name in path:
        command = command.commands[name]  # type: ignore[attr-defined]
    return command


def _param_surface(command: click.Command) -> tuple[str, ...]:
    return tuple(
        sorted(
            "/".join(sorted(param.opts + param.secondary_opts))
            for param in command.params
        )
    )


def _choices_of(command: click.Command) -> dict[str, tuple[str, ...]]:
    return {
        param.name: tuple(param.type.choices)
        for param in command.params
        if isinstance(param.type, click.Choice) and param.name is not None
    }


def _content_type_choices(command: click.Command) -> dict[str, tuple[str, ...]]:
    return {
        name: choices
        for name, choices in _choices_of(command).items()
        if name in _CONTENT_TYPE_DESTS
    }


def _leaf_paths(prefix: tuple[str, ...], node: dict[str, Any]) -> set[tuple[str, ...]]:
    leaves: set[tuple[str, ...]] = set()
    for name, child in node.items():
        if child is None:
            leaves.add((*prefix, name))
        else:
            leaves |= _leaf_paths((*prefix, name), child)
    return leaves


class TestEveryCommandSurvivedTheSplit:
    def test_the_whole_command_tree_is_registered(self) -> None:
        assert _subtree(cli) == _COMMAND_TREE

    def test_the_surface_map_covers_every_leaf_command(self) -> None:
        """Every sweep below parametrizes off ``_PARAM_SURFACE``, so one that
        had drifted short of the tree would quietly stop checking commands."""
        assert _leaf_paths((), _COMMAND_TREE) == set(_PARAM_SURFACE)

    @pytest.mark.parametrize("path", sorted(_PARAM_SURFACE))
    def test_each_command_declares_the_parameters_it_always_had(
        self, path: tuple[str, ...]
    ) -> None:
        assert _param_surface(_command_at(path)) == _PARAM_SURFACE[path]

    @pytest.mark.parametrize("path", sorted(_PARAM_SURFACE))
    def test_each_command_is_invocable_and_keeps_its_help(
        self, cli_runner: CliRunner, path: tuple[str, ...]
    ) -> None:
        """Registration is not reachability: a command whose module fails to
        import answers non-zero here rather than at collection."""
        result = _invoke_with_mocks(
            cli_runner, [*path, "--help"], MagicMock(spec=StorageManager)
        )

        assert result.exit_code == 0
        assert _command_at(path).help
        assert f"Usage: cli {' '.join(path)}" in result.output


class TestTheChoiceListsAreUnchanged:
    @pytest.mark.parametrize("path", sorted(_NAMED_CHOICES))
    def test_named_choice_lists_are_unchanged(self, path: tuple[str, ...]) -> None:
        actual = _choices_of(_command_at(path))
        expected = _NAMED_CHOICES[path]

        assert {name: actual[name] for name in expected} == expected

    @pytest.mark.parametrize("path", sorted(_CONTENT_TYPE_COMMANDS))
    def test_content_type_options_offer_the_four_types(
        self, path: tuple[str, ...]
    ) -> None:
        assert list(_content_type_choices(_command_at(path)).values()) == [
            _CONTENT_TYPES
        ]

    def test_no_other_command_takes_a_content_type(self) -> None:
        """Anchors the sweep above at both ends: a renamed dest empties this
        set, and a command that grows one is not silently left out of it."""
        found = {
            path for path in _PARAM_SURFACE if _content_type_choices(_command_at(path))
        }

        assert found == _CONTENT_TYPE_COMMANDS

    @pytest.mark.parametrize("path", sorted(_FORMAT_COMMANDS))
    def test_format_offers_table_and_json_everywhere_but_export(
        self, path: tuple[str, ...]
    ) -> None:
        expected = _EXPORT_FORMATS if path == ("library", "export") else _TABLE_JSON

        assert _choices_of(_command_at(path))["output_format"] == expected

    def test_every_format_option_is_the_one_dest_the_sweeps_read(self) -> None:
        """The anchor for the two sweeps keyed on ``output_format``, and what
        ties that dest to the ``--format`` spelling they parametrize off."""
        found = {
            path
            for path in _PARAM_SURFACE
            if "output_format" in _choices_of(_command_at(path))
        }

        assert found == _FORMAT_COMMANDS != set()

    @pytest.mark.parametrize("path", sorted(_FORMAT_COMMANDS))
    def test_format_case_sensitivity_is_unchanged(self, path: tuple[str, ...]) -> None:
        (param,) = [p for p in _command_at(path).params if p.name == "output_format"]

        assert isinstance(param.type, click.Choice)
        assert param.type.case_sensitive is (path in _CASE_SENSITIVE_FORMATS)


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "cli.db")


@pytest.fixture()
def second_storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "cli-second.db")


@pytest.fixture()
def base_config() -> dict[str, Any]:
    return {
        "inputs": {
            "my_games": {
                "plugin": "fake_api",
                "enabled": True,
                "api_key": "yaml_key",
                "user_id": "yaml_user",
                "min_minutes": 30,
                "tags": ["rpg", "indie"],
                "active": True,
            }
        }
    }


@pytest.mark.usefixtures("registry_with_source_fakes")
class TestTheSourceGroupWordsARefusedValueAsItAlwaysDid:
    """One ``coerce_value`` now serves both groups, each re-wording the refusal.

    A merge letting the settings wording reach ``source set`` would still abort
    non-zero, which is all the sibling tests assert.
    """

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("active", "maybe", "Error: Field 'active' is bool — pass true/false"),
            (
                "min_minutes",
                "not_a_number",
                "Error: Field 'min_minutes' must be an integer",
            ),
        ],
    )
    def test_a_value_the_field_type_cannot_represent_is_refused_by_name(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
        field: str,
        value: str,
        message: str,
    ) -> None:
        storage.upsert_source_config(1, "my_games", "fake_api", {}, enabled=True)

        result = _invoke_with_mocks(
            cli_runner,
            ["source", "set", "my_games", field, value],
            mock_storage=storage,
            config=base_config,
        )

        assert result.exit_code != 0
        assert message in result.output

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("  TRUE  ", True),
            ("On", True),
            ("1", True),
            ("  FALSE ", False),
            ("Off", False),
            ("0", False),
        ],
    )
    def test_a_boolean_is_stripped_and_case_folded_before_it_is_read(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
        raw: str,
        expected: bool,
    ) -> None:
        storage.upsert_source_config(1, "my_games", "fake_api", {}, enabled=True)

        result = _invoke_with_mocks(
            cli_runner,
            ["source", "set", "my_games", "active", raw],
            mock_storage=storage,
            config=base_config,
        )

        assert result.exit_code == 0
        row = storage.get_source_config(1, "my_games")
        assert row is not None and row["config"]["active"] is expected

    def test_an_all_separator_list_value_stores_an_empty_list(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        """A list of nothing but separators means no entries, not ``[""]``."""
        storage.upsert_source_config(
            1, "my_games", "fake_api", {"tags": ["rpg"]}, enabled=True
        )

        result = _invoke_with_mocks(
            cli_runner,
            ["source", "set", "my_games", "tags", " , ,"],
            mock_storage=storage,
            config=base_config,
        )

        assert result.exit_code == 0
        row = storage.get_source_config(1, "my_games")
        assert row is not None and row["config"]["tags"] == []

    def test_a_string_field_keeps_a_value_no_coercion_would_survive(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        """A ``str`` field passes through verbatim — no strip, no split."""
        storage.upsert_source_config(1, "my_games", "fake_api", {}, enabled=True)

        result = _invoke_with_mocks(
            cli_runner,
            ["source", "set", "my_games", "user_id", " Ünïcode, true "],
            mock_storage=storage,
            config=base_config,
        )

        assert result.exit_code == 0
        row = storage.get_source_config(1, "my_games")
        assert row is not None and row["config"]["user_id"] == " Ünïcode, true "


class EveryFieldTypePlugin(SourcePlugin):
    """One non-sensitive field per ``ConfigField.field_type``.

    ``ratio`` is the ``float`` no shipped plugin declares and ``payload`` a type
    no branch handles — the two the coercion is otherwise never asked about.
    """

    @property
    def name(self) -> str:
        return "every_type"

    @property
    def display_name(self) -> str:
        return "Every Field Type"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.VIDEO_GAME]

    @property
    def requires_api_key(self) -> bool:
        return False

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(name="label", field_type=str, required=False, default=""),
            ConfigField(name="count", field_type=int, required=False, default=0),
            ConfigField(name="ratio", field_type=float, required=False, default=0.0),
            ConfigField(name="tags", field_type=list, required=False, default=[]),
            ConfigField(name="active", field_type=bool, required=False, default=False),
            ConfigField(name="payload", field_type=dict, required=False, default=None),
        ]

    def validate_config(self, config: dict[str, Any], **kwargs: Any) -> list[str]:
        return []

    def fetch(self, config: dict[str, Any]) -> Iterator[ContentItem]:
        yield ContentItem(
            id="g",
            title="Stub",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            source=self.get_source_identifier(config),
        )


@pytest.fixture()
def registry_with_every_field_type() -> Iterator[None]:
    """Swap the plugin registry for the one plugin declaring all six types."""
    registry = PluginRegistry.get_instance()
    registry._discovered = True
    registry._plugins.clear()
    registry.register(EveryFieldTypePlugin())
    yield
    PluginRegistry.reset_instance()


@pytest.fixture()
def typed_config() -> dict[str, Any]:
    return {"inputs": {"typed": {"plugin": "every_type", "enabled": True}}}


@pytest.mark.usefixtures("registry_with_every_field_type")
class TestSourceSetCoercesEveryFieldTypeAsItAlwaysDid:
    """``source set`` asks ``field_type_name`` for the name it used to look up
    in a table of its own, and the two spell a plain string differently.
    Equivalent only while every type still lands on the same branch.
    """

    @pytest.mark.parametrize(
        ("field", "raw", "stored"),
        [
            ("active", "true", True),
            ("active", "  YES ", True),
            ("active", "0", False),
            ("count", "60", 60),
            ("count", " -3 ", -3),
            ("count", "٥", 5),
            ("ratio", "3.5", 3.5),
            ("ratio", "2", 2.0),
            ("ratio", " 1e3 ", 1000.0),
            ("ratio", " -0.5 ", -0.5),
            ("tags", "rpg, indie ,,strategy", ["rpg", "indie", "strategy"]),
            ("tags", "", []),
            ("label", " true ", " true "),
            ("label", "5", "5"),
            ("label", "a, b", "a, b"),
            ("label", "", ""),
        ],
    )
    def test_a_value_is_stored_as_the_type_its_field_declares(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        typed_config: dict[str, Any],
        field: str,
        raw: str,
        stored: object,
    ) -> None:
        storage.upsert_source_config(1, "typed", "every_type", {}, enabled=True)

        result = _invoke_with_mocks(
            cli_runner,
            ["source", "set", "typed", field, raw],
            mock_storage=storage,
            config=typed_config,
        )

        assert result.exit_code == 0
        row = storage.get_source_config(1, "typed")
        assert row is not None
        assert row["config"][field] == stored
        # ``2.0 == 2`` and ``1 == True``, so the value alone does not say which
        # branch coerced it.
        assert type(row["config"][field]) is type(stored)

    @pytest.mark.parametrize(
        ("field", "raw", "message"),
        [
            ("active", "maybe", "Error: Field 'active' is bool — pass true/false"),
            ("active", "", "Error: Field 'active' is bool — pass true/false"),
            ("count", "6.5", "Error: Field 'count' must be an integer"),
            ("count", "0x10", "Error: Field 'count' must be an integer"),
            ("count", "", "Error: Field 'count' must be an integer"),
            ("ratio", "warm", "Error: Field 'ratio' must be a number"),
            ("ratio", "", "Error: Field 'ratio' must be a number"),
        ],
    )
    def test_a_value_the_field_type_cannot_represent_is_refused_and_writes_nothing(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        typed_config: dict[str, Any],
        field: str,
        raw: str,
        message: str,
    ) -> None:
        seeded = {"active": True, "count": 1, "ratio": 1.5}
        storage.upsert_source_config(1, "typed", "every_type", seeded, enabled=True)

        result = _invoke_with_mocks(
            cli_runner,
            ["source", "set", "typed", field, raw],
            mock_storage=storage,
            config=typed_config,
        )

        assert result.exit_code == 1
        assert message in result.output
        row = storage.get_source_config(1, "typed")
        assert row is not None and row["config"] == seeded

    def test_a_field_type_no_branch_handles_passes_its_value_through(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        typed_config: dict[str, Any],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """``payload`` is declared ``dict``, which neither the old table nor the
        ``"str"`` it now falls back to has a branch for. The fallback's warning
        is the one thing the swap added, and it goes to the log, not to output.
        """
        storage.upsert_source_config(1, "typed", "every_type", {}, enabled=True)

        result = _invoke_with_mocks(
            cli_runner,
            ["source", "set", "typed", "payload", " 1, 2 "],
            mock_storage=storage,
            config=typed_config,
        )

        assert result.exit_code == 0
        assert result.output == "Set typed.payload = ' 1, 2 '\n"
        row = storage.get_source_config(1, "typed")
        assert row is not None and row["config"]["payload"] == " 1, 2 "
        assert "falling back to 'str'" in caplog.text


@pytest.mark.usefixtures("registry_with_source_fakes")
class TestTheGuardsThatMovedIntoTheSharedModuleKeepTheirWording:
    """``abort_with`` and ``read_json_payload`` left the group that owned them.

    Every sibling test asserts a non-zero exit alone, which a swapped message
    still satisfies.
    """

    @pytest.mark.parametrize(
        "args",
        [
            ["source", "show", "nope"],
            ["source", "enable", "nope"],
            ["source", "set", "nope", "user_id", "x"],
            ["source", "apply", "nope", "--from-json", "-"],
        ],
    )
    def test_a_source_that_does_not_exist_is_named_in_the_refusal(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
        args: list[str],
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner,
            args,
            mock_storage=storage,
            config=base_config,
            input_text="{}",
        )

        assert result.exit_code != 0
        assert "Error: Unknown source: nope" in result.output

    @pytest.mark.parametrize(
        ("payload", "message"),
        [
            ("{not json", "Error: Invalid JSON:"),
            ("[1, 2, 3]", "Error: JSON payload must be an object"),
            ('"a string"', "Error: JSON payload must be an object"),
            ("null", "Error: JSON payload must be an object"),
            ("", "Error: Invalid JSON:"),
        ],
    )
    def test_a_malformed_stdin_payload_is_refused_in_prose(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
        payload: str,
        message: str,
    ) -> None:
        storage.upsert_source_config(1, "my_games", "fake_api", {}, enabled=True)

        result = _invoke_with_mocks(
            cli_runner,
            ["source", "apply", "my_games", "--from-json", "-"],
            mock_storage=storage,
            config=base_config,
            input_text=payload,
        )

        assert result.exit_code != 0
        assert message in result.output
        row = storage.get_source_config(1, "my_games")
        assert row is not None and row["config"] == {}

    def test_an_empty_json_object_applies_nothing_and_says_so(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        storage.upsert_source_config(1, "my_games", "fake_api", {}, enabled=True)

        result = _invoke_with_mocks(
            cli_runner,
            ["source", "apply", "my_games", "--from-json", "-"],
            mock_storage=storage,
            config=base_config,
            input_text="{}",
        )

        assert result.exit_code == 0
        assert result.output == "Applied 0 field(s) to my_games.\n"

    def test_settings_apply_refuses_the_same_malformed_payload(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        """One reader now serves both groups, so both must still refuse it."""
        result = _invoke_with_mocks(
            cli_runner,
            ["settings", "apply", "--from-json", "-"],
            storage,
            input_text="[1, 2, 3]",
        )

        assert result.exit_code != 0
        assert "Error: JSON payload must be an object" in result.output

    def test_a_from_json_path_that_does_not_exist_names_the_path(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
        tmp_path: Path,
    ) -> None:
        storage.upsert_source_config(1, "my_games", "fake_api", {}, enabled=True)
        missing = tmp_path / "absent.json"

        result = _invoke_with_mocks(
            cli_runner,
            ["source", "apply", "my_games", "--from-json", str(missing)],
            mock_storage=storage,
            config=base_config,
        )

        assert result.exit_code != 0
        assert f"Error: Could not read {missing}:" in result.output


class TestTheSettingsGroupWordsARefusedValueAsItAlwaysDid:
    @pytest.mark.parametrize(
        ("key", "value", "message"),
        [
            ("enrichment.enabled", "maybe", "Error: expected true or false"),
            ("recommendations.default_count", "abc", "Error: expected an integer"),
            (
                "recommendations.scorer_weights.genre_match",
                "warm",
                "Error: expected a number",
            ),
        ],
    )
    def test_a_value_the_setting_type_cannot_represent_is_refused(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        key: str,
        value: str,
        message: str,
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner, ["settings", "set", key, value], storage
        )

        assert result.exit_code != 0
        assert message in result.output
        assert storage.get_setting(key) is None

    def test_the_refusal_names_no_field_the_way_the_source_group_does(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        """Proof the two wordings did not converge on one of them."""
        result = _invoke_with_mocks(
            cli_runner, ["settings", "set", "enrichment.enabled", "maybe"], storage
        )

        assert "Field '" not in result.output

    @pytest.mark.parametrize("raw", ["  TRUE  ", "On", "1"])
    def test_a_boolean_is_stripped_and_case_folded_before_it_is_read(
        self, cli_runner: CliRunner, storage: StorageManager, raw: str
    ) -> None:
        result = _invoke_with_mocks(
            cli_runner, ["settings", "set", "enrichment.enabled", raw], storage
        )

        assert result.exit_code == 0
        assert storage.get_setting("enrichment.enabled") is True


def _returned_string_constants(tree: ast.AST, function_name: str) -> set[str]:
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    return {
        node.value.value
        for node in ast.walk(function)
        if isinstance(node, ast.Return)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }


def _type_names_the_source_group_can_pass() -> set[str]:
    """Read off ``field_type_name`` rather than listed here: a hand-written
    population stops tracking the function the moment a branch is added, and
    the name it missed is the one that reaches ``_FIELD_TYPE_ERRORS`` as a
    ``KeyError`` in front of a user.
    """
    return _returned_string_constants(
        ast.parse(Path(sources_service.__file__).read_text(encoding="utf-8")),
        "field_type_name",
    )


class TestEveryTypeTheCoercionRefusesHasWordingInBothGroups:
    """A refusable type missing from a caller's table reaches the user as a
    ``KeyError`` traceback rather than an abort."""

    def test_the_type_names_are_read_off_the_function_that_answers_them(self) -> None:
        """A blind reader reports an empty vocabulary, leaving the sweep below
        to check the settings side alone and pass."""
        assert _type_names_the_source_group_can_pass() >= {
            field_type_name(python_type)
            for python_type in (bool, int, float, list, str)
        }

    def test_a_type_name_added_to_the_function_joins_the_sweep(self) -> None:
        """What the reader buys over a hand-listed population."""
        invented = ast.parse(
            "def field_type_name(field_type):\n"
            "    if field_type is dict:\n"
            '        return "dict"\n'
            '    return "str"\n'
        )

        assert _returned_string_constants(invented, "field_type_name") == {
            "dict",
            "str",
        }

    def test_both_wording_tables_cover_every_refusable_type(self) -> None:
        # The settings registry's own vocabulary, and every name
        # ``field_type_name`` is written to return.
        vocabulary = {*get_args(SettingType), *_type_names_the_source_group_can_pass()}

        refusable = set()
        for value_type in vocabulary:
            try:
                coerce_value(value_type, "no value of any type spells this")
            except ValueCoercionError as error:
                refusable.add(error.value_type)

        assert refusable
        assert refusable <= set(_FIELD_TYPE_ERRORS)
        assert refusable <= set(_VALUE_TYPE_ERRORS)


@pytest.mark.usefixtures("registry_with_source_fakes")
class TestBothOutputModesSayWhatTheyAlwaysSaidAndLeaveTheSameState:
    """Two per-group emitters merged into ``emit_view``.

    Pinned here: each branch's wording, and that the state they leave is the
    same one. The thunk's laziness is a unit test in ``test_command_package``.
    """

    @pytest.mark.parametrize(
        ("args", "expected"),
        [
            (["source", "enable", "my_games"], "Enabled source 'my_games'.\n"),
            (["source", "disable", "my_games"], "Disabled source 'my_games'.\n"),
            (
                ["source", "set", "my_games", "min_minutes", "60"],
                "Set my_games.min_minutes = 60\n",
            ),
        ],
    )
    def test_table_mode_prints_the_confirmation_and_nothing_else(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
        args: list[str],
        expected: str,
    ) -> None:
        storage.upsert_source_config(1, "my_games", "fake_api", {}, enabled=True)

        result = _invoke_with_mocks(
            cli_runner, args, mock_storage=storage, config=base_config
        )

        assert result.exit_code == 0
        assert result.output == expected

    def test_table_mode_apply_counts_the_fields_it_wrote(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        storage.upsert_source_config(1, "my_games", "fake_api", {}, enabled=True)

        result = _invoke_with_mocks(
            cli_runner,
            ["source", "apply", "my_games", "--from-json", "-"],
            mock_storage=storage,
            config=base_config,
            input_text=json.dumps({"user_id": "a", "min_minutes": 1}),
        )

        assert result.exit_code == 0
        assert result.output == "Applied 2 field(s) to my_games.\n"

    def test_the_state_table_mode_leaves_is_the_view_json_mode_reports(
        self,
        cli_runner: CliRunner,
        storage: StorageManager,
        second_storage: StorageManager,
        base_config: dict[str, Any],
    ) -> None:
        """Two identically-seeded databases, one enabled through each branch. A
        read-back with a side effect would leave them describing different
        sources."""
        for store in (storage, second_storage):
            store.upsert_source_config(1, "my_games", "fake_api", {}, enabled=False)

        table_run = _invoke_with_mocks(
            cli_runner,
            ["source", "enable", "my_games"],
            mock_storage=storage,
            config=base_config,
        )
        json_run = _invoke_with_mocks(
            cli_runner,
            ["source", "enable", "my_games", "--format", "json"],
            mock_storage=second_storage,
            config=base_config,
        )
        after_table = _invoke_with_mocks(
            cli_runner,
            ["source", "show", "my_games", "--format", "json"],
            mock_storage=storage,
            config=base_config,
        )

        assert table_run.exit_code == 0
        assert json_run.exit_code == 0
        emitted = json.loads(json_run.output)
        observed = json.loads(after_table.output)
        assert emitted["enabled"] is True
        # Per-row insert timestamps, so the two rows differ there and nowhere
        # else.
        emitted.pop("migrated_at")
        observed.pop("migrated_at")
        assert observed == emitted

    def test_settings_reset_and_apply_keep_their_table_confirmations(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        storage.set_setting("recommendations.default_count", 9)

        reset_result = _invoke_with_mocks(
            cli_runner, ["settings", "reset", "recommendations.default_count"], storage
        )
        apply_result = _invoke_with_mocks(
            cli_runner,
            ["settings", "apply", "--from-json", "-"],
            storage,
            input_text=json.dumps({"recommendations.default_count": 7}),
        )

        assert reset_result.output == (
            "Reset recommendations.default_count to its default.\n"
        )
        assert apply_result.output == "Applied 1 setting(s).\n"

    def test_json_mode_omits_the_restart_advisory_the_table_mode_prints(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        """The advisory echoes after ``emit_view``, pinning its order.

        The confirmation reads the running config, which a restart-required
        leaf is deliberately not live-applied into, so it echoes the value
        still in force and not the one just stored. Pre-existing.
        """
        table_result = _invoke_with_mocks(
            cli_runner, ["settings", "set", "logging.level", "DEBUG"], storage
        )
        json_result = _invoke_with_mocks(
            cli_runner,
            ["settings", "set", "logging.level", "DEBUG", "--format", "json"],
            storage,
        )

        assert storage.get_setting("logging.level") == "DEBUG"
        assert table_result.output == (
            "Set logging.level = INFO.\nThis change takes effect after a restart.\n"
        )
        assert "This change takes effect" not in json_result.output
        assert set(json.loads(json_result.output)) == {"sections"}

    def test_a_live_applied_leaf_echoes_the_value_it_just_stored(
        self, cli_runner: CliRunner, storage: StorageManager
    ) -> None:
        """The counterpart, so the sibling above cannot be read as the rule."""
        result = _invoke_with_mocks(
            cli_runner, ["settings", "set", "enrichment.enabled", "off"], storage
        )

        assert result.output == "Set enrichment.enabled = false.\n"
        assert storage.get_setting("enrichment.enabled") is False
