"""REST API endpoints."""

import logging
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Annotated, Any, assert_never, cast

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, UploadFile
from fastapi import Path as PathParam  # this module's ``Path`` is pathlib's
from fastapi.responses import Response
from pydantic import AfterValidator, BaseModel, Field, StringConstraints

from src import __version__ as APP_VERSION
from src.auth.epic import (
    EPIC_PLUGIN,
    EPIC_SOURCE_ID,
    EpicAuthError,
    get_epic_auth_url,
    has_epic_token,
    is_epic_enabled,
    save_epic_token,
)
from src.auth.epic import exchange_code_for_tokens as exchange_epic_tokens
from src.auth.epic import extract_code_from_input as extract_epic_code
from src.auth.gog import (
    GOG_PLUGIN,
    GOG_SOURCE_ID,
    GogAuthError,
    get_gog_auth_url,
    has_gog_token,
    is_gog_enabled,
    save_gog_token,
)
from src.auth.gog import exchange_code_for_tokens as exchange_gog_tokens
from src.auth.gog import extract_code_from_input as extract_gog_code
from src.auth.oauth_sources import REFRESH_TOKEN_KEY, may_revoke
from src.auth.trakt import (
    TRAKT_PLUGIN,
    TRAKT_SOURCE_ID,
    DevicePollStatus,
    TraktAuthError,
    has_trakt_token,
    poll_device_token,
    resolve_trakt_client_credentials,
    save_trakt_token,
    start_device_auth_flow,
)
from src.config.service import auto_enrich_enabled
from src.enrichment.manager import EnrichmentManager, job_status
from src.ingestion.import_templates import (
    TemplatesUnavailable,
    available_templates,
    find_template,
    read_template,
)
from src.ingestion.importers.base import ImporterError
from src.ingestion.importers.registry import IMPORTERS, get_importer
from src.ingestion.importers.service import decode_import_text, import_file
from src.ingestion.plugin_base import SourcePlugin
from src.ingestion.schedule import SYNC_INTERVAL_KEYS
from src.ingestion.sync import (
    ALL_SOURCES_KEY,
    ALL_SOURCES_LABEL,
    MAX_WORKERS_CEILING,
    already_syncing_detail,
    claim_sources,
    release_sources,
)
from src.models.content import (
    MAX_CREATOR_LENGTH,
    MAX_DESCRIPTION_LENGTH,
    MAX_GENRE_TAG_LENGTH,
    MAX_GENRES,
    MAX_RELEASE_YEAR,
    MAX_REVIEW_LENGTH,
    MAX_TAGS,
    MAX_TITLE_LENGTH,
    MIN_RELEASE_YEAR,
    ConsumptionStatus,
    ContentItem,
    ContentType,
    EnrichmentFilter,
    ExternalId,
    get_enum_value,
)
from src.models.user_preferences import UserPreferenceConfig
from src.recommendations.content_length import LengthPreference
from src.recommendations.profile import ProfileGenerator, profile_payload
from src.recommendations.scorers import SCORER_NAME_MAP
from src.settings.metadata import get_entry
from src.settings.service import (
    SettingsValidationError,
    apply_settings,
    build_settings_view,
    clear_secret,
    reset_setting,
    set_secret,
)
from src.sources.service import (
    SOURCE_ID_PATTERN,
    SourceConfigError,
    build_config_view,
    build_plugins_view,
    build_runs_view,
    build_schema_view,
    clear_source_secret_value,
    create_source,
    delete_source,
    get_available_sync_sources,
    migrate_source,
    misconfigured_detail,
    redact_credentials,
    resolve_inputs,
    resolve_source_plugin,
    set_source_enabled_state,
    set_source_schedule,
    set_source_secret_value,
    source_plugin_not_loaded,
    unusable_detail,
    update_source_config_values,
)
from src.storage.accounts import (
    PASSWORD_TOO_SHORT,
    AccountNameError,
    PasswordTooShortError,
    normalize_account_name,
)
from src.storage.manager import (
    MAX_DECLINE_OTHERS,
    SUGGESTION_PAGE_DEFAULT,
    SUGGESTION_PAGE_MAX,
    UNSET,
    VALID_SORT_OPTIONS,
    DeclinedPair,
    MergeError,
    MergeEvidence,
    MergeRecord,
    StorageManager,
    UncorrectableFieldError,
    UnknownUserError,
    Unset,
)
from src.storage.schema import UserDict
from src.utils.duplicate_serialization import (
    decline_refusal_message,
    declined_pair_to_dict,
    merge_to_dict,
    suggestion_page_to_dict,
)
from src.utils.export import export_items_csv, export_items_json
from src.utils.item_serialization import (
    completion_to_dict,
    ignore_result_to_dict,
    item_to_dict,
)
from src.utils.series import MAX_SEASONS
from src.utils.sorting import MAX_SEARCH_LENGTH
from src.utils.text import (
    exception_for_log,
    humanize_source_id,
    is_blank,
    sanitize_for_log,
)
from src.web.auth import SESSION_COOKIE, CurrentUser, require_session
from src.web.csrf import refuse_cross_origin
from src.web.guards import (
    RequiredConfig,
    RequiredEngine,
    RequiredStorage,
    require_config,
    writable_config,
)
from src.web.responses import SurrogateSafeResponse
from src.web.state import (
    get_config,
    get_engine,
    get_storage,
    reload_config,
)
from src.web.sync_dispatch import build_sync_job
from src.web.sync_manager import get_sync_manager
from src.web.themes import (
    DEFAULT_THEME_ID,
    MAX_THEME_ID_LENGTH,
    ThemeResponse,
    installed_theme_ids,
    installed_themes,
)

logger = logging.getLogger(__name__)

# On the router rather than at ``include_router``: a route is then
# authenticated by being registered, even where a test mounts this router bare.
router = APIRouter(
    prefix="/api",
    tags=["api"],
    dependencies=[Depends(require_session), Depends(refuse_cross_origin)],
)


def _blank_rejector(field: str) -> Callable[[str], str]:
    """The lower bound refuses ``""``; spaces are the same claim, unsayable in
    a schema. The CLI splits it the same way, around the same ``is_blank``."""

    def reject(value: str) -> str:
        if is_blank(value):
            raise ValueError(f"{field} cannot be blank")
        return value

    return reject


#: Blank is not a review: stored, it stops a later import filling the field.
CompletionReviewText = Annotated[
    str,
    Field(min_length=1, max_length=MAX_REVIEW_LENGTH),
    AfterValidator(_blank_rejector("review")),
]

#: Blank is not a title either: it is the item's only name in the library.
CompletionTitle = Annotated[
    str,
    Field(min_length=1, max_length=MAX_TITLE_LENGTH),
    AfterValidator(_blank_rejector("title")),
]

#: Stripped, so a pasted trailing space is not another name to the veto. Its
#: bounds are ``edit_item``'s to refuse: a constraint answers an unreadable 422.
CorrectedCreator = Annotated[str, StringConstraints(strip_whitespace=True)]


def _account_name_validator(required: bool) -> Callable[[str], str]:
    """Build one account-name field's check, over the storage door's own rule.

    No ``Field(max_length=...)`` beside it: Pydantic runs that before any
    ``AfterValidator``, so it measured the padding the trim removes and the
    web refused a name ``account set-name`` stored.
    """

    def normalize(value: str) -> str:
        try:
            return normalize_account_name(value, required=required)
        except AccountNameError as error:
            raise ValueError(str(error)) from error

    return normalize


AccountUsername = Annotated[str, AfterValidator(_account_name_validator(True))]

#: Trimmed but not required: "" is how a form clears it, and the handlers read
#: an empty display name as "fall back to the username".
AccountDisplayName = Annotated[str, AfterValidator(_account_name_validator(False))]


def _member_validator(noun: str, allowed: Iterable[str]) -> Callable[[str], str]:
    """Build the membership check for one closed set of preference keys.

    The CLI spells each as a Click choice. A name outside the set weights
    nothing and sits in ``users.settings`` for every later read to parse.
    """
    permitted = sorted(allowed)

    def reject(value: str) -> str:
        if value not in permitted:
            raise ValueError(f"unknown {noun}; expected one of {', '.join(permitted)}")
        return value

    return reject


ScorerName = Annotated[
    str, AfterValidator(_member_validator("scorer", SCORER_NAME_MAP))
]

ContentTypeName = Annotated[
    str,
    AfterValidator(
        _member_validator("content type", (member.value for member in ContentType))
    ),
]

LengthPreferenceName = Annotated[
    str,
    AfterValidator(
        _member_validator(
            "length preference", (member.value for member in LengthPreference)
        )
    ),
]

ThemeId = Annotated[str, Field(max_length=MAX_THEME_ID_LENGTH)]

#: ``json.loads`` accepts the ``Infinity`` and ``NaN`` literals and
#: ``JSONResponse`` refuses to render them, so one stored non-finite weight
#: answers 500 on every later read of the preferences page.
PreferenceWeight = Annotated[float, Field(allow_inf_nan=False)]

CustomRuleText = Annotated[
    str, Field(max_length=UserPreferenceConfig.MAX_CUSTOM_RULE_LENGTH)
]

#: Every user id in a path. The query-param siblings all carry ``ge=1``, and a
#: non-positive id matches no row.
UserIdPath = Annotated[int, PathParam(ge=1, description="User ID")]

ItemDbId = Annotated[int, Field(ge=1)]
ItemIdPath = Annotated[int, PathParam(ge=1)]

_WRONG_CURRENT_PASSWORD = "Your current password is not correct."


def _refuse_another_account(user_id: int, user: UserDict) -> None:
    """Answer 403 when *user_id* is not the signed-in account.

    A 404 would say which ids exist, and this is the shape a Users page needs
    anyway: an admin distinction is a change here, not a new route.
    """
    if user_id != user["id"]:
        raise HTTPException(
            status_code=403, detail="You may only change your own account."
        )


# Request/Response models
class CompletionRequest(BaseModel):
    """Request model for marking content as completed."""

    content_type: str = Field(
        ..., description="Content type (book, movie, tv_show, video_game)"
    )
    title: CompletionTitle = Field(..., description="Title of the content")
    author: str | None = Field(
        None,
        max_length=MAX_CREATOR_LENGTH,
        description="Creator: author, director, creator or developer",
    )
    rating: int | None = Field(None, ge=1, le=5, description="Rating (1-5)")
    review: CompletionReviewText | None = Field(None, description="Review text")


class CompletionResponse(BaseModel):
    message: str
    id: int


class UpdateRequest(BaseModel):
    """Request model for updating data."""

    source: str = Field(
        ...,
        description=(
            "Source id, or 'all' for every enabled source. "
            "GET /api/sync/sources lists the ones this install has."
        ),
    )
    max_workers: int | None = Field(
        None,
        ge=1,
        le=MAX_WORKERS_CEILING,
        description=(
            "Override config['sync']['max_workers'] for this invocation. "
            "Mirrors the CLI's --workers flag."
        ),
    )


class PluginImportErrorResponse(BaseModel):
    """A plugin module that did not load, and the exception that lost it."""

    module: str
    reason: str


class PluginNotLoadedResponse(BaseModel):
    """The plugin a source asks for, and every module that failed to import.

    ``failures`` is the whole pass: none of them can be tied to ``plugin``.
    """

    plugin: str
    failures: list[PluginImportErrorResponse]


class SyncSourceResponse(BaseModel):
    """Response model for a sync source.

    ``plugin_not_loaded`` is set when the source's plugin is missing; it is
    listed anyway, and still cannot sync. ``sync_interval`` is resolved, so a
    client never has to know the plugin's default to render the cadence.
    """

    id: str
    display_name: str
    plugin_display_name: str
    enabled: bool
    plugin_not_loaded: PluginNotLoadedResponse | None = None
    sync_interval: str
    last_run_at: str | None
    last_run_status: str | None
    next_run_at: str | None


