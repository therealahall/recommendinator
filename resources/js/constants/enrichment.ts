/** The providers a reset can be narrowed to. Mirrors the `--provider` choices
 *  on `enrichment reset` (src/cli/commands/_enrichment.py). */
export const ENRICHMENT_PROVIDERS = ['all', 'tmdb', 'openlibrary', 'rawg'] as const

export const PROVIDER_LABELS: Record<string, string> = {
  all: 'All providers',
  tmdb: 'TMDB',
  openlibrary: 'OpenLibrary',
  rawg: 'RAWG',
}
