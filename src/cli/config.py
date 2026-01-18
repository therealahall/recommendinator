"""Configuration loading for CLI."""

import yaml
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from src.llm.client import OllamaClient
from src.llm.embeddings import EmbeddingGenerator
from src.llm.recommendations import RecommendationGenerator
from src.storage.manager import StorageManager
from src.recommendations.engine import RecommendationEngine


def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load configuration from YAML file.

    Args:
        config_path: Path to config file (default: config/config.yaml)

    Returns:
        Configuration dictionary

    Raises:
        FileNotFoundError: If config file doesn't exist
    """
    if config_path is None:
        config_path = Path("config/config.yaml")

    if not config_path.exists():
        # Try example config
        example_path = Path("config/example.yaml")
        if example_path.exists():
            config_path = example_path
        else:
            raise FileNotFoundError(
                f"Config file not found: {config_path}. "
                "Copy config/example.yaml to config/config.yaml"
            )

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    return config


def create_storage_manager(config: Dict[str, Any]) -> StorageManager:
    """Create storage manager from config.

    Args:
        config: Configuration dictionary

    Returns:
        StorageManager instance
    """
    storage_config = config.get("storage", {})
    db_path = Path(storage_config.get("database_path", "data/recommendations.db"))
    vector_db_path = Path(storage_config.get("vector_db_path", "data/chroma_db"))

    return StorageManager(
        sqlite_path=db_path,
        vector_db_path=vector_db_path,
    )


def create_llm_components(
    config: Dict[str, Any],
) -> Tuple[OllamaClient, EmbeddingGenerator, RecommendationGenerator]:
    """Create LLM components from config.

    Args:
        config: Configuration dictionary

    Returns:
        Tuple of (OllamaClient, EmbeddingGenerator, RecommendationGenerator)
    """
    ollama_config = config.get("ollama", {})
    base_url = ollama_config.get("base_url", "http://localhost:11434")
    model = ollama_config.get("model", "mistral:7b")
    embedding_model = ollama_config.get("embedding_model", "nomic-embed-text")

    client = OllamaClient(
        base_url=base_url,
        default_model=model,
        embedding_model=embedding_model,
    )

    embedding_gen = EmbeddingGenerator(client)
    recommendation_gen = RecommendationGenerator(client)

    return client, embedding_gen, recommendation_gen


def create_recommendation_engine(
    storage_manager: StorageManager,
    embedding_generator: EmbeddingGenerator,
    recommendation_generator: RecommendationGenerator,
    config: Dict[str, Any],
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

    return RecommendationEngine(
        storage_manager=storage_manager,
        embedding_generator=embedding_generator,
        recommendation_generator=recommendation_generator,
        min_rating=min_rating,
    )
