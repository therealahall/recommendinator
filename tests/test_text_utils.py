"""Tests for text formatting utilities."""

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

# Read off the module's own definition, so a break the rule side learns about
# is one the log side is tested against too.
EXOTIC_LINE_BREAKS = [character for character in LINE_BREAKS if character not in "\n\r"]

ALL_LINE_BREAKS = ["\n", "\r", "\r\n", *EXOTIC_LINE_BREAKS]


class TestHumanizeSourceIdBasic:
    """Tests for basic snake_case to human-readable conversion."""

    @pytest.mark.parametrize(
        ("source_id", "expected"),
        [
            ("my_books", "My Books"),
            ("finished_movies", "Finished Movies"),
            ("personal_site_games", "Personal Site Games"),
            ("steam_wishlist", "Steam Wishlist"),
            ("currently_reading", "Currently Reading"),
        ],
    )
    def test_normal_snake_case_inputs(self, source_id: str, expected: str) -> None:
        """Normal snake_case source IDs are title-cased with spaces."""
        assert humanize_source_id(source_id) == expected

    @pytest.mark.parametrize(
        ("source_id", "expected"),
        [
            ("calibre-web", "Calibre Web"),
            ("my-books", "My Books"),
            ("read-only-shelf", "Read Only Shelf"),
        ],
    )
    def test_hyphenated_inputs(self, source_id: str, expected: str) -> None:
        """Hyphenated source IDs split on hyphens like underscores.

        Hyphens became valid source-id characters so the "Add data source"
        modal can prefill plugin names such as ``calibre-web``; the humanizer
        must split on both separators to render "Calibre Web".
        """
        assert humanize_source_id(source_id) == expected

    def test_mixed_separators(self) -> None:
        """A source ID mixing underscores and hyphens splits on both."""
        assert humanize_source_id("calibre-web_books") == "Calibre Web Books"


class TestHumanizeSourceIdAcronyms:
    """Tests for acronym uppercasing in humanize_source_id."""

    @pytest.mark.parametrize(
        ("source_id", "expected"),
        [
            ("tv", "TV"),
            ("gog", "GOG"),
            ("api", "API"),
            ("id", "ID"),
            ("csv", "CSV"),
            ("json", "JSON"),
        ],
    )
    def test_single_acronym(self, source_id: str, expected: str) -> None:
        """Known acronyms are fully uppercased instead of title-cased."""
        assert humanize_source_id(source_id) == expected

    @pytest.mark.parametrize(
        ("source_id", "expected"),
        [
            ("finished_tv_shows", "Finished TV Shows"),
            ("gog_wishlist", "GOG Wishlist"),
            ("api_key", "API Key"),
            ("source_id", "Source ID"),
            ("export_csv", "Export CSV"),
            ("import_json", "Import JSON"),
        ],
    )
    def test_acronym_within_multi_word_id(self, source_id: str, expected: str) -> None:
        """Acronyms within longer snake_case IDs are uppercased correctly."""
        assert humanize_source_id(source_id) == expected

    @pytest.mark.parametrize(
        ("source_id", "expected"),
        [
            ("gog_api", "GOG API"),
            ("tv_json", "TV JSON"),
            ("csv_api_id", "CSV API ID"),
        ],
    )
    def test_multiple_acronyms_in_one_id(self, source_id: str, expected: str) -> None:
        """Multiple acronyms in a single source ID are all uppercased."""
        assert humanize_source_id(source_id) == expected


