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
"""

from pathlib import Path
from typing import Any

import pytest

from src.cli.config import create_recommendation_engine
from src.recommendations.scorers import (
    CreatorMatchScorer,
    GenreMatchScorer,
    Scorer,
    build_scorers_with_overrides,
)
from src.settings.metadata import get_entry
from src.storage.manager import StorageManager
from src.storage.settings_migration import migrate_config_settings
from src.web.api import _get_recommendations_config


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