class UserResponse(BaseModel):
    """Response model for user listing."""

    id: int
    username: str
    display_name: str | None
    #: What ``account show`` calls "Password changed", so both interfaces
    #: report one account shape.
    password_updated_at: str | None = None


def as_user_response(storage: StorageManager, user: UserDict) -> UserResponse:
    """Render *user* as the account both interfaces report.

    The stamp is fetched rather than read off *user*: no other reader of a
    ``users`` row wants a credential column beside it.
    """
    account = storage.accounts.describe(user["id"])
    return UserResponse(
        id=user["id"],
        username=user["username"],
        display_name=user.get("display_name"),
        password_updated_at=account["password_updated_at"] if account else None,
    )


class UserUpdateRequest(BaseModel):
    """Rename request for an account."""

    username: AccountUsername
    display_name: AccountDisplayName = ""


class PasswordChangeRequest(BaseModel):
    """A password change, which costs the current password.

    The session alone must not be enough, or a borrowed unlocked browser is a
    permanent takeover.
    """

    current_password: str = Field(..., max_length=1000)
    # No ``min_length``: the floor is enforced where the password is written
    # and reported as a 400 — see :class:`SetupRequest`.
    new_password: str = Field(..., max_length=1000)


class ContentItemResponse(BaseModel):
    """Response model for content item listing."""

    # Which source contributed which id, one entry per source that named it.
    external_ids: list[ExternalId] = Field(default_factory=list)
    db_id: int | None = None  # Database ID for actions like ignore
    title: str
    author: str | None
    content_type: str
    status: str
    rating: int | None
    review: str | None
    source: str | None
    date_completed: str | None = None
    ignored: bool = False
    seasons_watched: list[int] | None = None
    total_seasons: int | None = None
    release_year: int | None = None
    series: str | None = None
    series_index: float | None = None
    enriched: bool = False
    manually_enriched: bool = False
    genres: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    description: str | None = None


class RecommendationResponse(BaseModel):
    """Response model for recommendations.

    The fields are declared in the order
    :class:`src.recommendations.record.RecommendationPayload` builds them, and
    every one of them is validated from that mapping.
    """

    db_id: int | None = None  # Database ID for actions like ignore
    title: str
    author: str | None
    series: str | None = None
    series_index: float | None = None
    score: float
    reasoning: str
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    # Stepped genre-fatigue penalty applied when the variety_penalty preference
    # is set (0.0 when off or the item's genre was not recently finished).
    variety_penalty: float = Field(0.0, ge=0.0, le=1.0)


class ProfileResponse(BaseModel):
    """Response model for a user's preference profile."""

    user_id: int
    genre_affinities: dict[str, float]
    theme_preferences: list[str]
    anti_preferences: list[str]
    cross_media_patterns: list[str]
    generated_at: datetime | None = None


class RecommendationsConfig(BaseModel):
    """Recommendations configuration exposed to the frontend.

    Defaults mirror config/example.yaml recommendations section.
    """

    max_count: int = 20
    default_count: int = 5


class StatusResponse(BaseModel):
    """Response model for system status."""

    status: str
    version: str
    components: dict[str, bool]
    recommendations_config: RecommendationsConfig = Field(
        default_factory=RecommendationsConfig
    )


class UserPreferenceResponse(BaseModel):
    """Response model for user preferences.

    ``scorer_weights`` needs no bound of its own: ``from_dict`` drops the
    non-finite ones on read, and a second refusal here would answer a bare
    ``ValidationError`` rather than anything an operator can act on.
    """

    scorer_weights: dict[str, float]
    series_in_order: bool
    variety_penalty: float = Field(
        0.0, ge=0.0, le=UserPreferenceConfig.MAX_VARIETY_PENALTY
    )
    custom_rules: list[str]
    content_length_preferences: dict[str, str] = Field(default_factory=dict)


class SyncSourceProgressResponse(BaseModel):
    """Per-source progress slot for a multi-source sync job."""

    source: str
    items_processed: int
    total_items: int | None
    current_item: str | None
    progress_percent: int | None
    items_added: int
    items_updated: int
    items_unchanged: int
    #: Failures past the executor's cap, so a door showing fewer than it was
    #: sent can still name the run's true error total.
    omitted_errors: int


class SyncErrorResponse(BaseModel):
    source: str
    message: str


class SyncJobResponse(BaseModel):
    """Response model for sync job status."""

    source: str
    status: str
    started_at: str | None
    completed_at: str | None
    items_processed: int
    total_items: int | None
    current_item: str | None
    current_source: str | None
    error_message: str | None
    progress_percent: int | None
    items_added: int
    items_updated: int
    items_unchanged: int
    errors: list[SyncErrorResponse] = []
    sources: list[SyncSourceProgressResponse] = []


class SyncStatusResponse(BaseModel):
    """Response model for the aggregate sync status across every job."""

    status: str
    jobs: list[SyncJobResponse] = []


class SyncRunResponse(BaseModel):
    """One finished sync run, as the history endpoint reports it."""

    source_id: str
    started_at: str
    finished_at: str
    status: str
    items_added: int
    items_updated: int
    items_unchanged: int
    total_items: int
    errors: list[str]
    omitted_errors: int


class UserPreferenceUpdateRequest(BaseModel):
    """Request model for updating user preferences (partial merge).

    The merge is additive, so the key set is what bounds the stored blob.
    Both dicts are keyed on a closed one; ``custom_rules`` is free text and
    carries a count.
    """

    scorer_weights: dict[ScorerName, PreferenceWeight] | None = None
    series_in_order: bool | None = None
    variety_penalty: float | None = Field(
        None, ge=0.0, le=UserPreferenceConfig.MAX_VARIETY_PENALTY
    )
    custom_rules: list[CustomRuleText] | None = Field(
        None, max_length=UserPreferenceConfig.MAX_CUSTOM_RULES
    )
    content_length_preferences: dict[ContentTypeName, LengthPreferenceName] | None = (
        None
    )


class IgnoreItemRequest(BaseModel):
    """Request model for setting item ignored status."""

    ignored: bool = Field(..., description="Whether to ignore the item")


class IgnoreItemResponse(BaseModel):
    db_id: int
    title: str
    ignored: bool
    message: str


class ItemEditRequest(BaseModel):
    """Request model for editing a content item from the UI.

    Every field distinguishes omitted from supplied: an absent one leaves the
    stored value alone, and a null clears ``rating`` or ``review``. A null
    ``status`` is refused instead. See ``edit_item``.

    The fields the edit dialog can overrun — review, creator, release year —
    carry no bound here: ``edit_item`` refuses them with a sentence the dialog
    can show, where a constraint would answer an unreadable 422.
    """

    status: str | None = Field(None, description="Status value")
    rating: int | None = Field(None, ge=1, le=5)
    review: str | None = None
    seasons_watched: list[Annotated[int, Field(ge=1, le=MAX_SEASONS)]] | None = Field(
        None, max_length=MAX_SEASONS
    )
    genres: list[Annotated[str, Field(max_length=MAX_GENRE_TAG_LENGTH)]] | None = Field(
        None, max_length=MAX_GENRES, description="Manual genres (overwrite)"
    )
    tags: list[Annotated[str, Field(max_length=MAX_GENRE_TAG_LENGTH)]] | None = Field(
        None, max_length=MAX_TAGS, description="Manual tags (overwrite)"
    )
    description: str | None = Field(
        None, max_length=MAX_DESCRIPTION_LENGTH, description="Manual description"
    )
    release_year: int | str | None = Field(None, description="Corrected year")
    creator: CorrectedCreator | None = None

    @property
    def corrected_year(self) -> int | None:
        """``None`` for text no ``int`` takes, which ``edit_item`` refuses."""
        if self.release_year is None:
            return None
        text = str(self.release_year).strip()
        # Python refuses ``int`` on a decimal string over 4300 digits, and that
        # ValueError would escape as a 500 rather than the refusal sentence.
        if len(text) > len(str(MAX_RELEASE_YEAR)) or not text.isdecimal():
            return None
        return int(text)


class EnrichmentStartRequest(BaseModel):
    """Request model for starting enrichment."""

    content_type: str | None = Field(
        None, description="Content type filter (book, movie, tv_show, video_game)"
    )
    user_id: int = Field(1, ge=1, description="User ID for filtering items")
    retry_not_found: bool = Field(
        False, description="Re-process items previously marked as not_found"
    )


class EnrichmentResetRequest(BaseModel):
    """Request model for resetting enrichment status."""

    provider: str | None = Field(
        None,
        description="Reset items enriched by this provider (tmdb, openlibrary, rawg)",
    )
    content_type: str | None = Field(
        None, description="Reset items of this content type"
    )
    item_id: int | None = Field(
        None, ge=1, description="Restore this one item to automatic enrichment"
    )
    user_id: int = Field(1, ge=1, description="User ID for filtering items")


class GogExchangeRequest(BaseModel):
    """Request model for GOG token exchange."""

    code_or_url: str = Field(
        ...,
        max_length=2000,
        description="Authorization code or full redirect URL from GOG",
    )


class EpicExchangeRequest(BaseModel):
    """Request model for Epic Games token exchange."""

    code_or_json: str = Field(
        ...,
        max_length=4000,
        description="Authorization code or JSON response from Epic Games",
    )


class TraktPollRequest(BaseModel):
    """Request model for one Trakt device-approval poll."""

    device_code: str = Field(
        ...,
        min_length=10,
        max_length=256,
        description="Device code returned by POST /trakt/start-device-flow",
    )


class EnrichmentJobStatusResponse(BaseModel):
    """Response model for enrichment job status."""

    running: bool = False
    completed: bool = False
    cancelled: bool = False
    items_processed: int = 0
    items_enriched: int = 0
    items_failed: int = 0
    items_not_found: int = 0
    total_items: int = 0
    current_item: str = ""
    content_type: str | None = None
    errors: list[str] = Field(default_factory=list)
    elapsed_seconds: float = 0.0
    progress_percent: float = 0.0


class EnrichmentStatsResponse(BaseModel):
    """Response model for enrichment statistics."""

    enabled: bool = False
    total: int = 0
    resettable: int = 0
    enriched: int = 0
    pending: int = 0
    not_found: int = 0
    failed: int = 0
    by_provider: dict[str, int] = Field(default_factory=dict)
    by_quality: dict[str, int] = Field(default_factory=dict)


class ThemePreferenceResponse(BaseModel):
    """The theme a user's interface paints, empty when they have picked none."""

    theme: str


class ThemePreferenceRequest(BaseModel):
    """Request model for picking a user's theme."""

    theme: ThemeId


class SourceFieldSchema(BaseModel):
    """One field in a source plugin's config schema."""

    name: str
    field_type: str
    required: bool
    default: Any = None
    description: str = ""
    sensitive: bool = False


class SyncIntervalOption(BaseModel):
    """One cadence preset the client offers, key and its label."""

    key: str
    label: str


class SourceSchemaResponse(BaseModel):
    """Plugin config schema for a single source (drives autogen UI/CLI)."""

    source_id: str
    plugin: str
    plugin_display_name: str
    fields: list[SourceFieldSchema]
    sync_intervals: list[SyncIntervalOption]


class SourceConfigResponse(BaseModel):
    """Current config values for a source. Sensitive fields are never returned."""

    source_id: str
    plugin: str
    plugin_display_name: str
    enabled: bool
    migrated: bool
    migrated_at: str | None
    field_values: dict[str, Any]
    secret_status: dict[str, bool]
    sync_interval: str


class SourceConfigUpdateRequest(BaseModel):
    """Bulk update of non-sensitive fields for a migrated source."""

    values: dict[str, Any]


