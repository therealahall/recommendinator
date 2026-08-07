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

from collections.abc import Iterator
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from src.cli.config import create_recommendation_engine
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.user_preferences import UserPreferenceConfig
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
    """Assemble the config (const<YAML<DB) then build the engine, as boot does.

    Uses the non-AI path (no embedding generator) so the pipeline holds exactly
    the config-driven scorers with no ``SemanticSimilarityScorer`` appended.
    """
    migrate_config_settings(config, storage)
    return create_recommendation_engine(
        storage_manager=storage,
        embedding_generator=None,
        recommendation_generator=None,
        config=config,
    )


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
                }
            }
        )
    )
    try:
        yield TestClient(create_app(config_path))
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
            rec["item"].title: rec["score"]
            for rec in engine.generate_recommendations(
                content_type=ContentType.BOOK,
                count=5,
                use_llm=False,
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
    Fix: the engine holds that same dict by reference and re-resolves both knobs
    on every read, so the next ``generate_recommendations`` call sees the change.
    """

    _GENRE_WEIGHT_KEY = "recommendations.scorer_weights.genre_match"

    @staticmethod
    def _seed_library(storage: StorageManager) -> None:
        """Two rated books and two candidates that swap genre for creator.

        The liked genre and the liked author are deliberately split across the
        two candidates, so ``genre_match`` alone decides the order: at its
        default weight the sci-fi book by the disliked author leads, and at
        weight 0 the poetry book by the liked author takes over.
        """
        completed = ConsumptionStatus.COMPLETED
        unread = ConsumptionStatus.UNREAD
        for item_id, title, author, genre, status, rating in (
            ("liked", "Ash Harbour", "Vera Lang", "Science Fiction", completed, 5),
            ("disliked", "Grey Ledger", "Miles Crane", "Poetry", completed, 1),
            (
                "genre-led",
                "Neon Divide",
                "Miles Crane",
                "Science Fiction",
                unread,
                None,
            ),
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

    @staticmethod
    def _recommendations(client: TestClient) -> list[dict[str, Any]]:
        """Return the book recommendations the API serves right now."""
        response = client.get(
            "/api/recommendations",
            params={"type": "book", "count": 5, "use_llm": False},
        )
        assert response.status_code == 200, response.text
        return list(response.json())

    def test_scorer_weight_change_reaches_running_engine_regression(
        self, booted_app: TestClient
    ) -> None:
        """Zeroing the genre weight after boot reaches the running pipeline."""
        before = _weight_of(_running_engine().pipeline.scorers, GenreMatchScorer)
        assert before == _const_default(self._GENRE_WEIGHT_KEY)

        _apply_running({self._GENRE_WEIGHT_KEY: 0.0})

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
        self._seed_library(storage)

        before = self._recommendations(booted_app)
        response = booted_app.put(
            "/api/settings", json={"updates": {self._GENRE_WEIGHT_KEY: 0.0}}
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