class TestHumanizeSourceIdEdgeCases:
    """Tests for edge cases in humanize_source_id."""

    def test_empty_string(self) -> None:
        """Empty string input returns an empty string."""
        assert humanize_source_id("") == ""

    def test_single_word_no_underscores(self) -> None:
        """Single word without underscores is title-cased."""
        assert humanize_source_id("books") == "Books"

    def test_single_word_already_capitalized(self) -> None:
        """Single capitalized word is returned title-cased (unchanged)."""
        assert humanize_source_id("Books") == "Books"

    def test_multiple_consecutive_underscores(self) -> None:
        """Multiple consecutive underscores produce empty segments that capitalize to empty strings."""
        result = humanize_source_id("my__books")
        # split("_") on "my__books" gives ["my", "", "books"]
        # "".capitalize() returns "", so we get "My  Books" with double space
        assert result == "My  Books"

    def test_leading_underscore(self) -> None:
        """Leading underscore produces an empty first segment."""
        result = humanize_source_id("_private_source")
        # split("_") on "_private_source" gives ["", "private", "source"]
        assert result == " Private Source"

    def test_trailing_underscore(self) -> None:
        """Trailing underscore produces an empty last segment."""
        result = humanize_source_id("my_source_")
        # split("_") on "my_source_" gives ["my", "source", ""]
        assert result == "My Source "

    def test_all_underscores(self) -> None:
        """String of only underscores produces spaces."""
        result = humanize_source_id("___")
        # split("_") on "___" gives ["", "", "", ""]
        assert result == "   "

    def test_leading_hyphen(self) -> None:
        """Leading hyphen produces an empty first segment, mirroring underscore."""
        # re.split(r"[_-]", "-private-source") gives ["", "private", "source"]
        assert humanize_source_id("-private-source") == " Private Source"

    def test_trailing_hyphen(self) -> None:
        """Trailing hyphen produces an empty last segment, mirroring underscore."""
        # re.split(r"[_-]", "my-source-") gives ["my", "source", ""]
        assert humanize_source_id("my-source-") == "My Source "

    def test_multiple_consecutive_hyphens(self) -> None:
        """Consecutive hyphens produce empty segments, mirroring underscore."""
        # re.split(r"[_-]", "my--books") gives ["my", "", "books"]
        assert humanize_source_id("my--books") == "My  Books"

    def test_non_acronym_short_word(self) -> None:
        """Short words not in the acronym list are title-cased, not uppercased."""
        assert humanize_source_id("my_app") == "My App"

    def test_acronym_case_sensitivity(self) -> None:
        """Acronym lookup is case-sensitive; uppercase input is not matched."""
        # "TV" (uppercase) is not in the lookup dict (key is "tv" lowercase)
        # so it goes through .capitalize() which gives "Tv"
        assert humanize_source_id("TV") == "Tv"
        assert humanize_source_id("GOG") == "Gog"


class TestSanitizeRuleText:
    """Rules are stripped, not allowlisted: an allowlist ate the ``+`` from
    ``prefer 4+ star ratings``. Only slot structure and characters that
    cannot survive a UTF-8 encode go.
    """

    @pytest.mark.parametrize(
        "rule",
        [
            "prefer 4+ star ratings",
            "no more than 20% horror",
            "rating >= 4",
            "only #1 entries in a series",
            "prefer Café Society and 攻殻機動隊",
            "\U0001f3ac and \U0001f47b only",
            "avoid A. Author | B. Author [2020]",
        ],
    )
    def test_operator_typed_characters_survive(self, rule: str) -> None:
        """Punctuation, accents, CJK and emoji are the operator's own words."""
        assert sanitize_rule_text(rule) == rule

    @pytest.mark.parametrize("structure", ['"', "{", "}"])
    def test_slot_structure_characters_are_dropped(self, structure: str) -> None:
        """The rule sits in a quoted slot beside a JSON template."""
        assert sanitize_rule_text(f"avoid {structure}horror") == "avoid horror"

    @pytest.mark.parametrize(
        "code",
        [*range(0x00, 0x20), 0x7F, *range(0x80, 0xA0), 0xD800, 0xDC00, 0xDFFF],
    )
    def test_no_control_or_surrogate_survives(self, code: int) -> None:
        """C0, DEL, C1 and both halves of the surrogate range are removed."""
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

    @pytest.mark.parametrize("code", [0xA0, 0x2000, 0x3000])
    def test_a_unicode_space_collapses_like_an_ascii_one(self, code: int) -> None:
        """CUSTOM_RULES.md names U+00A0, and ``\\s`` covers its siblings."""
        space = chr(code)

        assert sanitize_rule_text(f"{space}avoid{space}horror{space}") == "avoid horror"