class SourceSecretUpdateRequest(BaseModel):
    """Set or rotate a single sensitive field."""

    value: str


class SourceEnabledUpdateRequest(BaseModel):
    """Toggle the enabled flag for a migrated source."""

    enabled: bool


class SourceScheduleUpdateRequest(BaseModel):
    """Set the sync cadence for a migrated source."""

    interval: str


class SourceMigrationResponse(BaseModel):
    """Result of migrating a YAML source entry into the database."""

    source_id: str
    migrated_at: str
    fields_migrated: list[str] = Field(
        description="Non-sensitive fields the source's database row now holds."
    )
    secrets_migrated: list[str] = Field(
        description=(
            "Sensitive fields the source now holds an encrypted credential "
            "for, whichever pass stored it — startup migrates a file-held "
            "secret before any request reaches this route."
        )
    )


class PluginInfoResponse(BaseModel):
    """One installed source plugin's metadata for the Add-Source picker."""

    name: str
    display_name: str
    description: str
    content_types: list[str]
    requires_api_key: bool
    requires_network: bool
    fields: list[SourceFieldSchema]


class PluginListResponse(BaseModel):
    """Every installed plugin, and every module that failed to become one."""

    plugins: list[PluginInfoResponse]
    import_errors: list[PluginImportErrorResponse]


class SourceCreateRequest(BaseModel):
    """Body for ``POST /api/sync/sources`` — create a new DB-backed source."""

    id: str = Field(..., max_length=64)
    plugin: str = Field(..., max_length=128)
    values: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class ImporterResponse(BaseModel):
    """One import format, for the upload panel's picker.

    ``import-formats`` emits this key set field for field.
    """

    name: str
    display_name: str
    description: str
    #: False where the format itself decides, as a book-site export does.
    requires_content_type: bool


class ImportTemplateResponse(BaseModel):
    """One blank file the operator can fill in and upload back.

    ``import-template`` emits this key set field for field.
    """

    importer: str
    content_type: str
    filename: str


class ImportResponse(BaseModel):
    """What one upload did: five counts, capped per-row misses with a tally,
    and file-level notes.

    The ``import`` command's ``--format json`` emits this key set field for
    field, so neither interface may add, drop or rename one alone.
    """

    importer: str
    content_type: str | None
    filename: str | None
    added: int
    updated: int
    unchanged: int
    skipped: int
    failed: int
    total_rows: int
    errors: list[str]
    notes: list[str]


class SettingValidationView(BaseModel):
    """Validation constraints for a setting (any field may be null)."""

    min: float | None = None
    max: float | None = None
    max_length: int | None = None
    pattern: str | None = None


class SettingView(BaseModel):
    """One global setting's metadata plus its value/secret state.

    ``value`` and ``db_overridden`` are present only for non-sensitive
    settings; ``has_secret`` is present only for sensitive ones. The omitted
    fields are dropped from the response via ``response_model_exclude_unset``.
    """

    key: str
    section: str
    label: str
    help: str
    type: str
    widget: str
    choices: list[str] | None
    validation: SettingValidationView | None
    advanced: bool
    restart_required: bool
    sensitive: bool
    value: Any = None
    db_overridden: bool | None = None
    has_secret: bool | None = None


class SettingsSection(BaseModel):
    """A group of settings sharing a top-level section."""

    section: str
    settings: list[SettingView]


class SettingsResponse(BaseModel):
    """Every in-scope setting grouped by section."""

    sections: list[SettingsSection]


class SettingsUpdateRequest(BaseModel):
    """Bulk update of non-sensitive settings (validated all-or-nothing)."""

    updates: dict[str, Any]


class SettingSecretRequest(BaseModel):
    """Set or rotate a single sensitive setting's value."""

    key: str
    value: str


class DuplicateSideResponse(BaseModel):
    db_id: int
    title: str
    source: str | None
    creator: str | None
    release_year: int | None
    also_offered: str


class DuplicateSuggestionResponse(BaseModel):
    content_type: str
    evidence: str
    evidence_label: str
    evidence_detail: str
    survivor_id: int
    copies: list[DuplicateSideResponse]


class DuplicateSuggestionPageResponse(BaseModel):
    total: int
    skipped_note: str
    suggestions: list[DuplicateSuggestionResponse]


class MergeResponse(BaseModel):
    id: int
    survivor_id: int
    survivor_title: str
    absorbed_id: int
    absorbed_title: str
    evidence: str
    evidence_label: str
    evidence_detail: str | None
    merged_at: str


class DeclinedPairResponse(BaseModel):
    one_id: int
    one_title: str
    other_id: int
    other_title: str


class MergeRequest(BaseModel):
    survivor_id: ItemDbId
    absorbed_id: ItemDbId


class DeclineDuplicateRequest(BaseModel):
    """*one_id* is the copy set apart, *other_ids* the copies it is not; storage
    stores one pair per refusal, lowest id first, either order round."""

    one_id: ItemDbId
    other_ids: Annotated[
        list[ItemDbId], Field(min_length=1, max_length=MAX_DECLINE_OTHERS)
    ]


def _get_recommendations_config(config: dict[str, Any] | None) -> RecommendationsConfig:
    """Extract recommendations config from the loaded config dict.

    Falls back to model defaults when the config or section is absent.
    """
    rec_section = config.get("recommendations", {}) if config else {}
    return RecommendationsConfig(
        **{
            k: rec_section[k]
            for k in ("max_count", "default_count")
            if k in rec_section
        }
    )


@router.get("/recommendations", response_model=list[RecommendationResponse])
def get_recommendations(
    engine: RequiredEngine,
    type: str = Query(
        ..., description="Content type (book, movie, tv_show, video_game)"
    ),
    count: int = Query(5, ge=1, description="Number of recommendations"),
    user_id: int = Query(1, ge=1, description="User ID for personalized preferences"),
) -> list[RecommendationResponse]:
    """Get personalized recommendations.

    Args:
        type: Content type
        count: Number of recommendations
        user_id: User ID for loading per-user preferences

    Returns:
        List of recommendations
    """
    storage = get_storage()
    config = get_config()

    # Validate count against config-driven max_count (may be tighter than hard limit)
    max_count = _get_recommendations_config(config).max_count
    if count > max_count:
        raise HTTPException(
            status_code=400,
            detail="Requested count exceeds the maximum allowed",
        )

    try:
        content_type = ContentType.from_string(type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid content type. Valid options: book, movie, tv_show, video_game",
        ) from None

    try:
        # Load user preferences if storage is available
        user_preference_config: UserPreferenceConfig | None = None
        if storage:
            user_preference_config = storage.get_user_preference_config(user_id)

        recommendations = engine.generate_recommendations(
            content_type=content_type,
            count=count,
            user_preference_config=user_preference_config,
        )

        return [
            RecommendationResponse.model_validate(rec.to_payload())
            for rec in recommendations
        ]

    except Exception as error:
        # The engine walks the library, so its errors quote item titles.
        logger.error("Error generating recommendations: %s", exception_for_log(error))
        raise HTTPException(
            status_code=500, detail="Failed to generate recommendations"
        ) from error


@router.get("/users", response_model=list[UserResponse])
def list_users(storage: RequiredStorage) -> list[UserResponse]:
    """List all users.

    Returns:
        List of users.
    """
    return [as_user_response(storage, user) for user in storage.get_all_users()]


@router.patch("/users/{user_id}", response_model=UserResponse)
def rename_account(
    user_id: UserIdPath,
    request: UserUpdateRequest,
    storage: RequiredStorage,
    user: CurrentUser,
) -> UserResponse:
    """Change an account's username and display name.

    Returns:
        The renamed account.

    Raises:
        HTTPException: 403 for anybody else's account, 404 when it is gone.
    """
    _refuse_another_account(user_id, user)
    try:
        renamed = storage.update_user_identity(
            user_id, request.username, request.display_name or None
        )
    except UnknownUserError:
        raise HTTPException(status_code=404, detail="User not found.") from None
    return as_user_response(storage, renamed)


@router.put("/users/{user_id}/password", status_code=204)
def change_password(
    user_id: UserIdPath,
    request: PasswordChangeRequest,
    http_request: Request,
    storage: RequiredStorage,
    user: CurrentUser,
) -> None:
    """Replace an account's password, signing every other browser out.

    Raises:
        HTTPException: 403 for anybody else's account, 401 when the current
            password is wrong, 400 when the new one is under the floor.
    """
    _refuse_another_account(user_id, user)
    if (
        storage.accounts.verify_password(user["username"], request.current_password)
        is None
    ):
        raise HTTPException(status_code=401, detail=_WRONG_CURRENT_PASSWORD)

    try:
        storage.accounts.set_password(user_id, request.new_password)
    except PasswordTooShortError as error:
        raise HTTPException(status_code=400, detail=PASSWORD_TOO_SHORT) from error
    # The token is a live session by the time this runs — the dependency that
    # produced ``user`` looked it up — so the caller keeps the browser they
    # changed the password in, and every other one is signed out.
    token = http_request.cookies[SESSION_COOKIE]
    storage.accounts.revoke_other_sessions(user_id, token)


def _item_to_response(item: "ContentItem") -> ContentItemResponse:
    """Convert a ContentItem to a ContentItemResponse via the shared dict."""
    return ContentItemResponse.model_validate(item_to_dict(item))


def _merge_to_response(record: MergeRecord) -> MergeResponse:
    return MergeResponse.model_validate(merge_to_dict(record))


def _declined_to_response(pair: DeclinedPair) -> DeclinedPairResponse:
    return DeclinedPairResponse.model_validate(declined_pair_to_dict(pair))


def _duplicate_type(type_name: str | None) -> ContentType | None:
    if type_name is None:
        return None
    try:
        return ContentType.from_string(type_name)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid content type. Valid options: book, movie, tv_show, video_game",
        ) from None


def _refused_merge(error: MergeError) -> HTTPException:
    """409, not 404: it names the row or merge to deal with first."""
    return HTTPException(status_code=409, detail=str(error))


@router.get("/items", response_model=list[ContentItemResponse])
def list_items(
    storage: RequiredStorage,
    type: str | None = Query(None, description="Content type filter"),
    status: str | None = Query(None, description="Status filter"),
    user_id: int = Query(1, ge=1, description="User ID"),
    limit: int = Query(50, ge=1, le=200, description="Maximum results per page"),
    offset: int = Query(
        0, ge=0, description="Number of items to skip (for pagination)"
    ),
    sort_by: str = Query(
        "title",
        description="Sort order: title (ignores articles), updated_at, rating, created_at",
    ),
    include_ignored: bool = Query(
        False,
        description="Whether to include ignored items (default: hide ignored)",
    ),
    enrichment: EnrichmentFilter | None = Query(
        None,
        description="Filter by enrichment state: enriched or not_enriched",
    ),
    search: str | None = Query(
        None,
        max_length=MAX_SEARCH_LENGTH,
        description="Search term for title/creator/series",
    ),
    needs_rating: bool = Query(
        False,
        description="Only return completed items that have no rating yet",
    ),
) -> list[ContentItemResponse]:
    """List content items with optional filters.

    Args:
        type: Optional content type filter.
        status: Optional consumption status filter.
        user_id: User ID to filter by.
        limit: Maximum number of results per page.
        offset: Number of items to skip (for pagination).
        sort_by: Sort order (default: title, which ignores leading articles).
        include_ignored: Whether to include ignored items (default: False).
        enrichment: Optional enrichment-state filter (enriched/not_enriched).
        search: Optional search term matched against title, creator and series.
        needs_rating: When True, return only completed items with no rating.
            Forces status to completed (overriding any status param).

    Returns:
        List of content items.
    """
    content_type = None
    if type is not None:
        try:
            content_type = ContentType.from_string(type)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid content type. Valid options: book, movie, tv_show, video_game",
            ) from None

    consumption_status = None
    if status is not None:
        try:
            consumption_status = ConsumptionStatus(status.lower())
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid status. Valid options: unread, currently_consuming, completed",
            ) from None

    # Validate sort_by parameter
    if sort_by.lower() not in VALID_SORT_OPTIONS:
        raise HTTPException(
            status_code=400,
            detail="Invalid sort_by. Valid options: created_at, rating, title, updated_at",
        )

    # needs_rating means "completed AND unrated": completed status is implied
    # and takes precedence over any explicitly-passed status param.
    if needs_rating:
        consumption_status = ConsumptionStatus.COMPLETED

    items = storage.get_content_items(
        user_id=user_id,
        content_type=content_type,
        status=consumption_status,
        unrated_only=needs_rating,
        limit=limit,
        offset=offset,
        sort_by=sort_by.lower(),
        include_ignored=include_ignored,
        enrichment=enrichment,
        search=search,
    )

    return [_item_to_response(item) for item in items]


