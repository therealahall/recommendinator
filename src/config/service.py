import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

import yaml

from src.ingestion.paths import configure_allowed_source_roots
from src.recommendations.engine import RecommendationEngine
from src.recommendations.scorers import (
    AdaptationScorer,
    ContentLengthScorer,
    ContinuationScorer,
    CreatorMatchScorer,
    GenreMatchScorer,
    RatingPatternScorer,
    Scorer,
    SeriesAffinityScorer,
    SeriesOrderScorer,
    TagOverlapScorer,
)
from src.settings.metadata import default_config, default_of
from src.storage.manager import StorageManager
from src.utils.deep_merge import deep_merge

logger = logging.getLogger(__name__)

# The uvicorn launcher (``src/web/main.py``) reads these to bind the socket
# before any database is open, so they are deliberately NOT settings-registry
# leaves — a database-backed value could never be honoured.
BOOTSTRAP_WEB_HOST = "127.0.0.1"
BOOTSTRAP_WEB_PORT = 18473
BOOTSTRAP_WEB_DEBUG = False


class BootstrapWebSettings(NamedTuple):
    host: str
    port: int
    debug: bool


def resolve_bootstrap_web(
    config: dict[str, Any], *, warn: bool = True
) -> BootstrapWebSettings:
    raw_web = config.get("web")
    # A `web:` header with no children parses to None, so guard the type rather
    # than relying on a .get default (which only fires on an ABSENT key).
    web_config = raw_web if isinstance(raw_web, dict) else {}

    # Every leaf is type-guarded, because these come from a hand-edited file and
    # `web_config` is an untyped dict — mypy cannot see a bad value here.
    raw_host = web_config.get("host")
    raw_port = web_config.get("port")

    # This app never serves TLS, so an accidental wildcard puts the API token on
    # the wire — every malformed shape must land on loopback.
    host = BOOTSTRAP_WEB_HOST
    if isinstance(raw_host, str) and raw_host:
        host = raw_host
    elif warn and "host" in web_config:
        # Silently substituting loopback for a value the operator explicitly set
        # leaves them with an unreachable instance and nothing in the logs to
        # debug from.
        logger.warning(
            "Ignoring unusable web.host %r in config.yaml; binding %s instead. "
            "host must be a non-empty string.",
            raw_host,
            BOOTSTRAP_WEB_HOST,
        )

    port = BOOTSTRAP_WEB_PORT
    # `not isinstance(raw_port, bool)`: bool subclasses int, and YAML resolves
    # `port: no`/`off` to False and `yes`/`on` to True — without it, False would
    # pass the int check and reach uvicorn as 0, binding a random ephemeral port
    # instead of falling back.
    if (
        isinstance(raw_port, int)
        and not isinstance(raw_port, bool)
        and 0 <= raw_port <= 65535
    ):
        port = raw_port
    elif warn and "port" in web_config:
        logger.warning(
            "Ignoring unusable web.port %r in config.yaml; binding %s instead. "
            "port must be an integer between 0 and 65535.",
            raw_port,
            BOOTSTRAP_WEB_PORT,
        )

    # Structured exactly like host and port above so all three read alike: start
    # from the constant, accept only a well-typed value, warn otherwise.
    raw_debug = web_config.get("debug")
    debug = BOOTSTRAP_WEB_DEBUG
    if isinstance(raw_debug, bool):
        debug = raw_debug
    elif warn and "debug" in web_config:
        # Same reasoning as host/port: `debug: "true"` (quoted) is a truthy
        # string that resolves to the default, which is right, but silently —
        # leaving an operator with no /docs, no reload, and no explanation.
        logger.warning(
            "Ignoring unusable web.debug %r in config.yaml; debug is %s. "
            "It must be an unquoted true or false.",
            raw_debug,
            BOOTSTRAP_WEB_DEBUG,
        )

    return BootstrapWebSettings(
        host=host,
        port=port,
        debug=debug,
    )


def _without_childless_headers(section: dict[str, Any]) -> dict[str, Any]:
    """A ``.get(name, {})`` default fires on an absent key, never on a present
    one holding None, and every config section is read that way."""
    pruned: dict[str, Any] = {}
    for key, value in section.items():
        if value is None:
            continue
        pruned[key] = (
            _without_childless_headers(value) if isinstance(value, dict) else value
        )
    return pruned


