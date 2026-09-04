import threading
from collections.abc import Callable, Iterator
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from src.config.service import create_recommendation_engine
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.user_preferences import UserPreferenceConfig
from src.recommendations.engine import RecommendationEngine
from src.recommendations.scorers import (
    CreatorMatchScorer,
    GenreMatchScorer,
    Scorer,
    build_scorers_with_overrides,
)
from src.settings.metadata import get_entry
from src.settings.service import apply_settings
from src.storage.manager import StorageManager
from src.storage.settings_migration import migrate_config_settings
from src.web.api._shared import _get_recommendations_config
from src.web.app import create_app
from src.web.state import app_state, get_config, get_engine, get_storage
from tests.factories import authenticated_client, make_storage_mock

_GENRE_WEIGHT_KEY = "recommendations.scorer_weights.genre_match"
_CREATOR_WEIGHT_KEY = "recommendations.scorer_weights.creator_match"
_ADAPTATION_WEIGHT_KEY = "recommendations.scorer_weights.adaptation"


def _const_default(key: str) -> Any:
    entry = get_entry(key)
    assert entry is not None, f"{key!r} is not a registered setting"
    return entry.default


def _weight_of(scorers: list[Scorer], scorer_type: type[Scorer]) -> float:
    matches = [s.weight for s in scorers if type(s) is scorer_type]
    assert len(matches) == 1, f"expected exactly one {scorer_type.__name__}"
    return matches[0]


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    return StorageManager(sqlite_path=tmp_path / "test.db")


def _build_engine(config: dict[str, Any], storage: StorageManager) -> Any:
    """Assemble the config (const<YAML<DB) then build the engine, as boot does."""
    migrate_config_settings(config, storage)
    return create_recommendation_engine(storage_manager=storage, config=config)


@pytest.fixture()
def booted_app(tmp_path: Path) -> Iterator[TestClient]:
    """Goes through ``create_app`` rather than ``_build_engine`` so the engine
    under test is the one ``get_engine()`` hands the API, wired to the running
    config dict the settings service mutates."""
    saved = {field.name: getattr(app_state, field.name) for field in fields(app_state)}
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "storage": {
                    "database_path": str(tmp_path / "recommendations.db"),
                },
            }
        )
    )
    try:
        yield authenticated_client(create_app(config_path))
    finally:
        for name, value in saved.items():
            setattr(app_state, name, value)


def _running_engine() -> Any:
    engine = get_engine()
    assert engine is not None, "no engine in app_state"
    return engine


def _apply_running(updates: dict[str, Any]) -> None:
    config = get_config()
    storage = get_storage()
    assert config is not None and storage is not None, "app_state is not booted"
    apply_settings(config, storage, updates)


def _seed_split_taste_library(storage: StorageManager) -> None:
    """Two rated books and two candidates that swap genre for creator."""
    completed = ConsumptionStatus.COMPLETED
    unread = ConsumptionStatus.UNREAD
    for item_id, title, author, genre, status, rating in (
        ("liked", "Ash Harbour", "Vera Lang", "Science Fiction", completed, 5),
        ("disliked", "Grey Ledger", "Miles Crane", "Poetry", completed, 1),
        ("genre-led", "Neon Divide", "Miles Crane", "Science Fiction", unread, None),
        ("creator-led", "Quiet Harvest", "Vera Lang", "Poetry", unread, None),
    ):
        storage.save_content_item(
            ContentItem(
                id=item_id,
                title=title,
                author=author,
                content_type=ContentType.BOOK,
                status=status,
                rating=rating,
                metadata={"genre": genre},
            )
        )