@router.get("/items/export")
def export_items(
    storage: RequiredStorage,
    type: str | None = Query(
        None, description="Content type (book, movie, tv_show, video_game)"
    ),
    format: str = Query("csv", description="Export format: csv or json"),
    user_id: int = Query(1, ge=1, description="User ID"),
) -> Response:
    """Export library items as CSV or JSON file download.

    Args:
        type: Content type to export, or None for the whole library
        format: Export format (csv or json)
        user_id: User ID for filtering items

    Returns:
        File download response
    """
    content_type: ContentType | None = None
    if type is not None:
        try:
            content_type = ContentType.from_string(type)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid content type. Valid options: book, movie, tv_show, video_game",
            ) from None

    export_format = format.lower()
    if export_format not in {"csv", "json"}:
        raise HTTPException(
            status_code=400,
            detail="Invalid format. Valid options: csv, json",
        )

    items = storage.get_content_items(
        user_id=user_id,
        content_type=content_type,
        include_ignored=True,
    )

    stem = "library" if content_type is None else f"{get_enum_value(content_type)}s"
    filename = f"{stem}.{export_format}"

    if export_format == "csv":
        content = export_items_csv(items, content_type)
        media_type = "text/csv"
    else:
        content = export_items_json(items, content_type)
        media_type = "application/json"

    # The app's response class only covers a body FastAPI renders, and this
    # one arrives serialised, so it names the same encode itself.
    return SurrogateSafeResponse(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/importers", response_model=list[ImporterResponse])
def list_importers() -> list[ImporterResponse]:
    """List every import format, in the order the picker offers them."""
    return [
        ImporterResponse(
            name=importer.name,
            display_name=importer.display_name,
            description=importer.description,
            requires_content_type=not importer.content_types,
        )
        for importer in IMPORTERS
    ]


@router.get("/import/templates", response_model=list[ImportTemplateResponse])
def list_import_templates() -> list[ImportTemplateResponse]:
    """List every template this install ships, keyed as the picker is."""
    try:
        templates = available_templates()
    except TemplatesUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    return [
        ImportTemplateResponse(
            importer=template.importer,
            content_type=template.content_type,
            filename=template.filename,
        )
        for template in templates
    ]


@router.get("/import/templates/download")
def download_import_template(
    importer: str = Query(..., description="Import format name"),
    content_type: str = Query(
        ..., description="Content type (book, movie, tv_show, video_game)"
    ),
) -> Response:
    """Serve one template as a download, byte for byte as it ships.

    Both parameters are looked up as dictionary keys, never joined onto a path.
    """
    try:
        template = find_template(importer, content_type)
    except TemplatesUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    if template is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No template for that import format and content type. "
                "GET /api/import/templates lists the ones this install ships."
            ),
        )

    return SurrogateSafeResponse(
        content=read_template(template),
        media_type=template.media_type,
        headers={"Content-Disposition": f'attachment; filename="{template.filename}"'},
    )


@router.post("/import", response_model=ImportResponse)
def import_upload(
    storage: RequiredStorage,
    config: RequiredConfig,
    user: CurrentUser,
    file: UploadFile,
    importer: Annotated[str, Form(description="Import format name")],
    content_type: Annotated[str | None, Form()] = None,
) -> ImportResponse:
    """Import an uploaded export in one shot.

    No source, no cadence, no sync run, and no importer opens a path. Starlette
    spools a part over ``spool_max_size`` into the temp directory, unnamed and
    gone when the form closes.
    """
    chosen = get_importer(importer)
    if chosen is None:
        offered = ", ".join(candidate.name for candidate in IMPORTERS)
        raise HTTPException(
            status_code=400, detail=f"Unknown import format. Valid options: {offered}"
        )

    resolved_type = None
    if content_type:
        try:
            resolved_type = ContentType.from_string(content_type)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid content type. Valid options: book, movie, tv_show, video_game",
            ) from None

    try:
        result = import_file(
            storage,
            user["id"],
            decode_import_text(file.file.read()),
            chosen,
            resolved_type,
            mark_for_enrichment=auto_enrich_enabled(config),
        )
    except ImporterError as error:
        logger.info(
            "[IMPORT] %s refused the file: %s", chosen.name, exception_for_log(error)
        )
        # Answered verbatim, unlike the source-config refusals: the message
        # quotes the operator's own file, which is what makes it actionable.
        raise HTTPException(status_code=400, detail=str(error)) from error

    return ImportResponse(
        importer=result.importer,
        content_type=(
            get_enum_value(result.content_type) if result.content_type else None
        ),
        filename=file.filename,
        added=result.added,
        updated=result.updated,
        unchanged=result.unchanged,
        skipped=result.skipped,
        failed=result.failed,
        total_rows=result.total_rows,
        errors=result.errors,
        notes=result.notes,
    )


