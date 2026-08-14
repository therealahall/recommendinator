"""Regression tests: global recommendation settings are the new-user fallback.

The global ``recommendations.*`` settings (``scorer_weights.*``,
``min_rating_for_preference``, ``default_count``, ``max_count``) are DB-backed
and edited on the Settings page. They are the admin-configurable defaults a user
falls back to when they have **not** set their own per-user preference (a fresh
install / new user with no override).

This module locks the fallback chain end to end. On boot,
``migrate_config_settings`` assembles the effective config with precedence

    registry const default < config.yaml < database settings

and mutates ``config["recommendations"]`` in place. ``create_recommendation_engine``
then reads that merged section to build the engine's pipeline scorer weights and
``min_rating``, and the API's ``_get_recommendations_config`` reads it for the
counts. A per-user ``UserPreferenceConfig.scorer_weights`` override wins per-key
(applied by ``build_scorers_with_overrides``); an unset key keeps the global
default. There is no per-user field for ``min_rating`` or the counts — those
resolve purely from the assembled global.

``TestLiveSettingsApply`` locks the other half of the promise: those same leaves
carry no ``restart_required``, so a change made after boot must reach the
already-running engine.
"""

import copy
import threading
from collections.abc import Callable, Iterator
from dataclasses import fields
from pathlib import Path
from typing import Any
from unittest.mock import Mock

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
from src.settings.metadata import entries_by_section, get_entry
from src.settings.service import apply_settings
from src.storage.manager import StorageManager
from src.storage.settings_migration import migrate_config_settings
from src.web.api import _get_recommendations_config
from src.web.app import create_app
from src.web.state import app_state, get_config, get_engine, get_storage
from tests.factories import authenticated_client

_GENRE_WEIGHT_KEY = "recommendations.scorer_weights.genre_match"
_CREATOR_WEIGHT_KEY = "recommendations.scorer_weights.creator_match"
_ADAPTATION_WEIGHT_KEY = "recommendations.scorer_weights.adaptation"
_MIN_RATING_KEY = "recommendations.min_rating_for_preference"


def _const_default(key: str) -> Any:
    """Return a registry leaf's hardcoded const default (the ultimate fallback)."""
    entry = get_entry(key)
    assert entry is not None, f"{key!r} is not a registered setting"
    return entry.default


def _weight_of(scorers: list[Scorer], scorer_type: type[Scorer]) -> float:
    """Return the weight of the single ``scorer_type`` instance in ``scorers``."""
    matches = [s.weight for s in scorers if type(s) is scorer_type]
    assert len(matches) == 1, f"expected exactly one {scorer_type.__name__}"
    return matches[0]


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    """A StorageManager backed by a temp SQLite DB (empty settings table)."""
    return StorageManager(sqlite_path=tmp_path / "test.db")


def _build_engine(config: dict[str, Any], storage: StorageManager) -> Any:
    """Assemble the config (const<YAML<DB) then build the engine, as boot does."""
    migrate_config_settings(config, storage)
    return create_recommendation_engine(storage_manager=storage, config=config)


