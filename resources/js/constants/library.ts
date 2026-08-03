// Mirrors MAX_SEARCH_LENGTH in src/utils/sorting.py: the API rejects a longer
// search term with a 422, so the UI must never submit one.
export const MAX_SEARCH_LENGTH = 200