@router.patch("/items/{db_id}/ignore", response_model=IgnoreItemResponse)
def set_item_ignored(
    db_id: int,
    request: IgnoreItemRequest,
    storage: RequiredStorage,
    user_id: int = Query(1, ge=1, description="User ID for authorization"),
) -> IgnoreItemResponse:
    """Set the ignored status of a content item.

    Ignored items are excluded from recommendations.

    Args:
        db_id: Database ID of the item
        request: Request with ignored status
        user_id: User ID for authorization

    Returns:
        Updated item info
    """
    # Verify item exists and belongs to user
    item = storage.get_content_item(db_id, user_id=user_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    success = storage.set_item_ignored(db_id, request.ignored, user_id=user_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update item")

    return IgnoreItemResponse.model_validate(
        ignore_result_to_dict(db_id, item.title, request.ignored)
    )


@router.get("/items/{db_id}", response_model=ContentItemResponse)
def get_single_item(
    db_id: int,
    storage: RequiredStorage,
    user_id: int = Query(1, ge=1, description="User ID for authorization"),
) -> ContentItemResponse:
    """Get a single content item by database ID.

    Args:
        db_id: Database ID of the item.
        user_id: User ID for authorization.

    Returns:
        Content item details.
    """
    item = storage.get_content_item(db_id, user_id=user_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    return _item_to_response(item)


def _edit_bound_crossed(request: ItemEditRequest) -> str | None:
    """The first bound an edit crosses, worded as the CLI words its own."""
    if request.review is not None:
        if not request.review.strip():
            return "Review cannot be blank. Send null to clear it."
        if len(request.review) > MAX_REVIEW_LENGTH:
            return f"Review must be at most {MAX_REVIEW_LENGTH} characters."
    if request.creator is not None:
        if not request.creator:
            return "Creator cannot be empty."
        if len(request.creator) > MAX_CREATOR_LENGTH:
            return f"Creator must be at most {MAX_CREATOR_LENGTH} characters."
    if request.release_year is not None:
        year = request.corrected_year
        if year is None or not MIN_RELEASE_YEAR <= year <= MAX_RELEASE_YEAR:
            return (
                "Release year must be a number between "
                f"{MIN_RELEASE_YEAR} and {MAX_RELEASE_YEAR}."
            )
    return None


@router.patch("/items/{db_id}", response_model=ContentItemResponse)
def edit_item(
    db_id: int,
    request: ItemEditRequest,
    storage: RequiredStorage,
    user_id: int = Query(1, ge=1, description="User ID for authorization"),
) -> ContentItemResponse:
    """Edit a content item from the UI.

    Only the fields the body carries are written: omitting ``rating`` or
    ``review`` leaves it alone, a null clears it, and an emptied
    ``description``, ``genres`` or ``tags`` clears that.
    """
    supplied = request.model_fields_set
    status: str | Unset = UNSET
    if "status" in supplied:
        if request.status not in {"unread", "currently_consuming", "completed"}:
            raise HTTPException(
                status_code=400,
                detail="Invalid status. Valid options: completed, currently_consuming, unread",
            )
        status = request.status

    crossed = _edit_bound_crossed(request)
    if crossed is not None:
        raise HTTPException(status_code=400, detail=crossed)

    try:
        success = storage.update_item_from_ui(
            db_id=db_id,
            status=status,
            rating=request.rating if "rating" in supplied else UNSET,
            review=request.review if "review" in supplied else UNSET,
            seasons_watched=request.seasons_watched,
            genres=request.genres,
            tags=request.tags,
            description=request.description,
            release_year=request.corrected_year,
            creator=request.creator,
            user_id=user_id,
        )
    except UncorrectableFieldError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not success:
        raise HTTPException(status_code=404, detail="Item not found")

    # Fetch and return the updated item
    updated_item = storage.get_content_item(db_id, user_id=user_id)
    if not updated_item:
        raise HTTPException(status_code=404, detail="Item not found after update")

    return _item_to_response(updated_item)


@router.get("/duplicates", response_model=DuplicateSuggestionPageResponse)
def list_duplicates(
    storage: RequiredStorage,
    user_id: int = Query(1, ge=1, description="User ID"),
    type: str | None = Query(None, description="Content type filter"),
    limit: int = Query(
        SUGGESTION_PAGE_DEFAULT,
        ge=1,
        le=SUGGESTION_PAGE_MAX,
        description="Maximum works to offer",
    ),
) -> DuplicateSuggestionPageResponse:
    """List each work's suspected copies, with the evidence that grouped them."""
    page = storage.list_duplicate_suggestions(
        user_id=user_id, content_type=_duplicate_type(type), limit=limit
    )
    return DuplicateSuggestionPageResponse.model_validate(suggestion_page_to_dict(page))


@router.get("/duplicates/declined", response_model=list[DeclinedPairResponse])
def list_declined_duplicates(
    storage: RequiredStorage,
    user_id: int = Query(1, ge=1, description="User ID"),
) -> list[DeclinedPairResponse]:
    """List the pairs refused for good, lowest id first."""
    return [
        _declined_to_response(pair)
        for pair in storage.list_declined_duplicates(user_id=user_id)
    ]


@router.post("/duplicates/declined", response_model=list[DeclinedPairResponse])
def decline_duplicate_pair(
    request: DeclineDuplicateRequest,
    storage: RequiredStorage,
    user_id: int = Query(1, ge=1, description="User ID"),
) -> list[DeclinedPairResponse]:
    """Keep one copy off the list for good, against every copy named."""
    pairs = storage.decline_duplicate_suggestion(
        request.one_id, request.other_ids, user_id=user_id
    )
    if not pairs:
        raise HTTPException(
            status_code=404,
            detail=decline_refusal_message(request.one_id, request.other_ids),
        )
    return [_declined_to_response(pair) for pair in pairs]


@router.delete(
    "/duplicates/declined/{one_id}/{other_id}", response_model=DeclinedPairResponse
)
def undecline_duplicate_pair(
    one_id: ItemIdPath,
    other_id: ItemIdPath,
    storage: RequiredStorage,
    user_id: int = Query(1, ge=1, description="User ID"),
) -> DeclinedPairResponse:
    """Offer a refused pair again."""
    try:
        pair = storage.undecline_duplicate_suggestion(one_id, other_id, user_id=user_id)
    except MergeError as error:
        raise _refused_merge(error) from error
    if pair is None:
        raise HTTPException(
            status_code=404,
            detail=f"Items {one_id} and {other_id} are not a declined pair.",
        )
    return _declined_to_response(pair)


@router.get("/merges", response_model=list[MergeResponse])
def list_merges(
    storage: RequiredStorage,
    user_id: int = Query(1, ge=1, description="User ID"),
) -> list[MergeResponse]:
    """List the merges in force, newest first — the order they undo in."""
    return [
        _merge_to_response(record)
        for record in storage.list_content_item_merges(user_id=user_id)
    ]


@router.post("/merges", response_model=MergeResponse)
def merge_items(
    request: MergeRequest,
    storage: RequiredStorage,
    user_id: int = Query(1, ge=1, description="User ID"),
) -> MergeResponse:
    """Merge one item into another, keeping ``survivor_id``."""
    try:
        record = storage.merge_content_items(
            request.survivor_id,
            request.absorbed_id,
            MergeEvidence.MANUAL,
            user_id=user_id,
        )
    except MergeError as error:
        raise _refused_merge(error) from error
    return _merge_to_response(record)


@router.delete("/merges/{merge_id}", response_model=MergeResponse)
def unmerge_items(
    merge_id: ItemIdPath,
    storage: RequiredStorage,
    user_id: int = Query(1, ge=1, description="User ID"),
) -> MergeResponse:
    """Undo one merge, putting the absorbed item back."""
    try:
        record = storage.unmerge_content_items(merge_id, user_id=user_id)
    except MergeError as error:
        raise _refused_merge(error) from error
    if record is None:
        raise HTTPException(status_code=404, detail=f"Merge {merge_id} not found.")
    return _merge_to_response(record)


@router.get("/users/{user_id}/preferences", response_model=UserPreferenceResponse)
def get_user_preferences(
    user_id: UserIdPath, storage: RequiredStorage
) -> UserPreferenceResponse:
    """Get user preference configuration.

    Args:
        user_id: User ID.

    Returns:
        User preference configuration.
    """
    preference_config = storage.get_user_preference_config(user_id)
    return UserPreferenceResponse(**preference_config.to_dict())


@router.put("/users/{user_id}/preferences", response_model=UserPreferenceResponse)
def update_user_preferences(
    user_id: UserIdPath,
    request: UserPreferenceUpdateRequest,
    storage: RequiredStorage,
) -> UserPreferenceResponse:
    """Update user preference configuration (partial merge).

    Only fields present in the request body are updated; omitted fields
    retain their current values.

    Args:
        user_id: User ID.
        request: Partial preference update.

    Returns:
        Updated user preference configuration.
    """

    # Storage does the read, this merge and the write as one locked operation:
    # two of these requests otherwise both read the old ``users.settings`` blob
    # and the later write discards the earlier one.
    def merge_supplied_fields(existing: UserPreferenceConfig) -> None:
        if request.scorer_weights is not None:
            existing.scorer_weights.update(request.scorer_weights)
        if request.series_in_order is not None:
            existing.series_in_order = request.series_in_order
        if request.variety_penalty is not None:
            existing.variety_penalty = request.variety_penalty
        if request.custom_rules is not None:
            existing.custom_rules = request.custom_rules
        if request.content_length_preferences is not None:
            existing.content_length_preferences.update(
                request.content_length_preferences
            )

    # The write is an UPDATE keyed on the id, so it is the write that knows
    # whether the user exists. A pre-check here is a second answer to the same
    # question, and the two disagreeing would be a 500.
    try:
        updated = storage.merge_user_preference_config(user_id, merge_supplied_fields)
    except UnknownUserError as error:
        raise HTTPException(status_code=404, detail="User not found.") from error

    return UserPreferenceResponse(**updated.to_dict())


@router.delete("/users/{user_id}/preferences", response_model=UserPreferenceResponse)
def reset_user_preferences(
    user_id: UserIdPath, storage: RequiredStorage
) -> UserPreferenceResponse:
    """Reset every preference to its default, as ``preferences reset`` does.

    Returns:
        The defaults now stored, so the caller need not read them back.

    Raises:
        HTTPException: 404 when nobody carries *user_id*.
    """
    defaults = UserPreferenceConfig()
    try:
        storage.save_user_preference_config(user_id, defaults)
    except UnknownUserError as error:
        raise HTTPException(status_code=404, detail="User not found.") from error
    return UserPreferenceResponse(**defaults.to_dict())


@router.post("/complete", response_model=CompletionResponse)
def mark_complete(
    request: CompletionRequest, storage: RequiredStorage
) -> CompletionResponse:
    """Mark content as completed.

    A blank ``review`` is rejected rather than stored, as it is on the edit
    endpoint and on ``complete --review``: this is an overwriting door, so a
    blank accepted here would replace a review the user wrote. Unlike the edit
    endpoint, a null cannot clear one: it is indistinguishable from omitting
    the field, and either way the stored review is left alone.

    Args:
        request: Completion request

    Returns:
        Success message
    """
    try:
        content_type = ContentType.from_string(request.content_type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid content type. Valid options: book, movie, tv_show, video_game",
        ) from None

    item = ContentItem(
        id=None,
        title=request.title,
        author=request.author,
        content_type=content_type,
        status=ConsumptionStatus.COMPLETED,
        rating=request.rating,
        review=request.review,
    )

    try:
        db_id = storage.complete_content_item(item)
    except Exception as error:
        # The failing write is this request's title, author and review.
        logger.error("Error marking content as completed: %s", exception_for_log(error))
        raise HTTPException(
            status_code=500, detail="Failed to mark content as completed"
        ) from error

    return CompletionResponse.model_validate(completion_to_dict(request.title, db_id))


@router.post("/update")
def update_data(
    request: UpdateRequest, storage: RequiredStorage, config: RequiredConfig
) -> dict[str, Any]:
    """Start a background sync job for the specified data source.

    The sync runs in the background. Use GET /sync/status to monitor progress.
    Different sources can sync concurrently; a duplicate of a running label,
    or anything overlapping the all-sources run, is rejected with 409.

    Args:
        request: Update request specifying the source to sync.

    Returns:
        Message indicating sync was started or error if already running.
    """
    sync_manager = get_sync_manager()
    source = request.source
    source_label = humanize_source_id(source) if source != "all" else ALL_SOURCES_LABEL
    job_key = ALL_SOURCES_KEY if source == "all" else source_label

    # Two POSTs racing on the same label both pass any pre-check here, so that
    # duplicate is left to ``start_sync``'s atomic check-and-set below.

    # Resolve which sources to sync. ``resolve_inputs`` is the single source
    # of truth: it merges YAML ``inputs`` with DB-backed ``source_configs``,
    # injects ``_source_id``, and layers decrypted credentials — so it covers
    # sources created via the Add-source modal that live only in the database.
    if source == "all":
        # Overlapping whatever single run is already going would fetch and save
        # that source twice.
        if sync_manager.is_running():
            raise HTTPException(status_code=409, detail="A sync is already in progress")
        resolved = resolve_inputs(config, storage=storage)
    else:
        if sync_manager.is_running(ALL_SOURCES_KEY):
            raise HTTPException(status_code=409, detail="A sync is already in progress")
        # Single source: select the enabled, resolved entry matching ``source``.
        # Filtering the resolved list (not the YAML ``inputs`` map) is what lets
        # a DB-only source sync — and a disabled/unknown source yields no entry.
        resolved = [
            entry
            for entry in resolve_inputs(config, storage=storage)
            if entry.source_id == source
        ]
        if not resolved:
            # A 4xx (not a 200 "message") is required so the frontend ``catch``
            # clears the optimistic "syncing" flag; a 200 leaves the Sync button
            # stuck because no SyncJob is ever created to end the polling.
            logger.info(
                "Sync requested for unavailable source_id=%s", sanitize_for_log(source)
            )
            not_loaded = source_plugin_not_loaded(source, config, storage=storage)
            raise HTTPException(
                status_code=400,
                detail=(
                    unusable_detail(not_loaded)
                    if not_loaded is not None
                    else "Source is disabled or not configured."
                ),
            )
        # Validate the entry resolved above rather than resolving the id again:
        # a delete landing between the two makes the second lookup miss, and its
        # "unknown source" text carries the caller's own id onto the wire.
        source_entry = resolved[0]
        validation_errors = source_entry.plugin.validate_config(
            source_entry.config, storage=storage
        )
        if validation_errors:
            logger.warning(
                "Sync config validation failed for %s: %s",
                sanitize_for_log(source),
                sanitize_for_log(
                    redact_credentials(
                        "; ".join(validation_errors),
                        source_entry.plugin,
                        source_entry.config,
                    )
                ),
            )
            raise HTTPException(
                status_code=400,
                detail=misconfigured_detail(source_entry.plugin, validation_errors),
            )

    if not resolved:
        return {"message": "No sources enabled or configured for sync", "count": 0}

    claimed, refused = claim_sources(storage, [entry.source_id for entry in resolved])
    if not claimed:
        raise HTTPException(status_code=409, detail=already_syncing_detail(refused))
    resolved = [entry for entry in resolved if entry.source_id in claimed]

    sources_to_sync = [entry.source_id for entry in resolved]

    # The same builder the scheduler dispatches through, so a requested run and
    # a scheduled one are the same job.
    dispatch = build_sync_job(
        sync_manager,
        job_key,
        resolved,
        list(claimed.values()),
        storage,
        config,
        max_workers=request.max_workers,
    )

    refusal = sync_manager.start_sync(
        job_key, dispatch.run, on_complete=dispatch.on_complete
    )

    if refusal is not None:
        release_sources(storage, claimed.values())
        raise HTTPException(status_code=409, detail="A sync is already in progress")

    # humanize_source_id title-cases but strips nothing, so the request's own
    # source id reaches here with its newlines intact.
    logger.info(
        "[SYNC] Started background sync for: %s", sanitize_for_log(source_label)
    )
    started = f"Sync started for {source_label}. Use GET /api/sync/status to monitor progress."
    return {
        # The CLI names a source it could not claim and syncs the rest; dropping
        # `refused` here would read to the operator as "all of them synced".
        "message": (
            f"{already_syncing_detail(refused)} {started}" if refused else started
        ),
        "sources": sources_to_sync,
    }


@router.get("/status", response_model=StatusResponse)
def get_status() -> StatusResponse:
    """Get system status.

    Returns:
        System status information
    """
    engine = get_engine()
    storage = get_storage()
    config = get_config()

    components = {
        "engine": engine is not None,
        "storage": storage is not None,
    }

    all_ready = all(components.values())

    return StatusResponse(
        status="ready" if all_ready else "initializing",
        version=APP_VERSION,
        components=components,
        recommendations_config=_get_recommendations_config(config),
    )


@router.post("/config/reload")
def reload_config_endpoint() -> dict[str, Any]:
    """Reload configuration from disk.

    Useful for picking up config changes without restarting the server.

    Returns:
        Success status.
    """
    success = reload_config()
    if success:
        return {"success": True, "message": "Configuration reloaded successfully"}
    else:
        raise HTTPException(status_code=500, detail="Failed to reload configuration")


@router.get("/sync/sources", response_model=list[SyncSourceResponse])
def get_sync_sources(
    config: RequiredConfig, storage: RequiredStorage
) -> list[SyncSourceResponse]:
    """Get list of available sync sources from config and the database.

    Both components are guarded because the answer is assembled from both, and
    a missing half read as a wrong library rather than an outage: without
    config this returned 200 and an empty list, and without storage a list that
    drops DB-only sources and reports every migrated one off its stale YAML
    row rather than the authoritative DB values.
    """
    sources = get_available_sync_sources(config, storage=storage)
    return [
        SyncSourceResponse(
            id=source.id,
            display_name=source.display_name,
            plugin_display_name=source.plugin_display_name,
            enabled=source.enabled,
            plugin_not_loaded=(
                PluginNotLoadedResponse(
                    plugin=source.plugin_not_loaded.plugin,
                    failures=[
                        PluginImportErrorResponse(
                            module=failure.module, reason=failure.reason
                        )
                        for failure in source.plugin_not_loaded.failures
                    ],
                )
                if source.plugin_not_loaded is not None
                else None
            ),
            sync_interval=source.sync_interval,
            last_run_at=source.last_run_at,
            last_run_status=source.last_run_status,
            next_run_at=source.next_run_at,
        )
        for source in sources
    ]


@router.get("/sync/runs", response_model=list[SyncRunResponse])
def get_sync_runs(
    storage: RequiredStorage,
    source_id: str | None = Query(None, description="Only this source's runs"),
    limit: int = Query(20, ge=1, le=100, description="Maximum runs to return"),
    user_id: int = Query(1, ge=1, description="User ID"),
) -> list[SyncRunResponse]:
    """Finished sync runs, newest first, for one source or every source."""
    runs = (
        storage.sync_runs.list_for_source(user_id, source_id, limit)
        if source_id is not None
        else storage.sync_runs.list_recent(user_id, limit)
    )
    return [SyncRunResponse(**view) for view in build_runs_view(runs)]


@router.get("/plugins", response_model=PluginListResponse)
def list_plugins() -> PluginListResponse:
    """List every registered source plugin (for the Add-Source picker)."""
    return PluginListResponse(**build_plugins_view())


@router.post(
    "/sync/sources",
    response_model=SourceConfigResponse,
    status_code=201,
)
def create_source_endpoint(
    payload: SourceCreateRequest,
    storage: RequiredStorage,
    config: RequiredConfig,
) -> SourceConfigResponse:
    """Create a new DB-backed source.

    Sensitive fields must be set via ``PUT /secret/{key}`` *after* this
    call returns; the create path rejects them in the body to keep the
    sensitive-write surface narrow.

    Config is guarded because the answer is assembled from both halves, as it
    is on every other route that reads them: the id is refused when YAML
    already holds it. Unguarded, config being unreadable was indistinguishable
    from there being no YAML entry, so an outage turned a 409 into a DB source
    shadowing the YAML one — a write, and one the caller cannot see is wrong.
    """
    try:
        view = create_source(
            payload.id,
            payload.plugin,
            payload.values,
            storage,
            enabled=payload.enabled,
            config=config,
        )
    except SourceConfigError as error:
        raise _config_error_to_http(error) from error
    return SourceConfigResponse(**view)


@router.delete("/sync/sources/{source_id}", status_code=204)
def delete_source_endpoint(
    source_id: str, storage: RequiredStorage, config: RequiredConfig
) -> Response:
    """Drop a DB-backed source and clear its credentials.

    Config is guarded because clearing a credential stranded under the plugin
    name reads both halves of the source list: unread, a YAML source still on
    that plugin is indistinguishable from none.
    """
    try:
        delete_source(source_id, storage, config=config)
    except SourceConfigError as error:
        raise _config_error_to_http(error) from error
    return Response(status_code=204)


# Per-source configuration endpoints. Business logic lives in
# ``src.sources.service``; the endpoints below adapt those helpers to
# FastAPI / Pydantic so the CLI ``source`` group can share them.


_ERROR_KIND_TO_STATUS: dict[str, int] = {
    "not_found": 404,
    "not_migrated": 404,
    "invalid_field": 400,
    "not_sensitive": 400,
    "sensitive_in_config": 400,
    "conflict": 409,
    "invalid_id": 400,
    "unknown_plugin": 400,
}

# Fixed user-facing strings keyed by error kind so HTTP responses never
# echo back caller-controlled identifiers (path params would otherwise
# end up in JSON `detail` fields).
_ERROR_KIND_TO_DETAIL: dict[str, str] = {
    "not_found": "Field or source not found.",
    "not_migrated": "Source has not been migrated to the database.",
    "invalid_field": "Request references an unknown field.",
    "not_sensitive": "Field is not sensitive — use the config endpoint instead.",
    "sensitive_in_config": "Sensitive fields must be set via the secret endpoint.",
    "conflict": "A source with that id already exists.",
    "invalid_id": (
        "Source id must start with a lowercase letter and contain only "
        "lowercase letters, digits, underscores, and hyphens."
    ),
    "unknown_plugin": "The requested plugin is not registered.",
}


def require_plugin(
    source_id: str, storage: RequiredStorage, config: RequiredConfig
) -> SourcePlugin:
    """Resolve a source id to its plugin, or 404 if no source carries that id.

    Both halves are guarded before the lookup because either one missing runs
    the resolution off the other alone: with storage down a source that exists
    only in the database (created through POST /sync/sources, which rejects ids
    YAML already holds) reads as "not found", and with config down a source
    that exists only in YAML does — while every write on those same sources
    answers 503. One server state, one answer.
    """
    plugin = resolve_source_plugin(source_id, config, storage)
    if plugin is None:
        # A source whose plugin died stays in the listing, so answering "not
        # found" here contradicts what the user is looking at. Same words as
        # the sync refusal, per the disclosure carve-out in docs/SECURITY.md.
        not_loaded = source_plugin_not_loaded(source_id, config, storage)
        if not_loaded is not None:
            raise HTTPException(status_code=400, detail=unusable_detail(not_loaded))
        # Server-side log carries the identifier; the wire response stays generic.
        logger.info("Source lookup miss for source_id=%s", sanitize_for_log(source_id))
        raise HTTPException(status_code=404, detail="Source not found.")
    return plugin


ResolvedPlugin = Annotated[SourcePlugin, Depends(require_plugin)]


# Deliberately absent from the maps above: the source service builds these two
# messages from schema field names and the containment guard alone — never from
# a plugin's own words or caller input.
_KINDS_ANSWERED_WITH_THEIR_MESSAGE = {"invalid_values", "credential_move"}


def _config_error_to_http(error: SourceConfigError) -> HTTPException:
    # error.message embeds caller-supplied values, so only the kind is logged.
    logger.info("Source config error kind=%s", sanitize_for_log(error.kind))
    if error.kind in _KINDS_ANSWERED_WITH_THEIR_MESSAGE:
        return HTTPException(status_code=400, detail=error.message)
    return HTTPException(
        status_code=_ERROR_KIND_TO_STATUS.get(error.kind, 400),
        detail=_ERROR_KIND_TO_DETAIL.get(error.kind, "Invalid request."),
    )


@router.get("/sync/sources/{source_id}/schema", response_model=SourceSchemaResponse)
def get_source_schema(source_id: str, plugin: ResolvedPlugin) -> SourceSchemaResponse:
    """Return the plugin config schema for a source (drives autogen forms)."""
    return SourceSchemaResponse(**build_schema_view(source_id, plugin))


@router.get("/sync/sources/{source_id}/config", response_model=SourceConfigResponse)
def get_source_config_endpoint(
    source_id: str,
    plugin: ResolvedPlugin,
    config: RequiredConfig,
    storage: RequiredStorage,
) -> SourceConfigResponse:
    """Return current config values for a source. Sensitive fields are stripped."""
    return SourceConfigResponse(**build_config_view(source_id, plugin, config, storage))


@router.post(
    "/sync/sources/{source_id}/migrate", response_model=SourceMigrationResponse
)
def migrate_source_to_db(
    source_id: str,
    plugin: ResolvedPlugin,
    config: RequiredConfig,
    storage: RequiredStorage,
) -> SourceMigrationResponse:
    """Copy a YAML source entry into the database (idempotent)."""
    return SourceMigrationResponse(**migrate_source(source_id, plugin, config, storage))


@router.put("/sync/sources/{source_id}/config", response_model=SourceConfigResponse)
def update_source_config_endpoint(
    source_id: str,
    payload: SourceConfigUpdateRequest,
    plugin: ResolvedPlugin,
    config: RequiredConfig,
    storage: RequiredStorage,
) -> SourceConfigResponse:
    """Update non-sensitive fields on a migrated source."""
    try:
        update_source_config_values(source_id, plugin, storage, payload.values)
    except SourceConfigError as error:
        raise _config_error_to_http(error) from error
    return SourceConfigResponse(**build_config_view(source_id, plugin, config, storage))


@router.put("/sync/sources/{source_id}/secret/{key}", status_code=204)
def set_source_secret_endpoint(
    source_id: str,
    key: str,
    payload: SourceSecretUpdateRequest,
    plugin: ResolvedPlugin,
    storage: RequiredStorage,
) -> Response:
    """Encrypt and store a sensitive field for a source."""
    try:
        set_source_secret_value(source_id, plugin, storage, key, payload.value)
    except SourceConfigError as error:
        raise _config_error_to_http(error) from error
    return Response(status_code=204)


@router.delete("/sync/sources/{source_id}/secret/{key}", status_code=204)
def clear_source_secret_endpoint(
    source_id: str,
    key: str,
    plugin: ResolvedPlugin,
    storage: RequiredStorage,
) -> Response:
    """Delete a sensitive field's stored value for a source."""
    try:
        clear_source_secret_value(source_id, plugin, storage, key)
    except SourceConfigError as error:
        raise _config_error_to_http(error) from error
    return Response(status_code=204)


@router.put("/sync/sources/{source_id}/enabled", response_model=SourceConfigResponse)
def set_source_enabled_endpoint(
    source_id: str,
    payload: SourceEnabledUpdateRequest,
    plugin: ResolvedPlugin,
    config: RequiredConfig,
    storage: RequiredStorage,
) -> SourceConfigResponse:
    """Toggle the enabled flag on a migrated source."""
    try:
        set_source_enabled_state(source_id, storage, payload.enabled)
    except SourceConfigError as error:
        raise _config_error_to_http(error) from error
    return SourceConfigResponse(**build_config_view(source_id, plugin, config, storage))


@router.put("/sync/sources/{source_id}/schedule", response_model=SourceConfigResponse)
def set_source_schedule_endpoint(
    source_id: str,
    payload: SourceScheduleUpdateRequest,
    plugin: ResolvedPlugin,
    config: RequiredConfig,
    storage: RequiredStorage,
) -> SourceConfigResponse:
    """Set the sync cadence on a migrated source."""
    if payload.interval not in SYNC_INTERVAL_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"Interval must be one of: {', '.join(SYNC_INTERVAL_KEYS)}.",
        )
    try:
        set_source_schedule(source_id, storage, payload.interval)
    except SourceConfigError as error:
        raise _config_error_to_http(error) from error
    return SourceConfigResponse(**build_config_view(source_id, plugin, config, storage))


# Global settings endpoints. Business logic lives in ``src.settings.service``
# (shared with the CLI ``settings`` group); the routes below adapt those
# framework-agnostic helpers to FastAPI / Pydantic.


@router.get(
    "/settings",
    response_model=SettingsResponse,
    response_model_exclude_unset=True,
)
def get_settings(config: RequiredConfig, storage: RequiredStorage) -> SettingsResponse:
    """Return every in-scope setting grouped by section (secrets masked)."""
    return SettingsResponse(**build_settings_view(config, storage))


@router.put(
    "/settings",
    response_model=SettingsResponse,
    response_model_exclude_unset=True,
    dependencies=[Depends(require_config)],
)
def update_settings(
    request: SettingsUpdateRequest, storage: RequiredStorage
) -> SettingsResponse:
    """Validate and apply a batch of non-sensitive setting updates.

    Validation is all-or-nothing: an invalid key or value returns 422 (with the
    offending key + reason) and nothing is written. Non-restart settings are
    live-applied to the running config; restart-required settings persist and
    apply on next boot.

    The config arrives through ``writable_config`` rather than as a
    ``RequiredConfig`` parameter because the live-apply is a read-copy-store of
    the running config and has to be serialised against the other writers of
    it. The view is built inside the same block, so the response describes the
    config the save landed in rather than one a concurrent reload has since
    replaced.
    """
    try:
        with writable_config() as config:
            apply_settings(config, storage, request.updates)
            view = build_settings_view(config, storage)
    except SettingsValidationError as error:
        raise HTTPException(
            status_code=422,
            detail={"key": error.key, "reason": error.reason},
        ) from error
    return SettingsResponse(**view)


@router.delete(
    "/settings/{key}",
    response_model=SettingsResponse,
    response_model_exclude_unset=True,
    dependencies=[Depends(require_config)],
)
def reset_setting_endpoint(key: str, storage: RequiredStorage) -> SettingsResponse:
    """Reset a setting to its default by dropping the DB override.

    Returns 404 for a key that is not in the settings registry, and 422 (with
    the offending key + reason) when the key is registered but cannot be reset
    this way — e.g. a sensitive leaf, which must go through the secret endpoint.

    Takes the config lock for the reason spelled out on ``update_settings``: it
    writes the running config the same way.
    """
    if get_entry(key) is None:
        logger.info("Settings reset miss for key=%s", sanitize_for_log(key))
        raise HTTPException(status_code=404, detail="Unknown setting.")
    try:
        with writable_config() as config:
            reset_setting(config, storage, key)
            view = build_settings_view(config, storage)
    except SettingsValidationError as error:
        raise HTTPException(
            status_code=422,
            detail={"key": error.key, "reason": error.reason},
        ) from error
    return SettingsResponse(**view)


@router.put("/settings/secret", status_code=204)
def set_setting_secret(
    request: SettingSecretRequest, storage: RequiredStorage
) -> Response:
    """Store a sensitive setting's value in the encrypted secret store."""
    try:
        set_secret(storage, request.key, request.value)
    except SettingsValidationError as error:
        raise HTTPException(
            status_code=400, detail="Not a configurable secret."
        ) from error
    return Response(status_code=204)


@router.delete("/settings/secret/{key}", status_code=204)
def clear_setting_secret(key: str, storage: RequiredStorage) -> Response:
    """Delete a sensitive setting's stored secret."""
    try:
        clear_secret(storage, key)
    except SettingsValidationError as error:
        raise HTTPException(
            status_code=400, detail="Not a configurable secret."
        ) from error
    return Response(status_code=204)


@router.get("/sync/status", response_model=SyncStatusResponse)
def get_sync_status() -> SyncStatusResponse:
    """Get the current status of every tracked sync job.

    Returns:
        Aggregate sync status with one entry per job in the
        ``jobs`` list. ``status`` is ``"running"`` when at least one job
        is still running, otherwise ``"idle"``.
    """
    sync_manager = get_sync_manager()
    status_dict = sync_manager.get_status()

    return SyncStatusResponse(
        status=status_dict["status"],
        jobs=[SyncJobResponse(**job) for job in status_dict.get("jobs", [])],
    )


# ---------------------------------------------------------------------------
# Enrichment endpoints
# ---------------------------------------------------------------------------


@router.post("/enrichment/start")
def start_enrichment(
    request: EnrichmentStartRequest,
    storage: RequiredStorage,
    config: RequiredConfig,
) -> dict[str, Any]:
    """Start background metadata enrichment.

    Enriches content items with genres, tags, and descriptions from
    external APIs (TMDB, OpenLibrary, RAWG).

    Args:
        request: Enrichment start request with optional filters

    Returns:
        Message indicating enrichment was started or error
    """
    enrichment_config = config.get("enrichment", {})
    if not enrichment_config.get("enabled", False):
        raise HTTPException(
            status_code=400,
            detail=(
                "Enrichment is disabled. Turn it on from the Data tab, or run: "
                "settings set enrichment.enabled true"
            ),
        )

    # Map content type if provided
    content_type = None
    if request.content_type:
        try:
            content_type = ContentType.from_string(request.content_type)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid content type. Valid options: book, movie, tv_show, video_game",
            ) from None

    # The claim inside is the mutual exclusion, and it holds against the CLI
    # too — which a check-then-build here never could.
    started = EnrichmentManager(storage, config).start_enrichment(
        content_type=content_type,
        user_id=request.user_id,
        include_not_found=request.retry_not_found,
    )
    if not started:
        raise HTTPException(status_code=409, detail="Enrichment job already running")

    type_desc = content_type.value if content_type else "all types"
    retry_msg = " (retrying not_found)" if request.retry_not_found else ""
    return {
        "message": f"Started enrichment for {type_desc}{retry_msg}",
        "status": "started",
    }


@router.post("/enrichment/stop")
def stop_enrichment(storage: RequiredStorage) -> dict[str, Any]:
    """Stop the current enrichment job, whichever process started it."""
    if not storage.enrichment_jobs.request_stop():
        raise HTTPException(status_code=400, detail="No enrichment job is running.")

    return {"message": "Enrichment job stop requested", "status": "stopping"}


@router.get("/enrichment/status", response_model=EnrichmentJobStatusResponse)
def get_enrichment_status(storage: RequiredStorage) -> EnrichmentJobStatusResponse:
    """The live enrichment job, whichever process started it."""
    status = job_status(storage)

    return EnrichmentJobStatusResponse(
        running=status.running,
        completed=status.completed,
        cancelled=status.cancelled,
        items_processed=status.items_processed,
        items_enriched=status.items_enriched,
        items_failed=status.items_failed,
        items_not_found=status.items_not_found,
        total_items=status.total_items,
        current_item=status.current_item,
        content_type=status.content_type,
        errors=status.errors,
        elapsed_seconds=status.elapsed_seconds,
        progress_percent=status.progress_percent,
    )


@router.get("/enrichment/stats", response_model=EnrichmentStatsResponse)
def get_enrichment_stats(
    config: RequiredConfig,
    storage: RequiredStorage,
    user_id: int = Query(1, ge=1, description="User ID for filtering stats"),
) -> EnrichmentStatsResponse:
    """Get enrichment statistics.

    Args:
        user_id: User ID for filtering stats

    Returns:
        Enrichment statistics
    """
    enrichment_config = config.get("enrichment", {})
    enrichment_enabled = enrichment_config.get("enabled", False)

    stats = storage.enrichment.stats(user_id=user_id)

    return EnrichmentStatsResponse(
        enabled=enrichment_enabled,
        total=cast(int, stats.get("total", 0)),
        resettable=cast(int, stats.get("resettable", 0)),
        enriched=cast(int, stats.get("enriched", 0)),
        pending=cast(int, stats.get("pending", 0)),
        not_found=cast(int, stats.get("not_found", 0)),
        failed=cast(int, stats.get("failed", 0)),
        by_provider=cast(dict[str, int], stats.get("by_provider", {})),
        by_quality=cast(dict[str, int], stats.get("by_quality", {})),
    )


@router.post("/enrichment/reset")
def reset_enrichment(
    request: EnrichmentResetRequest,
    storage: RequiredStorage,
) -> dict[str, Any]:
    """Re-queue items for enrichment, by provider, content type or one item."""
    content_type = None
    if request.content_type:
        try:
            content_type = ContentType.from_string(request.content_type)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid content type. Valid options: book, movie, tv_show, video_game",
            ) from None

    if request.item_id is not None:
        if request.provider or request.content_type:
            raise HTTPException(
                status_code=400,
                detail="item_id cannot be combined with provider or content_type.",
            )
        if not storage.get_content_item(request.item_id, user_id=request.user_id):
            raise HTTPException(status_code=404, detail="Item not found")

    count = storage.enrichment.reset(
        provider=request.provider,
        content_type=content_type,
        user_id=request.user_id,
        content_item_id=request.item_id,
    )

    return {"message": f"Reset enrichment status for {count} item(s)", "count": count}