class TestStripLoneSurrogates:
    """Narrower than the rule sanitizer on purpose: a review is prose, and
    flattening it to one line would lose the operator's own paragraphs.
    """

    @pytest.mark.parametrize("code", [0xD800, 0xDCFF, 0xDFFF])
    def test_every_surrogate_half_goes(self, code: int) -> None:
        assert strip_lone_surrogates(f"loved {chr(code)}it") == "loved it"

    def test_no_codepoint_survives_unencodable(self) -> None:
        """The whole invariant: whatever comes out, SQLite stores."""
        every_codepoint = "".join(chr(code) for code in range(sys.maxunicode + 1))

        assert strip_lone_surrogates(every_codepoint).encode("utf-8")

    @pytest.mark.parametrize("kept", ["a\nb", "a\tb", "a\x07b", '"quoted" {braced}'])
    def test_what_encodes_is_left_alone(self, kept: str) -> None:
        assert strip_lone_surrogates(kept) == kept


class TestSanitizeForLog:
    """Tests for sanitize_for_log — the one helper every log sink uses.

    Escapes rather than strips, unlike the rule sanitizer: a forged log
    line is the risk, and the reader still wants the value that caused it.
    """

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Real Title\nWARNING forged", "Real Title\\nWARNING forged"),
            ("Real Title\r\nWARNING", "Real Title\\r\\nWARNING"),
            ("Real Title\0WARNING", "Real Title\\0WARNING"),
        ],
    )
    def test_line_structure_characters_are_escaped(
        self, raw: str, expected: str
    ) -> None:
        """CR, LF and NUL become their two-character escapes."""
        assert sanitize_for_log(raw) == expected

    def test_ordinary_text_is_untouched(self) -> None:
        """Nothing else is rewritten, so the log still reads as the value."""
        assert sanitize_for_log("CSV file not found: /srv/books.csv") == (
            "CSV file not found: /srv/books.csv"
        )

    @pytest.mark.parametrize(
        "raw",
        ["", "攻殻機動隊 📚 «Café»", "a" * 10_000, " ~"],
    )
    def test_only_breaks_controls_and_surrogates_are_targets(self, raw: str) -> None:
        """Everything else comes back as it came, U+0020 and U+007E included.

        Those two bracket the escaped ASCII ranges; its neighbours in this
        module are allowlist strippers, and this is not one.
        """
        assert sanitize_for_log(raw) == raw

    @pytest.mark.parametrize("code", [*range(0x00, 0x20), 0x7F])
    def test_every_c0_control_and_del_is_escaped(self, code: int) -> None:
        """A terminal control rewrites the line an operator just read."""
        control = chr(code)
        expected = {"\n": "\\n", "\r": "\\r", "\0": "\\0"}.get(
            control, f"\\u{code:04x}"
        )

        assert sanitize_for_log(f"Real Title{control}ERROR") == (
            f"Real Title{expected}ERROR"
        )

    @pytest.mark.parametrize("code", range(0x80, 0xA0))
    def test_every_c1_control_is_escaped(self, code: int) -> None:
        """Reported: C1 was excluded as bytes UTF-8 never carries.

        Bug: true of the wire, not of the sink. The log file is UTF-8, so a
        terminal decodes U+009B out of it and obeys CSI.
        Fix: escaped like C0.
        """
        control = chr(code)

        assert sanitize_for_log(f"Real Title{control}ERROR") == (
            f"Real Title\\u{code:04x}ERROR"
        )

    @pytest.mark.parametrize("neighbour", ["~", "\N{NO-BREAK SPACE}", "ÿ"])
    def test_the_codepoints_bracketing_the_control_ranges_survive(
        self, neighbour: str
    ) -> None:
        """U+00A0 sits one past C1 and arrives in real scraped titles.

        Widening the range to 0x9f must not start eating printable Latin-1.
        """
        assert sanitize_for_log(f"Dune{neighbour}Part Two") == (
            f"Dune{neighbour}Part Two"
        )

    @pytest.mark.parametrize("breaker", EXOTIC_LINE_BREAKS)
    def test_the_exotic_breaks_are_escaped_too(self, breaker: str) -> None:
        """Reported: U+2028 forged a line straight through this function.

        Bug: only \\n, \\r and NUL were escaped, while ``str.splitlines`` and
        the single-line log format break on eight more.
        Fix: the escape table is built from the shared ``LINE_BREAKS``.
        """
        escaped = sanitize_for_log(f"Real Title{breaker}ERROR forged")

        assert escaped == f"Real Title\\u{ord(breaker):04x}ERROR forged"

    def test_no_codepoint_survives_into_a_second_log_line(self) -> None:
        """Every codepoint at once still logs as a single line."""
        every_codepoint = "".join(
            f"A{chr(code)}B" for code in range(sys.maxunicode + 1)
        )

        assert len(sanitize_for_log(every_codepoint).splitlines()) == 1


