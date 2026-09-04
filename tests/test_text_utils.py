import logging
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from src.utils.text import (
    LINE_BREAKS,
    exception_for_log,
    humanize_source_id,
    sanitize_for_log,
    sanitize_rule_text,
    strip_lone_surrogates,
)

EXOTIC_LINE_BREAKS = [character for character in LINE_BREAKS if character not in "\n\r"]

ALL_LINE_BREAKS = ["\n", "\r", "\r\n", *EXOTIC_LINE_BREAKS]


class TestHumanizeSourceIdBasic:
    @pytest.mark.parametrize(
        ("source_id", "expected"),
        [
            ("my_books", "My Books"),
            ("personal_site_games", "Personal Site Games"),
        ],
    )
    def test_normal_snake_case_inputs(self, source_id: str, expected: str) -> None:
        assert humanize_source_id(source_id) == expected

    @pytest.mark.parametrize(
        ("source_id", "expected"),
        [
            ("calibre-web", "Calibre Web"),
        ],
    )
    def test_hyphenated_inputs(self, source_id: str, expected: str) -> None:
        """Hyphens became valid source-id characters so the "Add data source" modal
        can prefill plugin names such as ``calibre-web``; the humanizer must split on
        both separators to render "Calibre Web"."""
        assert humanize_source_id(source_id) == expected


class TestHumanizeSourceIdAcronyms:
    @pytest.mark.parametrize(
        ("source_id", "expected"),
        [
            ("finished_tv_shows", "Finished TV Shows"),
            ("gog_api", "GOG API"),
        ],
    )
    def test_acronym_within_multi_word_id(self, source_id: str, expected: str) -> None:
        assert humanize_source_id(source_id) == expected


class TestSanitizeRuleText:
    """Rules are stripped, not allowlisted: an allowlist ate the ``+`` from
    ``prefer 4+ star ratings``."""

    @pytest.mark.parametrize(
        "rule",
        [
            "prefer 4+ star ratings",
            "prefer Café Society and 攻殻機動隊",
            "\U0001f3ac and \U0001f47b only",
        ],
    )
    def test_operator_typed_characters_survive(self, rule: str) -> None:
        assert sanitize_rule_text(rule) == rule

    @pytest.mark.parametrize("structure", ['"', "{", "}"])
    def test_slot_structure_characters_are_dropped(self, structure: str) -> None:
        """The rule sits in a quoted slot beside a JSON template."""
        assert sanitize_rule_text(f"avoid {structure}horror") == "avoid horror"

    @pytest.mark.parametrize("code", [0x00, 0x1B, 0x7F, 0x9B, 0xD800])
    def test_no_control_or_surrogate_survives(self, code: int) -> None:
        assert chr(code) not in sanitize_rule_text(f"avoid {chr(code)}horror")

    def test_no_codepoint_survives_unencodable(self) -> None:
        """The whole invariant: whatever comes out, the request body encodes."""
        every_codepoint = "".join(chr(code) for code in range(sys.maxunicode + 1))

        assert sanitize_rule_text(every_codepoint).encode("utf-8")

    @pytest.mark.parametrize("breaker", ALL_LINE_BREAKS)
    def test_a_whitespace_run_collapses_and_the_ends_are_trimmed(
        self, breaker: str
    ) -> None:
        """One rule, one prompt line, whatever it was typed with."""
        assert sanitize_rule_text(f" avoid\t{breaker}  horror ") == "avoid horror"


class TestStripLoneSurrogates:
    """Narrower than the rule sanitizer on purpose: a review is prose, and
    flattening it to one line would lose the operator's own paragraphs.
    """

    @pytest.mark.parametrize("code", [0xD800, 0xDFFF])
    def test_every_surrogate_half_goes(self, code: int) -> None:
        assert strip_lone_surrogates(f"loved {chr(code)}it") == "loved it"

    def test_no_codepoint_survives_unencodable(self) -> None:
        """The whole invariant: whatever comes out, SQLite stores."""
        every_codepoint = "".join(chr(code) for code in range(sys.maxunicode + 1))

        assert strip_lone_surrogates(every_codepoint).encode("utf-8")

    @pytest.mark.parametrize("kept", ["a\nb", '"quoted" {braced}'])
    def test_what_encodes_is_left_alone(self, kept: str) -> None:
        assert strip_lone_surrogates(kept) == kept