@router.get("/profile")
def get_profile(
    storage: RequiredStorage, user_id: int = Query(default=1, ge=1)
) -> ProfileResponse:
    """Get a user's preference profile summary."""
    return ProfileResponse.model_validate(
        profile_payload(user_id, storage.profiles.get(user_id))
    )


@router.post("/profile/regenerate")
def regenerate_profile(
    storage: RequiredStorage, user_id: int = Query(default=1, ge=1)
) -> ProfileResponse:
    """Force regeneration of a user's preference profile."""
    profile = ProfileGenerator(storage).regenerate_and_save(user_id)

    return ProfileResponse(
        user_id=profile.user_id,
        genre_affinities=profile.genre_affinities,
        theme_preferences=profile.theme_preferences,
        anti_preferences=profile.anti_preferences,
        cross_media_patterns=profile.cross_media_patterns,
        generated_at=profile.generated_at,
    )


# ---------------------------------------------------------------------------
# Theme endpoints
# ---------------------------------------------------------------------------


@router.get("/themes", response_model=list[ThemeResponse])
def list_themes() -> list[ThemeResponse]:
    """List the UI themes this install ships and the ones in private/themes/.

    Returns:
        Theme metadata sorted by id, each naming where its stylesheet is served.
    """
    return installed_themes()