def _write_one_record(message: str, log_file: Path) -> None:
    """Put one record through a handler built as ``configure_logging`` builds it.

    Strict errors here would test a sink the app does not configure, and the
    line-forging these cases are about is the same either way.
    """
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
    """The rendering every catch-all sink uses instead of a traceback.

    ``str(TimeoutError())`` is the empty string, so the class name is the
    whole diagnostic for a fault that carries no message.
    """

    def test_the_class_name_leads_the_message(self) -> None:
        rendered = exception_for_log(ValueError("no candidate for Dune"))

        assert rendered == "ValueError: no candidate for Dune"

    def test_a_message_less_exception_still_names_its_class(self) -> None:
        assert exception_for_log(TimeoutError()) == "TimeoutError: "

    @pytest.mark.parametrize("breaker", [*LINE_BREAKS, "\0"])
    def test_a_break_in_the_message_cannot_forge_a_line(
        self, breaker: str, tmp_path: Path
    ) -> None:
        """The message is the fault's own words, so it is escaped like any."""
        log_file = tmp_path / "app.log"

        _write_one_record(
            exception_for_log(ValueError(f"Dune{breaker}ERROR | forged")), log_file
        )

        assert len(log_file.read_text(encoding="utf-8").splitlines()) == 1

    def test_a_lone_surrogate_in_the_message_is_escaped(self) -> None:
        """The fault's words are a filename as often as not."""
        assert exception_for_log(OSError("Dune\udcff")) == "OSError: Dune\\udcff"

    def test_a_keyboard_interrupt_renders_like_any_other(self) -> None:
        """``BaseException``, not ``Exception``: the annotation admits these."""
        assert exception_for_log(KeyboardInterrupt()) == "KeyboardInterrupt: "


class TestARequestFaultIsScrubbedWhoeverRendersIt:
    """Regression: the renderer kept a ``requests`` message whole.

    Bug: consolidating ``_render_error`` dropped its
    ``isinstance(error, RequestException)`` branch, leaving the scrub to each
    caller's handler ordering at catch-alls around token exchanges.
    Fix: :func:`exception_for_log` dispatches on the exception itself.
    """

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

    def test_a_json_decode_fault_is_scrubbed_like_its_parent(self) -> None:
        """``JSONDecodeError`` subclasses ``RequestException``, so it lands here."""
        error = requests.exceptions.JSONDecodeError("Expecting value", "?key=SECRET", 0)

        assert exception_for_log(error) == "JSONDecodeError"

    def test_a_plain_exception_still_keeps_its_words(self) -> None:
        """The scrub is the ``requests`` branch, not a new rule for every fault."""
        assert exception_for_log(ValueError("no candidate for Dune")) == (
            "ValueError: no candidate for Dune"
        )


class TestTheEscapedValueReachesTheFileAsOneLine:
    """caplog holds a record; the forgery happens in the file it is written to.

    So these drive a real ``FileHandler`` with the single-line format the app
    configures, and count the physical lines it produced.
    """

    @pytest.mark.parametrize("breaker", [*LINE_BREAKS, "\0"])
    def test_one_call_writes_one_line(self, breaker: str, tmp_path: Path) -> None:
        log_file = tmp_path / "app.log"

        _write_one_record(sanitize_for_log(f"Dune{breaker}ERROR | forged"), log_file)

        assert len(log_file.read_text(encoding="utf-8").splitlines()) == 1

    def test_every_break_at_once_writes_one_line(self, tmp_path: Path) -> None:
        log_file = tmp_path / "app.log"
        raw = "Dune" + "".join(f"{breaker}ERROR" for breaker in LINE_BREAKS + "\0")

        _write_one_record(sanitize_for_log(raw), log_file)

        assert len(log_file.read_text(encoding="utf-8").splitlines()) == 1

    def test_a_decoded_csi_reaches_the_file_as_its_escape(self, tmp_path: Path) -> None:
        """U+009B is ESC[ in one codepoint, so it erases the line unescaped.

        The file is written UTF-8 and read back decoded, which is what the
        terminal rendering `docker logs` does too.
        """
        log_file = tmp_path / "app.log"

        _write_one_record(sanitize_for_log("Dune\x9b2KERROR | forged"), log_file)

        written = log_file.read_text(encoding="utf-8")
        assert "Dune\\u009b2KERROR | forged" in written
        assert "\x9b" not in written


