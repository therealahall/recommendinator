"""Tests for the internal ROM title cleaner used by the roms plugin."""

from __future__ import annotations

import re

import pytest

from src.ingestion.sources._rom_title import (
    clean_display_title,
    compile_extra_patterns,
    normalize_title_key,
)


class TestCleanDisplayTitleBasics:
    def test_empty_string(self) -> None:
        assert clean_display_title("") == ""

    def test_whitespace_only(self) -> None:
        assert clean_display_title("   ") == ""

    def test_plain_title_unchanged(self) -> None:
        assert clean_display_title("Chrono Trigger") == "Chrono Trigger"

    def test_trims_surrounding_whitespace(self) -> None:
        assert clean_display_title("  Chrono Trigger  ") == "Chrono Trigger"

    def test_collapses_internal_whitespace(self) -> None:
        assert clean_display_title("Mega  Man   X") == "Mega Man X"

    def test_preserves_case(self) -> None:
        assert clean_display_title("FINAL FANTASY") == "FINAL FANTASY"


class TestCleanDisplayTitleParens:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Tetris (USA)", "Tetris"),
            ("Tetris (Europe)", "Tetris"),
            ("Tetris (Japan)", "Tetris"),
            ("Tetris (World)", "Tetris"),
            ("Tetris (USA, Europe)", "Tetris"),
            ("Tetris (Japan, USA)", "Tetris"),
            ("1942 (Japan, USA) (En)", "1942"),
            ("Mario Kart Wii (USA) (En,Fr,Es)", "Mario Kart Wii"),
            ("Mario Party 9 (USA, Asia) (En,Fr,Es)", "Mario Party 9"),
            (
                "Phantasy Star III - Generations of Doom (USA, Europe, Korea) (En)",
                "Phantasy Star III - Generations of Doom",
            ),
            ("Golden Axe (World) (Rev A)", "Golden Axe"),
            ("Mario Party 4 (USA) (Rev 1)", "Mario Party 4"),
            (
                "Castlevania - Bloodlines (US) (1994) (Action Platform) (Sega Genesis)",
                "Castlevania - Bloodlines",
            ),
            (
                "Trials of Mana (World) (Rev 1) (Collection of Mana)",
                "Trials of Mana",
            ),
            ("Final Fantasy VII (Disc 1)", "Final Fantasy VII"),
            ("Dragon Warrior VII (Disc 2)", "Dragon Warrior VII"),
            (
                "Tactics Ogre - Let Us Cling Together (tr)",
                "Tactics Ogre - Let Us Cling Together",
            ),
        ],
    )
    def test_strips_trailing_paren_groups(self, raw: str, expected: str) -> None:
        assert clean_display_title(raw) == expected

    def test_does_not_strip_internal_parens(self) -> None:
        # Parens inside the title (not at the end) are preserved.
        assert (
            clean_display_title("Game (HD Remix) Edition") == "Game (HD Remix) Edition"
        )

    def test_no_paren_at_all(self) -> None:
        assert clean_display_title("Bard's Tale, The") == "Bard's Tale, The"


class TestCleanDisplayTitleBrackets:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Castlevania - SoTN [NTSC-U]", "Castlevania - SoTN"),
            ("Castlevania - SoTN [NTSC-U] [SLUS-00067]", "Castlevania - SoTN"),
            ("Game [!]", "Game"),
            ("Game [b]", "Game"),
            ("Game [h]", "Game"),
            ("Game [T+En]", "Game"),
            ("Game [v0]", "Game"),
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

    def test_inline_nsw2u_noise_stripped(self) -> None:
        # nsw2u.com tag mid-title is removed even without trailing strip.
        assert clean_display_title("Game (nsw2u.com) Title") == "Game Title"


class TestCleanDisplayTitleUnderscores:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Mortal_Kombat", "Mortal Kombat"),
            ("Final_Fantasy_VII", "Final Fantasy VII"),
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
            ("Some Game (Proto)", "Some Game"),
            ("Some Game (Sample)", "Some Game"),
            ("Some Game (Demo)", "Some Game"),
            ("Some Game (Unl)", "Some Game"),
            ("Some Game (Alpha)", "Some Game"),
            ("Some Game (v1.0)", "Some Game"),
            ("Some Game (USA) (Beta)", "Some Game"),
        ],
    )
    def test_status_tags_stripped(self, raw: str, expected: str) -> None:
        assert clean_display_title(raw) == expected


class TestCleanDisplayTitleEdgeCases:
    def test_only_paren_returns_empty(self) -> None:
        assert clean_display_title("(USA)") == ""

    def test_only_bracket_returns_empty(self) -> None:
        assert clean_display_title("[!]") == ""

    def test_paren_then_bracket_then_paren(self) -> None:
        assert clean_display_title("Game [a] (b) [c]") == "Game"

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

    def test_extra_pattern_can_strip_what_defaults_miss(self) -> None:
        # No-Intro tag ":Special Edition:" — non-paren marker user wants gone.
        extra = compile_extra_patterns([r"\s*:Special Edition:$"])
        assert clean_display_title("Some Game:Special Edition:", extra) == "Some Game"

    def test_no_extra_patterns_still_works(self) -> None:
        assert clean_display_title("Tetris (USA)", None) == "Tetris"


class TestCompileExtraPatterns:
    def test_compiles_valid_patterns(self) -> None:
        compiled = compile_extra_patterns([r"\d+$", r"foo"])
        assert len(compiled) == 2
        assert all(isinstance(p, re.Pattern) for p in compiled)

    def test_raises_value_error_on_invalid_regex(self) -> None:
        with pytest.raises(ValueError, match=r"\[unclosed"):
            compile_extra_patterns(["[unclosed"])

    def test_empty_input_returns_empty_list(self) -> None:
        assert compile_extra_patterns([]) == []

    def test_rejects_pattern_exceeding_length_cap(self) -> None:
        """Long patterns are bounded — pragmatic ReDoS mitigation."""
        with pytest.raises(ValueError, match="exceeds 200 chars"):
            compile_extra_patterns(["a" * 201])


class TestNormalizeTitleKey:
    def test_lowercases(self) -> None:
        assert normalize_title_key("TETRIS") == "tetris"

    def test_collapses_whitespace(self) -> None:
        assert normalize_title_key("Mega  Man   X") == "mega man x"

    def test_strips_surrounding_whitespace(self) -> None:
        assert normalize_title_key("  Tetris  ") == "tetris"

    def test_distinct_titles_distinct_keys(self) -> None:
        assert normalize_title_key("Tetris") != normalize_title_key("Tetris 2")
