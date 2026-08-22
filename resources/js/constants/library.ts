// Mirrors MAX_SEARCH_LENGTH in src/utils/sorting.py: the API rejects a longer
// search term with a 422, so the UI must never submit one.
export const MAX_SEARCH_LENGTH = 200

export const MAX_CREATOR_LENGTH = 500
export const RELEASE_YEAR_TYPES: readonly string[] = ['movie', 'tv_show', 'video_game']
