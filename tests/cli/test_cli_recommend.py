"""Tests for CLI recommend command."""

import json
from unittest.mock import MagicMock

from click.testing import CliRunner

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.recommendations.engine import RecommendationEngine
from src.recommendations.record import Recommendation
from src.storage.manager import StorageManager

from .conftest import _invoke_with_mocks


class TestRecommendEmptyResultsRegression:
    """Regression tests for the recommend command empty-results messaging."""

    def test_empty_recommendations_shows_unconsumed_guidance_regression(
        self, cli_runner: CliRunner
    ) -> None:
        """CLI explains that recommendations come from unconsumed items.

        Bug: the old message said 'add more consumed content' which was
        misleading — recommendations are based on items the user has NOT
        consumed yet. If all items are completed, there is nothing to
        recommend. The message now explains this.
        """
        mock_engine = MagicMock(spec=RecommendationEngine)
        mock_engine.generate_recommendations.return_value = []

        result = _invoke_recommend_with_engine(
            cli_runner,
            ["recommend", "--type", "video_game"],
            mock_engine,
        )

        assert result.exit_code == 0
        assert "have not consumed yet" in result.output
        assert "add more consumed content" not in result.output
        # Named, because the web state names it: an empty run for one type and
        # an empty library read identically without it.
        assert "No video game recommendations" in result.output


class TestRecommendCountMaxEnforcement:
    """Tests for config-driven --count max enforcement (matches web API)."""

    def test_count_exceeds_max_count_aborts(self, cli_runner: CliRunner) -> None:
        """--count greater than configured max_count aborts."""
        mock_storage = MagicMock(spec=StorageManager)
        config = {"recommendations": {"max_count": 5}}
        result = _invoke_with_mocks(
            cli_runner,
            ["recommend", "--type", "video_game", "--count", "10"],
            mock_storage,
            config=config,
        )

        assert result.exit_code != 0
        assert "exceeds configured max_count=5" in result.output


def _invoke_recommend_with_engine(
    cli_runner: CliRunner,
    args: list[str],
    mock_engine: MagicMock,
    config: dict | None = None,
) -> object:
    """Invoke the recommend CLI with a pre-configured engine mock."""
    mock_storage = MagicMock(spec=StorageManager)
    mock_storage.get_user_preference_config.return_value = MagicMock()
    return _invoke_with_mocks(
        cli_runner, args, mock_storage, config=config, engine=mock_engine
    )


def _piping_runner() -> CliRunner:
    """A runner keeping the streams apart, the way a shell pipe sees them.

    The shared ``cli_runner`` fixture merges stderr into stdout, which cannot
    tell a document from the chatter printed alongside it.
    """
    return CliRunner(mix_stderr=False)


def _book(
    title: str,
    author: str | None = "Author A",
    db_id: int = 1,
) -> ContentItem:
    """An unread book to recommend."""
    return ContentItem(
        id=f"ext-{db_id}",
        db_id=db_id,
        title=title,
        author=author,
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
    )


def _engine_returning(*recommendations: Recommendation) -> MagicMock:
    """An engine that recommends exactly these, in this order."""
    engine = MagicMock(spec=RecommendationEngine)
    engine.generate_recommendations.return_value = list(recommendations)
    return engine


def _data_rows(output: str) -> list[list[str]]:
    """The table's body rows, as stripped cells.

    Keyed on the rank digit, which the header and wrapped cells lack. Scores
    arrive as tabulate re-rendered them: it parses numeric cells, so the
    command's ``0.50`` prints as ``0.5``.
    """
    rows: list[list[str]] = []
    for line in output.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells[0].isdigit():
            rows.append(cells)
    return rows


class TestRecommendJsonOutput:
    """Tests for recommend --format json matching web RecommendationResponse."""

    def test_json_output_matches_web_shape(self) -> None:
        """Test JSON output includes all RecommendationResponse fields."""
        mock_engine = _engine_returning(
            Recommendation(
                item=_book("Book One", db_id=42),
                score=0.9,
                reasoning="Great match",
                score_breakdown={"genre": 0.5, "theme": 0.4},
            )
        )

        result = _invoke_recommend_with_engine(
            _piping_runner(),
            ["recommend", "--type", "book", "--format", "json"],
            mock_engine,
        )

        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        rec = parsed[0]
        # Field set matches web RecommendationResponse
        assert set(rec.keys()) == {
            "db_id",
            "title",
            "author",
            "score",
            "reasoning",
            "score_breakdown",
            "variety_penalty",
        }
        assert rec["db_id"] == 42
        assert rec["score_breakdown"] == {"genre": 0.5, "theme": 0.4}

    def test_zero_results_emit_an_empty_array_and_no_prose(self) -> None:
        """Nothing to recommend is ``[]``, as GET /api/recommendations answers.

        The empty-state sentence belongs to the table branch alone: a caller
        parsing this document cannot read a paragraph.
        """
        result = _invoke_recommend_with_engine(
            _piping_runner(),
            ["recommend", "--type", "book", "--format", "json"],
            _engine_returning(),
        )

        assert result.exit_code == 0
        assert result.stdout == "[]\n"
        assert "No recommendations available" not in result.stderr


