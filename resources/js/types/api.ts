/* TypeScript interfaces matching backend Pydantic models */

// --- Content ---

/** One source's id for an item; an item holds one per source that named it. */
export interface ExternalId {
  source: string
  external_id: string
}

export interface ContentItemResponse {
  external_ids: ExternalId[]
  db_id: number | null
  title: string
  author: string | null
  content_type: string
  status: string
  rating: number | null
  review: string | null
  source: string | null
  ignored: boolean
  seasons_watched: number[] | null
  total_seasons: number | null
  release_year: number | null
  enriched: boolean
  genres: string[]
  tags: string[]
  description: string | null
}

export interface RecommendationResponse {
  db_id: number | null
  title: string
  author: string | null
  score: number
  reasoning: string
  score_breakdown: Record<string, number>
  variety_penalty: number
}

// --- Users ---

export interface UserResponse {
  id: number
  username: string
  display_name: string | null
  /** null for an account whose password has not changed since it was set up. */
  password_updated_at: string | null
}

/** What the SPA needs on boot to choose setup, sign-in or the app itself. */
export interface SessionResponse {
  claimed: boolean
  authenticated: boolean
  user: UserResponse | null
  /** The floor the API enforces, so the forms state and refuse the same one. */
  min_password_length: number
}

/** First-run account creation. Sent once, before any account exists. */
export interface SetupRequest {
  username: string
  display_name: string
  password: string
}

export interface LoginRequest {
  username: string
  password: string
}

export interface UserUpdateRequest {
  username: string
  display_name: string
}

/** The current password travels with the new one: the session alone must not be
 *  enough to change it, or a borrowed unlocked phone is a permanent takeover. */
export interface PasswordChangeRequest {
  current_password: string
  new_password: string
}

// --- Status ---

export interface RecommendationsConfig {
  max_count: number
  default_count: number
}

export interface StatusResponse {
  status: string
  version: string
  components: Record<string, boolean>
  recommendations_config: RecommendationsConfig
}

// --- Preferences ---

export interface UserPreferenceResponse {
  scorer_weights: Record<string, number>
  series_in_order: boolean
  variety_penalty: number
  custom_rules: string[]
  content_length_preferences: Record<string, string>
  theme: string
}

export interface UserPreferenceUpdateRequest {
  scorer_weights?: Record<string, number>
  series_in_order?: boolean
  variety_penalty?: number
  custom_rules?: string[]
  content_length_preferences?: Record<string, string>
  theme?: string
}

// --- Sync ---

export interface PluginImportErrorResponse {
  module: string
  reason: string
}

export interface PluginNotLoadedResponse {
  plugin: string
  // The whole discovery pass: no failure can be tied to the plugin above.
  failures: PluginImportErrorResponse[]
}

export interface SyncSourceResponse {
  id: string
  display_name: string
  plugin_display_name: string
  enabled: boolean
  // Set when the source's plugin is missing. Such a source is listed so the
  // failure is visible, and can never sync.
  plugin_not_loaded: PluginNotLoadedResponse | null
  /** Already resolved, so the plugin default is never applied client-side. */
  sync_interval: string
  last_run_at: string | null
  last_run_status: string | null
  next_run_at: string | null
}

/** A cadence preset. Server-side, so no interface retypes the list. */
export interface SyncIntervalOption {
  key: string
  label: string
}

export type SourceFieldType = 'str' | 'int' | 'float' | 'bool' | 'list'

export interface SourceFieldSchema {
  name: string
  field_type: SourceFieldType
  required: boolean
  default: unknown
  description: string
  sensitive: boolean
}

export interface SourceSchemaResponse {
  source_id: string
  plugin: string
  plugin_display_name: string
  fields: SourceFieldSchema[]
  sync_intervals: SyncIntervalOption[]
}

export interface SourceConfigResponse {
  source_id: string
  plugin: string
  plugin_display_name: string
  enabled: boolean
  migrated: boolean
  migrated_at: string | null
  field_values: Record<string, unknown>
  secret_status: Record<string, boolean>
  sync_interval: string
}

export interface SourceMigrationResponse {
  source_id: string
  migrated_at: string
  fields_migrated: string[]
  secrets_migrated: string[]
}

export interface PluginInfoResponse {
  name: string
  display_name: string
  description: string
  content_types: string[]
  requires_api_key: boolean
  requires_network: boolean
  fields: SourceFieldSchema[]
}

export interface PluginListResponse {
  plugins: PluginInfoResponse[]
  import_errors: PluginImportErrorResponse[]
}

export interface SourceCreateRequest {
  id: string
  plugin: string
  values: Record<string, unknown>
  enabled: boolean
}

export interface SyncSourceProgressResponse {
  source: string
  items_processed: number
  total_items: number | null
  current_item: string | null
  progress_percent: number | null
  items_added: number
  items_updated: number
  items_unchanged: number
}

export interface SyncErrorResponse {
  source: string
  message: string
}

export interface SyncJobResponse {
  source: string
  status: string
  started_at: string | null
  completed_at: string | null
  items_processed: number
  total_items: number | null
  current_item: string | null
  current_source: string | null
  error_message: string | null
  progress_percent: number | null
  items_added: number
  items_updated: number
  items_unchanged: number
  errors: SyncErrorResponse[]
  sources: SyncSourceProgressResponse[]
}

export interface SyncStatusResponse {
  status: string
  jobs: SyncJobResponse[]
}

export interface UpdateResponse {
  message: string
  /** The source ids the run resolved to. Absent when none were enabled. */
  sources?: string[]
}

// --- Import ---

