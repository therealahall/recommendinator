"""Application configuration loading, shared by the CLI and web entry points.

Also owns the ``BOOTSTRAP_WEB_*`` constants and :func:`resolve_bootstrap_web`,
the single resolver both ``src/web/main.py`` and ``src/web/app.py`` call for the
pre-database web bind settings.
"""

import logging
from pathlib import Path
from typing import Any, NamedTuple

import yaml

from src.llm.client import OllamaClient
from src.llm.embeddings import EmbeddingGenerator
from src.llm.recommendations import RecommendationGenerator
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

# Bootstrap web-server settings. The uvicorn launcher (``src/web/main.py``)
# reads these to bind the socket before any database is open, so they are
# deliberately NOT settings-registry leaves — a database-backed value could
# never be honoured, and offering one on the Settings page would promise a
# restart would apply it when it never could. They come from ``config.yaml`` or
# the ``--host`` / ``--port`` flags (which is how the Docker image sets them)
# and fall back to these constants.
BOOTSTRAP_WEB_HOST = "127.0.0.1"
BOOTSTRAP_WEB_PORT = 18473
BOOTSTRAP_WEB_DEBUG = False


class BootstrapWebSettings(NamedTuple):
    """The pre-database web bind settings."""

    host: str
    port: int
    debug: bool


def resolve_bootstrap_web(
    config: dict[str, Any], *, warn: bool = True
) -> BootstrapWebSettings:
    """Resolve the pre-database web bind settings from raw YAML.

    The single implementation for both readers — the uvicorn launcher
    (``src/web/main.py``) and ``create_app`` (``src/web/app.py``). They MUST
    agree: if the launcher reads ``debug`` as false while ``create_app`` reads it
    as true, ``/docs`` and ``/redoc`` open on a bind the launcher believes is
    closed. Keeping one implementation makes that agreement structural rather
    than a convention two modules have to remember.

    Every fallback is deliberate:

    * ``host`` requires a non-empty ``str``. A blank ``host:`` yields None or ""
      and both are wildcard binds at the socket layer; a non-string (an unquoted
      ``8080``, a list) would reach uvicorn and either fail or bind somewhere
      unexpected. This app has no authentication, so an accidental wildcard
      exposes the whole library — every malformed shape must land on loopback.
    * ``port`` checks ``isinstance`` rather than truthiness, because 0 is a
      meaningful value (ask the OS for an ephemeral port) rather than a blank.
    * ``debug`` requires a real ``bool`` and falls back to
      ``BOOTSTRAP_WEB_DEBUG`` (False) for anything else, warning as it does.
      Truthiness would fail OPEN on the one leaf that publishes ``/docs`` and
      ``/openapi.json``: a quoted ``debug: "false"`` is a truthy string.

    Args:
        config: Raw config dict, BEFORE any database overlay — these leaves are
            deliberately not settings-registry entries.
        warn: Whether to log about unusable values. Both readers run per launch,
            so leaving this on for both prints every diagnostic twice and makes
            it read like two separate faults. The launcher resolves first and
            owns the warnings; ``create_app`` passes False.

    Returns:
        The resolved host, port, and debug flag.
    """
    raw_web = config.get("web")
    # A `web:` header with no children parses to None, so guard the type rather
    # than relying on a .get default (which only fires on an ABSENT key).
    web_config = raw_web if isinstance(raw_web, dict) else {}

    # Every leaf is type-guarded, because these come from a hand-edited file and
    # `web_config` is an untyped dict — mypy cannot see a bad value here.
    raw_host = web_config.get("host")
    raw_port = web_config.get("port")

    host = BOOTSTRAP_WEB_HOST
    if isinstance(raw_host, str) and raw_host:
        host = raw_host
    elif warn and "host" in web_config:
        # Present but unusable — say so. Silently substituting loopback for a
        # value the operator explicitly set leaves them with an unreachable
        # instance and nothing in the logs to debug from. The `in` guard keeps
        # the common case (no `web:` section at all) quiet.
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
    # instead of falling back, with the banner printing "http://localhost:False".
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
    # from the constant, accept only a well-typed value, warn otherwise. The
    # constant is False, so this is equivalent to the previous `raw_debug is
    # True` — but it makes BOOTSTRAP_WEB_DEBUG load-bearing rather than a name
    # only the tests referenced, which meant flipping it changed nothing.
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


def resolve_config_path(config_path: Path | None = None) -> Path:
    """Resolve config file path with fallback to example.yaml.

    Args:
        config_path: Explicit config path, or None for default.

    Returns:
        Resolved config file path.

    Raises:
        FileNotFoundError: If no config file can be found.
    """
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
    """Load configuration from YAML file, layered over the const defaults.

    The registry const defaults (:func:`src.settings.metadata.default_config`)
    are deep-merged UNDER the parsed YAML, so every in-scope global section
    resolves fully even from a bootstrap-only config. This is the const layer
    only — the database overlay is applied later by
    :func:`src.storage.settings_migration.migrate_config_settings`, keeping the
    end-to-end precedence const default < YAML < DB.

    ``migrate_config_settings`` re-merges the same const defaults so it stays
    independently callable/testable without ``load_config`` first. The overlap
    is deliberate and idempotent (an identical merge changes nothing), not an
    accident — do not drop the const layer from either site.

    Args:
        config_path: Path to config file (default: config/config.yaml)

    Returns:
        Configuration dictionary with const defaults resolved for in-scope
        sections.

    Raises:
        FileNotFoundError: If config file doesn't exist
    """
    resolved = resolve_config_path(config_path)

    with open(resolved) as f:
        raw = yaml.safe_load(f)

    yaml_config = raw if isinstance(raw, dict) else {}

    # Layer the registry const defaults UNDER the parsed YAML (const default <
    # YAML) so a minimal, bootstrap-only config still yields a complete
    # effective config for every in-scope global section. This is the const
    # layer only; the database overlay (migrate_config_settings) still wins on
    # top of this at boot, preserving const default < YAML < DB.
    config: dict[str, Any] = deep_merge(default_config(), yaml_config)

    return config