class TestALoneSurrogateIsEscaped:
    """Reported by QA: a surrogate deleted the whole entry, not one character.

    The handlers escape it themselves now, so what turns on this is one
    readable escape at every sink rather than each codec's own.
    """

    @pytest.mark.parametrize("surrogate", ["\ud800", "\udbff", "\udc00", "\udfff"])
    def test_each_end_of_the_range_is_escaped(self, surrogate: str) -> None:
        """Both halves of the range, not only the ``\\udcff`` QA reported."""
        assert sanitize_for_log(f"Dune{surrogate}") == f"Dune\\u{ord(surrogate):04x}"

    def test_every_surrogate_at_once_escapes_to_one_line(self) -> None:
        raw = "Dune" + "".join(chr(code) for code in range(0xD800, 0xE000))

        escaped = sanitize_for_log(raw)

        assert escaped.startswith("Dune\\ud800")
        assert len(escaped.splitlines()) == 1

    @pytest.mark.parametrize("neighbour", ["퟿", "", "\U0001f600"])
    def test_the_characters_either_side_of_the_range_survive(
        self, neighbour: str
    ) -> None:
        """The range is code units. An emoji is one codepoint, not two halves."""
        assert sanitize_for_log(f"Dune{neighbour}") == f"Dune{neighbour}"

    def test_no_codepoint_survives_unencodable(self) -> None:
        """The whole invariant: whatever comes out, the handler can write it."""
        every_codepoint = "".join(chr(code) for code in range(sys.maxunicode + 1))

        assert sanitize_for_log(every_codepoint).encode("utf-8")

    def test_a_second_pass_changes_nothing(self) -> None:
        """Output is already safe, so a double-sanitized value is not mangled."""
        once = sanitize_for_log("Dune\udcff\nERROR")

        assert sanitize_for_log(once) == once


class TestTheEscapeIsNotReversible:
    """Accepted ambiguity: a backslash is passed through, so a typed
    ``\\n`` renders as the character does. Escaping it too would make
    every Windows path unreadable to buy an inverse nobody calls.
    """

    @pytest.mark.parametrize(
        ("literal", "control"),
        [("Dune\\n", "Dune\n"), ("Dune\\udcff", "Dune\udcff")],
    )
    def test_a_typed_escape_collides_with_the_character(
        self, literal: str, control: str
    ) -> None:
        assert sanitize_for_log(literal) == sanitize_for_log(control)

    def test_the_collision_still_cannot_forge_a_line(self, tmp_path: Path) -> None:
        """Ambiguous, never injectable: the collision is one physical line."""
        log_file = tmp_path / "app.log"

        _write_one_record(sanitize_for_log("Dune\\nERROR | forged"), log_file)

        assert len(log_file.read_text(encoding="utf-8").splitlines()) == 1


class TestEveryCodepointIsAccountedFor:
    """Exhaustive sweeps, so a hand-written break list cannot go stale."""

    def test_line_break_inventory_is_complete(self) -> None:
        """No codepoint outside ALL_LINE_BREAKS splits a string in two."""
        breaking = {
            chr(code)
            for code in range(sys.maxunicode + 1)
            if len(f"A{chr(code)}B".splitlines()) > 1
        }
        assert breaking == set(ALL_LINE_BREAKS) - {"\r\n"}

    def test_no_codepoint_survives_as_a_line_break(self) -> None:
        """Every codepoint at once still sanitizes down to a single line."""
        every_codepoint = "".join(
            f"A{chr(code)}B" for code in range(sys.maxunicode + 1)
        )
        assert len(sanitize_rule_text(every_codepoint).splitlines()) == 1
