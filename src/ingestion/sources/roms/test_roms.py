"""Tests for the RomScannerPlugin (ROM Library)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from src.ingestion.plugin_base import SourceError
from src.ingestion.sources.roms.roms import (
    RomScannerPlugin,
    _safe_size_bytes,
)


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


class TestRomScannerValidation:
    """Tests for config validation."""

    def test_missing_paths(self, plugin: RomScannerPlugin) -> None:
        errors = plugin.validate_config({})
        assert any("paths" in error for error in errors)

    def test_nonexistent_path(self, plugin: RomScannerPlugin, tmp_path: Path) -> None:
        errors = plugin.validate_config({"paths": [str(tmp_path / "missing")]})
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

    def test_too_many_extra_strip_patterns(
        self, plugin: RomScannerPlugin, rom_dir: Path
    ) -> None:
        errors = plugin.validate_config(
            {"paths": [str(rom_dir)], "extra_strip_patterns": ["x"] * 33}
        )
        assert any("extra_strip_patterns" in error for error in errors)

    def test_include_extensions_must_be_list(
        self, plugin: RomScannerPlugin, rom_dir: Path
    ) -> None:
        errors = plugin.validate_config(
            {"paths": [str(rom_dir)], "include_extensions": ".zip"}
        )
        assert any("include_extensions" in error for error in errors)

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


class TestRomScannerDedup:
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

    def test_metadata_includes_size_for_files(
        self, plugin: RomScannerPlugin, rom_dir: Path
    ) -> None:
        items = list(plugin.fetch({"paths": [str(rom_dir)]}))
        by_title = {item.title: item for item in items}
        assert by_title["Chrono Trigger"].metadata["size_bytes"] == len(b"rom-data")
        # Directory entries have no size_bytes — only files do.
        assert "size_bytes" not in by_title["Doom"].metadata


class TestRomScannerItem:
    def test_id_uses_rom_prefix_and_is_stable(
        self, plugin: RomScannerPlugin, rom_dir: Path
    ) -> None:
        config = {"paths": [str(rom_dir)]}
        first = {item.title: item.id for item in plugin.fetch(config)}
        second = {item.title: item.id for item in plugin.fetch(config)}
        assert first == second
        for item_id in first.values():
            assert item_id.startswith("rom:")


class TestRomScannerErrors:
    def test_missing_path_raises(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        with pytest.raises(SourceError, match="not found"):
            list(plugin.fetch({"paths": [str(tmp_path / "missing")]}))

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


class TestRomScannerPathContainmentRegression:
    """Regression: source config as a filesystem enumeration primitive.

    Bug: ``paths`` came straight from HTTP-writable source config, so a scan of
    ``/home`` listed every directory as an item. Cause: no containment. Fix:
    every entry resolves against ``security.allowed_source_roots``.
    """

    def test_validate_refuses_a_scan_root_outside_every_allowed_root(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        """Two entries, because every one of them is resolved, not just the first."""
        allowed = tmp_path / "stash"
        allowed.mkdir()

        errors = plugin.validate_config({"paths": [str(allowed), "/etc"]})

        assert errors == [
            "Path is outside the allowed source roots: /etc. "
            "Add its directory to security.allowed_source_roots in config.yaml."
        ]

    def test_fetch_refuses_and_yields_nothing(
        self, plugin: RomScannerPlugin, tmp_path: Path
    ) -> None:
        outside = tmp_path.parent / f"{tmp_path.name}-outside"
        outside.mkdir()
        (outside / "Private Game.zip").write_bytes(b"x")
        allowed = tmp_path / "stash"
        allowed.mkdir()

        collected = []
        with pytest.raises(SourceError, match="outside the allowed source roots"):
            for item in plugin.fetch({"paths": [str(allowed), str(outside)]}):
                collected.append(item)

        # list() would discard these, leaving the leak half of the name unproven.
        assert collected == []


ROMS_LOGGER = "src.ingestion.sources.roms.roms"


class TestRomScannerLogInjectionRegression:
    """Regression: a scanned file name forged log entries.

    Bug: the path and the ``OSError`` quoting it are interpolated raw, and a
    file name may hold a break. Cause: the sanitiser pass covered the three
    generic import plugins alone. Fix: ``sanitize_for_log``/``exception_for_log``.
    """

    def test_a_newline_in_a_scanned_name_cannot_forge_a_log_entry(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        missing = tmp_path / "Chrono Trigger\nImported 9999 items from ROM scan.zip"

        with caplog.at_level(logging.WARNING, logger=ROMS_LOGGER):
            assert _safe_size_bytes(missing) is None

        messages = [
            record.getMessage()
            for record in caplog.records
            if record.name == ROMS_LOGGER
        ]
        assert messages, "nothing was logged, so this proves nothing"
        assert "\n" not in messages[0], messages

    def test_a_newline_in_a_deduplicated_path_cannot_forge_a_log_entry(
        self,
        plugin: RomScannerPlugin,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The duplicate-title sink escaped its title by ``%r`` and not the
        path beside it, so a break in the scan root still forged an entry."""
        root = tmp_path / "roms\nImported 9999 items from ROM scan"
        root.mkdir()
        (root / "Doom (Disc 1).zip").write_bytes(b"x")
        (root / "Doom (Disc 2).zip").write_bytes(b"x")

        with caplog.at_level(logging.DEBUG, logger=ROMS_LOGGER):
            list(plugin.fetch({"paths": [str(root)]}))

        messages = [
            record.getMessage()
            for record in caplog.records
            if record.name == ROMS_LOGGER
        ]
        assert any(
            "Skipping duplicate title" in message for message in messages
        ), messages
        assert all("\n" not in message for message in messages), messages