def get_feature_flags(config: dict[str, Any] | None) -> dict[str, bool]:
    """Extract feature flags from config.

    Returns a dict with pre-computed flag combinations.

    Args:
        config: Configuration dictionary (or None).

    Returns:
        Dict with keys: ai_enabled, embeddings_enabled,
        llm_reasoning_enabled, use_embeddings.
    """
    features_config = config.get("features", {}) if config else {}
    ai_enabled: bool = features_config.get(
        "ai_enabled", default_of("features.ai_enabled")
    )
    embeddings_enabled: bool = features_config.get(
        "embeddings_enabled", default_of("features.embeddings_enabled")
    )
    return {
        "ai_enabled": ai_enabled,
        "embeddings_enabled": embeddings_enabled,
        "llm_reasoning_enabled": features_config.get(
            "llm_reasoning_enabled", default_of("features.llm_reasoning_enabled")
        ),
        "use_embeddings": ai_enabled and embeddings_enabled,
    }


def create_storage_manager(config: dict[str, Any]) -> StorageManager:
    """Create storage manager from config.

    Args:
        config: Configuration dictionary

    Returns:
        StorageManager instance
    """
    storage_config = config.get("storage", {})
    db_path = Path(storage_config.get("database_path", "data/recommendations.db"))
    vector_db_path = Path(storage_config.get("vector_db_path", "data/chroma_db"))
    flags = get_feature_flags(config)

    return StorageManager(
        sqlite_path=db_path,
        vector_db_path=vector_db_path,
        ai_enabled=flags["use_embeddings"],
    )


def create_llm_components(
    config: dict[str, Any],
) -> tuple[
    OllamaClient | None, EmbeddingGenerator | None, RecommendationGenerator | None
]:
    """Create LLM components from config.

    When AI features are disabled (features.ai_enabled: false), returns
    (None, None, None) to prevent any LLM/embedding operations.

    Args:
        config: Configuration dictionary

    Returns:
        Tuple of (OllamaClient, EmbeddingGenerator, RecommendationGenerator)
        or (None, None, None) if AI is disabled.
    """
    if not get_feature_flags(config)["ai_enabled"]:
        return None, None, None

    ollama_config = config.get("ollama", {})
    base_url = ollama_config.get("base_url", default_of("ollama.base_url"))
    model = ollama_config.get("model", default_of("ollama.model"))
    embedding_model = ollama_config.get(
        "embedding_model", default_of("ollama.embedding_model")
    )
    conversation_model = ollama_config.get(
        "conversation_model", default_of("ollama.conversation_model")
    )

    try:
        client = OllamaClient(
            base_url=base_url,
            default_model=model,
            embedding_model=embedding_model,
            conversation_model=conversation_model,
        )
    except ImportError:
        logger.warning(
            "AI features enabled in config but ollama is not installed. "
            "LLM features disabled. Install with: uv sync --locked --extra ai"
        )
        return None, None, None

    embedding_gen = EmbeddingGenerator(client)
    recommendation_gen = RecommendationGenerator(client)

    return client, embedding_gen, recommendation_gen


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
    """Build scorer instances with weights from config YAML.

    Reads ``config["recommendations"]["scorer_weights"]`` and creates scorer
    instances with the specified weights. Falls back to each scorer's class
    default weight for any scorer not listed in the config.

    Does **not** include :class:`SemanticSimilarityScorer` or
    :class:`CustomPreferenceScorer` — the engine builds those conditionally,
    on whether AI is enabled and on the user's custom rules respectively, and
    :func:`create_recommendation_engine` passes it their configured weights.

    Args:
        config: Full configuration dictionary.

    Returns:
        List of scorer instances (without SemanticSimilarityScorer).
    """
    rec_config = config.get("recommendations", {})
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
    embedding_generator: EmbeddingGenerator | None,
    recommendation_generator: RecommendationGenerator | None,
    config: dict[str, Any],
) -> RecommendationEngine:
    """Create recommendation engine from components and config.

    The values read here seed the engine's baseline; *config* is also handed to
    the engine by reference so a settings change live-applied into it reaches
    the next ``generate_recommendations`` call without a restart.

    Args:
        storage_manager: Storage manager instance
        embedding_generator: Embedding generator instance
        recommendation_generator: Recommendation generator instance
        config: The running configuration dictionary

    Returns:
        RecommendationEngine instance
    """
    rec_config = config.get("recommendations", {})
    min_rating = rec_config.get(
        "min_rating_for_preference",
        default_of("recommendations.min_rating_for_preference"),
    )
    scorer_weights = rec_config.get("scorer_weights", {})
    semantic_similarity_weight = float(
        scorer_weights.get(
            "semantic_similarity",
            default_of("recommendations.scorer_weights.semantic_similarity"),
        )
    )
    custom_preference_weight = float(
        scorer_weights.get(
            "custom_preference",
            default_of("recommendations.scorer_weights.custom_preference"),
        )
    )

    scorers = build_scorers_from_config(config)

    return RecommendationEngine(
        storage_manager=storage_manager,
        embedding_generator=embedding_generator,
        recommendation_generator=recommendation_generator,
        min_rating=min_rating,
        scorers=scorers,
        semantic_similarity_weight=semantic_similarity_weight,
        custom_preference_weight=custom_preference_weight,
        config=config,
    )
