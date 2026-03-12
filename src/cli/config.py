"""Configuration loading for CLI."""

import logging
from pathlib import Path
from typing import Any

import yaml

from src.llm.client import OllamaClient
from src.llm.embeddings import EmbeddingGenerator
from src.llm.recommendations import RecommendationGenerator
from src.recommendations.engine import RecommendationEngine
from src.recommendations.scorers import (
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
from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)


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
    """Load configuration from YAML file.

    Args:
        config_path: Path to config file (default: config/config.yaml)

    Returns:
        Configuration dictionary

    Raises:
        FileNotFoundError: If config file doesn't exist
    """
    resolved = resolve_config_path(config_path)

    with open(resolved) as f:
        config: dict[str, Any] = yaml.safe_load(f)

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
    ai_enabled: bool = features_config.get("ai_enabled", False)
    embeddings_enabled: bool = features_config.get("embeddings_enabled", False)
    return {
        "ai_enabled": ai_enabled,
        "embeddings_enabled": embeddings_enabled,
        "llm_reasoning_enabled": features_config.get("llm_reasoning_enabled", False),
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
    base_url = ollama_config.get("base_url", "http://localhost:11434")
    model = ollama_config.get("model", "mistral:7b")
    embedding_model = ollama_config.get("embedding_model", "nomic-embed-text")
    conversation_model = ollama_config.get("conversation_model", "")

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
            "LLM features disabled. Install with: pip install recommendinator[ai]"
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
}


def build_scorers_from_config(config: dict[str, Any]) -> list[Scorer]:
    """Build scorer instances with weights from config YAML.

    Reads ``config["recommendations"]["scorer_weights"]`` and creates scorer
    instances with the specified weights. Falls back to each scorer's class
    default weight for any scorer not listed in the config.

    Does **not** include :class:`SemanticSimilarityScorer` — the engine
    handles that conditionally based on whether AI is enabled.

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

    Args:
        storage_manager: Storage manager instance
        embedding_generator: Embedding generator instance
        recommendation_generator: Recommendation generator instance
        config: Configuration dictionary

    Returns:
        RecommendationEngine instance
    """
    rec_config = config.get("recommendations", {})
    min_rating = rec_config.get("min_rating_for_preference", 4)
    scorer_weights = rec_config.get("scorer_weights", {})
    semantic_similarity_weight = float(scorer_weights.get("semantic_similarity", 1.5))

    scorers = build_scorers_from_config(config)

    return RecommendationEngine(
        storage_manager=storage_manager,
        embedding_generator=embedding_generator,
        recommendation_generator=recommendation_generator,
        min_rating=min_rating,
        scorers=scorers,
        semantic_similarity_weight=semantic_similarity_weight,
    )