export interface ImporterResponse {
  name: string
  display_name: string
  description: string
  /** False where the format itself decides, as a book-site export does. */
  requires_content_type: boolean
}

export interface ImportTemplateResponse {
  importer: string
  content_type: string
  filename: string
}

/** What one upload did. ``import --format json`` emits this key set field for
 *  field, so neither interface may add, drop or rename one alone. */
export interface ImportResponse {
  importer: string
  content_type: string | null
  filename: string | null
  added: number
  updated: number
  unchanged: number
  skipped: number
  failed: number
  total_rows: number
  /** One line per row that missed, capped, with a `… and N more` tally last. */
  errors: string[]
  /** What happened to the file as a whole, which no row count covers. */
  notes: string[]
}

// --- Enrichment ---

export interface EnrichmentJobStatusResponse {
  running: boolean
  completed: boolean
  cancelled: boolean
  items_processed: number
  items_enriched: number
  items_failed: number
  items_not_found: number
  total_items: number
  current_item: string
  content_type: string | null
  errors: string[]
  elapsed_seconds: number
  progress_percent: number
}

export interface EnrichmentStatsResponse {
  enabled: boolean
  total: number
  enriched: number
  pending: number
  not_found: number
  failed: number
  by_provider: Record<string, number>
  by_quality: Record<string, number>
}

// --- Themes ---

export interface ThemeResponse {
  id: string
  name: string
  description: string
  author: string
  version: string
  theme_type: string
}

// --- Preference profile ---

export interface ProfileResponse {
  user_id: number
  genre_affinities: Record<string, number>
  theme_preferences: string[]
  anti_preferences: string[]
  cross_media_patterns: string[]
  generated_at: string | null
}

// --- Auth ---

export interface GogExchangeRequest {
  code_or_url: string
}

export interface EpicExchangeRequest {
  code_or_json: string
}

/** GET /{gog,epic,trakt}/status. Only GOG and Epic carry an auth_url. */
export interface OAuthStatusResponse {
  enabled: boolean
  connected: boolean
  auth_url?: string | null
}

export interface TraktDeviceFlowResponse {
  user_code: string
  verification_url: string
  device_code: string
  expires_in: number
  interval: number
}

export type TraktPollStatus = 'pending' | 'slow_down' | 'expired' | 'denied'

export interface TraktPollResponse {
  connected: boolean
  status?: TraktPollStatus
  message: string
}

// --- Item Edit ---

export interface ItemEditRequest {
  status: string
  rating?: number | null
  review?: string | null
  seasons_watched?: number[] | null
  genres?: string[]
  tags?: string[]
  description?: string | null
  release_year?: number | null
  creator?: string | null
}

export interface IgnoreItemRequest {
  ignored: boolean
}

// --- Enrichment Requests ---

export interface EnrichmentStartRequest {
  content_type?: string
  user_id?: number
  retry_not_found?: boolean
}

// --- Settings (global database-backed config) ---

export type SettingType = 'bool' | 'int' | 'float' | 'string' | 'list' | 'enum'
export type SettingWidget = 'toggle' | 'number' | 'text' | 'tags' | 'select'

export interface SettingValidation {
  min: number | null
  max: number | null
  max_length: number | null
  pattern: string | null
}

// Discriminated by `sensitive`. Non-sensitive carry `value` + `db_overridden`;
// sensitive carry `has_secret` and no value.
interface SettingViewBase {
  key: string
  section: string
  label: string
  help: string
  type: SettingType
  widget: SettingWidget
  choices: string[] | null
  validation: SettingValidation | null
  advanced: boolean
  restart_required: boolean
  sensitive: boolean
}

export interface SettingViewValue extends SettingViewBase {
  sensitive: false
  value: string | number | boolean | string[] | null
  db_overridden: boolean
}

export interface SettingViewSecret extends SettingViewBase {
  sensitive: true
  has_secret: boolean
}

export type SettingView = SettingViewValue | SettingViewSecret

export interface SettingsSection {
  section: string
  settings: SettingView[]
}

export interface SettingsResponse {
  sections: SettingsSection[]
}

export interface SettingsUpdateRequest {
  updates: Record<string, unknown>
}

export interface SettingSecretRequest {
  key: string
  value: string
}

/** Body of a 422 from PUT /settings: which key failed and why. */
export interface SettingValidationError {
  key: string
  reason: string
}

// --- Duplicates ---

/** `also_offered` carries why the page holds this copy in a second block, or
 *  is empty where it does not. */
export interface DuplicateSide {
  db_id: number
  title: string
  source: string | null
  creator: string | null
  release_year: number | null
  also_offered: string
}

/** Every copy of one work, and `survivor_id` the copy proposed to keep.
 *  `evidence` is `normalized_title` (the save door's own key) or
 *  `title_qualifier` (the looser key, which drops a trailing parenthetical). */
export interface DuplicateSuggestion {
  content_type: string
  evidence: string
  evidence_label: string
  evidence_detail: string
  survivor_id: number
  copies: DuplicateSide[]
}

/** `total` counts the whole filtered set; `skipped_note` is outside it. */
export interface DuplicateSuggestionPage {
  total: number
  skipped_note: string
  suggestions: DuplicateSuggestion[]
}

export interface MergeRecord {
  id: number
  survivor_id: number
  survivor_title: string
  absorbed_id: number
  absorbed_title: string
  evidence: string
  /** Rendered as it arrives, and it carries `evidence_detail` where there is one. */
  evidence_label: string
  evidence_detail: string | null
  merged_at: string
}

export interface DeclinedPair {
  one_id: number
  one_title: string
  other_id: number
  other_title: string
}