@router.get("/themes/default")
def get_default_theme() -> dict[str, str]:
    """Get the theme a user who has picked none is painted.

    Returns:
        Dictionary with the default theme ID.
    """
    return {"theme": DEFAULT_THEME_ID}


@router.get("/users/{user_id}/theme", response_model=ThemePreferenceResponse)
def get_user_theme(
    user_id: UserIdPath, storage: RequiredStorage
) -> ThemePreferenceResponse:
    """Get the theme this user's interface paints, empty when none is stored.

    Returns:
        The stored theme id.
    """
    return ThemePreferenceResponse(theme=storage.ui_settings.get_theme(user_id))


@router.put("/users/{user_id}/theme", response_model=ThemePreferenceResponse)
def set_user_theme(
    user_id: UserIdPath, request: ThemePreferenceRequest, storage: RequiredStorage
) -> ThemePreferenceResponse:
    """Set the theme this user's interface paints, as ``theme set`` does.

    Returns:
        The stored theme id.

    Raises:
        HTTPException: 400 for a theme this install does not have, 404 when
            nobody carries *user_id*.
    """
    if request.theme not in installed_theme_ids():
        raise HTTPException(status_code=400, detail="Theme not installed.")
    try:
        storage.ui_settings.set_theme(user_id, request.theme)
    except UnknownUserError as error:
        raise HTTPException(status_code=404, detail="User not found.") from error
    return ThemePreferenceResponse(theme=request.theme)


# ---------------------------------------------------------------------------
# GOG OAuth endpoints
# ---------------------------------------------------------------------------


def _source_id_query(default: str) -> Any:
    """The id of the source being connected, which owns the stored token.

    Defaulted to the plugin's own name so a client written before the
    parameter existed still addresses the source it used to.
    """
    return Query(default, pattern=SOURCE_ID_PATTERN)


def _disconnect_source(
    source_id: str,
    plugin_name: str,
    config: dict[str, Any],
    storage: StorageManager,
    user_id: int,
    detail: str,
) -> None:
    """Delete *source_id*'s refresh token, or 404 with *detail*.

    An id this route may not act on gets the same refusal as one holding no
    token: telling them apart names sources the caller did not ask about.
    """
    if not may_revoke(plugin_name, source_id, config, storage, user_id):
        logger.info(
            "Disconnect refused for source_id=%s on plugin %s",
            sanitize_for_log(source_id),
            sanitize_for_log(plugin_name),
        )
        raise HTTPException(status_code=404, detail=detail)

    if not storage.credentials.delete(user_id, source_id, REFRESH_TOKEN_KEY):
        raise HTTPException(status_code=404, detail=detail)


