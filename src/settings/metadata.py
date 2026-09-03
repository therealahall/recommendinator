"""The dotted-key scheme and the in-scope section list match
``src.storage.settings_migration`` so the registry and the config overlay
describe the same leaves.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

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

_LOG_LEVEL_CHOICES: tuple[str, ...] = (
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
    "CRITICAL",
)


@dataclass(frozen=True)
class Validation:
    min: float | None = None
    max: float | None = None
    max_length: int | None = None
    pattern: str | None = None


@dataclass(frozen=True)
class SettingMetadata:
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
    # recommendations
    _entry(
        "recommendations.default_count",
        label="Default count",
        help="Number of recommendations returned by default.",
        type="int",
        # A run spans all four content types, so twenty is five of each.
        default=20,
        validation=Validation(min=1),
    ),
    _entry(
        "recommendations.max_count",
        label="Maximum count",
        help="Upper limit on recommendations returned per request.",
        type="int",
        # Headroom over the default, or the stepper opens pinned at its ceiling.
        default=50,
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
        "recommendations.scorer_weights.adaptation",
        label="Adaptation weight",
        help="Weight boosting films, shows and games adapting content you rated well (0 disables).",
        type="float",
        default=1.5,
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
        "enrichment.providers.tmdb.language",
        label="TMDB language",
        help="Language for TMDB results: a lowercase ISO 639-1 code, optionally with an uppercase region (en, en-US, pt-BR).",
        type="string",
        default="en-US",
        # The region is optional — TMDB accepts a bare ISO 639-1 code too, and
        # rejecting "en" in the UI while config.yaml still accepted it would be
        # an arbitrary asymmetry.
        validation=Validation(pattern=r"[a-z]{2}(-[A-Z]{2})?"),
    ),
    _entry(
        "enrichment.providers.tmdb.include_keywords",
        label="TMDB keywords as tags",
        help="Fetch TMDB keywords and store them as tags (costs an extra API call).",
        type="bool",
        default=True,
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
    # NOTE: web.host / web.port / web.debug are deliberately absent. They are
    # read by the uvicorn launcher (src/web/main.py) before any database is
    # open, so a database-backed value could never be honoured — see
    # BOOTSTRAP_WEB_* in src/config/service.py.
    _entry(
        "web.allowed_origins",
        label="Allowed CORS origins",
        help=(
            "Origins permitted by CORS. The session cookie is SameSite=Strict, "
            "so a listed origin reaches the app shell and static files only — "
            'never a signed-in route. Set to ["*"] to allow all (not '
            "recommended)."
        ),
        type="list",
        # Stored as a tuple so the registry cannot hand out a mutable it shares
        # with callers — see _public(). Declared type stays "list".
        default=("http://localhost:18473",),
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
        help="Path to the application log file; must sit under data/logs/ (e.g. data/logs/recommendations.log).",
        type="string",
        default="data/logs/recommendations.log",
        # The negative lookahead rejects `..` at the API boundary. Without it the
        # char class admits `.` and `/`, so `data/logs/../x.log` validated and
        # rendered as the effective log file while _safe_log_path silently
        # discarded it at boot. Both layers now agree.
        validation=Validation(pattern=r"data/logs/(?!.*\.\.)[A-Za-z0-9_.\-/]+\.log"),
        restart_required=True,
        advanced=True,
    ),
)

_BY_KEY: dict[str, SettingMetadata] = {entry.key: entry for entry in _REGISTRY}


def all_entries() -> tuple[SettingMetadata, ...]:
    return _REGISTRY


def get_entry(key: str) -> SettingMetadata | None:
    return _BY_KEY.get(key)


def _public(value: Any) -> Any:
    return list(value) if isinstance(value, tuple) else value


def default_of(key: str) -> Any:
    """The single source of truth for a leaf's fallback value, so callers never
    re-hardcode a default the registry already declares.
    """
    return _public(_BY_KEY[key].default)


def entries_by_section() -> dict[str, list[SettingMetadata]]:
    grouped: dict[str, list[SettingMetadata]] = {}
    for section in IN_SCOPE_SECTIONS:
        section_entries = [e for e in _REGISTRY if e.section == section]
        if section_entries:
            grouped[section] = section_entries
    return grouped


def flat_defaults() -> dict[str, Any]:
    return {entry.key: _public(entry.default) for entry in _REGISTRY}


def default_config() -> dict[str, Any]:
    nested: dict[str, Any] = {}
    for key, value in flat_defaults().items():
        parts = key.split(".")
        node = nested
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return nested


def is_sensitive(key: str) -> bool:
    entry = _BY_KEY.get(key)
    if entry is not None:
        return entry.sensitive
    return key.rsplit(".", 1)[-1] in SENSITIVE_LEAF_KEYS
