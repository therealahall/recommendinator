/* TypeScript interfaces matching backend Pydantic models */

// --- Content ---

export interface ContentItemResponse {
  id: string | null
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
}

export interface RecommendationResponse {
  db_id: number | null
  title: string
  author: string | null
  score: number
  similarity_score: number
  preference_score: number
  reasoning: string
  llm_reasoning: string | null
  score_breakdown: Record<string, number>
}

// --- Users ---

export interface UserResponse {
  id: number
  username: string
  display_name: string | null
}

// --- Status ---

export interface FeaturesStatus {
  ai_enabled: boolean
  embeddings_enabled: boolean
  llm_reasoning_enabled: boolean
}

export interface RecommendationsConfig {
  max_count: number
  default_count: number
}

export interface StatusResponse {
  status: string
  version: string
  components: Record<string, boolean>
  features: FeaturesStatus
  recommendations_config: RecommendationsConfig
}

// --- Preferences ---

export interface UserPreferenceResponse {
  scorer_weights: Record<string, number>
  series_in_order: boolean
  variety_after_completion: boolean
  custom_rules: string[]
  content_length_preferences: Record<string, string>
  theme: string
}

export interface UserPreferenceUpdateRequest {
  scorer_weights?: Record<string, number>
  series_in_order?: boolean
  variety_after_completion?: boolean
  custom_rules?: string[]
  content_length_preferences?: Record<string, string>
  theme?: string
}

// --- Sync ---

export interface SyncSourceResponse {
  id: string
  display_name: string
  plugin_display_name: string
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
  error_count: number
}

export interface SyncStatusResponse {
  status: string
  job: SyncJobResponse | null
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

// --- Chat ---

export interface ChatRequest {
  user_id: number
  message: string
  content_type?: string
}

export interface MessageResponse {
  id: number
  role: string
  content: string
  tool_calls: Record<string, unknown>[] | null
  created_at: string
}

export interface MemoryResponse {
  id: number
  memory_text: string
  memory_type: string
  confidence: number
  is_active: boolean
  source: string
  created_at: string
}

export interface MemoryCreateRequest {
  user_id: number
  memory_text: string
}

export interface MemoryUpdateRequest {
  memory_text?: string
  is_active?: boolean
}

export interface ProfileResponse {
  user_id: number
  genre_affinities: Record<string, number>
  theme_preferences: string[]
  anti_preferences: string[]
  cross_media_patterns: string[]
  generated_at: string | null
}

// --- SSE Chunk Types ---

export type SseChunk =
  | { type: 'text'; content: string }
  | { type: 'tool_call'; tool: string; params: Record<string, unknown> }
  | { type: 'tool_result'; tool: string; result: Record<string, unknown> }
  | { type: 'memory'; content: string }
  | { type: 'done' }
  | { type: 'error'; content: string }

// --- Auth ---

export interface GogExchangeRequest {
  code_or_url: string
}

export interface EpicExchangeRequest {
  code_or_json: string
}

export interface AuthStatusResponse {
  authenticated: boolean
  auth_url?: string
}

// --- Item Edit ---

export interface ItemEditRequest {
  status: string
  rating?: number | null
  review?: string | null
  seasons_watched?: number[] | null
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
