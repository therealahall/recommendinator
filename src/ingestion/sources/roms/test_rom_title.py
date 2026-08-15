"""Tests for the internal ROM title cleaner used by the roms plugin."""

from __future__ import annotations

import pytest

from src.ingestion.sources.roms._rom_title import (
    clean_display_title,
    compile_extra_patterns,
    normalize_title_key,
)


class TestCleanDisplayTitleBasics:
    def test_empty_string(self) -> None:
        assert clean_display_title("") == ""

    def test_collapses_internal_whitespace(self) -> None:
        assert clean_display_title("Mega  Man   X") == "Mega Man X"


class TestCleanDisplayTitleParens:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Tetris (USA)", "Tetris"),
            ("1942 (Japan, USA) (En)", "1942"),
            ("Golden Axe (World) (Rev A)", "Golden Axe"),
            (
                "Castlevania - Bloodlines (US) (1994) (Action Platform) (Sega Genesis)",
                "Castlevania - Bloodlines",
            ),
            ("Final Fantasy VII (Disc 1)", "Final Fantasy VII"),
        ],
    )
    def test_strips_trailing_paren_groups(self, raw: str, expected: str) -> None:
        assert clean_display_title(raw) == expected

    def test_does_not_strip_internal_parens(self) -> None:
        # Parens inside the title (not at the end) are preserved.
        assert (
            clean_display_title("Game (HD Remix) Edition") == "Game (HD Remix) Edition"
        )


class TestCleanDisplayTitleBrackets:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Castlevania - SoTN [NTSC-U] [SLUS-00067]", "Castlevania - SoTN"),
            ("Game [!]", "Game"),
            (
                "The Legend of Zelda Tears of the Kingdom [0100F2C0115B6000][v0][US]",
                "The Legend of Zelda Tears of the Kingdom",
            ),
        ],
    )
    def test_strips_trailing_bracket_groups(self, raw: str, expected: str) -> None:
        assert clean_display_title(raw) == expected

    def test_does_not_strip_internal_brackets(self) -> None:
        assert clean_display_title("Game [Mid] Edition") == "Game [Mid] Edition"


class TestCleanDisplayTitleMixed:
    def test_mixed_brackets_and_parens(self) -> None:
        assert (
            clean_display_title(
                "Super Mario Party Jamboree [0100965017338000][v0][US](nsw2u.com)"
            )
            == "Super Mario Party Jamboree"
        )


class TestCleanDisplayTitleUnderscores:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Some___Game", "Some Game"),  # multiple underscores collapse
            ("Some_Game_(USA)_(Beta)", "Some Game"),  # underscores expose tail parens
            ("Foo_Bar.Baz_Qux", "Foo Bar.Baz Qux"),  # dots preserved
        ],
    )
    def test_underscores_become_spaces(self, raw: str, expected: str) -> None:
        assert clean_display_title(raw) == expected


class TestCleanDisplayTitleStatusTags:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Some Game (Beta)", "Some Game"),
            ("Some Game (v1.0)", "Some Game"),
            ("Some Game (USA) (Beta)", "Some Game"),
        ],
    )
    def test_status_tags_stripped(self, raw: str, expected: str) -> None:
        assert clean_display_title(raw) == expected


class TestCleanDisplayTitleEdgeCases:
    def test_extreme_trailing_chain_capped(self) -> None:
        """12 trailing groups; cap is 8 passes, so 8 groups strip and 4 remain."""
        raw = "Game" + " (x)" * 12
        result = clean_display_title(raw)
        # 12 groups - 8 passes = 4 remaining trailing groups.
        assert result.count("(x)") == 4


class TestCleanDisplayTitleExtraPatterns:
    def test_extra_pattern_runs_after_defaults(self) -> None:
        extra = compile_extra_patterns([r"\s*-\s*Definitive Edition$"])
        assert (
            clean_display_title("Mass Effect - Definitive Edition (USA)", extra)
            == "Mass Effect"
        )


class TestCompileExtraPatterns:
    def test_raises_value_error_on_invalid_regex(self) -> None:
        with pytest.raises(ValueError, match=r"\[unclosed"):
            compile_extra_patterns(["[unclosed"])

    def test_rejects_pattern_exceeding_length_cap(self) -> None:
        """Long patterns are bounded — pragmatic ReDoS mitigation."""
        with pytest.raises(ValueError, match="exceeds 200 chars"):
            compile_extra_patterns(["a" * 201])

    def test_rejects_list_exceeding_count_cap(self) -> None:
        """Per-title regex work is bounded by capping how many may be supplied."""
        with pytest.raises(ValueError, match="exceeds 32 entries"):
            compile_extra_patterns(["x"] * 33)


class TestNormalizeTitleKey:
    def test_lowercases_and_collapses_whitespace(self) -> None:
        assert normalize_title_key("MEGA  Man   X") == "mega man x"