class TestScorerWeightFallback:
    """Global scorer weights are the baseline for a user with no override."""

    def test_db_scorer_weight_is_effective_without_user_override(
        self, storage: StorageManager
    ) -> None:
        """With an empty per-user config the engine uses ``pipeline.scorers``
        directly, so the pipeline weight *is* the effective weight for a new user."""
        storage.settings.set("recommendations.scorer_weights.genre_match", 7.0)

        engine = _build_engine({}, storage)

        assert _weight_of(engine.pipeline.scorers, GenreMatchScorer) == 7.0

    def test_user_override_wins_per_key_over_global(
        self, storage: StorageManager
    ) -> None:
        """This exercises the exact engine code path for a user *with* overrides:
        ``build_scorers_with_overrides`` clones only the overridden scorers."""
        storage.settings.set("recommendations.scorer_weights.genre_match", 7.0)
        storage.settings.set("recommendations.scorer_weights.creator_match", 6.0)

        engine = _build_engine({}, storage)

        overridden = build_scorers_with_overrides(
            engine.pipeline.scorers, {"genre_match": 3.0}
        )

        assert _weight_of(overridden, GenreMatchScorer) == 3.0
        assert _weight_of(overridden, CreatorMatchScorer) == 6.0

    def test_yaml_scorer_weight_used_when_db_absent(
        self, storage: StorageManager
    ) -> None:
        config: dict[str, Any] = {
            "recommendations": {"scorer_weights": {"genre_match": 4.0}}
        }

        engine = _build_engine(config, storage)

        assert _weight_of(engine.pipeline.scorers, GenreMatchScorer) == 4.0
        assert storage.settings.list() == {}


class TestMinRatingFallback:
    def test_db_min_rating_is_effective(self, storage: StorageManager) -> None:
        storage.settings.set("recommendations.min_rating_for_preference", 2)

        engine = _build_engine({}, storage)

        assert engine.preference_analyzer.min_rating == 2


class TestCountFallback:
    def test_db_counts_are_effective(self, storage: StorageManager) -> None:
        storage.settings.set("recommendations.default_count", 8)
        storage.settings.set("recommendations.max_count", 30)
        config: dict[str, Any] = {}

        migrate_config_settings(config, storage)
        rec_config = _get_recommendations_config(config)

        assert rec_config.default_count == 8
        assert rec_config.max_count == 30


class TestCustomPreferenceWeightRegression:
    """Bug reported: setting ``recommendations.scorer_weights.custom_preference`` to
    0 left horror candidates penalised, and a per-user override of the same key
    did nothing either."""

    _RULES = ["avoid horror"]

    @staticmethod
    def _seed_library(storage: StorageManager) -> None:
        for item_id, title, status, rating, genre in (
            ("liked", "Gone Girl", ConsumptionStatus.COMPLETED, 5, "Thriller"),
            ("horror", "Blood Chapel", ConsumptionStatus.UNREAD, None, "Horror"),
            (
                "thriller",
                "The Silent Patient",
                ConsumptionStatus.UNREAD,
                None,
                "Thriller",
            ),
        ):
            storage.save_content_item(
                ContentItem(
                    id=item_id,
                    title=title,
                    content_type=ContentType.BOOK,
                    status=status,
                    rating=rating,
                    metadata={"genre": genre},
                )
            )

    @staticmethod
    def _scores(engine: Any, config: UserPreferenceConfig) -> dict[str, float]:
        return {
            rec.item.title: rec.score
            for rec in engine.generate_recommendations(
                content_type=ContentType.BOOK,
                count=5,
                user_preference_config=config,
            )
        }

    def test_global_custom_preference_weight_is_effective_regression(
        self, storage: StorageManager
    ) -> None:
        self._seed_library(storage)
        storage.settings.set("recommendations.scorer_weights.custom_preference", 0.0)
        engine = _build_engine({}, storage)

        with_rule = self._scores(engine, UserPreferenceConfig(custom_rules=self._RULES))
        without_rule = self._scores(engine, UserPreferenceConfig())

        assert with_rule == without_rule

    def test_per_user_custom_preference_override_is_effective_regression(
        self, storage: StorageManager
    ) -> None:
        self._seed_library(storage)
        engine = _build_engine({}, storage)

        with_rule = self._scores(
            engine,
            UserPreferenceConfig(
                custom_rules=self._RULES, scorer_weights={"custom_preference": 0.0}
            ),
        )
        without_rule = self._scores(engine, UserPreferenceConfig())

        assert with_rule == without_rule

    def test_the_rule_still_bites_at_its_default_weight(
        self, storage: StorageManager
    ) -> None:
        """Without this, both zero-weight assertions above would pass on a scorer
        that never did anything."""
        self._seed_library(storage)
        engine = _build_engine({}, storage)

        with_rule = self._scores(engine, UserPreferenceConfig(custom_rules=self._RULES))
        without_rule = self._scores(engine, UserPreferenceConfig())

        assert with_rule["Blood Chapel"] < without_rule["Blood Chapel"]