class TestSanitizeForLog:
    """Escapes rather than strips, unlike the rule sanitizer: a forged log line is
    the risk, and the reader still wants the value that caused it."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Real Title\nWARNING forged", "Real Title\\nWARNING forged"),
            ("Real Title\0WARNING", "Real Title\\0WARNING"),
        ],
    )
    def test_line_structure_characters_are_escaped(
        self, raw: str, expected: str
    ) -> None:
        assert sanitize_for_log(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        ["攻殻機動隊 📚 «Café»", " ~"],
    )
    def test_only_breaks_controls_and_surrogates_are_targets(self, raw: str) -> None:
        """Everything else comes back as it came, U+0020 and U+007E included. Those
        two bracket the escaped ASCII ranges; its neighbours in this module are
        allowlist strippers, and this is not one."""
        assert sanitize_for_log(raw) == raw

    @pytest.mark.parametrize("code", [0x00, 0x1B, 0x7F])
    def test_every_c0_control_and_del_is_escaped(self, code: int) -> None:
        """A terminal control rewrites the line an operator just read."""
        control = chr(code)
        expected = {"\n": "\\n", "\r": "\\r", "\0": "\\0"}.get(
            control, f"\\u{code:04x}"
        )

        assert sanitize_for_log(f"Real Title{control}ERROR") == (
            f"Real Title{expected}ERROR"
        )

    @pytest.mark.parametrize("code", [0x80, 0x9B, 0x9F])
    def test_every_c1_control_is_escaped(self, code: int) -> None:
        """Reported: C1 was excluded as bytes UTF-8 never carries."""
        control = chr(code)

        assert sanitize_for_log(f"Real Title{control}ERROR") == (
            f"Real Title\\u{code:04x}ERROR"
        )

    @pytest.mark.parametrize("neighbour", ["\N{NO-BREAK SPACE}", "ÿ"])
    def test_the_codepoints_bracketing_the_control_ranges_survive(
        self, neighbour: str
    ) -> None:
        """U+00A0 sits one past C1 and arrives in real scraped titles."""
        assert sanitize_for_log(f"Dune{neighbour}Part Two") == (
            f"Dune{neighbour}Part Two"
        )

    @pytest.mark.parametrize("breaker", EXOTIC_LINE_BREAKS)
    def test_the_exotic_breaks_are_escaped_too(self, breaker: str) -> None:
        """Reported: U+2028 forged a line straight through this function."""
        escaped = sanitize_for_log(f"Real Title{breaker}ERROR forged")

        assert escaped == f"Real Title\\u{ord(breaker):04x}ERROR forged"

    def test_no_codepoint_survives_into_a_second_log_line(self) -> None:
        every_codepoint = "".join(
            f"A{chr(code)}B" for code in range(sys.maxunicode + 1)
        )

        assert len(sanitize_for_log(every_codepoint).splitlines()) == 1


def _write_one_record(message: str, log_file: Path) -> None:
    """Strict errors here would test a sink the app does not configure, and the
    line-forging these cases are about is the same either way."""
    handler = logging.FileHandler(log_file, encoding="utf-8", errors="backslashreplace")
    handler.setFormatter(logging.Formatter("%(levelname)s | %(message)s"))
    writer = logging.getLogger("tests.sanitize_for_log")
    writer.propagate = False
    writer.setLevel(logging.INFO)
    writer.addHandler(handler)
    try:
        writer.info("title=%s", message)
    finally:
        writer.removeHandler(handler)
        handler.close()


class TestExceptionForLog:
    """``str(TimeoutError())`` is the empty string, so the class name is the whole
    diagnostic for a fault that carries no message."""

    def test_a_message_less_exception_still_names_its_class(self) -> None:
        assert exception_for_log(TimeoutError()) == "TimeoutError: "

    def test_a_lone_surrogate_in_the_message_is_escaped(self) -> None:
        """The fault's words are a filename as often as not."""
        assert exception_for_log(OSError("Dune\udcff")) == "OSError: Dune\\udcff"


