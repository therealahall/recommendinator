import json
from unittest.mock import MagicMock

from click.testing import CliRunner

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.recommendations.engine import RecommendationEngine
from src.recommendations.record import Recommendation
from tests.factories import make_storage_mock

from .conftest import _invoke_with_mocks


class TestRecommendEmptyResultsRegression:
    def test_empty_recommendations_shows_unconsumed_guidance_regression(
        self, cli_runner: CliRunner
    ) -> None:
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
        assert "No video game left to rank" in result.output


class TestRecommendCrossTypeRun:
    """`--type` was required, so the one ranked list across all four types the
    web now serves had no CLI equivalent."""

    def test_no_type_ranks_all_four_and_every_row_names_its_own(
        self, cli_runner: CliRunner
    ) -> None:
        mock_engine = _engine_returning(
            Recommendation(
                item=_book("Hyperion", author="Dan Simmons", db_id=1),
                score=0.9,
                reasoning="Great match",
            ),
            Recommendation(
                item=_movie("Blade Runner"),
                score=0.8,
                reasoning="Also good",
            ),
        )

        result = _invoke_recommend_with_engine(cli_runner, ["recommend"], mock_engine)

        assert result.exit_code == 0
        call = mock_engine.generate_recommendations.call_args
        assert call.kwargs["content_type"] is None
        assert _data_rows(result.stdout) == [
            ["1", "book", "Hyperion", "N/A", "Dan Simmons", "0.9", "Great match", ""],
            [
                "2",
                "movie",
                "Blade Runner",
                "N/A",
                "Director A",
                "0.8",
                "Also good",
                "",
            ],
        ]
        assert "Creator" in result.stdout

    def test_a_mixed_run_with_nothing_left_names_no_type(
        self, cli_runner: CliRunner
    ) -> None:
        result = _invoke_recommend_with_engine(
            cli_runner, ["recommend"], _engine_returning()
        )

        assert result.exit_code == 0
        assert "Nothing left to rank" in result.output
        assert "Every item in the pool" in result.output


class TestRecommendCountMaxEnforcement:
    def test_count_exceeds_max_count_aborts(self, cli_runner: CliRunner) -> None:
        mock_storage = make_storage_mock()
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
    mock_storage = make_storage_mock()
    mock_storage.get_user_preference_config.return_value = MagicMock()
    return _invoke_with_mocks(
        cli_runner, args, mock_storage, config=config, engine=mock_engine
    )


def _book(
    title: str,
    author: str | None = "Author A",
    db_id: int = 1,
    series: tuple[str, float] | None = None,
) -> ContentItem:
    return ContentItem(
        id=f"ext-{db_id}",
        db_id=db_id,
        title=title,
        author=author,
        content_type=ContentType.BOOK,
        status=ConsumptionStatus.UNREAD,
        metadata={"series": series[0], "series_index": series[1]} if series else {},
    )


def _movie(title: str, db_id: int = 9) -> ContentItem:
    return ContentItem(
        id=f"ext-{db_id}",
        db_id=db_id,
        title=title,
        author="Director A",
        content_type=ContentType.MOVIE,
        status=ConsumptionStatus.COMPLETED,
        cover_url="https://1.2.3.4/blade.jpg",
    )


def _engine_returning(*recommendations: Recommendation) -> MagicMock:
    engine = MagicMock(spec=RecommendationEngine)
    engine.generate_recommendations.return_value = list(recommendations)
    return engine