class TestRecommendFailuresLeaveStdoutParseable:
    """A refusal is stderr's, so a pipe gets a document or nothing at all."""

    def test_an_engine_failure_writes_nothing_to_stdout(self) -> None:
        engine = MagicMock(spec=RecommendationEngine)
        engine.generate_recommendations.side_effect = RuntimeError("boom")

        result = _invoke_recommend_with_engine(
            _piping_runner(),
            ["recommend", "--type", "book", "--format", "json"],
            engine,
        )

        assert result.exit_code != 0
        assert result.stdout == ""
        assert "Failed to generate recommendations" in result.stderr


class TestRecommendProgressLineOnStdoutRegression:
    """`recommend --format json` wrote its progress line to stdout.

    Reported: piping the document into a parser broke on that line. Root
    cause: the echo ran before the format branch, on the data stream. Fix:
    chatter goes to stderr.
    """

    ARGS = ["recommend", "--type", "book", "--count", "1"]
    PROGRESS = "Generating 1 book recommendations..."

    def _engine(self) -> MagicMock:
        return _engine_returning(
            Recommendation(
                item=_book("Hyperion", author="Dan Simmons", db_id=42),
                score=0.9,
                reasoning="Great match",
            )
        )

    def test_the_json_document_is_the_whole_of_stdout_regression(self) -> None:
        """stdout parses whole, and the progress line is on stderr."""
        result = _invoke_recommend_with_engine(
            _piping_runner(), [*self.ARGS, "--format", "json"], self._engine()
        )

        assert result.exit_code == 0
        assert json.loads(result.stdout) == [
            {
                "db_id": 42,
                "title": "Hyperion",
                "author": "Dan Simmons",
                "score": 0.9,
                "reasoning": "Great match",
                "score_breakdown": {},
                "variety_penalty": 0.0,
            }
        ]
        assert self.PROGRESS in result.stderr

    def test_the_table_run_still_reports_progress_regression(self) -> None:
        """The human default keeps the line, off the data channel."""
        result = _invoke_recommend_with_engine(
            _piping_runner(), self.ARGS, self._engine()
        )

        assert result.exit_code == 0
        assert self.PROGRESS in result.stderr
        assert self.PROGRESS not in result.stdout
        assert _data_rows(result.stdout) == [
            ["1", "Hyperion", "Dan Simmons", "0.9", "Great match"]
        ]


class TestRecommendTableOutput:
    """What the default format renders, which is what a human reads."""

    def test_recommendations_render_as_ranked_rows_in_engine_order(self) -> None:
        """Rank, title, creator and score per row, best-scoring first."""
        result = _invoke_recommend_with_engine(
            _piping_runner(),
            ["recommend", "--type", "book"],
            _engine_returning(
                Recommendation(
                    item=_book("Hyperion", author="Dan Simmons", db_id=1),
                    score=0.9,
                    reasoning="Great match",
                ),
                Recommendation(
                    item=_book("Dune", author="Frank Herbert", db_id=2),
                    score=0.42,
                    reasoning="Also good",
                ),
            ),
        )

        assert result.exit_code == 0
        assert _data_rows(result.stdout) == [
            ["1", "Hyperion", "Dan Simmons", "0.9", "Great match"],
            ["2", "Dune", "Frank Herbert", "0.42", "Also good"],
        ]

    def test_an_unknown_author_renders_a_placeholder(self) -> None:
        """A book with no author shows N/A, never the literal None."""
        result = _invoke_recommend_with_engine(
            _piping_runner(),
            ["recommend", "--type", "book"],
            _engine_returning(
                Recommendation(
                    item=_book("Nowhere Man", author=None),
                    score=0.5,
                    reasoning="Pipeline line",
                )
            ),
        )

        assert result.exit_code == 0
        assert _data_rows(result.stdout) == [
            ["1", "Nowhere Man", "N/A", "0.5", "Pipeline line"]
        ]
        assert "None" not in result.stdout


class TestRecommendCreatorColumnRegression:
    """`recommend` headed the creator column "Author" for every type.

    Bug reported: `recommend --type movie` printed directors under a column
    headed "Author". Root cause: the header was hardcoded, which only looked
    right while non-book items read back with no author at all. Fix: every
    row is the one requested type, so the header is that type's own creator
    column.
    """

    def test_a_movie_run_heads_its_creator_column_director_regression(
        self, cli_runner: CliRunner
    ) -> None:
        """The column naming a director says so."""
        item = ContentItem(
            id="ext-1",
            title="Arrival",
            author="Villeneuve",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )
        item.db_id = 7
        mock_engine = MagicMock(spec=RecommendationEngine)
        mock_engine.generate_recommendations.return_value = [
            Recommendation(item=item, score=0.9, reasoning="Great match")
        ]

        result = _invoke_recommend_with_engine(
            cli_runner, ["recommend", "--type", "movie"], mock_engine
        )

        assert result.exit_code == 0
        assert "Director" in result.output
        assert "Author" not in result.output
        assert "Villeneuve" in result.output
