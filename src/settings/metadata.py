"""The dotted-key scheme and the in-scope section list match
``src.storage.settings_migration`` so the registry and the config overlay
describe the same leaves.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from src.enrichment.provider_base import flags_no_credential, stored_config_schema
from src.storage.settings_migration import IN_SCOPE_SECTIONS
from src.utils.text import humanize_source_id

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from src.enrichment.provider_base import ConfigField, EnrichmentProvider

SettingType = Literal["bool", "int", "float", "string", "list", "enum"]
Widget = Literal["toggle", "number", "text", "tags", "select", "provider-order"]

#: Precedence among enrichment providers, read by the registry and checked by the
#: settings service, so neither re-spells it.
PROVIDER_ORDER_KEY = "enrichment.provider_order"

_PROVIDER_PREFIX = "enrichment.providers."

#: Added to every provider rather than read off its schema, so one shipping
#: without it is still a provider the operator can turn off.
_ENABLED_FIELD = "enabled"

#: A ``ConfigField`` typed outside this map is left off the settings page rather
#: than given a control that cannot hold it. The second half is what a field
#: declaring no default falls back to.
_FIELD_TYPES: dict[type, tuple[SettingType, Any]] = {
    bool: ("bool", False),
    int: ("int", 0),
    float: ("float", 0.0),
    str: ("string", ""),
    list: ("list", ()),
}

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
    sensitive: bool = False,
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
        sensitive=sensitive,
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


def _installed_providers() -> list[EnrichmentProvider]:
    """Imported here rather than at module level: the enrichment registry reads
    this module for the order key, so naming it above would be a cycle.
    """
    from src.enrichment.registry import get_enrichment_registry

    return sorted(
        get_enrichment_registry().get_all_providers().values(),
        key=lambda provider: (provider.precedence, provider.name),
    )


#: Keys already reported. Every ``get_entry`` and ``default_of`` for a provider
#: rebuilds these entries, so one schema mistake is dozens of lines a page load.
_warned_keys: set[str] = set()


def _warn_once(key: str, message: str, *args: Any) -> None:
    if key in _warned_keys:
        return
    _warned_keys.add(key)
    logger.warning(message, *args)


def _immutable(value: Any) -> Any:
    """A list default becomes a tuple for the reason web.allowed_origins is one:
    ``_public`` copies a tuple, and a list would be the provider's own object.
    """
    return tuple(value) if isinstance(value, list) else value


def _provider_field_entry(
    provider: EnrichmentProvider, field: ConfigField
) -> SettingMetadata | None:
    typed = _FIELD_TYPES.get(field.field_type)
    if typed is None:
        _warn_once(
            f"{_PROVIDER_PREFIX}{provider.name}.{field.name}",
            "Enrichment provider %s declares %s as %s, which no setting control "
            "holds — leaving it off the settings page",
            provider.name,
            field.name,
            field.field_type,
        )
        return None
    setting_type, empty = typed
    return _entry(
        f"{_PROVIDER_PREFIX}{provider.name}.{field.name}",
        label=f"{provider.display_name} {humanize_source_id(field.name)}",
        help=field.description,
        type=setting_type,
        default=empty if field.default is None else _immutable(field.default),
        validation=None if field.pattern is None else Validation(pattern=field.pattern),
        sensitive=field.sensitive,
    )


def _provider_entries() -> tuple[SettingMetadata, ...]:
    """Each provider declares what it needs, so installing one is the whole of
    offering it a settings page.
    """
    installed = _installed_providers()
    entries = [
        _entry(
            PROVIDER_ORDER_KEY,
            label="Provider precedence",
            help=(
                "Providers are tried in this order and the first one to match an "
                "item enriches it. Set from the CLI, it must name every installed "
                "provider exactly once."
            ),
            type="list",
            default=tuple(provider.name for provider in installed),
            # Rendered by the provider list, where the order is read off the same
            # rows that turn a provider on, rather than by a generic list control.
            widget="provider-order",
        )
    ]
    for provider in installed:
        entries.append(
            _entry(
                f"{_PROVIDER_PREFIX}{provider.name}.{_ENABLED_FIELD}",
                label=f"{provider.display_name} enabled",
                help=provider.description,
                type="bool",
                default=False,
            )
        )
        if flags_no_credential(provider):
            _warn_once(
                f"{_PROVIDER_PREFIX}{provider.name}",
                "Enrichment provider %s needs an api key but flags no field "
                "sensitive — its text fields are offered as secrets until one "
                "declares sensitive=True",
                provider.name,
            )
        for field in stored_config_schema(provider):
            if field.name == _ENABLED_FIELD:
                continue
            entry = _provider_field_entry(provider, field)
            if entry is not None:
                entries.append(entry)
    return tuple(entries)


def all_entries() -> tuple[SettingMetadata, ...]:
    return _REGISTRY + _provider_entries()


def get_entry(key: str) -> SettingMetadata | None:
    entry = _BY_KEY.get(key)
    if entry is not None:
        return entry
    # Only a provider's own key discovers providers, so wiring the log and
    # loading the config never import one.
    if key != PROVIDER_ORDER_KEY and not key.startswith(_PROVIDER_PREFIX):
        return None
    return next(
        (derived for derived in _provider_entries() if derived.key == key), None
    )


def _public(value: Any) -> Any:
    return list(value) if isinstance(value, tuple) else value


def default_of(key: str) -> Any:
    """The single source of truth for a leaf's fallback value, so callers never
    re-hardcode a default the registry already declares.
    """
    entry = get_entry(key)
    if entry is None:
        raise KeyError(key)
    return _public(entry.default)


def entries_by_section() -> dict[str, list[SettingMetadata]]:
    entries = all_entries()
    grouped: dict[str, list[SettingMetadata]] = {}
    for section in IN_SCOPE_SECTIONS:
        section_entries = [e for e in entries if e.section == section]
        if section_entries:
            grouped[section] = section_entries
    return grouped


def flat_defaults() -> dict[str, Any]:
    """The app's own leaves only. A provider's are left out deliberately: the
    running config is assembled before the CLI has wired logging, and every
    provider already falls back to its schema's default for an absent key.
    """
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
