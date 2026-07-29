"""Tests for the RomScannerPlugin (ROM Library)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from src.ingestion.plugin_base import SourceError, SourcePlugin
from src.ingestion.sources.roms.roms import (
    DEFAULT_EXTENSIONS,
    SCAN_ROOTS_ENV_VAR,
    RomScannerPlugin,
    _safe_size_bytes,
    allowed_scan_roots,
)
from src.models.content import ConsumptionStatus, ContentType


@pytest.fixture(autouse=True)
def _scan_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Allow this test's tmp directory as a ROM root.

    Scan paths are contained to an allow-list, and a pytest tmp directory is
    outside the defaults. Setting the environment variable is exactly how an
    operator points the scanner at a library in an unusual place, so the whole
    suite exercises that path.
    """
    monkeypatch.setenv(SCAN_ROOTS_ENV_VAR, str(tmp_path))


@pytest.fixture()
def plugin() -> RomScannerPlugin:
    return RomScannerPlugin()


@pytest.fixture()
def rom_dir(tmp_path: Path) -> Path:
    """A scan root with a realistic mix of ROMs, a folder, and junk files.

    Default-extension matches: Chrono Trigger.zip, Mario Kart 64 (USA).z64
    Folder (always included): Doom/
    Filtered out by default-extension check: notes.txt, EMULATOR.cfg
    """
    root = tmp_path / "snes"
    root.mkdir()
    (root / "Chrono Trigger.zip").write_bytes(b"rom-data")
    (root / "Mario Kart 64 (USA).z64").write_bytes(b"rom-data-2")
    (root / "Doom").mkdir()
    (root / "Doom" / "doom.exe").write_bytes(b"exe")
    (root / "notes.txt").write_text("ignore me — wrong extension")
    (root / "EMULATOR.cfg").write_text("emulator config")
    (root / ".hidden").write_text("hidden")
    return root


class TestRomScannerProperties:
    """Tests for plugin metadata properties."""

    def test_is_source_plugin(self, plugin: RomScannerPlugin) -> None:
        assert isinstance(plugin, SourcePlugin)

    def test_name(self, plugin: RomScannerPlugin) -> None:
        assert plugin.name == "roms"

    def test_display_name(self, plugin: RomScannerPlugin) -> None:
        assert plugin.display_name == "ROM Library"

    def test_content_types(self, plugin: RomScannerPlugin) -> None:
        assert plugin.content_types == [ContentType.VIDEO_GAME]

    def test_requires_api_key(self, plugin: RomScannerPlugin) -> None:
        assert plugin.requires_api_key is False

    def test_requires_network(self, plugin: RomScannerPlugin) -> None:
        assert plugin.requires_network is False

    def test_description(self, plugin: RomScannerPlugin) -> None:
        assert plugin.description == (
            "Scan local directories for emulator ROMs and game files"
        )

    def test_config_schema_field_set(self, plugin: RomScannerPlugin) -> None:
        names = {field.name for field in plugin.get_config_schema()}
        assert names == {
            "paths",
            "include_extensions",
            "exclude_extensions",
            "exclude_names",
            "extra_strip_patterns",
        }

    def test_default_extensions_cover_common_systems(self) -> None:
        # Spot check: every major system the user actually has.
        for ext in (".nes", ".smc", ".z64", ".gba", ".rvz", ".7z", ".m3u", ".xci"):
            assert ext in DEFAULT_EXTENSIONS