class TestARequestFaultIsScrubbedWhoeverRendersIt:
    """Regression: the renderer kept a ``requests`` message whole."""

    def test_an_http_fault_surfaces_only_its_status(self) -> None:
        """The message quotes the URL, and providers key it with ``?api_key=``."""
        response = Mock(spec=requests.Response)
        response.status_code = 403
        error = requests.HTTPError(
            "403 Client Error for url: https://api.example.com/x?api_key=SECRET123",
            response=response,
        )

        assert exception_for_log(error) == "HTTP 403"

    def test_a_transport_fault_surfaces_only_its_class(self) -> None:
        error = requests.ConnectionError(
            "Failed to connect to https://api.example.com/x?key=SECRET123"
        )

        assert exception_for_log(error) == "ConnectionError"

    def test_a_plain_exception_still_keeps_its_words(self) -> None:
        """The scrub is the ``requests`` branch, not a new rule for every fault."""
        assert exception_for_log(ValueError("no candidate for Dune")) == (
            "ValueError: no candidate for Dune"
        )


class TestTheEscapedValueReachesTheFileAsOneLine:
    """caplog holds a record; the forgery happens in the file it is written to."""

    def test_every_break_at_once_writes_one_line(self, tmp_path: Path) -> None:
        log_file = tmp_path / "app.log"
        raw = "Dune" + "".join(f"{breaker}ERROR" for breaker in LINE_BREAKS + "\0")

        _write_one_record(sanitize_for_log(raw), log_file)

        assert len(log_file.read_text(encoding="utf-8").splitlines()) == 1

    def test_a_decoded_csi_reaches_the_file_as_its_escape(self, tmp_path: Path) -> None:
        """U+009B is ESC[ in one codepoint, so it erases the line unescaped."""
        log_file = tmp_path / "app.log"

        _write_one_record(sanitize_for_log("Dune\x9b2KERROR | forged"), log_file)

        written = log_file.read_text(encoding="utf-8")
        assert "Dune\\u009b2KERROR | forged" in written
        assert "\x9b" not in written


class TestALoneSurrogateIsEscaped:
    """Reported by QA: a surrogate deleted the whole entry, not one character."""

    @pytest.mark.parametrize("surrogate", ["\ud800", "\udfff"])
    def test_each_end_of_the_range_is_escaped(self, surrogate: str) -> None:
        """Both halves of the range, not only the ``\\udcff`` QA reported."""
        assert sanitize_for_log(f"Dune{surrogate}") == f"Dune\\u{ord(surrogate):04x}"

    @pytest.mark.parametrize("neighbour", ["", "\U0001f600"])
    def test_the_characters_either_side_of_the_range_survive(
        self, neighbour: str
    ) -> None:
        """The range is code units. An emoji is one codepoint, not two halves."""
        assert sanitize_for_log(f"Dune{neighbour}") == f"Dune{neighbour}"


class TestEveryCodepointIsAccountedFor:
    """Exhaustive sweeps, so a hand-written break list cannot go stale."""

    def test_line_break_inventory_is_complete(self) -> None:
        breaking = {
            chr(code)
            for code in range(sys.maxunicode + 1)
            if len(f"A{chr(code)}B".splitlines()) > 1
        }
        assert breaking == set(ALL_LINE_BREAKS) - {"\r\n"}

    def test_no_codepoint_survives_as_a_line_break(self) -> None:
        every_codepoint = "".join(
            f"A{chr(code)}B" for code in range(sys.maxunicode + 1)
        )
        assert len(sanitize_rule_text(every_codepoint).splitlines()) == 1
