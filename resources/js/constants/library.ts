// Mirrors MAX_SEARCH_LENGTH in src/utils/sorting.py: the API rejects a longer
// search term with a 422, so the UI must never submit one.
export const MAX_SEARCH_LENGTH = 200

// The four orders VALID_SORT_OPTIONS (src/storage/sqlite_db.py) accepts, which
// `library list --sort` offers too; anything else comes back 400.
export const SORT_OPTIONS = [
  { value: 'title', label: 'Title' },
  { value: 'rating', label: 'Rating' },
  { value: 'created_at', label: 'Recently added' },
  { value: 'updated_at', label: 'Recently updated' },
] as const

export const DEFAULT_SORT = 'title'

export const MAX_CREATOR_LENGTH = 500
export const RELEASE_YEAR_TYPES: readonly string[] = ['movie', 'tv_show', 'video_game']