@router.get("/gog/status")
def get_gog_status(
    config: RequiredConfig,
    storage: RequiredStorage,
    source_id: str = _source_id_query(GOG_SOURCE_ID),
) -> dict[str, Any]:
    """Get GOG integration status.

    Returns:
        Status of GOG integration (enabled, connected, auth_url).
    """
    enabled = is_gog_enabled(config, storage=storage, source_id=source_id)
    connected = has_gog_token(config, storage=storage, source_id=source_id)

    return {
        "enabled": enabled,
        "connected": connected,
        "auth_url": get_gog_auth_url() if enabled else None,
    }


@router.post("/gog/exchange")
def exchange_gog_token(
    request: GogExchangeRequest,
    config: RequiredConfig,
    storage: RequiredStorage,
    source_id: str = _source_id_query(GOG_SOURCE_ID),
) -> dict[str, Any]:
    """Exchange GOG authorization code for tokens.

    Accepts either the raw authorization code or the full redirect URL.
    Saves the refresh token under *source_id*.

    Returns:
        Success message. The token is never included in the HTTP response.
    """
    if not is_gog_enabled(config, storage=storage, source_id=source_id):
        raise HTTPException(
            status_code=400,
            detail="GOG is not enabled for that source.",
        )

    try:
        # Extract code from input (handles both URL and raw code)
        code = extract_gog_code(request.code_or_url)

        # Exchange code for tokens
        tokens = exchange_gog_tokens(code)
        refresh_token = tokens["refresh_token"]

        # Save token to encrypted database storage
        save_gog_token(storage, refresh_token, source_id=source_id)
        logger.info("Connected GOG account for %s", sanitize_for_log(source_id))

        return {
            "success": True,
            "message": "GOG account connected successfully! You can now sync your GOG library.",
        }

    except GogAuthError as error:
        logger.warning("GOG auth error: %s", exception_for_log(error))
        raise HTTPException(
            status_code=400, detail="GOG authentication failed"
        ) from error
    except Exception as error:
        logger.error(
            "Unexpected error during GOG token exchange: %s", exception_for_log(error)
        )
        raise HTTPException(
            status_code=500, detail="Unexpected error during GOG authentication"
        ) from error


@router.delete("/gog/token")
def disconnect_gog(
    config: RequiredConfig,
    storage: RequiredStorage,
    source_id: str = _source_id_query(GOG_SOURCE_ID),
    user_id: int = Query(1, ge=1),
) -> dict[str, Any]:
    """Disconnect GOG by deleting the stored refresh token.

    Mirrors the CLI `auth disconnect --source gog` command.
    """
    _disconnect_source(
        source_id,
        GOG_PLUGIN,
        config,
        storage,
        user_id,
        "No active GOG connection found",
    )
    logger.info(
        "Disconnected GOG account %s for user %s", sanitize_for_log(source_id), user_id
    )
    return {"success": True, "message": "GOG disconnected."}


# ---------------------------------------------------------------------------
# Epic Games OAuth endpoints
# ---------------------------------------------------------------------------


@router.get("/epic/status")
def get_epic_status(
    config: RequiredConfig,
    storage: RequiredStorage,
    source_id: str = _source_id_query(EPIC_SOURCE_ID),
) -> dict[str, Any]:
    """Get Epic Games integration status.

    Returns:
        Status of Epic Games integration (enabled, connected, auth_url).
    """
    enabled = is_epic_enabled(config, storage=storage, source_id=source_id)
    connected = has_epic_token(config, storage=storage, source_id=source_id)

    auth_url: str | None = None
    if enabled:
        try:
            auth_url = get_epic_auth_url()
        except Exception as error:
            logger.warning(
                "Failed to generate Epic auth URL: %s", exception_for_log(error)
            )

    return {
        "enabled": enabled,
        "connected": connected,
        "auth_url": auth_url,
    }


@router.post("/epic/exchange")
def exchange_epic_token(
    request: EpicExchangeRequest,
    config: RequiredConfig,
    storage: RequiredStorage,
    source_id: str = _source_id_query(EPIC_SOURCE_ID),
) -> dict[str, Any]:
    """Exchange Epic Games authorization code for tokens.

    Accepts either the raw authorization code or JSON containing it.
    Saves the refresh token under *source_id*.

    Returns:
        Success message. The token is never included in the HTTP response.
    """
    if not is_epic_enabled(config, storage=storage, source_id=source_id):
        raise HTTPException(
            status_code=400,
            detail="Epic Games is not enabled in the current configuration.",
        )

    try:
        # Extract code from input (handles both JSON and raw code)
        code = extract_epic_code(request.code_or_json)

        # Exchange code for tokens via EPCAPI
        tokens = exchange_epic_tokens(code)
        refresh_token = tokens["refresh_token"]

        # Save token to encrypted database storage
        save_epic_token(storage, refresh_token, source_id=source_id)
        logger.info("Connected Epic Games account for %s", sanitize_for_log(source_id))

        return {
            "success": True,
            "message": "Epic Games account connected successfully! You can now sync your Epic library.",
        }

    except EpicAuthError as error:
        logger.warning("Epic Games auth error: %s", exception_for_log(error))
        raise HTTPException(
            status_code=400, detail="Epic Games authentication failed"
        ) from error
    except Exception as error:
        logger.error(
            "Unexpected error during Epic Games token exchange: %s",
            exception_for_log(error),
        )
        raise HTTPException(
            status_code=500,
            detail="Unexpected error during Epic Games authentication",
        ) from error


@router.delete("/epic/token")
def disconnect_epic(
    config: RequiredConfig,
    storage: RequiredStorage,
    source_id: str = _source_id_query(EPIC_SOURCE_ID),
    user_id: int = Query(1, ge=1),
) -> dict[str, Any]:
    """Disconnect Epic Games by deleting the stored refresh token.

    Mirrors the CLI `auth disconnect --source epic` command.
    """
    _disconnect_source(
        source_id,
        EPIC_PLUGIN,
        config,
        storage,
        user_id,
        "No active Epic Games connection found",
    )
    logger.info(
        "Disconnected Epic Games account %s for user %s",
        sanitize_for_log(source_id),
        user_id,
    )
    return {"success": True, "message": "Epic Games disconnected."}


# ---------------------------------------------------------------------------
# Trakt OAuth device-code endpoints
# ---------------------------------------------------------------------------

_TRAKT_POLL_MESSAGES: dict[DevicePollStatus, str] = {
    DevicePollStatus.PENDING: "Waiting for you to approve the request on Trakt.",
    DevicePollStatus.SLOW_DOWN: "Polling too quickly — slowing down.",
    DevicePollStatus.EXPIRED: "The authorization code expired. Start over.",
    DevicePollStatus.DENIED: "The authorization request was denied.",
}


@router.get("/trakt/status")
def get_trakt_status(
    config: RequiredConfig,
    storage: RequiredStorage,
    source_id: str = _source_id_query(TRAKT_SOURCE_ID),
    user_id: int = Query(1, ge=1),
) -> dict[str, Any]:
    """Get Trakt integration status.

    ``enabled`` means an enabled Trakt source with client credentials saved,
    so the device flow can run. ``connected`` means a refresh token is stored
    under an id this route owns.
    """
    try:
        resolve_trakt_client_credentials(
            config, storage, source_id=source_id, user_id=user_id
        )
        enabled = True
    except TraktAuthError:
        enabled = False

    # Ownership, not credential completeness: clearing the client secret would
    # otherwise read as disconnected while the token is still stored, and the
    # Data tab hangs its only revoke control off ``connected``.
    connected = has_trakt_token(config, storage, source_id, user_id)

    return {"enabled": enabled, "connected": connected}


@router.post("/trakt/start-device-flow")
def start_trakt_device_flow(
    config: RequiredConfig,
    storage: RequiredStorage,
    source_id: str = _source_id_query(TRAKT_SOURCE_ID),
    user_id: int = Query(1, ge=1),
) -> dict[str, Any]:
    """Begin the Trakt device-code flow.

    Resolves the saved client_id/client_secret server-side and returns the
    user code plus verification URL for the user to enter. The client_id and
    client_secret are never returned.

    Returning ``device_code`` to the web client is inherent to the OAuth
    device-code flow (the browser drives the polling loop), and is a conscious,
    reviewed decision for this localhost single-user deployment: exposure is
    bounded by the short device-code expiry and a server-side session mapping
    is intentionally not used here.
    """
    try:
        client_id, _ = resolve_trakt_client_credentials(
            config, storage, source_id=source_id, user_id=user_id
        )
        flow = start_device_auth_flow(client_id)
    except TraktAuthError as error:
        logger.warning("Trakt device-flow start failed: %s", exception_for_log(error))
        raise HTTPException(
            status_code=400, detail="Trakt authentication failed"
        ) from error

    return {
        "user_code": flow["user_code"],
        "verification_url": flow["verification_url"],
        "device_code": flow["device_code"],
        "expires_in": flow["expires_in"],
        "interval": flow["interval"],
    }


@router.post("/trakt/poll-device-approval")
def poll_trakt_device_approval(
    request: TraktPollRequest,
    config: RequiredConfig,
    storage: RequiredStorage,
    source_id: str = _source_id_query(TRAKT_SOURCE_ID),
    user_id: int = Query(1, ge=1),
) -> dict[str, Any]:
    """Poll Trakt once for device approval.

    The frontend calls this repeatedly at the cadence Trakt returned. On
    approval the refresh token is saved and ``connected: true`` is returned;
    otherwise the current poll ``status`` is returned. This handler performs a
    single poll and never blocks on a server-side loop.
    """
    try:
        client_id, client_secret = resolve_trakt_client_credentials(
            config, storage, source_id=source_id, user_id=user_id
        )
        result = poll_device_token(request.device_code, client_id, client_secret)
    except TraktAuthError as error:
        logger.warning(
            "Trakt device-approval poll failed: %s", exception_for_log(error)
        )
        raise HTTPException(
            status_code=400, detail="Trakt authentication failed"
        ) from error

    status = result.status
    match status:
        case DevicePollStatus.SUCCESS:
            if result.refresh_token is None:
                logger.error("Trakt poll reported success without a refresh token")
                raise HTTPException(
                    status_code=500, detail="Trakt authentication failed"
                )
            save_trakt_token(
                storage, result.refresh_token, source_id=source_id, user_id=user_id
            )
            logger.info(
                "Connected Trakt account %s for user %s",
                sanitize_for_log(source_id),
                user_id,
            )
            return {
                "connected": True,
                "message": (
                    "Trakt account connected successfully! "
                    "You can now sync your Trakt library."
                ),
            }
        case (
            DevicePollStatus.PENDING
            | DevicePollStatus.SLOW_DOWN
            | DevicePollStatus.EXPIRED
            | DevicePollStatus.DENIED
        ):
            return {
                "connected": False,
                "status": status.value,
                "message": _TRAKT_POLL_MESSAGES[status],
            }
        case _:  # pragma: no cover - exhaustiveness guard for new enum members
            assert_never(status)


@router.delete("/trakt/token")
def disconnect_trakt(
    config: RequiredConfig,
    storage: RequiredStorage,
    source_id: str = _source_id_query(TRAKT_SOURCE_ID),
    user_id: int = Query(1, ge=1),
) -> dict[str, Any]:
    """Disconnect Trakt by deleting the stored refresh token.

    Mirrors the CLI `auth disconnect --source trakt` command.
    """
    _disconnect_source(
        source_id,
        TRAKT_PLUGIN,
        config,
        storage,
        user_id,
        "No active Trakt connection found",
    )
    logger.info(
        "Disconnected Trakt account %s for user %s",
        sanitize_for_log(source_id),
        user_id,
    )
    return {"success": True, "message": "Trakt disconnected."}