class TestRomScannerValidation:
    """Tests for config validation."""

    def test_valid_config(self, plugin: RomScannerPlugin, rom_dir: Path) -> None:
        errors = plugin.validate_config({"paths": [str(rom_dir)]})
        assert errors == []

    def test_missing_paths(self, plugin: RomScannerPlugin) -> None:
        errors = plugin.validate_config({})
        assert any("paths" in error for error in errors)

    def test_empty_paths(self, plugin: RomScannerPlugin) -> None:
        errors = plugin.validate_config({"paths": []})
        assert any("paths" in error for error in errors)

    def test_paths_not_a_list(self, plugin: RomScannerPlugin, rom_dir: Path) -> None:
        errors = plugin.validate_config({"paths": str(rom_dir)})
        assert any("list" in error.lower() for error in errors)

    def test_nonexistent_path(self, plugin: RomScannerPlugin, tmp_path: Path) -> None:
        errors = plugin.validate_config({"paths": [str(tmp_path / "not-here")]})
        assert any("not found" in error.lower() for error in errors)

    def test_path_is_file_not_directory(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        file_path = tmp_path / "not-a-dir.txt"
        file_path.write_text("x")
        errors = plugin.validate_config({"paths": [str(file_path)]})
        assert any("directory" in error.lower() for error in errors)

    def test_invalid_extra_strip_pattern_regex(
        self, plugin: RomScannerPlugin, rom_dir: Path
    ) -> None:
        errors = plugin.validate_config(
            {"paths": [str(rom_dir)], "extra_strip_patterns": ["[unclosed"]}
        )
        assert any("extra_strip_patterns" in error for error in errors)

    def test_include_extensions_must_be_list(
        self, plugin: RomScannerPlugin, rom_dir: Path
    ) -> None:
        errors = plugin.validate_config(
            {"paths": [str(rom_dir)], "include_extensions": ".zip"}
        )
        assert any("include_extensions" in error for error in errors)

    def test_exclude_extensions_must_be_list(
        self, plugin: RomScannerPlugin, rom_dir: Path
    ) -> None:
        errors = plugin.validate_config(
            {"paths": [str(rom_dir)], "exclude_extensions": ".zip"}
        )
        assert any("exclude_extensions" in error for error in errors)

    def test_exclude_names_must_be_list(
        self, plugin: RomScannerPlugin, rom_dir: Path
    ) -> None:
        errors = plugin.validate_config(
            {"paths": [str(rom_dir)], "exclude_names": "scripts"}
        )
        assert any("exclude_names" in error for error in errors)

    def test_collects_multiple_errors(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        errors = plugin.validate_config(
            {"paths": [str(tmp_path / "not-here")], "exclude_names": "scripts"}
        )
        assert any("not found" in error.lower() for error in errors)
        assert any("exclude_names" in error for error in errors)

    def test_include_extensions_int_value_rejected(
        self, plugin: RomScannerPlugin, rom_dir: Path
    ) -> None:
        """Coercion error when value is neither None, str, nor list."""
        errors = plugin.validate_config(
            {"paths": [str(rom_dir)], "include_extensions": 42}
        )
        assert any("include_extensions" in error for error in errors)

    def test_include_extensions_non_string_entry_rejected(
        self, plugin: RomScannerPlugin, rom_dir: Path
    ) -> None:
        errors = plugin.validate_config(
            {"paths": [str(rom_dir)], "include_extensions": [".zip", 99]}
        )
        assert any(
            "include_extensions" in error and "strings" in error for error in errors
        )

    def test_extra_strip_patterns_non_list_rejected(
        self, plugin: RomScannerPlugin, rom_dir: Path
    ) -> None:
        """Coercion error path (line 319 branch) — non-list value."""
        errors = plugin.validate_config(
            {"paths": [str(rom_dir)], "extra_strip_patterns": "not-a-list"}
        )
        assert any("extra_strip_patterns" in error for error in errors)

    def test_extra_strip_patterns_length_cap_rejected(
        self, plugin: RomScannerPlugin, rom_dir: Path
    ) -> None:
        errors = plugin.validate_config(
            {
                "paths": [str(rom_dir)],
                "extra_strip_patterns": ["a" * 201],
            }
        )
        assert any("extra_strip_patterns" in error for error in errors)


class TestRomScanPathContainment:
    """Scan paths are contained to an allow-list of roots.

    ``paths`` reaches the plugin from ``POST /api/sync/sources``, which stores
    a config value without ever calling ``validate_config``, and the app has no
    authentication. Without containment a request could point the scanner at
    ``/etc`` or ``/root`` and read back the directory listing as game titles.
    """

    def test_path_outside_the_allowed_roots_fails_validation(
        self, plugin: RomScannerPlugin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        monkeypatch.setenv(SCAN_ROOTS_ENV_VAR, str(allowed))

        errors = plugin.validate_config({"paths": [str(outside)]})

        assert len(errors) == 1
        assert "not an allowed ROM directory" in errors[0]
        assert SCAN_ROOTS_ENV_VAR in errors[0]

    def test_fetch_refuses_a_path_outside_the_allowed_roots(
        self, plugin: RomScannerPlugin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Refused at fetch too — source creation never runs validate_config."""
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "Secret.zip").write_bytes(b"x")
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        monkeypatch.setenv(SCAN_ROOTS_ENV_VAR, str(allowed))

        with pytest.raises(SourceError, match="not an allowed ROM directory"):
            list(plugin.fetch({"paths": [str(outside)]}))

    def test_traversal_out_of_an_allowed_root_is_refused(
        self, plugin: RomScannerPlugin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``..`` is resolved before the comparison, so it cannot escape."""
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        monkeypatch.setenv(SCAN_ROOTS_ENV_VAR, str(allowed))

        errors = plugin.validate_config({"paths": [f"{allowed}/../outside"]})

        assert any("not an allowed ROM directory" in error for error in errors)

    def test_symlink_out_of_an_allowed_root_is_refused(
        self, plugin: RomScannerPlugin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A symlink planted inside an allowed root resolves to its target."""
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (allowed / "link").symlink_to(outside, target_is_directory=True)
        monkeypatch.setenv(SCAN_ROOTS_ENV_VAR, str(allowed))

        errors = plugin.validate_config({"paths": [str(allowed / "link")]})

        assert any("not an allowed ROM directory" in error for error in errors)

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_a_blank_path_entry_is_refused(
        self, plugin: RomScannerPlugin, blank: str
    ) -> None:
        """An empty entry must not silently mean "scan the working directory".

        ``Path("").resolve()`` is the current working directory, which is one
        of the default roots — so a blank entry passed containment and turned
        into a scan of wherever the app was started from, without the config
        ever naming it. ``"."`` still means exactly that, spelled on purpose.
        """
        errors = plugin.validate_config({"paths": [blank]})

        assert errors == ["'paths' entries must not be empty"]

    def test_fetch_refuses_a_blank_path_entry(self, plugin: RomScannerPlugin) -> None:
        """Refused at fetch too — source creation never runs validate_config."""
        with pytest.raises(SourceError, match="not an allowed ROM directory"):
            list(plugin.fetch({"paths": [""]}))

    def test_a_library_under_an_allowed_root_still_validates(
        self, plugin: RomScannerPlugin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The containment must not break a real library: a nested dir passes."""
        library = tmp_path / "library" / "snes"
        library.mkdir(parents=True)
        monkeypatch.setenv(SCAN_ROOTS_ENV_VAR, str(tmp_path / "library"))

        assert plugin.validate_config({"paths": [str(library)]}) == []

    def test_default_roots_cover_home_cwd_and_media_mounts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With no environment override, the built-in roots apply.

        Pins the defaults so a future edit cannot quietly drop the home
        directory (where most libraries live) or add ``/`` (which would make
        the containment vacuous).
        """
        monkeypatch.delenv(SCAN_ROOTS_ENV_VAR, raising=False)

        roots = allowed_scan_roots()

        assert Path.home().resolve() in roots
        assert Path.cwd().resolve() in roots
        assert Path("/mnt") in roots
        assert Path("/media") in roots
        assert Path("/") not in roots

    @pytest.mark.parametrize("system_dir", ["/etc", "/root", "/proc", "/var/log"])
    def test_default_roots_refuse_system_directories(
        self,
        plugin: RomScannerPlugin,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        system_dir: str,
    ) -> None:
        """The directories the containment exists for are refused by default.

        Home and cwd are pointed at a tmp directory so the assertion holds
        whichever user runs the suite (as root, ``/root`` *is* the home
        directory); what is pinned is that the built-in root list does not
        name these.
        """
        monkeypatch.delenv(SCAN_ROOTS_ENV_VAR, raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(Path, "cwd", lambda: tmp_path)

        errors = plugin.validate_config({"paths": [system_dir]})

        assert any("not an allowed ROM directory" in error for error in errors)

    def test_environment_override_replaces_the_defaults(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Naming roots replaces the defaults rather than adding to them."""
        first = tmp_path / "one"
        second = tmp_path / "two"
        monkeypatch.setenv(SCAN_ROOTS_ENV_VAR, f"{first}{os.pathsep}{second}")

        roots = allowed_scan_roots()

        assert roots == [first.resolve(), second.resolve()]

    def test_whitespace_only_override_falls_back_to_the_defaults(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A blank value is an unset value, not an empty allow-list.

        An empty root list would refuse every path, so a stray space in a
        compose file must not silently disable the plugin.
        """
        monkeypatch.setenv(SCAN_ROOTS_ENV_VAR, "   ")

        roots = allowed_scan_roots()

        assert Path.home().resolve() in roots
        assert Path("/mnt") in roots

    def test_empty_entries_in_the_override_are_dropped(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``/a::/b`` names two roots, not three — an empty entry would
        resolve to the working directory and widen the allow-list."""
        first = tmp_path / "one"
        second = tmp_path / "two"
        monkeypatch.setenv(
            SCAN_ROOTS_ENV_VAR, f"{first}{os.pathsep}{os.pathsep}{second}"
        )

        roots = allowed_scan_roots()

        assert roots == [first.resolve(), second.resolve()]


class TestRomScanPathHiddenDirectories:
    """A scan path may not reach its root through a dot-prefixed directory.

    The allow-list has to include the home directory for the plugin to be
    usable, which on its own would make ``~/.ssh`` a legal scan root and turn
    ``id_rsa``, ``known_hosts`` and ``authorized_keys`` into game titles —
    ``_collect_entries`` skips dot-prefixed *children*, not a dot-prefixed
    *root*. ``~/.aws``, ``~/.gnupg`` and ``~/.config/*`` are the same shape.

    The rule covers the part below the matched root only. Roots come from the
    environment, so a dot in the root itself is the operator's own choice.
    """

    def test_hidden_scan_root_fails_validation(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        hidden = tmp_path / ".ssh"
        hidden.mkdir()
        (hidden / "id_rsa").write_text("private key")

        errors = plugin.validate_config({"paths": [str(hidden)]})

        assert len(errors) == 1
        assert "hidden (dot-prefixed) directory" in errors[0]

    def test_hidden_ancestor_fails_validation(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        """The rule applies to every component, not just the final one."""
        nested = tmp_path / ".config" / "recommendinator"
        nested.mkdir(parents=True)

        errors = plugin.validate_config({"paths": [str(nested)]})

        assert any("hidden (dot-prefixed) directory" in error for error in errors)

    def test_fetch_refuses_a_hidden_scan_root(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        """Refused at fetch too — source creation never runs validate_config."""
        hidden = tmp_path / ".ssh"
        hidden.mkdir()
        (hidden / "id_rsa").write_text("private key")

        with pytest.raises(SourceError, match="hidden"):
            list(plugin.fetch({"paths": [str(hidden)]}))

    def test_symlink_into_a_hidden_directory_is_refused(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        """Resolution runs first, so a plain-looking name cannot hide it."""
        hidden = tmp_path / ".gnupg"
        hidden.mkdir()
        (tmp_path / "roms").symlink_to(hidden, target_is_directory=True)

        errors = plugin.validate_config({"paths": [str(tmp_path / "roms")]})

        assert any("hidden (dot-prefixed) directory" in error for error in errors)

    def test_hidden_directory_in_home_is_refused_by_default(
        self, plugin: RomScannerPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``~/.ssh`` sits under an allowed root, so only the dot rule stops it."""
        monkeypatch.delenv(SCAN_ROOTS_ENV_VAR, raising=False)

        errors = plugin.validate_config({"paths": [str(Path.home() / ".ssh")]})

        assert any("hidden (dot-prefixed) directory" in error for error in errors)

    def test_a_plain_library_under_an_allowed_root_still_validates(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        """The common legitimate case (``~/roms``) must keep working."""
        library = tmp_path / "roms" / "snes"
        library.mkdir(parents=True)

        assert plugin.validate_config({"paths": [str(library)]}) == []

    def test_a_dot_prefixed_root_the_operator_named_is_allowed(
        self, plugin: RomScannerPlugin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The dot rule applies below the root, not to the root itself.

        A root only ever arrives from the environment, so a dot inside it is
        operator-chosen and never attacker-chosen. Refusing it would strand an
        XDG library at ``~/.local/share/roms`` and break the app whenever its
        working directory happens to sit under a dot-prefixed path.
        """
        library = tmp_path / ".local" / "share" / "roms"
        (library / "snes").mkdir(parents=True)
        monkeypatch.setenv(SCAN_ROOTS_ENV_VAR, str(library))

        assert plugin.validate_config({"paths": [str(library / "snes")]}) == []

    def test_a_general_root_does_not_veto_a_more_specific_one(
        self, plugin: RomScannerPlugin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Matching a root through a dot must not stop a later root matching.

        With both the home directory and a dot-prefixed library beneath it on
        the list, the home match reaches the library through ``.local`` — but
        the library's own entry reaches it cleanly, so it is scannable.
        """
        library = tmp_path / ".local" / "share" / "roms"
        library.mkdir(parents=True)
        monkeypatch.setenv(
            SCAN_ROOTS_ENV_VAR, os.pathsep.join([str(tmp_path), str(library)])
        )

        assert plugin.validate_config({"paths": [str(library)]}) == []

    def test_a_hidden_sibling_of_a_named_root_is_still_refused(
        self, plugin: RomScannerPlugin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Naming one dotted root must not unlock every other dotted directory."""
        library = tmp_path / ".local" / "share" / "roms"
        library.mkdir(parents=True)
        secrets = tmp_path / ".ssh"
        secrets.mkdir()
        monkeypatch.setenv(
            SCAN_ROOTS_ENV_VAR, os.pathsep.join([str(tmp_path), str(library)])
        )

        errors = plugin.validate_config({"paths": [str(secrets)]})

        assert any("hidden (dot-prefixed) directory" in error for error in errors)


class TestRomExtraStripPatternBounds:
    """``extra_strip_patterns`` is capped by count and by length.

    That is all it is: the caps bound how much regex runs against every title,
    not how long any one pattern takes. ``re`` has no execution timeout and no
    cheap static check separates a safe pattern from a catastrophic one, so a
    caller who can write source config can still burn a CPU for the length of
    a scan — documented in docs/SECURITY.md rather than half-mitigated here.
    """

    @pytest.mark.parametrize(
        "pattern",
        [r"\s*\(nsw2u\.com\)", r"-[A-Z0-9]+$", r"(USA|Europe)", r"\s*\[.*?\]"],
    )
    def test_realistic_patterns_are_accepted(
        self, plugin: RomScannerPlugin, rom_dir: Path, pattern: str
    ) -> None:
        """The patterns people actually write pass validation."""
        assert (
            plugin.validate_config(
                {"paths": [str(rom_dir)], "extra_strip_patterns": [pattern]}
            )
            == []
        )

    def test_too_many_patterns_are_refused(
        self, plugin: RomScannerPlugin, rom_dir: Path
    ) -> None:
        """Every pattern runs against every title, so the count is capped too."""
        errors = plugin.validate_config(
            {
                "paths": [str(rom_dir)],
                "extra_strip_patterns": [f"-tag{index}" for index in range(11)],
            }
        )

        assert any("At most 10 patterns" in error for error in errors)

    def test_patterns_exactly_at_both_caps_are_accepted(
        self, plugin: RomScannerPlugin, rom_dir: Path
    ) -> None:
        """Both caps are inclusive; a ``>=`` slip would refuse a legal config."""
        patterns = [f"-tag{index}" for index in range(9)] + ["a" * 200]

        assert (
            plugin.validate_config(
                {"paths": [str(rom_dir)], "extra_strip_patterns": patterns}
            )
            == []
        )

    def test_fetch_refuses_more_patterns_than_the_cap(
        self, plugin: RomScannerPlugin, rom_dir: Path
    ) -> None:
        """Refused at fetch too, the path a stored config actually takes."""
        with pytest.raises(SourceError, match="At most 10 patterns"):
            list(
                plugin.fetch(
                    {
                        "paths": [str(rom_dir)],
                        "extra_strip_patterns": [f"-tag{index}" for index in range(11)],
                    }
                )
            )


class TestRomScannerFetchExtensionFiltering:
    """Default extension filter and include/exclude knobs."""

    def test_only_extension_matching_files_included(
        self, plugin: RomScannerPlugin, rom_dir: Path
    ) -> None:
        items = list(plugin.fetch({"paths": [str(rom_dir)]}))
        titles = {item.title for item in items}
        # Doom/ folder always included; .zip + .z64 match defaults;
        # .txt and .cfg are filtered out by extension; dotfile skipped.
        assert titles == {"Chrono Trigger", "Mario Kart 64", "Doom"}

    def test_include_extensions_adds_to_defaults(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        root = tmp_path / "stash"
        root.mkdir()
        (root / "Game.zip").write_bytes(b"x")
        (root / "Installer.exe").write_bytes(b"y")
        items = list(
            plugin.fetch({"paths": [str(root)], "include_extensions": [".exe"]})
        )
        titles = {item.title for item in items}
        assert titles == {"Game", "Installer"}

    def test_exclude_extensions_removes_from_defaults(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        root = tmp_path / "stash"
        root.mkdir()
        (root / "Game.zip").write_bytes(b"x")
        (root / "Other.tgz").write_bytes(b"y")
        items = list(
            plugin.fetch({"paths": [str(root)], "exclude_extensions": [".tgz"]})
        )
        titles = {item.title for item in items}
        assert titles == {"Game"}

    def test_extensions_are_case_insensitive(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        root = tmp_path / "stash"
        root.mkdir()
        (root / "GameA.ZIP").write_bytes(b"x")
        (root / "GameB.Z64").write_bytes(b"y")
        items = list(plugin.fetch({"paths": [str(root)]}))
        assert {item.title for item in items} == {"GameA", "GameB"}

    def test_extension_normalization_accepts_no_dot(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        root = tmp_path / "stash"
        root.mkdir()
        (root / "Installer.exe").write_bytes(b"x")
        items = list(
            plugin.fetch({"paths": [str(root)], "include_extensions": ["exe"]})
        )
        assert {item.title for item in items} == {"Installer"}

    def test_empty_extension_entries_silently_dropped(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        """Empty/whitespace extension entries are skipped, not crashes."""
        root = tmp_path / "stash"
        root.mkdir()
        (root / "Game.zip").write_bytes(b"x")
        items = list(
            plugin.fetch(
                {"paths": [str(root)], "include_extensions": ["", "  ", ".exe"]}
            )
        )
        assert {item.title for item in items} == {"Game"}


class TestRomScannerFetchTitleCleaning:
    """Built-in cleaner and extra_strip_patterns interaction."""

    def test_default_cleaner_strips_region_and_year(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        root = tmp_path / "snes"
        root.mkdir()
        (root / "1942 (Japan, USA) (En).zip").write_bytes(b"x")
        items = list(plugin.fetch({"paths": [str(root)]}))
        assert items[0].title == "1942"

    def test_default_cleaner_strips_brackets(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        root = tmp_path / "psx"
        root.mkdir()
        (root / "Castlevania - SoTN [NTSC-U] [SLUS-00067].rar").write_bytes(b"x")
        items = list(plugin.fetch({"paths": [str(root)]}))
        assert items[0].title == "Castlevania - SoTN"

    def test_extra_strip_patterns_appended_after_defaults(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        root = tmp_path / "stash"
        root.mkdir()
        (root / "Mass Effect (USA) - Definitive Edition.zip").write_bytes(b"x")
        items = list(
            plugin.fetch(
                {
                    "paths": [str(root)],
                    "extra_strip_patterns": [r"\s*-\s*Definitive Edition$"],
                }
            )
        )
        assert items[0].title == "Mass Effect"

    def test_invalid_extra_strip_pattern_raises_in_fetch(
        self, plugin: RomScannerPlugin, rom_dir: Path
    ) -> None:
        with pytest.raises(SourceError, match="extra_strip_patterns"):
            list(
                plugin.fetch(
                    {
                        "paths": [str(rom_dir)],
                        "extra_strip_patterns": ["[unclosed"],
                    }
                )
            )

    def test_empty_title_after_strip_skips_entry(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        root = tmp_path / "stash"
        root.mkdir()
        (root / "(USA).zip").write_bytes(b"x")
        (root / "Tetris.zip").write_bytes(b"y")
        items = list(plugin.fetch({"paths": [str(root)]}))
        assert {item.title for item in items} == {"Tetris"}


class TestRomScannerMultiDiscCollapse:
    """The hero use case: 4 discs of one game collapse to one item."""

    def test_multi_disc_collapses_to_one_item(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        root = tmp_path / "psx"
        root.mkdir()
        for disc in range(1, 5):
            (root / f"Final Fantasy VII (USA) (Disc {disc}).bin").write_bytes(b"x")
        (root / "Chrono Trigger (USA).zip").write_bytes(b"y")

        items = list(plugin.fetch({"paths": [str(root)]}))
        titles = {item.title for item in items}
        assert titles == {"Final Fantasy VII", "Chrono Trigger"}

    def test_disc_1_wins_via_sort_order(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        root = tmp_path / "psx"
        root.mkdir()
        # Create out of order to prove sort wins, not creation order.
        (root / "Final Fantasy VII (USA) (Disc 2).bin").write_bytes(b"d2")
        (root / "Final Fantasy VII (USA) (Disc 1).bin").write_bytes(b"d1")
        items = list(plugin.fetch({"paths": [str(root)]}))
        assert len(items) == 1
        assert items[0].title == "Final Fantasy VII"
        assert items[0].metadata["path"].endswith("(Disc 1).bin")


class TestRomScannerFolders:
    """Folder entries are always included unless excluded by name."""

    def test_folder_included_regardless_of_extension(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        root = tmp_path / "psx"
        root.mkdir()
        nested = root / "Resident Evil"
        nested.mkdir()
        (nested / "track1.bin").write_bytes(b"x")
        (nested / "track1.cue").write_text("cue")
        items = list(plugin.fetch({"paths": [str(root)]}))
        assert {item.title for item in items} == {"Resident Evil"}

    def test_exclude_names_skips_folder(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        root = tmp_path / "model2"
        root.mkdir()
        (root / "scripts").mkdir()
        (root / "Daytona.zip").write_bytes(b"x")
        items = list(plugin.fetch({"paths": [str(root)], "exclude_names": ["scripts"]}))
        assert {item.title for item in items} == {"Daytona"}

    def test_exclude_names_glob_pattern(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        """Glob exclusion runs against names that would otherwise pass the
        extension filter — proves the glob is the operative filter, not a
        side-effect of the extension check.
        """
        root = tmp_path / "stash"
        root.mkdir()
        (root / "common.zip").write_bytes(b"a")
        (root / "daytona.zip").write_bytes(b"b")
        (root / "Daytona USA.zip").write_bytes(b"c")
        items = list(
            plugin.fetch(
                {
                    "paths": [str(root)],
                    "exclude_names": ["common.*", "daytona.*"],
                }
            )
        )
        assert {item.title for item in items} == {"Daytona USA"}

    def test_exclude_names_skips_files_too(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        root = tmp_path / "stash"
        root.mkdir()
        (root / "Tetris.zip").write_bytes(b"x")
        (root / "BadGame.zip").write_bytes(b"y")
        items = list(
            plugin.fetch({"paths": [str(root)], "exclude_names": ["BadGame.zip"]})
        )
        assert {item.title for item in items} == {"Tetris"}


class TestRomScannerHidden:
    def test_hidden_dotfiles_always_skipped(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        root = tmp_path / "stash"
        root.mkdir()
        (root / ".DS_Store").write_bytes(b"x")
        (root / ".cache").mkdir()
        (root / "Tetris.zip").write_bytes(b"y")
        items = list(plugin.fetch({"paths": [str(root)]}))
        assert {item.title for item in items} == {"Tetris"}

    def test_directory_with_only_hidden_yields_nothing(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        root = tmp_path / "stash"
        root.mkdir()
        (root / ".DS_Store").write_bytes(b"x")
        items = list(plugin.fetch({"paths": [str(root)]}))
        assert items == []


class TestRomScannerDedup:
    def test_dedupes_when_same_path_listed_twice(
        self, plugin: RomScannerPlugin, rom_dir: Path
    ) -> None:
        items = list(plugin.fetch({"paths": [str(rom_dir), str(rom_dir)]}))
        assert len(items) == 3

    def test_symlink_to_same_target_dedupes(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        root = tmp_path / "stash"
        root.mkdir()
        target = root / "Tetris.zip"
        target.write_bytes(b"x")
        (root / "tetris-link.zip").symlink_to(target)
        items = list(plugin.fetch({"paths": [str(root)]}))
        assert len(items) == 1

    def test_dangling_symlink_skipped(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        """A symlink whose target does not exist reports neither file nor dir;
        it's skipped rather than yielded as a phantom entry."""
        root = tmp_path / "stash"
        root.mkdir()
        (root / "Tetris.zip").write_bytes(b"x")
        (root / "broken.zip").symlink_to(tmp_path / "missing-target")

        items = list(plugin.fetch({"paths": [str(root)]}))
        assert {item.title for item in items} == {"Tetris"}

    def test_title_dedup_spans_scan_roots(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        nes = tmp_path / "nes"
        nes.mkdir()
        (nes / "Tetris.nes").write_bytes(b"x")
        snes = tmp_path / "snes"
        snes.mkdir()
        (snes / "Tetris.smc").write_bytes(b"y")
        items = list(plugin.fetch({"paths": [str(nes), str(snes)]}))
        assert len(items) == 1
        assert items[0].metadata["parent_dir"] == "nes"


class TestRomScannerMetadata:
    def test_metadata_includes_path_and_is_directory(
        self, plugin: RomScannerPlugin, rom_dir: Path
    ) -> None:
        items = list(plugin.fetch({"paths": [str(rom_dir)]}))
        by_title = {item.title: item for item in items}
        assert by_title["Chrono Trigger"].metadata["is_directory"] is False
        assert by_title["Doom"].metadata["is_directory"] is True

    def test_metadata_includes_parent_dir_name(
        self, plugin: RomScannerPlugin, rom_dir: Path
    ) -> None:
        items = list(plugin.fetch({"paths": [str(rom_dir)]}))
        for item in items:
            assert item.metadata["parent_dir"] == "snes"

    def test_metadata_includes_size_for_files(
        self, plugin: RomScannerPlugin, rom_dir: Path
    ) -> None:
        items = list(plugin.fetch({"paths": [str(rom_dir)]}))
        by_title = {item.title: item for item in items}
        assert by_title["Chrono Trigger"].metadata["size_bytes"] == len(b"rom-data")
        # Directory entries have no size_bytes — only files do.
        assert "size_bytes" not in by_title["Doom"].metadata


class TestRomScannerItem:
    def test_all_items_unread_video_game(
        self, plugin: RomScannerPlugin, rom_dir: Path
    ) -> None:
        items = list(plugin.fetch({"paths": [str(rom_dir)]}))
        assert len(items) == 3
        for item in items:
            assert item.content_type == ContentType.VIDEO_GAME.value
            assert item.status == ConsumptionStatus.UNREAD.value
            assert item.rating is None

    def test_id_uses_rom_prefix_and_is_stable(
        self, plugin: RomScannerPlugin, rom_dir: Path
    ) -> None:
        config = {"paths": [str(rom_dir)]}
        first = {item.title: item.id for item in plugin.fetch(config)}
        second = {item.title: item.id for item in plugin.fetch(config)}
        assert first == second
        for item_id in first.values():
            assert item_id.startswith("rom:")

    def test_source_set_from_source_id(
        self, plugin: RomScannerPlugin, rom_dir: Path
    ) -> None:
        items = list(plugin.fetch({"_source_id": "my_roms", "paths": [str(rom_dir)]}))
        for item in items:
            assert item.source == "my_roms"

    def test_source_falls_back_to_plugin_name(
        self, plugin: RomScannerPlugin, rom_dir: Path
    ) -> None:
        items = list(plugin.fetch({"paths": [str(rom_dir)]}))
        for item in items:
            assert item.source == "roms"


class TestRomScannerProgressCallback:
    def test_callback_fires_per_yielded_item(
        self, plugin: RomScannerPlugin, rom_dir: Path
    ) -> None:
        calls: list[tuple[int, int | None, str | None]] = []

        def cb(processed: int, total: int | None, current: str | None) -> None:
            calls.append((processed, total, current))

        list(plugin.fetch({"paths": [str(rom_dir)]}, progress_callback=cb))
        # 3 yielded items, processed monotonic, total = candidate count (5:
        # Chrono, Doom, EMULATOR.cfg, Mario Kart, notes.txt). The callback
        # receives the cleaned title at yield time.
        assert len(calls) == 3
        processed_values = [call[0] for call in calls]
        assert processed_values == sorted(processed_values)
        for call in calls:
            assert call[1] == 5
        titles_seen = {call[2] for call in calls}
        assert titles_seen == {"Chrono Trigger", "Mario Kart 64", "Doom"}

    def test_callback_skips_deduped_titles(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        root = tmp_path / "psx"
        root.mkdir()
        for disc in range(1, 5):
            (root / f"Final Fantasy VII (USA) (Disc {disc}).bin").write_bytes(b"x")
        (root / "Chrono Trigger.zip").write_bytes(b"y")

        calls: list[tuple[int, int | None, str | None]] = []

        def cb(processed: int, total: int | None, current: str | None) -> None:
            calls.append((processed, total, current))

        list(plugin.fetch({"paths": [str(root)]}, progress_callback=cb))
        # 5 candidates total; 2 unique titles after dedup.
        assert len(calls) == 2
        for call in calls:
            assert call[1] == 5


class TestRomScannerErrors:
    def test_missing_path_raises(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        with pytest.raises(SourceError, match="not found"):
            list(plugin.fetch({"paths": [str(tmp_path / "nonexistent")]}))

    def test_path_not_directory_raises(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        file_path = tmp_path / "file.txt"
        file_path.write_text("x")
        with pytest.raises(SourceError, match="directory"):
            list(plugin.fetch({"paths": [str(file_path)]}))

    def test_unreadable_scan_root_skipped(
        self,
        plugin: RomScannerPlugin,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        good = tmp_path / "good"
        good.mkdir()
        (good / "Zelda.zip").write_bytes(b"x")
        bad = tmp_path / "bad"
        bad.mkdir()

        original_iterdir = Path.iterdir

        def fake_iterdir(self: Path) -> Iterator[Path]:
            if self == bad:
                raise PermissionError("denied")
            return original_iterdir(self)

        monkeypatch.setattr(Path, "iterdir", fake_iterdir)
        items = list(plugin.fetch({"paths": [str(bad), str(good)]}))
        assert {item.title for item in items} == {"Zelda"}

    def test_is_file_oserror_skips_entry(
        self,
        plugin: RomScannerPlugin,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        root = tmp_path / "stash"
        root.mkdir()
        (root / "Bad.zip").write_bytes(b"x")
        (root / "Good.zip").write_bytes(b"y")
        original_is_file = Path.is_file

        def fake_is_file(self: Path) -> bool:
            if self.name == "Bad.zip":
                raise PermissionError("denied")
            return original_is_file(self)

        monkeypatch.setattr(Path, "is_file", fake_is_file)
        items = list(plugin.fetch({"paths": [str(root)]}))
        assert {item.title for item in items} == {"Good"}

    def test_size_lookup_failure_skips_size_bytes(
        self,
        plugin: RomScannerPlugin,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When _safe_size_bytes returns None, the entry is yielded without a
        size_bytes metadata key — flaky-mount tolerance."""
        root = tmp_path / "stash"
        root.mkdir()
        (root / "Tetris.zip").write_bytes(b"x")

        monkeypatch.setattr(
            "src.ingestion.sources.roms.roms._safe_size_bytes", lambda path: None
        )

        items = list(plugin.fetch({"paths": [str(root)]}))
        assert len(items) == 1
        assert "size_bytes" not in items[0].metadata

    def test_safe_size_bytes_returns_none_on_oserror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unit test for the size helper itself."""
        target = tmp_path / "Tetris.zip"
        target.write_bytes(b"x")

        def fake_stat(self: Path, *args: object, **kwargs: object) -> os.stat_result:
            raise OSError("stat failed")

        monkeypatch.setattr(Path, "stat", fake_stat)
        assert _safe_size_bytes(target) is None

    def test_resolve_failure_skips_entry(
        self,
        plugin: RomScannerPlugin,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An OSError from Path.resolve() (e.g. circular symlink) skips the
        entry instead of aborting the entire scan."""
        root = tmp_path / "stash"
        root.mkdir()
        (root / "Bad.zip").write_bytes(b"x")
        (root / "Good.zip").write_bytes(b"y")

        original_resolve = Path.resolve

        def fake_resolve(self: Path, *args: object, **kwargs: object) -> Path:
            if self.name == "Bad.zip":
                raise OSError("resolve failed")
            return original_resolve(self, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", fake_resolve)
        items = list(plugin.fetch({"paths": [str(root)]}))
        assert {item.title for item in items} == {"Good"}