def resolve_config_path(config_path: Path | None = None) -> Path:
    if config_path is None:
        config_path = Path("config/config.yaml")

    if not config_path.exists():
        example_path = Path("config/example.yaml")
        if example_path.exists():
            return example_path
        raise FileNotFoundError(
            f"Config file not found: {config_path}. "
            "Copy config/example.yaml to config/config.yaml"
        )

    return config_path


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    resolved = resolve_config_path(config_path)

    with open(resolved, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    yaml_config = _without_childless_headers(raw) if isinstance(raw, dict) else {}

    # Layer the registry const defaults UNDER the parsed YAML (const default <
    # YAML) so a minimal, bootstrap-only config still yields a complete
    # effective config for every in-scope global section.
    config: dict[str, Any] = deep_merge(default_config(), yaml_config)

    # Never from the database overlay: the settings API must not be able to
    # widen what a file-based source is allowed to read.
    configure_allowed_source_roots(config)

    return config


def database_path(config: dict[str, Any]) -> Path:
    storage_config = config.get("storage", {})
    return Path(storage_config.get("database_path", "data/recommendations.db"))


def create_storage_manager(config: dict[str, Any]) -> StorageManager:
    return StorageManager(sqlite_path=database_path(config))


def cover_cache_dir(config: dict[str, Any]) -> Path:
    """Beside the database, so one bind mount carries the whole library state."""
    return database_path(config).parent / "covers"


def auto_enrich_enabled(config: dict[str, Any]) -> bool:
    """The one gate for every writer — web sync, web import, ``update`` and
    ``import`` — so a condition added here cannot reach some of them and not others.
    """
    enrichment_config = config.get("enrichment", {})
    return bool(
        enrichment_config.get("enabled", False)
        and enrichment_config.get("auto_enrich_on_sync", False)
    )


_SCORER_CONFIG_MAP: dict[str, type[Scorer]] = {
    "genre_match": GenreMatchScorer,
    "creator_match": CreatorMatchScorer,
    "tag_overlap": TagOverlapScorer,
    "series_order": SeriesOrderScorer,
    "rating_pattern": RatingPatternScorer,
    "content_length": ContentLengthScorer,
    "continuation": ContinuationScorer,
    "series_affinity": SeriesAffinityScorer,
    "adaptation": AdaptationScorer,
}


def build_scorers_from_config(config: dict[str, Any]) -> list[Scorer]:
    """Does **not** include :class:`CustomPreferenceScorer` — the engine builds it
    per call from the user's custom rules, and
    :func:`create_recommendation_engine` passes it its configured weight.
    """
    # `or {}`, not a .get default: a childless `recommendations:` header parses
    # to None, and the default only fires on an ABSENT key.
    rec_config = config.get("recommendations") or {}
    weight_overrides = rec_config.get("scorer_weights", {})

    scorers: list[Scorer] = []
    for config_key, scorer_class in _SCORER_CONFIG_MAP.items():
        if config_key in weight_overrides:
            scorers.append(scorer_class(weight=float(weight_overrides[config_key])))
        else:
            scorers.append(scorer_class())
    return scorers


def create_recommendation_engine(
    storage_manager: StorageManager,
    config: dict[str, Any],
    config_provider: Callable[[], dict[str, Any] | None] | None = None,
) -> RecommendationEngine:
    """The values read here seed the engine's baseline, which the running config
    then overlays on every call so a settings change reaches the next one
    without a restart.
    """
    rec_config = config.get("recommendations") or {}
    min_rating = rec_config.get(
        "min_rating_for_preference",
        default_of("recommendations.min_rating_for_preference"),
    )
    scorer_weights = rec_config.get("scorer_weights", {})
    custom_preference_weight = float(
        scorer_weights.get(
            "custom_preference",
            default_of("recommendations.scorer_weights.custom_preference"),
        )
    )

    scorers = build_scorers_from_config(config)

    return RecommendationEngine(
        storage_manager=storage_manager,
        min_rating=min_rating,
        scorers=scorers,
        custom_preference_weight=custom_preference_weight,
        config_provider=(
            config_provider if config_provider is not None else lambda: config
        ),
    )
