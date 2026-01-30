"""Recommendation engine modules."""

from src.recommendations.engine import RecommendationEngine
from src.recommendations.preference_interpreter import (
    InterpretedPreference,
    LLMPreferenceInterpreter,
    PatternBasedInterpreter,
    PatternConfidence,
)
from src.recommendations.preferences import PreferenceAnalyzer, UserPreferences
from src.recommendations.ranking import RecommendationRanker
from src.recommendations.scorers import (
    CreatorMatchScorer,
    CustomPreferenceScorer,
    GenreMatchScorer,
    RatingPatternScorer,
    Scorer,
    ScoringContext,
    SemanticSimilarityScorer,
    SeriesOrderScorer,
    TagOverlapScorer,
)
from src.recommendations.scoring_pipeline import ScoringPipeline
from src.recommendations.similarity import SimilarityMatcher

__all__ = [
    "RecommendationEngine",
    "PreferenceAnalyzer",
    "UserPreferences",
    "SimilarityMatcher",
    "RecommendationRanker",
    "ScoringPipeline",
    "Scorer",
    "ScoringContext",
    "GenreMatchScorer",
    "CreatorMatchScorer",
    "TagOverlapScorer",
    "SeriesOrderScorer",
    "RatingPatternScorer",
    "SemanticSimilarityScorer",
    "CustomPreferenceScorer",
    "PatternBasedInterpreter",
    "LLMPreferenceInterpreter",
    "InterpretedPreference",
    "PatternConfidence",
]