def _data_rows(output: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in output.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells[0].isdigit():
            rows.append(cells)
    return rows


class TestRecommendJsonOutput:
    def test_json_output_matches_web_shape(self) -> None:
        mock_engine = _engine_returning(
            Recommendation(
                item=_book("Book One", db_id=42, series=("The Expanse", 2.0)),
                score=0.9,
                reasoning="Great match",
                score_breakdown={"genre": 0.5, "theme": 0.4},
            )
        )

        result = _invoke_recommend_with_engine(
            CliRunner(),
            ["recommend", "--type", "book", "--format", "json"],
            mock_engine,
        )

        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        rec = parsed[0]
        assert set(rec.keys()) == {
            "db_id",
            "title",
            "author",
            "content_type",
            "cover_url",
            "series",
            "series_index",
            "score",
            "reasoning",
            "score_breakdown",
            "variety_penalty",
            "contributing_items",
            "adaptations",
        }
        assert rec["db_id"] == 42
        assert rec["score_breakdown"] == {"genre": 0.5, "theme": 0.4}
        assert (rec["series"], rec["series_index"]) == ("The Expanse", 2.0)

    def test_json_output_names_every_item_behind_a_pick(self) -> None:
        result = _invoke_recommend_with_engine(
            CliRunner(),
            ["recommend", "--type", "book", "--format", "json"],
            _engine_returning(
                Recommendation(
                    item=_book("Hyperion", db_id=1),
                    score=0.9,
                    reasoning="Great match",
                    contributing_items=[
                        _book("Dune", db_id=2),
                        _book("Neuromancer", db_id=3),
                    ],
                    adaptations=[_movie("Blade Runner")],
                )
            ),
        )

        assert result.exit_code == 0
        rec = json.loads(result.stdout)[0]
        assert rec["contributing_items"] == [
            {
                "db_id": 2,
                "title": "Dune",
                "author": "Author A",
                "content_type": "book",
                "cover_url": None,
            },
            {
                "db_id": 3,
                "title": "Neuromancer",
                "author": "Author A",
                "content_type": "book",
                "cover_url": None,
            },
        ]
        assert rec["adaptations"] == [
            {
                "db_id": 9,
                "title": "Blade Runner",
                "author": "Director A",
                "content_type": "movie",
                "cover_url": "/api/covers/9",
            }
        ]

    def test_zero_results_emit_an_empty_array_and_no_prose(self) -> None:
        result = _invoke_recommend_with_engine(
            CliRunner(),
            ["recommend", "--type", "book", "--format", "json"],
            _engine_returning(),
        )

        assert result.exit_code == 0
        assert result.stdout == "[]\n"
        assert "No recommendations available" not in result.stderr


class TestRecommendFailuresLeaveStdoutParseable:
    def test_an_engine_failure_writes_nothing_to_stdout(self) -> None:
        engine = MagicMock(spec=RecommendationEngine)
        engine.generate_recommendations.side_effect = RuntimeError("boom")

        result = _invoke_recommend_with_engine(
            CliRunner(),
            ["recommend", "--type", "book", "--format", "json"],
            engine,
        )

        assert result.exit_code != 0
        assert result.stdout == ""
        assert "Failed to generate recommendations" in result.stderr


class TestRecommendProgressLineOnStdoutRegression:
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
        result = _invoke_recommend_with_engine(
            CliRunner(), [*self.ARGS, "--format", "json"], self._engine()
        )

        assert result.exit_code == 0
        assert json.loads(result.stdout) == [
            {
                "db_id": 42,
                "title": "Hyperion",
                "author": "Dan Simmons",
                "content_type": "book",
                "cover_url": None,
                "series": None,
                "series_index": None,
                "score": 0.9,
                "reasoning": "Great match",
                "score_breakdown": {},
                "variety_penalty": 0.0,
                "contributing_items": [],
                "adaptations": [],
            }
        ]
        assert self.PROGRESS in result.stderr

    def test_the_table_run_still_reports_progress_regression(self) -> None:
        result = _invoke_recommend_with_engine(CliRunner(), self.ARGS, self._engine())

        assert result.exit_code == 0
        assert self.PROGRESS in result.stderr
        assert self.PROGRESS not in result.stdout
        assert _data_rows(result.stdout) == [
            ["1", "book", "Hyperion", "N/A", "Dan Simmons", "0.9", "Great match", ""]
        ]


class TestRecommendTableOutput:
    def test_recommendations_render_as_ranked_rows_in_engine_order(self) -> None:
        result = _invoke_recommend_with_engine(
            CliRunner(),
            ["recommend", "--type", "book"],
            _engine_returning(
                Recommendation(
                    item=_book(
                        "Hyperion",
                        author="Dan Simmons",
                        db_id=1,
                        series=("Hyperion Cantos", 1.0),
                    ),
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
            [
                "1",
                "book",
                "Hyperion",
                "Hyperion Cantos #1",
                "Dan Simmons",
                "0.9",
                "Great match",
                "",
            ],
            ["2", "book", "Dune", "N/A", "Frank Herbert", "0.42", "Also good", ""],
        ]

    def test_every_item_behind_a_pick_is_named_in_the_table(self) -> None:
        result = _invoke_recommend_with_engine(
            CliRunner(),
            ["recommend", "--type", "book"],
            _engine_returning(
                Recommendation(
                    item=_book("Hyperion", db_id=1),
                    score=0.9,
                    reasoning="Great match",
                    contributing_items=[
                        _book("Dune", db_id=2),
                        _book("Neuromancer", db_id=3),
                    ],
                    adaptations=[_movie("Blade Runner")],
                )
            ),
        )

        assert result.exit_code == 0
        assert "From your library: Dune (book), Neuromancer (book)" in result.stdout
        assert "Adaptations: Blade Runner (movie)" in result.stdout

    def test_an_unknown_author_renders_a_placeholder(self) -> None:
        result = _invoke_recommend_with_engine(
            CliRunner(),
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
            ["1", "book", "Nowhere Man", "N/A", "N/A", "0.5", "Pipeline line", ""]
        ]
        assert "None" not in result.stdout


class TestRecommendCreatorColumnRegression:
    def test_a_movie_run_heads_its_creator_column_director_regression(
        self, cli_runner: CliRunner
    ) -> None:
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