class TestConstDefaultFallback:
    def test_registry_const_defaults_used_without_db_or_yaml(
        self, storage: StorageManager
    ) -> None:
        config: dict[str, Any] = {}

        engine = _build_engine(config, storage)
        rec_config = _get_recommendations_config(config)

        assert _weight_of(engine.pipeline.scorers, GenreMatchScorer) == _const_default(
            "recommendations.scorer_weights.genre_match"
        )
        assert engine.preference_analyzer.min_rating == _const_default(
            "recommendations.min_rating_for_preference"
        )
        assert rec_config.default_count == _const_default(
            "recommendations.default_count"
        )
        assert rec_config.max_count == _const_default("recommendations.max_count")
        assert storage.settings.list() == {}


class TestLiveSettingsApply:
    """Bug reported: a scorer weight (or the minimum liked rating) saved on the
    Settings page persisted, echoed the new value back and badged no restart, but
    regenerating returned identical recommendations until the process was restarted."""

    @staticmethod
    def _recommendations(client: TestClient) -> list[dict[str, Any]]:
        response = client.get(
            "/api/recommendations",
            params={"type": "book", "count": 5},
        )
        assert response.status_code == 200, response.text
        return list(response.json())

    def test_scorer_weight_change_reaches_running_engine_regression(
        self, booted_app: TestClient
    ) -> None:
        before = _weight_of(_running_engine().pipeline.scorers, GenreMatchScorer)
        assert before == _const_default(_GENRE_WEIGHT_KEY)

        _apply_running({_GENRE_WEIGHT_KEY: 0.0})

        assert _weight_of(_running_engine().pipeline.scorers, GenreMatchScorer) == 0.0

    def test_min_rating_change_reaches_running_analyzer_regression(
        self, booted_app: TestClient
    ) -> None:
        key = "recommendations.min_rating_for_preference"
        assert _running_engine().preference_analyzer.min_rating == _const_default(key)

        _apply_running({key: 2})

        assert _running_engine().preference_analyzer.min_rating == 2

    def test_settings_write_changes_the_next_api_response_regression(
        self, booted_app: TestClient
    ) -> None:
        """The end-to-end shape of the report: the user moves a slider, saves, and
        regenerates."""
        storage = get_storage()
        assert storage is not None, "app_state is not booted"
        _seed_split_taste_library(storage)

        before = self._recommendations(booted_app)
        response = booted_app.put(
            "/api/settings", json={"updates": {_GENRE_WEIGHT_KEY: 0.0}}
        )
        assert response.status_code == 200, response.text
        after = self._recommendations(booted_app)

        assert before[0]["title"] == "Neon Divide"
        assert after[0]["title"] == "Quiet Harvest"

        assert {rec["title"]: rec["score"] for rec in before} != {
            rec["title"]: rec["score"] for rec in after
        }
        assert [rec["score_breakdown"] for rec in before] != [
            rec["score_breakdown"] for rec in after
        ]


class TestUnusableRunningConfig:
    """Each of these is a hand-edited ``config.yaml`` away, and each is guarded in
    the engine because ``dict.get``'s default does not catch it: the key is
    present, its value is simply not what the resolver can read."""

    @staticmethod
    def _engine_reading(config: dict[str, Any]) -> Any:
        return RecommendationEngine(
            storage_manager=make_storage_mock(),
            min_rating=4,
            config_provider=lambda: config,
        )

    def test_a_childless_header_falls_back_to_the_baseline(self) -> None:
        """``recommendations:`` with nothing under it parses to ``None``."""
        engine = self._engine_reading({"recommendations": None})

        assert engine.preference_analyzer.min_rating == 4


