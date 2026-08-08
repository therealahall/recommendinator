"""Tests for the file-import allowlist behind ``security.allowed_source_roots``."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from src.cli.config import load_config
from src.ingestion.paths import (
    DEFAULT_ALLOWED_SOURCE_ROOTS,
    PathNotAllowed,
    configure_allowed_source_roots,
    get_allowed_source_roots,
    resolve_source_path,
)
from src.ingestion.plugin_base import SourceError, SourcePlugin
from src.ingestion.registry import PluginRegistry
from src.ingestion.sources.calibre_web.calibre_web import CalibreWebPlugin
from src.ingestion.sources.generic_csv.generic_csv import CsvImportPlugin
from src.ingestion.sources.generic_json.generic_json import JsonImportPlugin
from src.ingestion.sources.goodreads_csv.goodreads_csv import GoodreadsCsvPlugin
from src.ingestion.sources.markdown.markdown import MarkdownImportPlugin
from src.ingestion.sources.radarr.radarr import RadarrPlugin
from src.ingestion.sources.roms.roms import RomScannerPlugin
from src.ingestion.sources.sonarr.sonarr import SonarrPlugin
from src.ingestion.sources.storygraph_csv.storygraph_csv import StorygraphCsvPlugin
from src.models.config_field import ConfigField
from src.settings.metadata import get_entry
from src.storage.manager import StorageManager
from tests.cli.conftest import _invoke_with_mocks

_ALLOWLIST_KEY = "security.allowed_source_roots"


def _declared_path_fields(plugin: SourcePlugin) -> list[ConfigField]:
    """Read off the declared flag, never guessed from the field's name.

    A name-shaped rule shared its blind spot with the sweep it guards:
    ``scan_folder`` matched neither, so the plugin escaped containment and
    the guard agreed it should.
    """
    return [field for field in plugin.get_config_schema() if field.reads_path]


def _reads_a_configured_path(plugin: SourcePlugin) -> bool:
    return bool(_declared_path_fields(plugin))


def _params(plugins: list[SourcePlugin]) -> list[Any]:
    """Identify each case by registry name, which the sweeps compare against."""
    return [pytest.param(plugin, id=plugin.name) for plugin in plugins]


_FILE_BASED_PLUGINS = _params(
    [
        CsvImportPlugin(),
        JsonImportPlugin(),
        MarkdownImportPlugin(),
        RomScannerPlugin(),
        GoodreadsCsvPlugin(),
        StorygraphCsvPlugin(),
    ]
)

_URL_PLUGINS = _params([CalibreWebPlugin(), RadarrPlugin(), SonarrPlugin()])


def _builtin_plugins() -> dict[str, SourcePlugin]:
    """Every plugin shipped in this repository, private ones excluded.

    Built off a throwaway registry so the singleton other tests install fakes
    into is left alone.
    """
    registry = PluginRegistry()
    registry.discover_plugins()
    return {
        name: plugin
        for name, plugin in registry.get_all_plugins().items()
        if type(plugin).__module__.startswith("src.ingestion.sources.")
    }


def _escaping_config(plugin: SourcePlugin, target: Path) -> dict[str, Any]:
    """Point *plugin* at *target*.

    ``content_type`` is unconditional: the plugins without it ignore the key,
    and the ones with it fail validation for another reason when it is absent.
    """
    if _declared_path_fields(plugin)[0].name == "paths":
        return {"paths": [str(target)]}
    return {"path": str(target), "content_type": "book"}


@pytest.fixture()
def outside(tmp_path: Path) -> Path:
    """A directory beside the only allowed root, holding a readable file."""
    directory = tmp_path.parent / f"{tmp_path.name}-outside"
    directory.mkdir()
    (directory / "secret.csv").write_text("title\nLeaked\n")
    return directory


def _outside_target(plugin: SourcePlugin, outside: Path) -> Path:
    """The out-of-bounds thing this plugin reads — a directory or a file."""
    reads_a_directory = _declared_path_fields(plugin)[0].name == "paths"
    return outside if reads_a_directory else outside / "secret.csv"


class TestConfigureAllowedSourceRoots:
    """Reading the allowlist out of config.yaml."""

    def test_reads_and_strips_the_configured_list(self) -> None:
        configure_allowed_source_roots(
            {"security": {"allowed_source_roots": ["/srv/media", "  /srv/roms  "]}}
        )
        assert get_allowed_source_roots() == ("/srv/media", "/srv/roms")

    def test_absent_section_falls_back_to_the_default(self) -> None:
        configure_allowed_source_roots({})
        assert get_allowed_source_roots() == DEFAULT_ALLOWED_SOURCE_ROOTS

    @pytest.mark.parametrize("unusable", ["/", 7, ["/srv", ""], ["/srv", 3]])
    def test_an_unusable_value_falls_back_rather_than_widening(
        self, unusable: object
    ) -> None:
        configure_allowed_source_roots({"security": {"allowed_source_roots": unusable}})
        assert get_allowed_source_roots() == DEFAULT_ALLOWED_SOURCE_ROOTS

    def test_an_explicit_empty_list_allows_nothing(self, tmp_path: Path) -> None:
        configure_allowed_source_roots({"security": {"allowed_source_roots": []}})
        with pytest.raises(PathNotAllowed):
            resolve_source_path(str(tmp_path))

    def test_load_config_installs_the_allowlist(self) -> None:
        load_config(Path("config/example.yaml"))
        assert get_allowed_source_roots() == DEFAULT_ALLOWED_SOURCE_ROOTS


class TestResolveSourcePath:
    """Containment, resolved on both sides."""

    def test_accepts_a_file_under_an_allowed_root(self, tmp_path: Path) -> None:
        target = tmp_path / "books.csv"
        target.write_text("title\n")
        assert resolve_source_path(str(target)) == target.resolve()

    def test_accepts_the_root_itself(self, tmp_path: Path) -> None:
        assert resolve_source_path(str(tmp_path)) == tmp_path.resolve()

    def test_refuses_a_path_under_no_root(self) -> None:
        with pytest.raises(PathNotAllowed, match="outside the allowed source roots"):
            resolve_source_path("/etc/passwd")

    def test_refuses_a_traversal_out_of_a_root(self, tmp_path: Path) -> None:
        with pytest.raises(PathNotAllowed):
            resolve_source_path(str(tmp_path / ".." / "escaped.csv"))

    def test_refuses_a_symlink_that_escapes_its_root(self, tmp_path: Path) -> None:
        """Resolve-then-compare, not a string prefix check.

        A writable allowed root is the attacker's foothold: a link inside it
        pointing anywhere on disk would otherwise pass containment and be read.
        """
        outside = tmp_path.parent / f"{tmp_path.name}-outside"
        outside.mkdir()
        secret = outside / "secret.csv"
        secret.write_text("title\nLeaked\n")
        link = tmp_path / "link.csv"
        link.symlink_to(secret)

        with pytest.raises(PathNotAllowed):
            resolve_source_path(str(link))

    def test_a_sibling_sharing_the_roots_name_prefix_is_refused(
        self, tmp_path: Path
    ) -> None:
        sibling = tmp_path.parent / f"{tmp_path.name}-evil"
        sibling.mkdir()
        with pytest.raises(PathNotAllowed):
            resolve_source_path(str(sibling / "books.csv"))

    def test_a_home_relative_path_is_refused(self) -> None:
        """A leading tilde is expanded before the comparison, not after it."""
        with pytest.raises(PathNotAllowed):
            home_path = "~/.ssh/id_rsa"  # self-contained: allow this test's subject
            resolve_source_path(home_path)

    def test_a_relative_path_is_resolved_against_the_working_directory(
        self, tmp_path: Path
    ) -> None:
        """The shipped root ``inputs`` is relative, so both sides must be."""
        configure_allowed_source_roots({"security": {"allowed_source_roots": ["."]}})
        assert resolve_source_path("config/example.yaml").is_absolute()
        configure_allowed_source_roots(
            {"security": {"allowed_source_roots": [str(tmp_path)]}}
        )
        with pytest.raises(PathNotAllowed):
            resolve_source_path("config/example.yaml")

    def test_a_path_carrying_a_nul_byte_is_refused_as_a_containment_failure(
        self,
    ) -> None:
        """Callers catch ``PathNotAllowed`` only.

        Any other exception out of here leaves ``validate_config`` raising
        instead of returning errors, which the API answers 500.
        """
        with pytest.raises(PathNotAllowed):
            resolve_source_path("/etc/passwd\x00.csv")

    def test_a_tilde_naming_no_such_user_is_refused_the_same_way(self) -> None:
        """The tilde expansion raises ``RuntimeError``, which the guard missed.

        ``path`` is HTTP-writable, so this reached ``validate_config`` as a 500
        — the one outcome the NUL-byte branch above exists to prevent.
        """
        with pytest.raises(PathNotAllowed, match="cannot be resolved"):
            unknown_user = "~nosuchuser/books.csv"  # self-contained: test subject
            resolve_source_path(unknown_user)


class TestEveryFileReadingPluginIsContained:
    """Containment has to hold for every plugin that opens a configured path.

    The attacker picks the plugin, not the user: source config is writable over
    HTTP, so one unguarded plugin restores the whole arbitrary-read primitive.
    """

    def test_the_sweep_covers_every_built_in_plugin_that_reads_a_path(self) -> None:
        """Without this a new file-based plugin escapes the parametrised tests."""
        reads_a_path = {
            name
            for name, plugin in _builtin_plugins().items()
            if _reads_a_configured_path(plugin)
        }
        assert reads_a_path == {param.id for param in _FILE_BASED_PLUGINS}

    def test_no_offline_plugin_leaves_the_path_it_reads_undeclared(self) -> None:
        """The equality above catches a missed call, this a missed declaration.

        A plugin reading a configured path while declaring none is missing from
        both sides of it, so it holds with containment gone. No network means
        reading off disk.
        """
        plugins = _builtin_plugins()
        offline = {
            name for name, plugin in plugins.items() if not plugin.requires_network
        }
        undeclared = {
            name
            for name, plugin in plugins.items()
            if not _reads_a_configured_path(plugin)
        }

        assert offline & undeclared == set()

    def test_the_deriver_answers_the_flag_rather_than_the_field_name(self) -> None:
        """Otherwise the sweep holds on a deriver that matches nothing.

        ``scan_folder`` is the shape that escaped the old name-matching rule
        and its guard alike, because both ran the same match.
        """
        undeclared = Mock(spec=SourcePlugin)
        undeclared.get_config_schema.return_value = [
            ConfigField(name="scan_folder", field_type=str)
        ]
        declared = Mock(spec=SourcePlugin)
        declared.get_config_schema.return_value = [
            ConfigField(name="scan_folder", field_type=str, reads_path=True)
        ]

        assert _reads_a_configured_path(undeclared) is False
        assert _reads_a_configured_path(declared) is True

    @pytest.mark.parametrize("plugin", _FILE_BASED_PLUGINS)
    def test_the_one_path_field_declared_is_the_one_the_cases_point_outside(
        self, plugin: SourcePlugin
    ) -> None:
        """``_escaping_config`` builds for a single field.

        A plugin growing a second path-valued field would have it exercised by
        none of the cases below, the same shape as the ``scan_folder`` escape.
        """
        assert [field.name for field in _declared_path_fields(plugin)] in (
            ["path"],
            ["paths"],
        )

    @pytest.mark.parametrize("plugin", _FILE_BASED_PLUGINS)
    def test_validate_reports_a_path_outside_every_root(
        self, plugin: SourcePlugin, outside: Path
    ) -> None:
        errors = plugin.validate_config(
            _escaping_config(plugin, _outside_target(plugin, outside))
        )

        assert any("outside the allowed source roots" in error for error in errors)

    @pytest.mark.parametrize("plugin", _FILE_BASED_PLUGINS)
    def test_fetch_refuses_a_path_outside_every_root(
        self, plugin: SourcePlugin, outside: Path
    ) -> None:
        """Matched on the message: a parse failure is not containment."""
        config = _escaping_config(plugin, _outside_target(plugin, outside))

        with pytest.raises(SourceError, match="outside the allowed source roots"):
            list(plugin.fetch(config))

    @pytest.mark.parametrize("plugin", _FILE_BASED_PLUGINS)
    def test_fetch_refuses_a_symlink_that_escapes_its_root(
        self, plugin: SourcePlugin, tmp_path: Path, outside: Path
    ) -> None:
        target = _outside_target(plugin, outside)
        link = tmp_path / "link"
        link.symlink_to(target, target_is_directory=target.is_dir())

        with pytest.raises(SourceError, match="outside the allowed source roots"):
            list(plugin.fetch(_escaping_config(plugin, link)))


class TestEveryNetworkPluginGuardsItsSecret:
    """The url a secret is sent to is as much a part of the secret as its value."""

    def test_the_sweep_covers_every_built_in_plugin_with_a_url(self) -> None:
        has_a_url = {
            name
            for name, plugin in _builtin_plugins().items()
            if any(field.name == "url" for field in plugin.get_config_schema())
        }
        assert has_a_url == {param.id for param in _URL_PLUGINS}

    @pytest.mark.parametrize("plugin", _URL_PLUGINS)
    def test_the_url_is_credential_bound_when_the_plugin_stores_a_secret(
        self, plugin: SourcePlugin
    ) -> None:
        schema = plugin.get_config_schema()
        assert any(field.sensitive for field in schema)
        url_field = next(field for field in schema if field.name == "url")
        assert url_field.credential_bound is True

    @pytest.mark.parametrize("plugin", _URL_PLUGINS)
    def test_validate_refuses_a_url_that_would_read_local_files(
        self, plugin: SourcePlugin
    ) -> None:
        errors = plugin.validate_config(
            {
                "url": "file:///etc/passwd",
                "api_key": "k",
                "username": "u",
                "password": "p",
            }
        )

        assert any("http:// or https://" in error for error in errors)


class TestTheAllowlistIsNotASetting:
    """Neither surface may widen what a file-based source can read."""

    def test_the_settings_registry_has_no_entry_for_it(self) -> None:
        assert get_entry(_ALLOWLIST_KEY) is None

    def test_the_cli_refuses_to_set_it(self, tmp_path: Path) -> None:
        """The web mirror of this lives in ``tests/test_web_api.py``."""
        storage = StorageManager(sqlite_path=tmp_path / "settings.db")
        before = get_allowed_source_roots()

        result = _invoke_with_mocks(
            CliRunner(),
            ["settings", "set", _ALLOWLIST_KEY, "/"],
            mock_storage=storage,
        )

        assert result.exit_code != 0
        assert storage.list_settings() == {}
        assert get_allowed_source_roots() == before