@pytest.fixture()
def booted_app(tmp_path: Path) -> Iterator[TestClient]:
    """Boot a real app on a tmp_path database and yield its test client.

    Goes through ``create_app`` rather than ``_build_engine`` so the engine
    under test is the one ``get_engine()`` hands the API, wired to the running
    config dict the settings service mutates. The module-level ``app_state``
    is restored afterwards so nothing leaks into other tests.
    """
    saved = {field.name: getattr(app_state, field.name) for field in fields(app_state)}
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "storage": {
                    "database_path": str(tmp_path / "recommendations.db"),
                    "vector_db_path": str(tmp_path / "chroma_db"),
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
    """Return the engine the API would use for the next request."""
    engine = get_engine()
    assert engine is not None, "no engine in app_state"
    return engine


def _apply_running(updates: dict[str, Any]) -> None:
    """Apply *updates* through the settings service against the running app."""
    config = get_config()
    storage = get_storage()
    assert config is not None and storage is not None, "app_state is not booted"
    apply_settings(config, storage, updates)


def _seed_split_taste_library(storage: StorageManager) -> None:
    """Two rated books and two candidates that swap genre for creator.

    The liked genre and the liked author are deliberately split across the two
    candidates, so ``genre_match`` alone decides the order: at its default
    weight the sci-fi book by the disliked author leads ("Neon Divide"), and at
    weight 0 the poetry book by the liked author takes over ("Quiet Harvest").
    """
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
        """A DB-set global scorer weight becomes the engine's effective weight.

        With an empty per-user config the engine uses ``pipeline.scorers``
        directly, so the pipeline weight *is* the effective weight for a new
        user. It must equal the DB value, not the class/const default.
        """
        storage.set_setting("recommendations.scorer_weights.genre_match", 7.0)

        engine = _build_engine({}, storage)

        assert _weight_of(engine.pipeline.scorers, GenreMatchScorer) == 7.0

    def test_user_override_wins_per_key_over_global(
        self, storage: StorageManager
    ) -> None:
        """A user's sparse override wins for its key; unset keys keep the global.

        This exercises the exact engine code path for a user *with* overrides:
        ``build_scorers_with_overrides`` clones only the overridden scorers.
        """
        storage.set_setting("recommendations.scorer_weights.genre_match", 7.0)
        storage.set_setting("recommendations.scorer_weights.creator_match", 6.0)

        engine = _build_engine({}, storage)

        # A new user overrides only genre_match; creator_match is left unset.
        overridden = build_scorers_with_overrides(
            engine.pipeline.scorers, {"genre_match": 3.0}
        )

        assert _weight_of(overridden, GenreMatchScorer) == 3.0
        # The unset key falls back to the global (DB) default, not the class one.
        assert _weight_of(overridden, CreatorMatchScorer) == 6.0

    @pytest.mark.parametrize("global_weight", [1.0, 9.0])
    def test_changing_global_default_shifts_baseline_for_new_user(
        self, storage: StorageManager, global_weight: float
    ) -> None:
        """Editing the global default changes the baseline a new user inherits.

        A new user has an empty ``scorer_weights``; applying that empty override
        leaves the engine's global weight untouched, so whatever the admin set
        in the DB is exactly what the new user gets.
        """
        storage.set_setting("recommendations.scorer_weights.genre_match", global_weight)

        engine = _build_engine({}, storage)
        new_user_scorers = build_scorers_with_overrides(engine.pipeline.scorers, {})

        assert _weight_of(new_user_scorers, GenreMatchScorer) == global_weight

    def test_yaml_scorer_weight_used_when_db_absent(
        self, storage: StorageManager
    ) -> None:
        """With no DB row, the YAML value (over the const default) is effective."""
        config: dict[str, Any] = {
            "recommendations": {"scorer_weights": {"genre_match": 4.0}}
        }

        engine = _build_engine(config, storage)

        assert _weight_of(engine.pipeline.scorers, GenreMatchScorer) == 4.0
        assert storage.list_settings() == {}


class TestMinRatingFallback:
    """``min_rating_for_preference`` resolves from the assembled global."""

    def test_db_min_rating_is_effective(self, storage: StorageManager) -> None:
        """A DB-set global min rating flows into the engine's analyzer."""
        storage.set_setting("recommendations.min_rating_for_preference", 2)

        engine = _build_engine({}, storage)

        assert engine.preference_analyzer.min_rating == 2

    def test_yaml_min_rating_used_when_db_absent(self, storage: StorageManager) -> None:
        """With no DB row, the YAML value (over the const default) is used."""
        config: dict[str, Any] = {"recommendations": {"min_rating_for_preference": 3}}

        engine = _build_engine(config, storage)

        assert engine.preference_analyzer.min_rating == 3


class TestCountFallback:
    """``default_count`` / ``max_count`` resolve from the assembled global."""

    def test_db_counts_are_effective(self, storage: StorageManager) -> None:
        """DB-set counts flow through the merged config to the API reader."""
        storage.set_setting("recommendations.default_count", 8)
        storage.set_setting("recommendations.max_count", 30)
        config: dict[str, Any] = {}

        migrate_config_settings(config, storage)
        rec_config = _get_recommendations_config(config)

        assert rec_config.default_count == 8
        assert rec_config.max_count == 30

    def test_db_counts_win_over_yaml(self, storage: StorageManager) -> None:
        """A DB count overrides the YAML value for the same leaf."""
        storage.set_setting("recommendations.default_count", 8)
        config: dict[str, Any] = {"recommendations": {"default_count": 12}}

        migrate_config_settings(config, storage)

        assert _get_recommendations_config(config).default_count == 8


class TestCustomPreferenceWeightRegression:
    """Bug reported: the custom-rule scorer ignored both of its weight knobs.

    Bug reported: setting ``recommendations.scorer_weights.custom_preference``
    to 0 left horror candidates penalised, and a per-user override of the same
    key did nothing either.
    Root cause: the engine appended ``CustomPreferenceScorer`` at its class
    default weight *after* the per-user override pass had already run, so
    neither the global setting nor the override could reach it.
    Fix: the scorer is built at the configured weight and joins the list before
    the override pass, so it resolves through the same chain as every other
    scorer. A weight of 0 therefore disables it, which is what the docs
    promise.
    """

    _RULES = ["avoid horror"]

    @staticmethod
    def _seed_library(storage: StorageManager) -> None:
        """A liked thriller, plus a horror and a thriller candidate."""
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
        """Title -> emitted score for a book run under *config*."""
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
        """A global weight of 0 makes the rule inert, matching no rules at all."""
        self._seed_library(storage)
        storage.set_setting("recommendations.scorer_weights.custom_preference", 0.0)
        engine = _build_engine({}, storage)

        with_rule = self._scores(engine, UserPreferenceConfig(custom_rules=self._RULES))
        without_rule = self._scores(engine, UserPreferenceConfig())

        assert with_rule == without_rule

    def test_per_user_custom_preference_override_is_effective_regression(
        self, storage: StorageManager
    ) -> None:
        """A per-user 0 disables it too, with the global left at its default."""
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
        """Positive control: at the default weight the rule demotes horror.

        Without this, both zero-weight assertions above would pass on a scorer
        that never did anything.
        """
        self._seed_library(storage)
        engine = _build_engine({}, storage)

        with_rule = self._scores(engine, UserPreferenceConfig(custom_rules=self._RULES))
        without_rule = self._scores(engine, UserPreferenceConfig())

        assert with_rule["Blood Chapel"] < without_rule["Blood Chapel"]


class TestConstDefaultFallback:
    """With neither DB nor YAML supplying a knob, the const default is used."""

    def test_registry_const_defaults_used_without_db_or_yaml(
        self, storage: StorageManager
    ) -> None:
        """A fresh install (empty DB, empty YAML) resolves to registry consts."""
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
        # No writes on boot — the fallback comes from consts, not a seeded row.
        assert storage.list_settings() == {}


class TestLiveSettingsApply:
    """Bug reported: Settings-page recommendation changes needed a restart.

    Bug reported: a scorer weight (or the minimum liked rating) saved on the
    Settings page persisted, echoed the new value back and badged no restart,
    but regenerating returned identical recommendations until the process was
    restarted.
    Root cause: ``create_recommendation_engine`` read ``scorer_weights`` and
    ``min_rating_for_preference`` once at boot and froze them into the scorer
    instances and the ``PreferenceAnalyzer``, while ``apply_settings`` wrote the
    DB row and live-applied only into the running config dict.
    Fix: the engine resolves the running config on every read, so the next
    ``generate_recommendations`` call sees the change.
    """

    @staticmethod
    def _recommendations(client: TestClient) -> list[dict[str, Any]]:
        """Return the book recommendations the API serves right now."""
        response = client.get(
            "/api/recommendations",
            params={"type": "book", "count": 5},
        )
        assert response.status_code == 200, response.text
        return list(response.json())

    def test_scorer_weight_change_reaches_running_engine_regression(
        self, booted_app: TestClient
    ) -> None:
        """Zeroing the genre weight after boot reaches the running pipeline."""
        before = _weight_of(_running_engine().pipeline.scorers, GenreMatchScorer)
        assert before == _const_default(_GENRE_WEIGHT_KEY)

        _apply_running({_GENRE_WEIGHT_KEY: 0.0})

        assert _weight_of(_running_engine().pipeline.scorers, GenreMatchScorer) == 0.0

    def test_min_rating_change_reaches_running_analyzer_regression(
        self, booted_app: TestClient
    ) -> None:
        """Lowering the minimum liked rating reaches the running analyzer."""
        key = "recommendations.min_rating_for_preference"
        assert _running_engine().preference_analyzer.min_rating == _const_default(key)

        _apply_running({key: 2})

        assert _running_engine().preference_analyzer.min_rating == 2

    def test_settings_write_changes_the_next_api_response_regression(
        self, booted_app: TestClient
    ) -> None:
        """Two GETs straddling a weight-zeroing PUT return different scoring.

        The end-to-end shape of the report: the user moves a slider, saves, and
        regenerates. Before the fix both GETs returned the boot-time scoring.
        """
        storage = get_storage()
        assert storage is not None, "app_state is not booted"
        _seed_split_taste_library(storage)

        before = self._recommendations(booted_app)
        response = booted_app.put(
            "/api/settings", json={"updates": {_GENRE_WEIGHT_KEY: 0.0}}
        )
        assert response.status_code == 200, response.text
        after = self._recommendations(booted_app)

        # Positive control on the seeded flip: without it the inequalities
        # below could pass on noise instead of on the weight actually applying.
        assert before[0]["title"] == "Neon Divide"
        assert after[0]["title"] == "Quiet Harvest"

        # Zeroing a weight re-normalises every aggregate and re-orders the list,
        # so both the per-title scores and the breakdown sequence must move.
        assert {rec["title"]: rec["score"] for rec in before} != {
            rec["title"]: rec["score"] for rec in after
        }
        assert [rec["score_breakdown"] for rec in before] != [
            rec["score_breakdown"] for rec in after
        ]

    def test_no_recommendations_setting_requires_a_restart(self) -> None:
        """The registry must keep every ``recommendations.*`` leaf live-applied.

        Marking one ``restart_required`` would stop ``apply_settings`` writing
        it into the running config, silently re-freezing the engine.
        """
        restart_gated = [
            entry.key
            for entries in entries_by_section().values()
            for entry in entries
            if entry.key.startswith("recommendations.") and entry.restart_required
        ]

        assert restart_gated == []


class TestUnusableRunningConfig:
    """What the engine does with a ``recommendations`` section it cannot use.

    Each of these is a hand-edited ``config.yaml`` away, and each is guarded in
    the engine because ``dict.get``'s default does not catch it: the key is
    present, its value is simply not what the resolver can read.
    """

    @staticmethod
    def _engine_reading(config: dict[str, Any]) -> Any:
        """An engine whose running config is *config* and nothing else."""
        return RecommendationEngine(
            storage_manager=Mock(spec=StorageManager),
            min_rating=4,
            config_provider=lambda: config,
        )

    def test_a_childless_header_falls_back_to_the_baseline(self) -> None:
        """``recommendations:`` with nothing under it parses to ``None``."""
        engine = self._engine_reading({"recommendations": None})

        assert engine.preference_analyzer.min_rating == 4

    def test_non_mapping_weights_fall_back_to_the_baseline(self) -> None:
        """``scorer_weights`` written as a list weights nothing."""
        engine = self._engine_reading(
            {"recommendations": {"scorer_weights": ["genre_match"]}}
        )

        assert (
            _weight_of(engine.pipeline.scorers, GenreMatchScorer)
            == GenreMatchScorer().weight
        )

    def test_a_weight_named_after_no_scorer_is_dropped(self) -> None:
        """A typo weights nothing and leaves the scorers it sits beside alone."""
        engine = self._engine_reading(
            {
                "recommendations": {
                    "scorer_weights": {"genre_match": 7.0, "genre_matsh": 9.0}
                }
            }
        )

        assert _weight_of(engine.pipeline.scorers, GenreMatchScorer) == 7.0
        assert 9.0 not in [scorer.weight for scorer in engine.pipeline.scorers]


class _WeightsWatchedMidIteration(dict[str, float]):
    """A ``scorer_weights`` mapping that runs *interrupt* mid-iteration.

    ``RecommendationEngine._configured_weights`` iterates this mapping to
    resolve the running weights. Handing control to *interrupt* after the first
    entry puts a settings write inside that iteration every run, rather than
    once in however many thousand requests land there by luck.
    """

    def __init__(
        self, weights: dict[str, float], interrupt: Callable[[], None]
    ) -> None:
        super().__init__(weights)
        self._interrupt = interrupt
        self._interrupted = False

    def items(self) -> Iterator[tuple[str, float]]:
        """Yield the entries, interrupting once after the first."""
        for index, entry in enumerate(super().items()):
            if index == 0 and not self._interrupted:
                self._interrupted = True
                self._interrupt()
            yield entry


class _StorageThatReadsMidSave(StorageManager):
    """Real storage that runs :attr:`interrupt` just before persisting *key*.

    ``apply_settings`` walks the keys of one save, so the moment before the
    last of them is written is the window in which a reader could see the
    earlier keys applied and that one not. Hooking it puts a request in that
    window on every run rather than once in however many thousand land there
    by luck. The reader runs on this thread because what it observes is the
    state of the config dict at that instant, which does not depend on who is
    looking.
    """

    def __init__(self, sqlite_path: Path, key: str) -> None:
        """Build storage at *sqlite_path* hooked on writes of *key*."""
        super().__init__(sqlite_path=sqlite_path)
        self._hooked_key = key
        self.interrupt: Callable[[], None] = lambda: None

    def set_setting(self, key: str, value: Any) -> None:
        """Persist *key*, running the interrupt first when it is the hooked one."""
        if key == self._hooked_key:
            self.interrupt()
        super().set_setting(key, value)


def _apply_on_another_thread(
    config: dict[str, Any], storage: StorageManager, updates: dict[str, Any]
) -> None:
    """Run one settings write to completion on a worker thread."""
    raised: list[Exception] = []

    def write() -> None:
        try:
            apply_settings(config, storage, updates)
        except Exception as error:
            # A thread's exception dies with it, so it is carried out by hand.
            raised.append(error)

    worker = threading.Thread(target=write)
    worker.start()
    worker.join(timeout=10)

    assert not worker.is_alive(), "the settings write never finished"
    assert not raised, f"the settings write raised {raised[0]!r}"


class TestSettingsWriteDuringScoringRegression:
    """Bug: a request scoring while a setting was saved used a broken config.

    Bug: the Settings page wrote into the same ``recommendations`` mapping the
    engine was reading. Scoring does not run on the event loop — the streaming
    endpoint hands Starlette a synchronous generator, which it runs in a
    threadpool worker — so the write and the read genuinely overlap. Moving the
    adaptation slider on a config predating it inserts a key, and inserting
    into a dict another thread is iterating raises ``RuntimeError: dictionary
    changed size during iteration``, surfacing as a 500. Moving any other
    slider replaced a value mid-iteration instead, and the request quietly
    returned a list ranked by a configuration nobody ever saved.
    Root cause: ``_apply_live`` wrote through ``set_leaf``, which mutates the
    nested mapping in place, and the engine held the config dict by reference.
    Two narrower windows survived that first fix: the engine still read the
    config two or three times per request, and ``apply_settings`` still
    published one key at a time, so a save of several keys was several swaps.
    Fix: ``_apply_live`` publishes a whole save through
    ``set_leaves_atomically``, one store per section, and the engine resolves
    every configured knob from a single read taken at the start of the request.
    A reader therefore always finishes on the configuration it started with.
    The tests here cover the windows a run can actually observe. That the
    publish is one store per section is pinned in
    ``tests/utils/test_dotted_path.py``, which watches the stores themselves.
    """

    @staticmethod
    def _ranked_titles(engine: Any) -> list[str]:
        """The book titles the engine ranks right now, best first."""
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
        """A running config whose weights run *interrupt* while being read.

        ``creator_match`` is declared first so that ``genre_match`` — the
        weight the writes below move — is still unread when the write lands.
        """
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
        """A weight the config has never carried can be added under a reader."""
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
        """The run finishes on its own weights, and the next one sees the new."""
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

    def test_one_run_reads_the_running_config_once_regression(
        self, storage: StorageManager
    ) -> None:
        """A run resolves every configured knob from a single read.

        Bug: ``generate_recommendations`` asked the provider once for
        ``min_rating_for_preference``, again for the pipeline weights and
        again for the custom-rule weight. A save landing between two of those
        reads gave the request one leaf from before it and another from
        after, which is a configuration nobody ever saved.
        Fix: the request resolves all three from one read and uses that
        snapshot throughout, so the count below *is* the guarantee.
        """
        _seed_split_taste_library(storage)
        config: dict[str, Any] = {}
        reads: list[dict[str, Any]] = []

        def running_config() -> dict[str, Any]:
            reads.append(config)
            return config

        engine = create_recommendation_engine(
            storage_manager=storage,
            config=config,
            config_provider=running_config,
        )
        reads.clear()

        recommendations = engine.generate_recommendations(
            content_type=ContentType.BOOK,
            count=5,
            # Custom rules put the third read, the custom-rule weight, on the
            # path as well.
            user_preference_config=UserPreferenceConfig(custom_rules=["avoid poetry"]),
        )

        assert [rec.item.title for rec in recommendations] == [
            "Neon Divide",
            "Quiet Harvest",
        ]
        assert len(reads) == 1

    def test_a_two_key_save_reaches_a_run_whole_or_not_at_all_regression(
        self, tmp_path: Path
    ) -> None:
        """A run never ranks on half of one Settings-page save.

        Bug: ``apply_settings`` published each key on its own, so a run
        landing between two of the swaps of a single save read the new
        ``genre_match`` weight and the old ``min_rating_for_preference``.
        Fix: ``apply_settings`` persists every key before it publishes any of
        them, which is the part this hook can see. It fires mid-persist, so a
        run landing there ranks on the whole pre-save config. That the publish
        itself is one store per section rather than one per key is pinned by
        ``test_no_state_the_config_passes_through_holds_half_the_batch`` in
        ``tests/utils/test_dotted_path.py``, which watches the stores directly.
        """
        storage = _StorageThatReadsMidSave(tmp_path / "test.db", _MIN_RATING_KEY)
        _seed_split_taste_library(storage)
        config: dict[str, Any] = {}
        engine = create_recommendation_engine(
            storage_manager=storage,
            config=config,
        )
        mid_save: list[list[str]] = []
        storage.interrupt = lambda: mid_save.append(self._ranked_titles(engine))

        apply_settings(config, storage, {_GENRE_WEIGHT_KEY: 0.0, _MIN_RATING_KEY: 1})

        # The run landing inside the save ranks on the whole pre-save config,
        # not on the weight alone, which on this library flips the order.
        assert mid_save == [["Neon Divide", "Quiet Harvest"]]
        assert config["recommendations"]["scorer_weights"]["genre_match"] == 0.0
        assert config["recommendations"]["min_rating_for_preference"] == 1

        # And the next run ranks exactly as an engine booted on the saved
        # config does, so both keys reached it rather than only the first.
        # The literal order is spelled out as well as compared, because it is
        # where the margin lives: the raised rating floor holds this order,
        # while the weight alone would reverse it, as
        # test_a_weight_changed_mid_read_does_not_score_a_mixture_regression
        # shows. A comparison of two engines alone would pass on either.
        saved_from_the_start = create_recommendation_engine(
            storage_manager=storage,
            config=copy.deepcopy(config),
        )
        after_the_save = self._ranked_titles(engine)
        assert after_the_save == ["Neon Divide", "Quiet Harvest"]
        assert after_the_save == self._ranked_titles(saved_from_the_start)

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
