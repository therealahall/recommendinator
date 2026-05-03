"""Tests for the RomScannerPlugin (ROM Library)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from src.ingestion.plugin_base import SourceError, SourcePlugin
from src.ingestion.sources.roms.roms import (
    DEFAULT_EXTENSIONS,
    RomScannerPlugin,
    _safe_size_bytes,
)
from src.models.content import ConsumptionStatus, ContentType


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

    def test_nonexistent_path(self, plugin: RomScannerPlugin) -> None:
        errors = plugin.validate_config({"paths": ["/does/not/exist"]})
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

    def test_collects_multiple_errors(self, plugin: RomScannerPlugin) -> None:
        errors = plugin.validate_config(
            {"paths": ["/does/not/exist"], "exclude_names": "scripts"}
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
    def test_missing_path_raises(self, plugin: RomScannerPlugin) -> None:
        with pytest.raises(SourceError, match="not found"):
            list(plugin.fetch({"paths": ["/nonexistent/scan/root"]}))

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
