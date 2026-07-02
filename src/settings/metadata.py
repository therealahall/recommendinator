"""Metadata registry for global/system config leaves — the single source of truth.

Every in-scope global-config leaf (the sections in
:data:`~src.storage.settings_migration.IN_SCOPE_SECTIONS`) has exactly one
:class:`SettingMetadata` entry here. The registry pairs each dotted leaf key
(e.g. ``"web.port"``) with its human label, help text, value type, frontend
widget hint, validation bounds, and — crucially — the **hardcoded default**
used as the const fallback when neither the database nor ``config.yaml`` supply
the leaf.

The dotted-key scheme and the in-scope section list match
``src.storage.settings_migration`` so the registry, the DB seed, and the config
overlay all describe the same leaves. Out-of-scope config (``storage.*``,
``inputs``, per-source config) is intentionally absent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from src.ingestion.conflict import ConflictStrategy
from src.storage.settings_migration import IN_SCOPE_SECTIONS, SENSITIVE_LEAF_KEYS

SettingType = Literal["bool", "int", "float", "string", "list", "enum"]
Widget = Literal["toggle", "number", "text", "tags", "select"]

# Default frontend widget for each value type. A registry entry may override
# this (e.g. an ``enum`` renders as ``select``) via the ``widget`` argument.
_DEFAULT_WIDGETS: dict[SettingType, Widget] = {
    "bool": "toggle",
    "int": "number",
    "float": "number",
    "string": "text",
    "list": "tags",
    "enum": "select",
}

_CONFLICT_STRATEGY_CHOICES: tuple[str, ...] = tuple(s.value for s in ConflictStrategy)
_LOG_LEVEL_CHOICES: tuple[str, ...] = (
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
    "CRITICAL",
)


@dataclass(frozen=True)
class Validation:
    """Optional constraints for a setting's value.

    ``min``/``max`` bound numeric values; ``max_length``/``pattern`` constrain
    strings. Every field is optional — an unset field imposes no constraint.
    """

    min: float | None = None
    max: float | None = None
    max_length: int | None = None
    pattern: str | None = None


@dataclass(frozen=True)
class SettingMetadata:
    """Describes one global-config leaf for the API, CLI, and frontend.

    Attributes:
        key: Dotted leaf path (e.g. ``"recommendations.scorer_weights.genre_match"``).
        section: Top-level section, derived from the key prefix.
        label: Concise human label.
        help: One-line description of what the setting does.
        type: The value type (``bool``/``int``/``float``/``string``/``list``/``enum``).
        default: The hardcoded fallback value used when neither DB nor YAML supply
            the leaf. This is NOT seeded into the DB; it is the const default.
        widget: Frontend rendering hint, defaulted from ``type``.
        sensitive: True for secret leaves that must never be persisted plaintext.
        restart_required: True when a change only takes effect after a restart.
        advanced: True for infra/security leaves grouped under "Advanced" in the UI.
        choices: Allowed values for ``enum`` types (``None`` otherwise).
        validation: Optional value constraints.
    """

    key: str
    section: str
    label: str
    help: str
    type: SettingType
    default: Any
    widget: Widget
    sensitive: bool
    restart_required: bool
    advanced: bool
    choices: tuple[str, ...] | None = None
    validation: Validation | None = None


def _entry(
    key: str,
    *,
    label: str,
    help: str,
    type: SettingType,
    default: Any,
    choices: tuple[str, ...] | None = None,
    validation: Validation | None = None,
    widget: Widget | None = None,
    restart_required: bool = False,
    advanced: bool = False,
) -> SettingMetadata:
    """Build a :class:`SettingMetadata`, deriving section, widget, and sensitivity.

    ``section`` comes from the key prefix, ``widget`` defaults from ``type``, and
    ``sensitive`` is True when the final key segment is a known secret name (see
    :data:`SENSITIVE_LEAF_KEYS`).
    """
    return SettingMetadata(
        key=key,
        section=key.split(".", 1)[0],
        label=label,
        help=help,
        type=type,
        default=default,
        widget=widget or _DEFAULT_WIDGETS[type],
        sensitive=key.rsplit(".", 1)[-1] in SENSITIVE_LEAF_KEYS,
        restart_required=restart_required,
        advanced=advanced,
        choices=choices,
        validation=validation,
    )


_REGISTRY: tuple[SettingMetadata, ...] = (
    # features — capability gates that decide subsystem loading (restart_required).
    _entry(
        "features.ai_enabled",
        label="AI features",
        help="Master toggle for all AI features; when off, only content-based and rule-based scoring is used.",
        type="bool",
        default=False,
        restart_required=True,
    ),
    _entry(
        "features.embeddings_enabled",
        label="Vector embeddings",
        help="Enable ChromaDB semantic-similarity embeddings (requires AI features).",
        type="bool",
        default=False,
        restart_required=True,
    ),
    _entry(
        "features.llm_reasoning_enabled",
        label="LLM reasoning",
        help="Use Ollama to generate natural-language recommendation explanations (requires AI features).",
        type="bool",
        default=False,
        restart_required=True,
    ),
    # ollama
    _entry(
        "ollama.base_url",
        label="Ollama base URL",
        help="Base URL of the Ollama server.",
        type="string",
        default="http://ollama:11434",
    ),
    _entry(
        "ollama.model",
        label="Recommendation model",
        help="Ollama model used for recommendation text generation.",
        type="string",
        default="mistral:7b",
    ),
    _entry(
        "ollama.embedding_model",
        label="Embedding model",
        help="Ollama model used to generate embeddings.",
        type="string",
        default="nomic-embed-text",
    ),
    _entry(
        "ollama.conversation_model",
        label="Conversation model",
        help="Ollama model for chat; falls back to the recommendation model when empty.",
        type="string",
        default="",
    ),
    # ingestion
    _entry(
        "ingestion.conflict_strategy",
        label="Conflict strategy",
        help="How to resolve an item imported from multiple sources.",
        type="enum",
        default=ConflictStrategy.LAST_WRITE_WINS.value,
        choices=_CONFLICT_STRATEGY_CHOICES,
    ),
    _entry(
        "ingestion.source_priority",
        label="Source priority",
        help="Source ordering (highest first) used by the source_priority strategy.",
        type="list",
        default=["goodreads", "steam"],
    ),
    # recommendations
    _entry(
        "recommendations.default_count",
        label="Default count",
        help="Number of recommendations returned by default.",
        type="int",
        default=5,
        validation=Validation(min=1),
    ),
    _entry(
        "recommendations.max_count",
        label="Maximum count",
        help="Upper limit on recommendations returned per request.",
        type="int",
        default=20,
        validation=Validation(min=1),
    ),
    _entry(
        "recommendations.min_rating_for_preference",
        label="Minimum liked rating",
        help="Items rated at least this value count as liked when profiling taste.",
        type="int",
        default=4,
        validation=Validation(min=1, max=5),
    ),
    _entry(
        "recommendations.scorer_weights.genre_match",
        label="Genre match weight",
        help="Weight for matching a candidate's genres to liked items (0 disables).",
        type="float",
        default=2.0,
        validation=Validation(min=0.0),
    ),
    _entry(
        "recommendations.scorer_weights.creator_match",
        label="Creator match weight",
        help="Weight for matching a candidate's creators to liked items (0 disables).",
        type="float",
        default=1.5,
        validation=Validation(min=0.0),
    ),
    _entry(
        "recommendations.scorer_weights.tag_overlap",
        label="Tag overlap weight",
        help="Weight for overlap between candidate and liked-item tags (0 disables).",
        type="float",
        default=1.0,
        validation=Validation(min=0.0),
    ),
    _entry(
        "recommendations.scorer_weights.series_order",
        label="Series order weight",
        help="Weight for the next unread entry in a series (0 disables).",
        type="float",
        default=1.5,
        validation=Validation(min=0.0),
    ),
    _entry(
        "recommendations.scorer_weights.rating_pattern",
        label="Rating pattern weight",
        help="Weight for matching learned rating patterns (0 disables).",
        type="float",
        default=1.0,
        validation=Validation(min=0.0),
    ),
    _entry(
        "recommendations.scorer_weights.semantic_similarity",
        label="Semantic similarity weight",
        help="Weight for embedding similarity (0 disables); only active with AI features.",
        type="float",
        default=1.5,
        validation=Validation(min=0.0),
    ),
    _entry(
        "recommendations.scorer_weights.content_length",
        label="Content length weight",
        help="Soft penalty weight for items not matching length preferences (0 disables).",
        type="float",
        default=1.0,
        validation=Validation(min=0.0),
    ),
    _entry(
        "recommendations.scorer_weights.continuation",
        label="Continuation weight",
        help="Weight boosting items you are currently consuming (0 disables).",
        type="float",
        default=2.0,
        validation=Validation(min=0.0),
    ),
    _entry(
        "recommendations.scorer_weights.series_affinity",
        label="Series affinity weight",
        help="Weight boosting franchises you have rated well (0 disables).",
        type="float",
        default=1.0,
        validation=Validation(min=0.0),
    ),
    _entry(
        "recommendations.scorer_weights.custom_preference",
        label="Custom preference weight",
        help="Weight for user-defined natural-language preference rules (0 disables).",
        type="float",
        default=1.0,
        validation=Validation(min=0.0),
    ),
    # conversation
    _entry(
        "conversation.enabled",
        label="Chat enabled",
        help="Enable the conversational chat interface (requires AI features).",
        type="bool",
        default=True,
    ),
    _entry(
        "conversation.max_history_messages",
        label="Max history messages",
        help="Maximum conversation messages kept in context.",
        type="int",
        default=50,
        validation=Validation(min=1),
    ),
    _entry(
        "conversation.memory_extraction_enabled",
        label="Memory extraction",
        help="Automatically extract memories and preferences from conversations.",
        type="bool",
        default=True,
    ),
    _entry(
        "conversation.profile_regeneration_interval",
        label="Profile regeneration interval",
        help="Hours between automatic preference-profile regeneration (0 disables).",
        type="int",
        default=24,
        validation=Validation(min=0),
    ),
    _entry(
        "conversation.llm.temperature",
        label="Temperature",
        help="Sampling temperature for chat responses (higher is more creative).",
        type="float",
        default=0.7,
        validation=Validation(min=0.0, max=2.0),
    ),
    _entry(
        "conversation.llm.max_tokens",
        label="Max tokens",
        help="Maximum tokens generated in a chat response.",
        type="int",
        default=2000,
        validation=Validation(min=1),
    ),
    _entry(
        "conversation.context.max_relevant_items",
        label="Max relevant items",
        help="Maximum items retrieved via semantic search for chat context.",
        type="int",
        default=10,
        validation=Validation(min=1),
    ),
    _entry(
        "conversation.context.max_unconsumed_items",
        label="Max backlog items",
        help="Maximum unconsumed/backlog items included in chat context.",
        type="int",
        default=20,
        validation=Validation(min=0),
    ),
    _entry(
        "conversation.context.include_algorithmic_recs",
        label="Include recommendations in context",
        help="Include algorithmic recommendations in chat context.",
        type="bool",
        default=True,
    ),
    _entry(
        "conversation.context.compact_mode",
        label="Compact mode",
        help="Reduce prompt size for small (3B) models via a condensed context.",
        type="bool",
        default=False,
    ),
    # sync
    _entry(
        "sync.max_workers",
        label="Sync workers",
        help="Number of data sources to sync in parallel (1 for sequential).",
        type="int",
        default=4,
        validation=Validation(min=1),
    ),
    # enrichment
    _entry(
        "enrichment.enabled",
        label="Enrichment enabled",
        help="Enable background metadata enrichment.",
        type="bool",
        default=False,
    ),
    _entry(
        "enrichment.auto_enrich_on_sync",
        label="Auto-enrich on sync",
        help="Automatically queue new items for enrichment after a sync.",
        type="bool",
        default=False,
    ),
    _entry(
        "enrichment.batch_size",
        label="Enrichment batch size",
        help="Number of items processed per enrichment batch.",
        type="int",
        default=50,
        validation=Validation(min=1),
    ),
    _entry(
        "enrichment.providers.tmdb.api_key",
        label="TMDB API key",
        help="API key for The Movie Database enrichment provider.",
        type="string",
        default="",
    ),
    _entry(
        "enrichment.providers.tmdb.enabled",
        label="TMDB enabled",
        help="Enable the TMDB (movies and TV) enrichment provider.",
        type="bool",
        default=False,
    ),
    _entry(
        "enrichment.providers.openlibrary.enabled",
        label="Open Library enabled",
        help="Enable the Open Library (books) enrichment provider.",
        type="bool",
        default=False,
    ),
    _entry(
        "enrichment.providers.rawg.api_key",
        label="RAWG API key",
        help="API key for the RAWG video-game database enrichment provider.",
        type="string",
        default="",
    ),
    _entry(
        "enrichment.providers.rawg.enabled",
        label="RAWG enabled",
        help="Enable the RAWG (video games) enrichment provider.",
        type="bool",
        default=False,
    ),
    # web — server bind and CORS take effect only on restart; infra/security → advanced.
    _entry(
        "web.host",
        label="Bind host",
        help="Interface the web server binds to (127.0.0.1 is localhost-only).",
        type="string",
        default="127.0.0.1",
        restart_required=True,
        advanced=True,
    ),
    _entry(
        "web.port",
        label="Bind port",
        help="TCP port the web server listens on.",
        type="int",
        default=18473,
        validation=Validation(min=1, max=65535),
        restart_required=True,
        advanced=True,
    ),
    _entry(
        "web.debug",
        label="Debug mode",
        help="Enable auto-reload on file changes (development only).",
        type="bool",
        default=False,
        restart_required=True,
        advanced=True,
    ),
    _entry(
        "web.allowed_origins",
        label="Allowed CORS origins",
        help='Origins permitted by CORS; set to ["*"] to allow all (not recommended).',
        type="list",
        default=["http://localhost:18473"],
        restart_required=True,
        advanced=True,
    ),
    # logging — configured once at startup → restart_required; infra → advanced.
    _entry(
        "logging.level",
        label="Log level",
        help="Minimum severity of log messages emitted.",
        type="enum",
        default="INFO",
        choices=_LOG_LEVEL_CHOICES,
        restart_required=True,
        advanced=True,
    ),
    _entry(
        "logging.file",
        label="Log file",
        help="Path to the application log file, relative to the logs/ directory.",
        type="string",
        default="logs/recommendations.log",
        validation=Validation(pattern=r"logs/[A-Za-z0-9_.\-/]+\.log"),
        restart_required=True,
        advanced=True,
    ),
)

_BY_KEY: dict[str, SettingMetadata] = {entry.key: entry for entry in _REGISTRY}


def all_entries() -> tuple[SettingMetadata, ...]:
    """Return every registry entry in declaration (``example.yaml``) order."""
    return _REGISTRY


def get_entry(key: str) -> SettingMetadata | None:
    """Return the entry for a dotted leaf key, or ``None`` if it is not in scope."""
    return _BY_KEY.get(key)


def default_of(key: str) -> Any:
    """Return the const default for a registered leaf key.

    The single source of truth for a leaf's fallback value, so callers never
    re-hardcode a default the registry already declares.

    Raises:
        KeyError: If *key* is not a registered leaf (a programming error).
    """
    return _BY_KEY[key].default


def entries_by_section() -> dict[str, list[SettingMetadata]]:
    """Return entries grouped by section, ordered by :data:`IN_SCOPE_SECTIONS`.

    Within each section, entries keep their declaration order. Only sections
    that actually have entries appear in the result.
    """
    grouped: dict[str, list[SettingMetadata]] = {}
    for section in IN_SCOPE_SECTIONS:
        section_entries = [e for e in _REGISTRY if e.section == section]
        if section_entries:
            grouped[section] = section_entries
    return grouped


def flat_defaults() -> dict[str, Any]:
    """Return the hardcoded defaults as a flat ``{dotted_key: value}`` mapping."""
    return {entry.key: entry.default for entry in _REGISTRY}


def default_config() -> dict[str, Any]:
    """Return the hardcoded defaults as a nested dict keyed by section.

    Suitable as the base for ``deep_merge(default_config(), yaml_config)`` so a
    later config-assembly step has a complete const fallback for every leaf.
    """
    nested: dict[str, Any] = {}
    for key, value in flat_defaults().items():
        parts = key.split(".")
        node = nested
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return nested


def is_sensitive(key: str) -> bool:
    """Return True when the leaf key holds a secret that must not be persisted.

    Uses the registry entry when present; otherwise falls back to matching the
    final key segment against :data:`SENSITIVE_LEAF_KEYS`.
    """
    entry = _BY_KEY.get(key)
    if entry is not None:
        return entry.sensitive
    return key.rsplit(".", 1)[-1] in SENSITIVE_LEAF_KEYS