class _WeightsWatchedMidIteration(dict[str, float]):
    """Handing control to *interrupt* after the first entry puts a settings write
    inside that iteration every run, rather than once in however many thousand
    requests land there by luck."""

    def __init__(
        self, weights: dict[str, float], interrupt: Callable[[], None]
    ) -> None:
        super().__init__(weights)
        self._interrupt = interrupt
        self._interrupted = False

    def items(self) -> Iterator[tuple[str, float]]:
        for index, entry in enumerate(super().items()):
            if index == 0 and not self._interrupted:
                self._interrupted = True
                self._interrupt()
            yield entry


def _apply_on_another_thread(
    config: dict[str, Any], storage: StorageManager, updates: dict[str, Any]
) -> None:
    raised: list[Exception] = []

    def write() -> None:
        try:
            apply_settings(config, storage, updates)
        except Exception as error:
            raised.append(error)

    worker = threading.Thread(target=write)
    worker.start()
    worker.join(timeout=10)

    assert not worker.is_alive(), "the settings write never finished"
    assert not raised, f"the settings write raised {raised[0]!r}"


class TestSettingsWriteDuringScoringRegression:
    """Bug: the Settings page wrote into the same ``recommendations`` mapping the
    engine was reading."""

    @staticmethod
    def _ranked_titles(engine: Any) -> list[str]:
        return [
            rec.item.title
            for rec in engine.generate_recommendations(
                content_type=ContentType.BOOK, count=5
            )
        ]

    @staticmethod
    def _config_written_mid_read(
        interrupt: Callable[[], None],
    ) -> dict[str, Any]:
        """``creator_match`` is declared first so that ``genre_match`` — the weight
        the writes below move — is still unread when the write lands."""
        return {
            "recommendations": {
                "scorer_weights": _WeightsWatchedMidIteration(
                    {
                        "creator_match": _const_default(_CREATOR_WEIGHT_KEY),
                        "genre_match": _const_default(_GENRE_WEIGHT_KEY),
                    },
                    interrupt,
                )
            }
        }

    def test_a_weight_inserted_mid_read_does_not_break_the_run_regression(
        self, storage: StorageManager
    ) -> None:
        _seed_split_taste_library(storage)
        config: dict[str, Any] = {}

        def interrupt() -> None:
            _apply_on_another_thread(config, storage, {_ADAPTATION_WEIGHT_KEY: 0.5})

        config.update(self._config_written_mid_read(interrupt))
        engine = create_recommendation_engine(
            storage_manager=storage,
            config=config,
        )

        assert self._ranked_titles(engine) == ["Neon Divide", "Quiet Harvest"]
        assert config["recommendations"]["scorer_weights"]["adaptation"] == 0.5

    def test_a_weight_changed_mid_read_does_not_score_a_mixture_regression(
        self, storage: StorageManager
    ) -> None:
        _seed_split_taste_library(storage)
        config: dict[str, Any] = {}

        def interrupt() -> None:
            _apply_on_another_thread(config, storage, {_GENRE_WEIGHT_KEY: 0.0})

        config.update(self._config_written_mid_read(interrupt))
        engine = create_recommendation_engine(
            storage_manager=storage,
            config=config,
        )

        during_the_write = self._ranked_titles(engine)
        after_the_write = self._ranked_titles(engine)

        assert during_the_write == ["Neon Divide", "Quiet Harvest"]
        assert after_the_write == ["Quiet Harvest", "Neon Divide"]

    def test_apply_settings_leaves_the_mapping_a_reader_holds_alone(
        self, storage: StorageManager
    ) -> None:
        """The mechanism behind both: the old mapping is replaced, not edited."""
        held = {"genre_match": 3.0}
        config: dict[str, Any] = {"recommendations": {"scorer_weights": held}}

        apply_settings(config, storage, {_ADAPTATION_WEIGHT_KEY: 0.5})

        assert held == {"genre_match": 3.0}
        assert config["recommendations"]["scorer_weights"] == {
            "genre_match": 3.0,
            "adaptation": 0.5,
        }
