"""Shared constants for the recommendation system."""

# Items whose relevance scores differ by at most this amount are shuffled
# together for variety across runs.
SCORE_PROXIMITY_THRESHOLD = 0.05

# Minimum genre/creator overlap for a cross-type item to qualify as
# a meaningful reference.  Prevents incidental single-genre matches
# (e.g., "Drama" alone ~ 0.2 Jaccard) from producing identical
# cross-type lists across all recommendations.
CROSS_TYPE_MIN_OVERLAP = 0.25
